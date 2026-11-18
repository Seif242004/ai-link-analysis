"""Application errors.

Every error maps to an HTTP status + a stable machine-readable `code`, so the
API always answers with the same envelope:
    {"success": false, "error": {"code": "...", "message": "..."}}
"""


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidURLError(AppError):
    status_code = 422
    code = "invalid_url"


class BlockedAddressError(AppError):
    """URL resolves to a private/loopback/link-local/reserved address (SSRF guard)."""

    status_code = 400
    code = "blocked_address"


class WebsiteUnreachableError(AppError):
    status_code = 422
    code = "website_unreachable"


class WebsiteBlockedError(AppError):
    """Site answered 401/403/429/503 (login wall, bot protection, rate limit)."""

    status_code = 422
    code = "website_access_denied"


class WebsiteTimeoutError(AppError):
    status_code = 504
    code = "website_timeout"


class ContentExtractionError(AppError):
    status_code = 422
    code = "no_extractable_content"


class LLMError(AppError):
    status_code = 502
    code = "llm_error"


class LLMOutputError(AppError):
    status_code = 502
    code = "llm_invalid_output"
