"""Orchestrates: validate -> extract -> LLM -> validate output -> post-process."""
import logging
import time
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.errors import LLMOutputError
from app.prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from app.schemas import AnalysisMeta, AnalysisSections, LinkAnalysisResponse, LLMOutput
from app.services.competitor_service import build_comparison_table, finalize_competitors
from app.services.llm_service import LLMResult, LLMService, extract_json
from app.services.website_extractor import build_context, extract_website
from app.utils.validators import validate_url

log = logging.getLogger(__name__)


def _first(items: list[str]) -> str:
    return items[0] if items else ""


class AnalysisService:
    def __init__(self, client: httpx.AsyncClient, llm: LLMService, settings: Settings | None = None):
        self.client = client
        self.llm = llm
        self.s = settings or get_settings()
        # In-memory TTL cache: avoids paying for the same analysis twice.
        # Single-process only; swap for Redis if you run multiple workers.
        self._cache: dict[str, tuple[float, LinkAnalysisResponse]] = {}

    # ------------------------------------------------------------- cache
    def _cache_get(self, key: str) -> LinkAnalysisResponse | None:
        hit = self._cache.get(key)
        if not hit:
            return None
        stored_at, value = hit
        if time.monotonic() - stored_at > self.s.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return value

    def _cache_put(self, key: str, value: LinkAnalysisResponse) -> None:
        if len(self._cache) >= self.s.cache_max_entries:
            self._cache.pop(min(self._cache, key=lambda k: self._cache[k][0]))  # evict oldest
        self._cache[key] = (time.monotonic(), value)

    # ----------------------------------------------------------- LLM step
    async def _run_llm(self, context: str) -> tuple[LLMOutput, list[LLMResult]]:
        """Call the LLM; on invalid output do ONE repair round-trip, then give up."""
        results: list[LLMResult] = []
        first = await self.llm.generate(SYSTEM_PROMPT, build_user_prompt(context))
        results.append(first)
        try:
            return LLMOutput.model_validate(extract_json(first.text)), results
        except ValueError as err:  # JSONDecodeError and pydantic.ValidationError both subclass ValueError
            log.warning("LLM output invalid (%s); attempting repair", type(err).__name__)
            detail = _short_errors(err)

        second = await self.llm.generate(SYSTEM_PROMPT, build_repair_prompt(first.text[:8000], detail))
        results.append(second)
        try:
            return LLMOutput.model_validate(extract_json(second.text)), results
        except ValueError as err:
            log.error("LLM output still invalid after repair: %s", _short_errors(err))
            raise LLMOutputError("The language model returned an invalid analysis. Please try again.") from None

    # -------------------------------------------------------------- main
    async def analyze(self, raw_url: str) -> LinkAnalysisResponse:
        t0 = time.perf_counter()
        url = await validate_url(raw_url)  # syntax + SSRF check

        cached = self._cache_get(url)
        if cached:
            meta = cached.meta.model_copy(update={"cached": True, "total_ms": int((time.perf_counter() - t0) * 1000)})
            return cached.model_copy(update={"meta": meta})

        site = await extract_website(self.client, url)
        context = build_context(site, self.s.max_content_chars)
        out, results = await self._run_llm(context)

        # ---- post-processing (code-owned fields; the LLM never sets these)
        brand = out.brand
        if not brand.name.strip():
            brand.name = site.brand_hint or urlsplit(site.final_url).hostname or url
        ca = out.competitor_analysis
        ca.competitors = await finalize_competitors(
            self.client, ca.competitors, brand.name, site.final_url, self.s.verify_competitors
        )
        ca.comparison_table = build_comparison_table(
            brand.name,
            _first(ca.main_products_services),
            _first(out.target_audience.primary_audience),
            _first(ca.strengths),
            _first(ca.opportunities),
            ca.competitors,
        )
        seo = out.seo_improvements
        seo.current_title = site.main.title or None
        seo.current_meta_description = site.main.meta_description or None
        seo.title_length = len(seo.recommended_title)
        seo.meta_description_length = len(seo.meta_description)

        warnings = list(site.warnings)
        if len(ca.competitors) < 3:
            warnings.append("Fewer than 3 competitors could be suggested with confidence.")
        if any(s.source == "inferred" for s in out.top_services):
            warnings.append("Some services are marked 'inferred': they were not found explicitly on the website.")
        warnings.append("Competitors, keywords and queries are AI-generated suggestions; no search-volume or ranking data is included.")

        costs = [r.cost_usd for r in results]
        last = results[-1]
        meta = AnalysisMeta(
            provider=last.provider,
            model=last.model,
            used_fallback=any(r.used_fallback for r in results),
            input_tokens=sum(r.input_tokens for r in results),
            output_tokens=sum(r.output_tokens for r in results),
            llm_latency_ms=sum(r.latency_ms for r in results),
            total_ms=int((time.perf_counter() - t0) * 1000),
            estimated_cost_usd=round(sum(costs), 6) if all(c is not None for c in costs) else None,
            pages_analyzed=site.pages_analyzed,
        )
        response = LinkAnalysisResponse(
            website=url,
            brand=brand,
            analysis=AnalysisSections(**{f: getattr(out, f) for f in AnalysisSections.model_fields}),
            meta=meta,
            warnings=warnings,
        )
        self._cache_put(url, response)
        log.info(
            "analyzed %s pages=%d tokens=%d/%d cost=$%s total=%dms",
            url, len(site.pages_analyzed), meta.input_tokens, meta.output_tokens, meta.estimated_cost_usd, meta.total_ms,
        )
        return response


def _short_errors(err: Exception) -> str:
    if isinstance(err, ValidationError):
        return "\n".join(f"- {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors()[:15])
    return f"- invalid JSON: {err}"
