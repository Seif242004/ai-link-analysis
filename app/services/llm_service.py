"""Provider-agnostic LLM client (Gemini / OpenAI / Anthropic) over plain HTTPS.

Uses httpx directly (no vendor SDKs) so providers are swappable via env vars,
requests share one connection pool, and retries/fallback live in one place.
"""
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.errors import LLMError

log = logging.getLogger(__name__)

# USD per 1M tokens (input, output). List prices collected Nov 2026 from provider
# docs/aggregators - they change often; verify before relying on them.
PRICING_PER_M: dict[str, tuple[float, float]] = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-4.1-nano": (0.10, 0.40),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00),
}


def estimate_cost(model: str, in_tok: int, out_tok: int) -> float | None:
    for name in sorted(PRICING_PER_M, key=len, reverse=True):  # longest prefix wins
        if model.startswith(name):
            p_in, p_out = PRICING_PER_M[name]
            return (in_tok * p_in + out_tok * p_out) / 1_000_000
    return None


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    used_fallback: bool = False

    @property
    def cost_usd(self) -> float | None:
        return estimate_cost(self.model, self.input_tokens, self.output_tokens)


def extract_json(text: str) -> dict:
    """Parse JSON from model text, tolerating ```json fences and stray prose."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


class _Transient(Exception):
    """429 / 5xx / network timeout: worth retrying."""


class LLMService:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.s = settings

    # ---- public --------------------------------------------------------
    async def generate(self, system: str, user: str) -> LLMResult:
        """Primary provider with retries; fall back to the secondary on failure."""
        primary = (self.s.llm_provider, self.s.llm_model)
        fallback = (self.s.llm_fallback_provider, self.s.llm_fallback_model)
        try:
            return await self._with_retries(*primary, system, user)
        except LLMError as primary_err:
            if fallback != primary and self._key(fallback[0]):
                log.warning("primary LLM %s failed (%s); trying fallback %s", primary, primary_err.message, fallback)
                res = await self._with_retries(*fallback, system, user)
                res.used_fallback = True
                return res
            raise

    # ---- internals -----------------------------------------------------
    def _key(self, provider: str) -> str | None:
        secret = {
            "gemini": self.s.gemini_api_key,
            "openai": self.s.openai_api_key,
            "anthropic": self.s.anthropic_api_key,
            "nvidia": self.s.nvidia_api_key,
        }.get(provider)
        return secret.get_secret_value() if secret else None

    async def _with_retries(self, provider: str, model: str, system: str, user: str) -> LLMResult:
        key = self._key(provider)
        if not key:
            raise LLMError(f"No API key configured for LLM provider '{provider}'.")
        call = {"gemini": self._gemini, "openai": self._openai, "anthropic": self._anthropic, "nvidia": self._nvidia}.get(provider)
        if call is None:
            raise LLMError(f"Unknown LLM provider '{provider}'.")

        last: Exception | None = None
        for attempt in range(self.s.llm_max_retries + 1):
            started = time.perf_counter()
            try:
                text, in_tok, out_tok = await call(key, model, system, user)
            except _Transient as exc:
                last = exc
                await asyncio.sleep(min(2**attempt, 8))  # 1s, 2s, 4s...
                continue
            except httpx.TimeoutException as exc:
                last = exc
                continue
            except httpx.RequestError as exc:
                last = exc
                await asyncio.sleep(1)
                continue
            if not text:
                last = LLMError("Empty response from model.")
                continue
            return LLMResult(text, provider, model, in_tok, out_tok, int((time.perf_counter() - started) * 1000))
        raise LLMError(f"LLM provider '{provider}' failed after retries: {type(last).__name__}: {last}")

    @staticmethod
    def _check(resp: httpx.Response) -> dict:
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _Transient(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            # Never echo response bodies verbatim; they can contain request details.
            raise LLMError(f"LLM API rejected the request (HTTP {resp.status_code}). Check model name and API key.")
        return resp.json()

    async def _gemini(self, key: str, model: str, system: str, user: str):
        resp = await self.client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key},  # header, not ?key=, so it never lands in logs/URLs
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",  # native JSON mode
                    "temperature": 0.2,
                    "maxOutputTokens": self.s.llm_max_output_tokens,
                },
            },
            timeout=self.s.llm_timeout_seconds,
        )
        data = self._check(resp)
        cands = data.get("candidates") or []
        parts = (cands[0].get("content", {}).get("parts", []) if cands else [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        u = data.get("usageMetadata", {})
        out_tok = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)  # thinking is billed as output
        return text, u.get("promptTokenCount", 0), out_tok

    async def _nvidia(self, key: str, model: str, system: str, user: str):
        # build.nvidia.com exposes an OpenAI-compatible API. JSON mode isn't supported by
        # every hosted model, so we rely on the prompt + extract_json() + Pydantic repair.
        return await self._openai(
            key, model, system, user,
            url="https://integrate.api.nvidia.com/v1/chat/completions", json_mode=False, temperature=0.2,
            extra={"chat_template_kwargs": {"enable_thinking": False}},  # skip slow reasoning phase
        )

    async def _openai(self, key: str, model: str, system: str, user: str, *,
                      url: str = "https://api.openai.com/v1/chat/completions",
                      json_mode: bool = True, temperature: float | None = None, extra: dict | None = None):
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_completion_tokens" if json_mode else "max_tokens": self.s.llm_max_output_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if temperature is not None:
            body["temperature"] = temperature
        if extra:
            body.update(extra)
        resp = await self.client.post(
            url, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=self.s.llm_timeout_seconds,
        )
        data = self._check(resp)
        choices = data.get("choices") or []
        text = choices[0]["message"].get("content") or "" if choices else ""
        u = data.get("usage", {})
        return text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)

    async def _anthropic(self, key: str, model: str, system: str, user: str):
        resp = await self.client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            json={
                "model": model,
                "max_tokens": self.s.llm_max_output_tokens,
                "temperature": 0.2,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.s.llm_timeout_seconds,
        )
        data = self._check(resp)
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        u = data.get("usage", {})
        return text, u.get("input_tokens", 0), u.get("output_tokens", 0)
