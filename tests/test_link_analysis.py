"""Offline tests: no network, no real LLM. Run with:  pytest -q"""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import AppError, BlockedAddressError, InvalidURLError, WebsiteBlockedError
from app.main import app
from app.schemas import LLMOutput, SearchQuery
from app.services.analysis_service import AnalysisService
from app.services.llm_service import LLMResult, estimate_cost, extract_json
from app.services.website_extractor import build_context, extract_website, fetch_html, parse_html
from app.utils.validators import ensure_public_host, normalize_url

PUBLIC_IP_URL = "http://93.184.216.34/"  # literal public IP => no DNS needed in tests

SAMPLE_HTML = """<html><head><title>Acme Web Studio | Websites for Small Business</title>
<meta name="description" content="Acme builds affordable websites and online stores.">
<meta property="og:site_name" content="Acme Web Studio">
<script type="application/ld+json">{"@type":"Organization","name":"Acme Web Studio"}</script>
<script>var secret = "SHOULD_NOT_APPEAR";</script></head>
<body><nav><a href="/services">Services</a><a href="/about">About us</a></nav>
<h1>Websites that grow your business</h1>
<p>We design and build fast, affordable websites and e-commerce stores for small businesses in Cairo.</p>
<p>We design and build fast, affordable websites and e-commerce stores for small businesses in Cairo.</p>
<h2>Web Design</h2><p>Custom responsive design tailored to your brand and customers.</p>
<footer>Copyright footer junk text that should be dropped entirely</footer></body></html>"""

VALID_LLM_JSON = {
    "brand": {"name": "Acme Web Studio", "category": "Web Development", "summary": "Affordable websites for small businesses."},
    "competitor_analysis": {
        "brand_overview": "Acme builds websites and stores for small businesses in Cairo.",
        "main_category": "Web Development",
        "main_products_services": ["Web design", "E-commerce"],
        "competitors": [
            {"name": "Wix", "website": "https://www.wix.com", "main_service": "Website builder", "target_market": "DIY", "key_strength": "Brand", "weaknesses": ["Generic"], "opportunity": "More support"},
            {"name": "Acme Web Studio", "website": None, "main_service": "x", "target_market": "x", "key_strength": "x", "weaknesses": [], "opportunity": "x"},
            {"name": "Squarespace", "website": None, "main_service": "Builder", "target_market": "Creatives", "key_strength": "Templates", "weaknesses": [], "opportunity": "x"},
            {"name": "Shopify", "website": None, "main_service": "E-commerce", "target_market": "Retail", "key_strength": "Apps", "weaknesses": [], "opportunity": "x"},
        ],
        "strengths": ["Affordable pricing"],
        "opportunities": ["Add case studies"],
    },
    "target_search_queries": [
        {"query": f"query {i}", "platforms": ["Google", "Twitter"], "intent": "Service Discovery"} for i in range(7)
    ],
    "seo_improvements": {
        "primary_keyword": "web design cairo", "secondary_keywords": ["e-commerce"], "long_tail_keywords": [], "business_keywords": [],
        "recommended_title": "Web Design in Cairo | Acme Web Studio", "meta_description": "Affordable websites for small businesses.",
        "supporting_content": [{"type": "FAQ", "title": "How much does a website cost?", "rationale": "High intent"}],
    },
    "top_services": [{"name": f"Service {i}", "description": "d", "source": "website"} for i in range(5)],
    "target_audience": {"primary_audience": ["Small business owners"], "secondary_audience": ["Startups"], "industries": ["Retail"], "customer_needs": ["Online presence"]},
}


class FakeLLM:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), 0

    async def generate(self, system, user):
        self.calls += 1
        assert "<website_content>" in user or "failed validation" in user
        text = self.outputs.pop(0)
        return LLMResult(text, "gemini", "gemini-3.1-flash-lite", 4000, 2500, 1200)


def mock_site_client(html=SAMPLE_HTML, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=html, headers={"content-type": "text/html; charset=utf-8"})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------ validators
@pytest.mark.parametrize("bad", ["", "   ", "ftp://example.com", "file:///etc/passwd", "javascript:alert(1)",
                                 "http://user:pw@example.com", "http://localhost", "http://2130706433",
                                 "http://example.com:8080", "http://exa mple.com", "https://"])
def test_normalize_rejects_bad_urls(bad):
    with pytest.raises(AppError):
        normalize_url(bad)


def test_normalize_accepts_and_canonicalizes():
    assert normalize_url("Example.COM") == "https://example.com/"
    assert normalize_url("http://example.com/a?b=1#frag") == "http://example.com/a?b=1"


def test_internal_suffix_blocked():
    with pytest.raises(BlockedAddressError):
        normalize_url("http://printer.local/")


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://10.0.0.5", "http://192.168.1.1", "http://172.16.0.1",
                                 "http://169.254.169.254/latest/meta-data", "http://100.64.0.1", "http://0.0.0.0",
                                 "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://[fd00::1]/"])
def test_ssrf_private_addresses_blocked(url):
    from urllib.parse import urlsplit
    parts = urlsplit(normalize_url(url))
    with pytest.raises(BlockedAddressError):
        asyncio.run(ensure_public_host(parts.hostname, parts.port))


def test_public_ip_allowed():
    asyncio.run(ensure_public_host("93.184.216.34", 80))


# ------------------------------------------------------------- extractor
def test_parse_html_extracts_and_cleans():
    page = parse_html(SAMPLE_HTML, "https://acme.test/")
    assert page.title.startswith("Acme Web Studio")
    assert page.meta_description == "Acme builds affordable websites and online stores."
    assert page.site_name == "Acme Web Studio"
    assert "Web Design" in page.headings
    assert page.nav_items == ["Services", "About us"]
    assert "SHOULD_NOT_APPEAR" not in page.text          # scripts stripped
    assert "footer junk" not in page.text                # footer stripped
    assert page.text.count("We design and build") == 1   # duplicates removed


def test_build_context_respects_budget():
    page = parse_html(SAMPLE_HTML, "http://93.184.216.34/")
    from app.services.website_extractor import ExtractedSite
    ctx = build_context(ExtractedSite("u", "http://93.184.216.34/", page), 1200)
    assert "Acme Web Studio" in ctx and len(ctx) < 2500


def test_fetch_blocks_redirect_to_metadata_service():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await fetch_html(c, PUBLIC_IP_URL)
    with pytest.raises(BlockedAddressError):
        asyncio.run(run())


def test_fetch_reports_access_denied():
    async def run():
        async with mock_site_client(status=403) as c:
            await fetch_html(c, PUBLIC_IP_URL)
    with pytest.raises(WebsiteBlockedError):
        asyncio.run(run())


def test_extract_website_end_to_end():
    async def run():
        async with mock_site_client() as c:
            return await extract_website(c, PUBLIC_IP_URL)
    site = asyncio.run(run())
    assert site.brand_hint == "Acme Web Studio"


# ------------------------------------------------------------------- LLM
def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": {"b": 2}} thanks') == {"a": {"b": 2}}


def test_cost_estimate():
    assert estimate_cost("gemini-3.1-flash-lite", 4000, 2500) == pytest.approx(0.00475)
    assert estimate_cost("unknown-model", 1, 1) is None


def test_schema_coerces_platforms_and_trims_lists():
    q = SearchQuery(query="q", platforms=["Twitter", "google search", "Bogus", "X"], intent="i")
    assert q.platforms == ["X", "Google"]
    out = LLMOutput.model_validate(VALID_LLM_JSON)
    assert len(out.target_search_queries) == 5


def test_schema_rejects_too_few_queries():
    bad = {**VALID_LLM_JSON, "target_search_queries": VALID_LLM_JSON["target_search_queries"][:2]}
    with pytest.raises(ValueError):
        LLMOutput.model_validate(bad)


# ------------------------------------------------------------- pipeline
def make_service(outputs, client):
    settings = Settings(verify_competitors=False)
    llm = FakeLLM(outputs)
    return AnalysisService(client, llm, settings), llm


def test_pipeline_success_postprocessing_and_cache():
    async def run():
        async with mock_site_client() as c:
            svc, llm = make_service([json.dumps(VALID_LLM_JSON)], c)
            first = await svc.analyze(PUBLIC_IP_URL)
            second = await svc.analyze(PUBLIC_IP_URL)  # served from cache: FakeLLM has no more outputs
            return first, second, llm
    first, second, llm = asyncio.run(run())
    names = [c.name for c in first.analysis.competitor_analysis.competitors]
    assert names == ["Wix", "Squarespace", "Shopify"]  # brand itself removed
    assert first.analysis.competitor_analysis.comparison_table[0].attribute == "Brand Name"
    assert first.analysis.seo_improvements.current_title.startswith("Acme")
    assert len(first.analysis.target_search_queries) == 5
    assert first.meta.estimated_cost_usd == pytest.approx(0.00475)
    assert second.meta.cached and llm.calls == 1


def test_pipeline_repairs_invalid_output_once():
    async def run():
        async with mock_site_client() as c:
            svc, llm = make_service(["not json at all", json.dumps(VALID_LLM_JSON)], c)
            return await svc.analyze(PUBLIC_IP_URL), llm
    result, llm = asyncio.run(run())
    assert llm.calls == 2 and result.meta.input_tokens == 8000


def test_pipeline_gives_up_after_failed_repair():
    from app.errors import LLMOutputError
    async def run():
        async with mock_site_client() as c:
            svc, _ = make_service(["nope", "still nope"], c)
            await svc.analyze(PUBLIC_IP_URL)
    with pytest.raises(LLMOutputError):
        asyncio.run(run())


# ------------------------------------------------------------------- API
class StubService:
    async def analyze(self, url):
        raise InvalidURLError("boom")


def test_api_error_envelopes():
    with TestClient(app) as client:
        r = client.post("/link-analysis", json={"url": "ftp://example.com"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_url" and r.json()["success"] is False

        r = client.post("/link-analysis", json={"url": "http://127.0.0.1:80/admin"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "blocked_address"

        r = client.post("/link-analysis", json={})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"

        assert client.get("/health").json() == {"status": "ok"}


def test_api_success_shape():
    async def run():
        async with mock_site_client() as c:
            svc, _ = make_service([json.dumps(VALID_LLM_JSON)], c)
            return await svc.analyze(PUBLIC_IP_URL)
    result = asyncio.run(run())

    class Fixed:
        async def analyze(self, url):
            return result

    with TestClient(app) as client:
        app.state.service = Fixed()
        body = client.post("/link-analysis", json={"url": "https://acme.test"}).json()
    assert body["success"] is True
    assert set(body["analysis"]) == {"competitor_analysis", "target_search_queries", "seo_improvements", "top_services", "target_audience"}


def test_nvidia_provider_uses_openai_compatible_endpoint():
    from pydantic import SecretStr
    from app.services.llm_service import LLMService
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            s = Settings(llm_provider="nvidia", llm_model="meta/llama-3.3-70b-instruct", nvidia_api_key=SecretStr("nvapi-x"),
                         llm_fallback_provider="", llm_fallback_model="")
            return await LLMService(c, s).generate("sys", "user")
    res = asyncio.run(run())
    assert seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert "response_format" not in seen["body"] and res.input_tokens == 5 and res.cost_usd is None
