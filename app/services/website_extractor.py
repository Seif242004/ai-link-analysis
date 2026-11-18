"""Fetch a website safely and turn its HTML into compact, LLM-ready text.

Pipeline: validate -> fetch (manual redirects, SSRF check on every hop, size
cap) -> parse (title/meta/headings/JSON-LD/nav/body) -> pick up to N extra
same-site pages (about/services/...) -> build a de-duplicated, truncated context.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.errors import (
    AppError,
    ContentExtractionError,
    WebsiteBlockedError,
    WebsiteTimeoutError,
    WebsiteUnreachableError,
)
from app.utils.validators import ensure_public_host, normalize_url

log = logging.getLogger(__name__)

MAX_REDIRECTS = 5
REDIRECT_CODES = {301, 302, 303, 307, 308}
STRIP_TAGS = ["script", "style", "noscript", "svg", "iframe", "canvas", "template", "form"]
CHROME_TAGS = ["nav", "footer", "header", "aside"]  # boilerplate, excluded from body text
EXTRA_PAGE_HINTS = ("service", "solution", "product", "about", "what-we-do", "offering", "company", "pricing")
SKIP_LINK_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".mp4", ".webp", ".css", ".js")


@dataclass
class ParsedPage:
    url: str
    title: str = ""
    meta_description: str = ""
    site_name: str = ""
    headings: list[str] = field(default_factory=list)
    nav_items: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)  # (absolute href, anchor text)
    structured_names: list[str] = field(default_factory=list)  # JSON-LD name/description
    text: str = ""


@dataclass
class ExtractedSite:
    requested_url: str
    final_url: str
    main: ParsedPage
    extra_pages: list[ParsedPage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def pages_analyzed(self) -> list[str]:
        return [self.final_url] + [p.url for p in self.extra_pages]

    @property
    def brand_hint(self) -> str:
        if self.main.site_name:
            return self.main.site_name
        if self.main.title:  # "Brand | Tagline" or "Tagline - Brand": take the shortest segment
            segments = [seg.strip() for seg in re.split(r"\s[|\-–—:·•]\s", self.main.title) if seg.strip()]
            if segments:
                return min(segments, key=len)
        return urlsplit(self.final_url).hostname or ""


# ------------------------------------------------------------------ fetching
async def fetch_html(client: httpx.AsyncClient, url: str) -> tuple[str, str]:
    """GET `url`; follow redirects manually so each hop is SSRF-checked.

    Returns (final_url, html_text). Raises AppError subclasses only.
    """
    s = get_settings()
    current = url
    headers = {
        "User-Agent": s.user_agent,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.8",
    }
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        await ensure_public_host(parts.hostname, parts.port)
        try:
            async with client.stream("GET", current, headers=headers, follow_redirects=False) as resp:
                if resp.status_code in REDIRECT_CODES:
                    location = resp.headers.get("location")
                    if not location:
                        raise WebsiteUnreachableError("Website sent a redirect without a target.")
                    current = normalize_url(urljoin(current, location))
                    continue
                if resp.status_code in (401, 403, 429, 503):
                    raise WebsiteBlockedError(
                        f"The website refused automated access (HTTP {resp.status_code}). "
                        "It may require login or use bot protection."
                    )
                if resp.status_code >= 400:
                    raise WebsiteUnreachableError(f"The website returned HTTP {resp.status_code}.")
                ctype = resp.headers.get("content-type", "").lower()
                if ctype and not any(t in ctype for t in ("html", "xml", "text/plain")):
                    raise ContentExtractionError(f"Unsupported content type '{ctype.split(';')[0]}'; expected an HTML page.")

                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > s.max_page_bytes:  # truncate instead of failing: the head holds the useful content
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks)
                try:
                    return current, raw.decode(resp.encoding or "utf-8", errors="replace")
                except LookupError:
                    return current, raw.decode("utf-8", errors="replace")
        except httpx.TimeoutException:
            raise WebsiteTimeoutError("The website took too long to respond.") from None
        except httpx.RequestError as exc:
            raise WebsiteUnreachableError(f"Could not connect to the website ({type(exc).__name__}).") from None
    raise WebsiteUnreachableError("Too many redirects.")


# ------------------------------------------------------------------- parsing
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _dedupe(items, limit: int) -> list[str]:
    seen, out = set(), []
    for item in items:
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
            if len(out) >= limit:
                break
    return out


def _json_ld_names(soup: BeautifulSoup) -> list[str]:
    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for key in ("name", "description", "slogan"):
                val = node.get(key)
                if isinstance(val, str) and 2 < len(val) < 400:
                    out.append(_clean(val))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or ""))
        except (ValueError, TypeError):
            continue
    return _dedupe(out, 12)


def parse_html(html: str, url: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    page = ParsedPage(url=url)

    page.title = _clean(soup.title.string) if soup.title and soup.title.string else ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    page.meta_description = _clean(meta.get("content", "")) if meta else ""
    og_site = soup.find("meta", property="og:site_name")
    page.site_name = _clean(og_site.get("content", "")) if og_site else ""
    if not page.meta_description:
        og_desc = soup.find("meta", property="og:description")
        page.meta_description = _clean(og_desc.get("content", "")) if og_desc else ""

    page.structured_names = _json_ld_names(soup)

    # Links + nav labels must be collected BEFORE we strip the chrome.
    nav_labels, links = [], []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(url, href)
        text = _clean(a.get_text(" "))
        links.append((absolute, text))
        if a.find_parent("nav") and 1 < len(text) < 40:
            nav_labels.append(text)
    page.links = links
    page.nav_items = _dedupe(nav_labels, 25)

    for tag in soup(STRIP_TAGS):
        tag.decompose()

    headings = [_clean(h.get_text(" ")) for h in soup.find_all(["h1", "h2", "h3"])]
    page.headings = _dedupe([h for h in headings if 2 < len(h) < 200], 40)

    for tag in soup(CHROME_TAGS):
        tag.decompose()
    root = soup.body or soup
    lines = [_clean(line) for line in root.get_text("\n").split("\n")]
    # Drop tiny fragments (menu crumbs, icons) and exact duplicates.
    page.text = "\n".join(_dedupe([l for l in lines if len(l) >= 25 or (len(l) >= 3 and " " in l)], 2000))
    return page


def pick_extra_pages(page: ParsedPage, base_url: str, limit: int) -> list[str]:
    """Choose same-site pages most likely to describe services/about."""
    if limit <= 0:
        return []
    base = urlsplit(base_url)
    base_host = (base.hostname or "").removeprefix("www.")
    scored: dict[str, int] = {}
    for href, text in page.links:
        parts = urlsplit(href)
        if parts.scheme not in ("http", "https") or (parts.hostname or "").removeprefix("www.") != base_host:
            continue
        if parts.path.lower().endswith(SKIP_LINK_EXT):
            continue
        clean = parts._replace(fragment="", query="").geturl()
        if clean.rstrip("/") == base_url.rstrip("/"):
            continue
        haystack = f"{parts.path} {text}".lower()
        score = sum(1 for hint in EXTRA_PAGE_HINTS if hint in haystack)
        if score and len(parts.path.strip("/").split("/")) <= 2:  # prefer top-level pages
            scored[clean] = max(scored.get(clean, 0), score)
    ranked = sorted(scored, key=lambda u: (-scored[u], len(u)))
    return ranked[:limit]


# ------------------------------------------------------------- orchestration
async def extract_website(client: httpx.AsyncClient, url: str) -> ExtractedSite:
    s = get_settings()
    final_url, html = await fetch_html(client, url)
    main = parse_html(html, final_url)
    site = ExtractedSite(requested_url=url, final_url=final_url, main=main)

    if len(main.text) < 200:
        site.warnings.append(
            "The page has very little server-rendered text (possibly a JavaScript-only site); analysis relies on titles/metadata and may be less specific."
        )

    extras = pick_extra_pages(main, final_url, s.max_extra_pages)
    if extras:
        results = await asyncio.gather(*(fetch_html(client, u) for u in extras), return_exceptions=True)
        for target, res in zip(extras, results):
            if isinstance(res, BaseException):
                if not isinstance(res, AppError):
                    log.warning("extra page %s failed unexpectedly: %r", target, res)
                continue  # extra pages are best-effort
            page_url, page_html = res
            site.extra_pages.append(parse_html(page_html, page_url))

    informative = len(main.text) + len(main.title) + len(main.meta_description) + sum(len(p.text) for p in site.extra_pages)
    if informative < 40:
        raise ContentExtractionError("No readable content could be extracted from this website.")
    return site


def build_context(site: ExtractedSite, max_chars: int) -> str:
    """Compact text block for the LLM. Main page gets ~60% of the budget."""
    m = site.main
    head = [f"URL: {site.final_url}", f"Title: {m.title}", f"Meta description: {m.meta_description}"]
    if m.site_name:
        head.append(f"Site name: {m.site_name}")
    if m.structured_names:
        head.append("Structured data: " + " | ".join(m.structured_names))
    if m.nav_items:
        head.append("Navigation: " + ", ".join(m.nav_items))
    if m.headings:
        head.append("Headings: " + " | ".join(m.headings))
    header = "\n".join(head)

    remaining = max(max_chars - len(header), 1000)
    main_budget = int(remaining * (0.6 if site.extra_pages else 1.0))
    parts = [header, "MAIN PAGE TEXT:\n" + m.text[:main_budget]]
    if site.extra_pages:
        per_page = (remaining - main_budget) // len(site.extra_pages)
        for p in site.extra_pages:
            body = f"PAGE {p.url} (title: {p.title}):\n" + "\n".join(p.headings[:10]) + "\n" + p.text
            parts.append(body[:per_page])
    return "\n\n".join(parts)
