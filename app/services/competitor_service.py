"""Post-processing of the LLM's competitor suggestions.

We do NOT claim the competitors are researched facts. This service:
  * drops the brand itself and duplicates,
  * normalizes + SSRF-checks competitor URLs,
  * optionally checks the competitor domain actually responds (cheap sanity
    check that filters hallucinated companies), recording the result in
    `verification`,
  * builds the side-by-side comparison table from Section 1 data.
"""
import asyncio
import logging
from urllib.parse import urlsplit

import httpx

from app.errors import AppError
from app.schemas import Competitor, ComparisonRow
from app.utils.validators import ensure_public_host, normalize_url

log = logging.getLogger(__name__)
MAX_COMPETITORS = 5


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


async def _check_reachable(client: httpx.AsyncClient, url: str) -> str:
    try:
        parts = urlsplit(url)
        await ensure_public_host(parts.hostname, parts.port)
        # Any answer below 500 (even 403/405 from bot protection) proves the domain is live.
        resp = await client.get(url, follow_redirects=False, timeout=5.0, headers={"Range": "bytes=0-0"})
        return "domain_reachable" if resp.status_code < 500 else "domain_error"
    except (AppError, httpx.HTTPError):
        return "domain_unreachable"


async def finalize_competitors(
    client: httpx.AsyncClient,
    competitors: list[Competitor],
    brand_name: str,
    brand_url: str,
    verify: bool,
) -> list[Competitor]:
    brand_host, brand_key = _host(brand_url), brand_name.strip().lower()
    seen: set[str] = set()
    kept: list[Competitor] = []

    for comp in competitors:
        key = comp.name.strip().lower()
        if not key or key in seen or key == brand_key:
            continue
        if comp.website:
            try:
                comp.website = normalize_url(comp.website)
            except AppError:
                comp.website = None
        if comp.website and _host(comp.website) == brand_host:
            continue  # it's the analysed site itself
        seen.add(key)
        comp.verification = "unverified_ai_suggestion"
        kept.append(comp)
        if len(kept) >= MAX_COMPETITORS:
            break

    if verify:
        targets = [c for c in kept if c.website]
        results = await asyncio.gather(*(_check_reachable(client, c.website) for c in targets))
        for comp, status in zip(targets, results):
            comp.verification = status
    return kept


def build_comparison_table(
    brand_name: str,
    brand_service: str,
    brand_market: str,
    brand_strength: str,
    brand_opportunity: str,
    competitors: list[Competitor],
) -> list[ComparisonRow]:
    """Rows = attributes; columns = your brand vs each competitor (spec's example layout)."""
    if not competitors:
        return []

    def col(attr: str) -> dict[str, str]:
        return {c.name: getattr(c, attr) or "—" for c in competitors}

    return [
        ComparisonRow(attribute="Brand Name", your_brand=brand_name, competitors={c.name: c.name for c in competitors}),
        ComparisonRow(attribute="Main Service", your_brand=brand_service or "—", competitors=col("main_service")),
        ComparisonRow(attribute="Target Market", your_brand=brand_market or "—", competitors=col("target_market")),
        ComparisonRow(attribute="Key Strength", your_brand=brand_strength or "—", competitors=col("key_strength")),
        ComparisonRow(attribute="Opportunity", your_brand=brand_opportunity or "—", competitors=col("opportunity")),
    ]
