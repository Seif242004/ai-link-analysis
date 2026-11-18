"""Pydantic models: API request/response AND the contract the LLM must satisfy.

The LLM output is validated with `LLMOutput`; validators are deliberately
tolerant (coerce platform names, trim over-long lists) so cosmetic deviations
don't cost a retry, while structural problems still raise ValidationError.
"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Platform = Literal["Google", "Facebook", "Instagram", "LinkedIn", "X"]

_PLATFORM_ALIASES = {
    "google": "Google", "google search": "Google",
    "facebook": "Facebook", "fb": "Facebook", "meta": "Facebook",
    "instagram": "Instagram", "ig": "Instagram",
    "linkedin": "LinkedIn", "linked in": "LinkedIn",
    "x": "X", "twitter": "X", "x (twitter)": "X", "twitter/x": "X", "x/twitter": "X",
}


# ----------------------------------------------------------------- request
class LinkAnalysisRequest(BaseModel):
    url: str = Field(..., examples=["https://example.com"], description="Website URL to analyze.")


# ------------------------------------------------------------- shared bits
class BrandInfo(BaseModel):
    name: str
    category: str = ""
    summary: str = ""


# --------------------------------------------------------------- section 1
class Competitor(BaseModel):
    name: str
    website: str | None = None
    main_service: str = ""
    target_market: str = ""
    key_strength: str = ""
    weaknesses: list[str] = []
    opportunity: str = ""
    # Always an AI suggestion (we do not scrape search engines). `verification`
    # is filled in by code, never by the LLM.
    source: Literal["ai_suggestion"] = "ai_suggestion"
    verification: str | None = None


class ComparisonRow(BaseModel):
    attribute: str
    your_brand: str
    competitors: dict[str, str]


class CompetitorAnalysis(BaseModel):
    brand_overview: str
    main_category: str = ""
    main_products_services: list[str] = []
    competitors: list[Competitor] = []
    strengths: list[str] = []  # the submitted brand's competitive advantages
    opportunities: list[str] = []  # positioning opportunities
    comparison_table: list[ComparisonRow] = []  # built in code, not by the LLM


# --------------------------------------------------------------- section 2
class SearchQuery(BaseModel):
    query: str
    platforms: list[Platform] = Field(min_length=1)
    intent: str
    business_relevance: str = ""
    target_audience: str = ""

    @field_validator("platforms", mode="before")
    @classmethod
    def _normalise_platforms(cls, v):
        if isinstance(v, str):
            v = [v]
        out: list[str] = []
        for item in v or []:
            mapped = _PLATFORM_ALIASES.get(str(item).strip().lower())
            if mapped and mapped not in out:
                out.append(mapped)
        return out


# --------------------------------------------------------------- section 3
class SupportingContent(BaseModel):
    type: str  # e.g. "Service landing page", "FAQ", "Blog topic"
    title: str
    rationale: str = ""


class SEOImprovements(BaseModel):
    primary_keyword: str
    secondary_keywords: list[str] = []
    long_tail_keywords: list[str] = []
    business_keywords: list[str] = []
    recommended_title: str
    title_structure: str = ""
    meta_description: str
    supporting_content: list[SupportingContent] = []
    # filled by code
    current_title: str | None = None
    current_meta_description: str | None = None
    title_length: int = 0
    meta_description_length: int = 0
    note: str = "Keywords are AI-suggested; no search-volume data is provided because no keyword data source is connected."


# --------------------------------------------------------------- section 4
class Service(BaseModel):
    name: str
    description: str
    target_industry: str = ""
    target_customer: str = ""
    main_benefit: str = ""
    source: Literal["website", "inferred"] = "website"


# --------------------------------------------------------------- section 5
class AudienceRow(BaseModel):
    audience: str
    business_type: str = ""
    requirement: str = ""


class TargetAudience(BaseModel):
    primary_audience: list[str] = []
    secondary_audience: list[str] = []
    industries: list[str] = []
    business_types: list[str] = []
    customer_profiles: list[str] = []
    geographic_focus: str | None = None
    customer_needs: list[str] = []
    customer_interests: list[str] = []
    recommended_segments: list[str] = []
    audience_table: list[AudienceRow] = []


class AnalysisSections(BaseModel):
    competitor_analysis: CompetitorAnalysis
    target_search_queries: list[SearchQuery]
    seo_improvements: SEOImprovements
    top_services: list[Service]
    target_audience: TargetAudience

    @field_validator("target_search_queries", mode="after")
    @classmethod
    def _exactly_five_queries(cls, v):
        if len(v) < 5:
            raise ValueError(f"expected 5 target_search_queries, got {len(v)}")
        return v[:5]

    @field_validator("top_services", mode="after")
    @classmethod
    def _five_services(cls, v):
        if len(v) < 3:
            raise ValueError(f"expected 5 top_services, got {len(v)}")
        return v[:5]


class LLMOutput(AnalysisSections):
    """Exactly what the LLM must return."""

    brand: BrandInfo


# ---------------------------------------------------------------- response
class AnalysisMeta(BaseModel):
    provider: str
    model: str
    used_fallback: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    llm_latency_ms: int = 0
    total_ms: int = 0
    estimated_cost_usd: float | None = None
    cached: bool = False
    pages_analyzed: list[str] = []


class LinkAnalysisResponse(BaseModel):
    success: bool = True
    website: str
    brand: BrandInfo
    analysis: AnalysisSections
    meta: AnalysisMeta
    warnings: list[str] = []


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorBody
