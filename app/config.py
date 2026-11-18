"""Central configuration. All secrets come from environment variables / .env."""
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- LLM routing -------------------------------------------------------
    llm_provider: str = "gemini"  # gemini | openai | anthropic | nvidia
    llm_model: str = "gemini-3.1-flash-lite"
    # Fallback is only used if its API key is set and the primary fails.
    llm_fallback_provider: str = "openai"
    llm_fallback_model: str = "gpt-5.4-mini"

    gemini_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    nvidia_api_key: SecretStr | None = None  # build.nvidia.com key (nvapi-...)

    llm_timeout_seconds: float = 60.0
    llm_max_output_tokens: int = 4096
    llm_max_retries: int = 2  # per provider, for 429/5xx/timeouts

    # --- Website fetching --------------------------------------------------
    request_timeout_seconds: float = 12.0
    max_page_bytes: int = 2_000_000
    max_extra_pages: int = 2  # extra same-site pages (about/services/...)
    max_content_chars: int = 12_000  # text budget sent to the LLM (~3k tokens)
    user_agent: str = "Mozilla/5.0 (compatible; LinkAnalysisBot/1.0; +https://example.com/bot)"

    # --- Behaviour ---------------------------------------------------------
    cache_ttl_seconds: int = 3600
    cache_max_entries: int = 256
    verify_competitors: bool = True  # check suggested competitor domains respond


@lru_cache
def get_settings() -> Settings:
    return Settings()
