"""Measure latency, tokens, cost and schema-validity per model on real sites.

Usage (needs the relevant API keys in .env):
    python -m scripts.benchmark https://site1.com https://site2.com \
        --models gemini:gemini-3.1-flash-lite openai:gpt-5.4-mini anthropic:claude-haiku-4-5 --runs 2

Output: a markdown table you can paste into the README. Latency/cost/validity are
MEASURED; "accuracy" is NOT scored automatically - review the saved outputs
(benchmark_outputs/) by hand and grade them against the site content.
"""
import argparse
import asyncio
import json
import statistics
from pathlib import Path

import httpx

from app.config import get_settings
from app.errors import AppError
from app.prompts import SYSTEM_PROMPT, build_user_prompt
from app.schemas import LLMOutput
from app.services.llm_service import LLMService, extract_json
from app.services.website_extractor import build_context, extract_website
from app.utils.validators import validate_url


async def main(urls: list[str], models: list[str], runs: int) -> None:
    base = get_settings()
    out_dir = Path("benchmark_outputs")
    out_dir.mkdir(exist_ok=True)
    stats: dict[str, dict] = {m: {"lat": [], "cost": [], "in": [], "out": [], "ok": 0, "n": 0} for m in models}

    async with httpx.AsyncClient(timeout=15) as client:
        contexts = {}
        for raw in urls:
            url = await validate_url(raw)
            contexts[url] = build_context(await extract_website(client, url), base.max_content_chars)

        for spec in models:
            provider, model = spec.split(":", 1)
            # no fallback during benchmarking: we want to measure this model only
            settings = base.model_copy(update={"llm_provider": provider, "llm_model": model, "llm_fallback_provider": provider, "llm_fallback_model": model})
            llm = LLMService(client, settings)
            for url, ctx in contexts.items():
                for i in range(runs):
                    st = stats[spec]
                    st["n"] += 1
                    try:
                        res = await llm.generate(SYSTEM_PROMPT, build_user_prompt(ctx))
                    except AppError as exc:
                        print(f"[{spec}] {url} run {i}: {exc.message}")
                        continue
                    st["lat"].append(res.latency_ms)
                    st["in"].append(res.input_tokens)
                    st["out"].append(res.output_tokens)
                    if res.cost_usd is not None:
                        st["cost"].append(res.cost_usd)
                    try:
                        LLMOutput.model_validate(extract_json(res.text))
                        st["ok"] += 1
                    except ValueError:
                        pass
                    safe = url.split("//")[1].strip("/").replace("/", "_")
                    fname = f"{spec}__{safe}__{i}.json".replace(":", "_").replace("/", "_")  # model ids contain '/'
                    (out_dir / fname).write_text(res.text, encoding="utf-8")

    print("\n| Model | Runs | Valid JSON/schema | Median latency (s) | Avg in tok | Avg out tok | Avg cost / analysis |")
    print("|---|---|---|---|---|---|---|")
    for spec, st in stats.items():
        if not st["lat"]:
            print(f"| {spec} | {st['n']} | no successful runs | - | - | - | - |")
            continue
        cost = f"${statistics.mean(st['cost']):.4f}" if st["cost"] else "n/a"
        print(f"| {spec} | {st['n']} | {st['ok']}/{st['n']} | {statistics.median(st['lat']) / 1000:.1f} | "
              f"{statistics.mean(st['in']):.0f} | {statistics.mean(st['out']):.0f} | {cost} |")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--models", nargs="+", required=True, help="provider:model, e.g. gemini:gemini-3.1-flash-lite")
    ap.add_argument("--runs", type=int, default=1)
    a = ap.parse_args()
    asyncio.run(main(a.urls, a.models, a.runs))
