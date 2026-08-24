"""Application exception hierarchy.

A single root exception lets the one FastAPI handler in main.py map any domain
error to a clean HTTP response, while genuine bugs (anything NOT derived from
RagPlatformError) still surface as 500s with full tracebacks in the logs.

Subclasses declare their own status_code/detail instead of routes hand-rolling
HTTPException everywhere. The hierarchy grows stage by stage (auth, tenancy,
ingestion, retrieval); nothing is pre-declared before it is actually used.
"""


class RagPlatformError(Exception):
    """Root of all expected application errors."""

    status_code: int = 500
    detail: str = "Internal server error"
    headers: dict[str, str] | None = None  # e.g. Retry-After; merged by the handler

    def __init__(self, detail: str | None = None) -> None:
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class NotFoundError(RagPlatformError):
    status_code = 404
    detail = "Resource not found"


class ConfigurationError(RagPlatformError):
    detail = "Service is misconfigured"


class ProviderNotConfiguredError(ConfigurationError):
    detail = "LLM/embedding provider is not configured"


class UnsupportedDocumentTypeError(RagPlatformError):
    status_code = 415
    detail = "Unsupported document type"


class ParseError(RagPlatformError):
    """Document is permanently unparseable (corrupt file, no extractable text).

    Deliberately distinct from transient failures: ingestion marks the document
    failed and does NOT retry on ParseError, but re-raises anything else so the
    worker's retry policy (stage 5) can have another go.
    """

    status_code = 422
    detail = "Document could not be parsed"


class DuplicateDocumentError(RagPlatformError):
    status_code = 409
    detail = "A document with identical content already exists for this tenant"


class InvalidQueryError(RagPlatformError):
    status_code = 422
    detail = "Query must be non-empty"


class AuthenticationError(RagPlatformError):
    status_code = 401
    detail = "Not authenticated"


class InvalidGrantError(RagPlatformError):
    """OAuth2 token endpoint: wrong grant_type or malformed request (RFC 6749 §5.2)."""

    status_code = 400
    detail = "Invalid grant"


class FileTooLargeError(RagPlatformError):
    status_code = 413
    detail = "Uploaded file exceeds the size limit"


class StorageQuotaExceededError(RagPlatformError):
    # 413 (same family as FileTooLarge): the request entity can't be accepted
    # because it would push the tenant over its storage allotment. A distinct
    # class so the message names the real reason and tests can assert on it.
    status_code = 413
    detail = "Tenant storage quota exceeded"

    def __init__(self, *, used_bytes: int, limit_bytes: int, incoming_bytes: int) -> None:
        super().__init__(
            f"Tenant storage quota exceeded: {used_bytes} bytes used + "
            f"{incoming_bytes} incoming > {limit_bytes} byte limit"
        )
        self.used_bytes = used_bytes
        self.limit_bytes = limit_bytes
        self.incoming_bytes = incoming_bytes


class QueueUnavailableError(RagPlatformError):
    status_code = 503
    detail = "Ingestion queue is unavailable; try again shortly"


class RateLimitedError(RagPlatformError):
    status_code = 429
    detail = "Rate limit exceeded"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__()
        self.headers = {"Retry-After": str(retry_after_seconds)}
