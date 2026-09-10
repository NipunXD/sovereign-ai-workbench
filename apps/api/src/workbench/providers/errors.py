"""Provider failure taxonomy.

Callers need to distinguish "retry against a fallback model" from "this request
will never succeed", because retrying a malformed prompt against every model in
the lane just burns the run's time budget.
"""

from __future__ import annotations

from workbench.core.errors import ProviderError


class ProviderTimeoutError(ProviderError):
    """The backend did not respond in time. Retryable."""

    code = "provider_timeout"
    detail = "The model backend timed out."


class ProviderUnavailableError(ProviderError):
    """The backend is unreachable — not started, or the wrong port."""

    code = "provider_unavailable"
    detail = "The model backend is not reachable."


class ModelNotFoundError(ProviderError):
    """The physical model is not present on this backend.

    Almost always a manifest that drifted from what is actually installed;
    ``scripts/pull_models.sh --check`` reports exactly this.
    """

    status_code = 404
    code = "model_not_found"
    detail = "The requested model is not available on this backend."


class ProviderResponseError(ProviderError):
    """The backend replied, but with something unusable."""

    code = "provider_bad_response"
    detail = "The model backend returned a malformed response."


class ContextLengthExceededError(ProviderError):
    """The prompt does not fit. Retrying the same prompt cannot help."""

    status_code = 422
    code = "context_length_exceeded"
    detail = "The prompt exceeds the model's context window."


#: Failures worth retrying against the next candidate model in the lane.
RETRYABLE: tuple[type[ProviderError], ...] = (
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderResponseError,
)


def is_retryable(exc: BaseException) -> bool:
    """Whether a fallback model is worth attempting."""
    return isinstance(exc, RETRYABLE)
