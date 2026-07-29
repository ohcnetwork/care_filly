"""Shared HTTP helper that maps transport failures to provider errors."""

import requests

from care_filly.providers.base import ProviderError, TransientProviderError

# HTTP status codes worth retrying (rate limit + transient server errors).
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


def post(url: str, **kwargs) -> requests.Response:
    """POST wrapper that raises a classified ``ProviderError`` on failure.

    Network/timeout errors and transient HTTP statuses raise
    ``TransientProviderError`` (retriable); other 4xx raise
    ``ProviderError`` (permanent).
    """
    try:
        response = requests.post(url, **kwargs)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise TransientProviderError(str(exc)) from exc
    except requests.RequestException as exc:
        raise ProviderError(str(exc)) from exc

    if response.status_code in _TRANSIENT_STATUS:
        raise TransientProviderError(
            f"provider returned {response.status_code}: {response.text[:200]}"
        )
    if response.status_code >= 400:
        raise ProviderError(
            f"provider returned {response.status_code}: {response.text[:200]}"
        )
    return response
