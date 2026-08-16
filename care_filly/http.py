"""HTTP helper for the Medispeak account API.

The plugin talks to exactly one upstream, so there is no provider registry
here — just the verb ``medispeak_client`` needs and an error type that
distinguishes retriable failures from permanent ones.
"""

import requests

# HTTP status codes worth retrying (rate limit + transient server errors).
_TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


class MedispeakError(Exception):
    """Permanent Medispeak API failure — do not retry."""


class TransientMedispeakError(MedispeakError):
    """Temporary failure (timeout, 5xx, rate limit) — safe to retry."""


def _classify(response: requests.Response) -> requests.Response:
    if response.status_code in _TRANSIENT_STATUS:
        raise TransientMedispeakError(
            f"medispeak returned {response.status_code}: {response.text[:200]}"
        )
    if response.status_code >= 400:
        raise MedispeakError(
            f"medispeak returned {response.status_code}: {response.text[:200]}"
        )
    return response


def post(url: str, **kwargs) -> requests.Response:
    """POST wrapper that raises a classified ``MedispeakError`` on failure."""
    try:
        response = requests.post(url, **kwargs)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise TransientMedispeakError(str(exc)) from exc
    except requests.RequestException as exc:
        raise MedispeakError(str(exc)) from exc
    return _classify(response)
