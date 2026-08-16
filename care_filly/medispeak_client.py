"""Server-side Medispeak account client — the session/token seam.

Holds the long-lived account secret (``MEDISPEAK_API_KEY``, ``msk_live_...``)
and is only ever called from the backend. Mints short-lived, session-scoped
tokens (``mss_...``) that are safe to hand to the browser: they verify by
signature + expiry only, and are scoped to one session id and
``["audio", "read"]`` (see Medispeak's ``Scribe::SessionToken``).
"""

from care_filly.providers.base import ProviderError
from care_filly.providers.http import get, post
from care_filly.settings import plugin_settings


def _base_url() -> str:
    base_url = plugin_settings.MEDISPEAK_BASE_URL
    if not base_url:
        raise ProviderError("MEDISPEAK_BASE_URL is not configured")
    return base_url.rstrip("/")


def _headers() -> dict:
    api_key = plugin_settings.MEDISPEAK_API_KEY
    if not api_key:
        raise ProviderError("MEDISPEAK_API_KEY is not configured")
    return {"Authorization": f"Bearer {api_key}"}


def create_session(
    outputs: list[dict],
    language: list[str] | None = None,
    mode: str = "consultation",
) -> dict:
    """Create a Medispeak scribe session. Returns the raw session object."""
    return post(
        f"{_base_url()}/scribe_sessions",
        headers=_headers(),
        json={
            "outputs": outputs,
            "language": language or ["auto"],
            "mode": mode,
        },
        timeout=10,
    ).json()


def mint_session_token(medispeak_session_id: str) -> dict:
    """Mint a short-lived token (``mss_...``) scoped to one session."""
    return post(
        f"{_base_url()}/scribe_sessions/{medispeak_session_id}/tokens",
        headers=_headers(),
        timeout=10,
    ).json()


def get_session(medispeak_session_id: str) -> dict:
    """Fetch the authoritative session state (status, results, usage)."""
    return get(
        f"{_base_url()}/scribe_sessions/{medispeak_session_id}",
        headers=_headers(),
        timeout=10,
    ).json()
