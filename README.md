# AI-Powered Website Link Analysis API

FastAPI service that fetches a website, extracts its real content, and uses an LLM to produce five structured sections: **competitors vs. your brand**, **top-5 target search queries**, **SEO improvements**, **top-5 services**, and **target audience**.

- `POST /link-analysis` – the analysis endpoint (port 4000)
- `GET /docs` – interactive Swagger UI · `GET /health` – liveness check

## Quick start

```bash
git clone <this-repo> && cd ai-link-analysis
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                    # then add NVIDIA_API_KEY (from build.nvidia.com); or switch LLM_PROVIDER to gemini/openai/anthropic
uvicorn app.main:app --host 0.0.0.0 --port 4000
```

Open http://localhost:4000/docs, or:

```bash
curl -X POST http://localhost:4000/link-analysis \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

Docker: `docker build -t link-analysis . && docker run -p 4000:4000 --env-file .env link-analysis`
(Hosts such as Render/Railway/Fly inject `PORT`; the image honours it. Set the API keys as environment variables in the host dashboard, not in the repo.)

Tests (offline, no API key needed): `pytest -q` → **38 passed** (see `docs/test-run.txt`).

## How it works

```
POST {url}
  └─ validate_url          syntax checks + DNS resolution + SSRF guard           utils/validators.py
  └─ cache lookup          in-memory TTL cache (skips fetch + LLM on repeat)     services/analysis_service.py
  └─ extract_website       manual-redirect fetch (each hop SSRF-checked, 2 MB cap),
                           parse title/meta/OG/JSON-LD/nav/headings/body,
                           + up to 2 same-site pages (about/services/…)          services/website_extractor.py
  └─ build_context         de-duplicated, boilerplate-free text, ≤12k chars (~3k tokens)
  └─ LLM (JSON mode)       primary model → retries → fallback provider           services/llm_service.py
  └─ validate (Pydantic)   on failure: ONE repair call, then 502                 schemas.py
  └─ post-process          drop brand/dupes from competitors, verify competitor
                           domains respond, build comparison table, SEO lengths  services/competitor_service.py
  └─ JSON response (+ token/cost/latency metadata)
```

Design choices worth knowing:

| Requirement | How it is handled |
|---|---|
| Use *actual* content | Main page + up to 2 relevant internal pages are fetched; the prompt forbids generic output and each service is labelled `source: "website"` or `"inferred"`. |
| No invented search volumes | The schema has no volume fields; prompt forbids them; response carries an explicit note. |
| Competitors honesty | Always labelled `source: "ai_suggestion"`. Code removes the brand itself/duplicates and checks each suggested domain responds (`verification`: `domain_reachable` / `domain_unreachable` / `unverified_ai_suggestion`) – a cheap filter for hallucinated companies, **not** proof the competitor analysis is correct. |
| SSRF | Only http/https on ports 80/443, no credentials, FQDN or IP literal only; every resolved address (and every redirect hop) must be globally routable – blocks loopback, RFC1918, link-local/cloud metadata (169.254.169.254), CGNAT, IPv6 ULA, IPv4-mapped IPv6, `*.local/.internal`, decimal/hex IP tricks. |
| Prompt injection | Website text is wrapped in `<website_content>` and declared untrusted data in the system prompt. |
| Secrets | Only via env vars / `.env` (git-ignored); stored as `SecretStr`; sent in headers, never in URLs; LLM error bodies are never echoed to clients. |
| Errors | Uniform envelope `{"success": false, "error": {"code", "message"}}` – 400 blocked address, 422 invalid URL / unreachable / access denied / no content, 502 LLM failure, 504 site timeout. |

Known limitations: DNS-rebinding between our check and httpx's lookup is not fully closed in-process (add an egress firewall in production); JavaScript-rendered sites yield thin content (a warning is returned; a headless-browser fetch would be the next step); the cache is per-process.

## Response (abridged)

**Real responses** from the live NVIDIA API are in `docs/`: [`live-www_python_org.json`](docs/live-www_python_org.json), [`live-www_wikipedia_org.json`](docs/live-www_wikipedia_org.json), [`live-fastapi_tiangolo_com.json`](docs/live-fastapi_tiangolo_com.json), [`live-www_djangoproject_com.json`](docs/live-www_djangoproject_com.json) (run log: `docs/run-log.txt`, `docs/run-log2.txt`). `docs/sample-response.json` is only a stubbed-LLM shape example used by the tests.

```json
{
  "success": true,
  "website": "https://example.com/",
  "brand": {"name": "…", "category": "…", "summary": "…"},
  "analysis": {
    "competitor_analysis": {"brand_overview": "…", "competitors": [], "strengths": [], "opportunities": [], "comparison_table": []},
    "target_search_queries": [{"query": "…", "platforms": ["Google", "LinkedIn"], "intent": "…", "business_relevance": "…", "target_audience": "…"}],
    "seo_improvements": {"primary_keyword": "…", "secondary_keywords": [], "long_tail_keywords": [], "business_keywords": [], "recommended_title": "…", "meta_description": "…", "supporting_content": []},
    "top_services": [{"name": "…", "description": "…", "target_industry": "…", "target_customer": "…", "main_benefit": "…", "source": "website"}],
    "target_audience": {"primary_audience": [], "secondary_audience": [], "industries": [], "customer_needs": [], "audience_table": []}
  },
  "meta": {"provider": "gemini", "model": "…", "input_tokens": 0, "output_tokens": 0, "llm_latency_ms": 0, "total_ms": 0, "estimated_cost_usd": 0.0, "cached": false, "pages_analyzed": []},
  "warnings": []
}
```

Error examples (real output from the running server, `docs/smoke-test.txt`):

```
POST {"url":"http://169.254.169.254/latest/meta-data"} → 400 {"success":false,"error":{"code":"blocked_address","message":"The URL resolves to a private or reserved network address."}}
POST {"url":"ftp://example.com"}                       → 422 {"success":false,"error":{"code":"invalid_url","message":"Only http:// and https:// URLs are supported."}}
POST {"url":"https://this-domain-does-not-exist-xyz123.com"} → 422 {"success":false,"error":{"code":"website_unreachable", …}}
```

---

# LLM Platform & Model Research

> **Evidence labels.** **[Measured]** = recorded by this app / scripts against the live API on 18 Nov 2026 (files in `docs/`). **[Listed]** = published price/spec found during research. **[Calculated]** = arithmetic from measured tokens × listed prices. **[Not tested]** = no run was made, so no accuracy claim is made. Accuracy was **not** benchmarked against ground truth; "valid" below means the output passed the app's strict Pydantic schema (all 5 sections, exactly 5 queries/services, platforms from the allowed set) and was manually skimmed for plausibility.

## A. Measured: models available on build.nvidia.com (the account used for this test)

| Model | Result in this app | Speed | Notes |
|---|---|---|---|
| `nvidia/nemotron-3.5-lightning-30b-a3b` | 2/2 outputs schema-valid, all 5 platforms covered, sensible services/competitors **[Measured]** | **≈ 15 s** for one full analysis (derived from output-file timestamps of the benchmark run; the benchmark's own timer did not complete) **[Measured, approximate]** | MoE, ~3B active params → fast. **Chosen primary.** |
| `nvidia/nemotron-3-ultra-550b-a55b` | 4/4 live analyses valid (python.org, wikipedia.org, fastapi.tiangolo.com, djangoproject.com); 1 request hit a 60 s read timeout before the limit was raised **[Measured]** | LLM time 52.0 s, 55.7 s, 88.7 s, 110.2 s → **median ≈ 72 s** **[Measured]** | Largest model → best expected depth, but too slow for an interactive endpoint. Avg 2,576 input / 3,764 output tokens. **Chosen fallback.** |
| `z-ai/glm-5.3-flash` | no response within 100 s (ReadTimeout) on python.org **[Measured]** | > 100 s | Not usable at this endpoint's current latency. |
| `minimaxai/minimax-m3.1-flash` | ReadTimeout at 150 s (twice) **[Measured]** | > 150 s | Not usable. |
| `meta/llama-3.3-70b-instruct` | HTTP 410 (retired) **[Measured]** | – | Removed from the platform. |
| `google/gemma-4-31b-it` | benchmark not completed | – | **[Not tested]** |

Cost on NVIDIA's hosted trial API is credit/rate-limited, not per-token priced, so `estimated_cost_usd` is `null` for these runs.

## B. Per-token providers (not run – prices only)

| Provider | Model | Input / Output per 1M tokens [Listed] | Context [Listed] | JSON | Cost per analysis at the measured ≈2.6k in / ≈3.8k out [Calculated] |
|---|---|---|---|---|---|
| Google | `gemini-3.1-flash-lite` | $0.25 / $1.50 | 1M | native JSON mode | ≈ $0.006 |
| OpenAI | `gpt-5.4-mini` | $0.75 / $4.50 | not verified | `json_object` / structured outputs | ≈ $0.019 |
| Anthropic | `claude-haiku-4-5` | $1.00 / $5.00 | 200K | prompt-enforced here | ≈ $0.021 |

Gemini, OpenAI and Anthropic are already wired in (`LLM_PROVIDER=gemini|openai|anthropic`) but were **not benchmarked** (no keys). Prices come from provider docs and aggregators that sometimes disagree and change often – re-check before relying on them. All three offer ≈50% batch discounts.

## C. Selection and why

**Primary `nvidia/nemotron-3.5-lightning-30b-a3b`, fallback `nvidia/nemotron-3-ultra-550b-a55b`** (both on the same NVIDIA key).

1. **Speed** – ≈15 s vs ≈72 s median for the 550B model; the only tested model that is practical behind a public HTTP endpoint (hosts commonly cut requests at 30–100 s).
2. **Reliability of structured output** – both tested models produced schema-valid JSON every time; the app additionally validates with Pydantic and performs one repair call.
3. **Resilience** – if the primary times out or errors, the request automatically retries on the larger model.
4. **Cost** – no per-token cost on the trial API; for paid production use, a per-token small model (e.g. Gemini Flash-Lite, ≈$0.006/analysis calculated above) is the cheapest listed option but is untested here.
5. **Honest caveat** – the 550B model likely gives richer competitor reasoning; the lightning model is a smaller model and its competitor suggestions are more generic (e.g. python.org → "Java, JavaScript, R"). Competitors are labelled AI suggestions for this reason.

## D. Cost / latency optimisation (implemented ✔ / recommended ☐)

- ✔ Content cleaned before sending (scripts/styles/nav/footer/duplicates removed); ≈2.6k input tokens on average.
- ✔ Token budget (`MAX_CONTENT_CHARS`), lean prompt, one LLM call for all five sections, repair call re-sends only the previous output + errors.
- ✔ In-memory TTL cache (1 h): repeat URLs return in milliseconds at no cost (observed: a repeated python.org request returned in 72 ms).
- ✔ Small model first, large model only on failure.
- ☐ Redis cache, provider prompt caching, batch API for bulk jobs, streaming, and a per-IP rate limit for the public demo.

## E. Final recommendation

| Item | Value |
|---|---|
| Recommended provider / model | NVIDIA build.nvidia.com · `nvidia/nemotron-3.5-lightning-30b-a3b` |
| Fallback | `nvidia/nemotron-3-ultra-550b-a55b` |
| Input tokens per analysis | ≈ 2,600 (measured average, range 1.9k–3.5k) |
| Output tokens per analysis | ≈ 3,800 (measured average, range 3.5k–3.9k) |
| Cost per analysis | trial credits (no per-token price); ≈ $0.006 on Gemini 3.1 Flash-Lite at list price [Calculated, untested] |
| Response time | ≈ 15 s (primary, approximate) / ≈ 55–110 s (fallback) **[Measured]** |
| Balance of accuracy/speed/cost | Fast small model by default, large model only on failure, cache everything; grade outputs by hand before claiming accuracy. |

Reproduce the benchmark: `python -m scripts.benchmark https://www.python.org --runs 1 --models nvidia:nvidia/nemotron-3.5-lightning-30b-a3b` (prints latency, tokens, schema-validity per model; raw outputs go to `benchmark_outputs/`).

---

## Project structure

```
app/main.py                      FastAPI app, error handlers, endpoint
app/config.py                    env-driven settings
app/schemas.py                   request/response + LLM output contract
app/prompts.py                   system/user/repair prompts
app/errors.py                    typed errors → HTTP codes
app/services/website_extractor.py   safe fetch + HTML → context
app/services/llm_service.py         Gemini/OpenAI/Anthropic client, retries, fallback, cost
app/services/analysis_service.py    orchestration, repair, cache
app/services/competitor_service.py  competitor cleanup/verification + comparison table
app/utils/validators.py          URL validation + SSRF protection
tests/test_link_analysis.py      38 offline tests
scripts/benchmark.py             measured latency/cost/validity per model
```

## Deploy (Render, free tier)

1. Push this repo to GitHub (public).
2. Render dashboard → **New → Blueprint** → select the repo (`render.yaml` is picked up automatically).
3. When prompted, set the secret `NVIDIA_API_KEY`. Do not commit it.
4. After the build: `https://<service>.onrender.com/docs`, endpoint `POST https://<service>.onrender.com/link-analysis`.

Free instances sleep after inactivity, so the first request can take ~1 minute to wake.

## Live demo

Free hosts I tried required payment details, so the demo is served from my own machine through a free Cloudflare quick tunnel (`RUN_DEMO.bat` / `run_demo.ps1` starts the API and the tunnel):

| Requirement | URL |
|---|---|
| Live demo (temporary) | https://blocked-readers-miss-joy.trycloudflare.com |
| API endpoint | https://blocked-readers-miss-joy.trycloudflare.com/link-analysis |
| API documentation | https://blocked-readers-miss-joy.trycloudflare.com/docs |

This URL is temporary: it is valid only while the demo window is running on my PC and changes each time it is restarted. If it is down, I can start it for you on request, or you can run the project locally with the Quick start above.
