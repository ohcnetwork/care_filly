"""Quota management endpoints (mirrors 10bedicu/care_scribe's quota API).

User-facing:
- GET  v1/quota/my?facility_id=   -> {quotas, tnc, tnc_accepted}
- POST v1/quota/accept-tnc        -> accepts TnC, auto-creates the user quota
- GET  v1/preferences/scribe      -> per-user scribe opt-in + TnC state
- PUT  v1/preferences/scribe      -> enable (accepts current TnC) / disable

Admin (CARE superusers only):
- GET    v1/quota                 -> facility-level quotas (or per-user rows
                                     of one facility with ?facility_id=)
- POST   v1/quota                 -> create a facility quota
- GET    v1/quota/<external_id>
- PATCH  v1/quota/<external_id>
- DELETE v1/quota/<external_id>   -> facility rows cascade their user rows
"""

from __future__ import annotations

import logging
from typing import Optional

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import FillyQuota, FillyUserPreference, used_tokens
from .quota import current_tnc, parse_facility_id
from .views import _authenticate, _body

logger = logging.getLogger("care_filly")

MAX_PAGE_SIZE = 100
ORDERINGS = {"created_date", "-created_date", "tokens", "-tokens"}


def _require_user(request: HttpRequest):
    err, user = _authenticate(request)
    if err:
        return err, None
    if user is None:
        return (
            JsonResponse(
                {
                    "error": {
                        "code": "unauthorized",
                        "message": "A CARE user token is required",
                    }
                },
                status=401,
            ),
            None,
        )
    return None, user


def _require_superuser(request: HttpRequest):
    err, user = _require_user(request)
    if err:
        return err, None
    if not getattr(user, "is_superuser", False):
        return (
            JsonResponse(
                {
                    "error": {
                        "code": "forbidden",
                        "message": "Superuser access required",
                    }
                },
                status=403,
            ),
            None,
        )
    return None, user


def _get_facility(facility_uuid) -> Optional[object]:
    """Resolve a CARE Facility lazily (None when not running inside CARE)."""
    try:
        from care.facility.models.facility import Facility
    except ImportError:
        return None
    return Facility.objects.filter(external_id=facility_uuid).first()


def _facility_name(facility_uuid) -> Optional[str]:
    facility = _get_facility(facility_uuid)
    return getattr(facility, "name", None)


def _user_dict(user) -> Optional[dict]:
    if user is None:
        return None
    return {
        "username": user.username,
        "first_name": getattr(user, "first_name", ""),
        "last_name": getattr(user, "last_name", ""),
    }


def _quota_dict(q: FillyQuota, facility_name: Optional[str] = None) -> dict:
    return {
        "external_id": str(q.external_id),
        "user": _user_dict(q.user),
        "facility_external_id": str(q.facility_external_id),
        "facility_name": facility_name
        if facility_name is not None
        else _facility_name(q.facility_external_id),
        "tokens": q.tokens,
        "tokens_per_user": q.tokens_per_user,
        "used": used_tokens(q.facility_external_id, q.user_id),
        "allow_scribe": q.allow_scribe,
        "tnc_accepted_date": q.tnc_accepted_date.isoformat()
        if q.tnc_accepted_date
        else None,
        "created_by": _user_dict(q.created_by),
        "created_date": q.created_date.isoformat() if q.created_date else None,
        "modified_date": q.modified_date.isoformat() if q.modified_date else None,
    }


def _error(code: str, message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


# ---------------------------------------------------------------------------
# User-facing endpoints


def my_quota(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return HttpResponse(status=405)
    err, user = _require_user(request)
    if err:
        return err

    facility_uuid = parse_facility_id(request.GET.get("facility_id"))
    if facility_uuid is None:
        return _error("facility_required", "A valid facility_id is required.", 400)

    tnc, tnc_hash = current_tnc()
    facility_quota = FillyQuota.objects.filter(
        facility_external_id=facility_uuid, user=None
    ).first()
    user_quota = FillyQuota.objects.filter(
        facility_external_id=facility_uuid, user=user
    ).first()

    facility_name = _facility_name(facility_uuid)
    quotas = [
        _quota_dict(q, facility_name)
        for q in (facility_quota, user_quota)
        if q is not None
    ]
    return JsonResponse(
        {
            "quotas": quotas,
            "tnc": tnc,
            "tnc_accepted": bool(user_quota and user_quota.tnc_hash == tnc_hash),
        }
    )


@csrf_exempt
def accept_tnc(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    err, user = _require_user(request)
    if err:
        return err

    facility_uuid = parse_facility_id(_body(request).get("facility_id"))
    if facility_uuid is None:
        return _error("facility_required", "A valid facility_id is required.", 400)

    tnc, tnc_hash = current_tnc()
    user_quota = FillyQuota.objects.filter(
        facility_external_id=facility_uuid, user=user
    ).first()
    if user_quota and user_quota.tnc_hash == tnc_hash:
        return JsonResponse({"detail": "Terms and Conditions already accepted."})

    if user_quota is None:
        facility_quota = FillyQuota.objects.filter(
            facility_external_id=facility_uuid, user=None
        ).first()
        if facility_quota is None:
            return _error("no_facility_quota", "Facility does not have a quota.", 400)
        user_quota = FillyQuota.objects.create(
            user=user,
            facility_external_id=facility_uuid,
            tokens=facility_quota.tokens_per_user,
            tnc_hash=tnc_hash,
            tnc_accepted_date=timezone.now(),
        )
    else:
        user_quota.tnc_hash = tnc_hash
        user_quota.tnc_accepted_date = timezone.now()
        user_quota.save(
            update_fields=["tnc_hash", "tnc_accepted_date", "modified_date"]
        )

    return JsonResponse({"detail": "Terms and Conditions accepted successfully."})


def _user_has_scribe_facility(user) -> bool:
    """True when any facility the user belongs to has scribe enabled.

    Used by the profile page: there is no point offering the scribe
    opt-in to a user none of whose facilities have a scribe quota.
    Outside CARE (standalone tests) this can't be resolved — report True.
    """
    try:
        from care.emr.models.organization import FacilityOrganizationUser
        from care.facility.models.facility import Facility
    except ImportError:
        return True

    facility_ids = FacilityOrganizationUser.objects.filter(user=user).values_list(
        "organization__facility_id", flat=True
    )
    facility_uuids = Facility.objects.filter(id__in=facility_ids).values_list(
        "external_id", flat=True
    )
    return FillyQuota.objects.filter(
        facility_external_id__in=list(facility_uuids),
        user=None,
        allow_scribe=True,
    ).exists()


@csrf_exempt
def scribe_preference(request: HttpRequest) -> HttpResponse:
    """Per-user scribe opt-in, managed from the user's profile page.

    Enabling records acceptance of the current TnC (the client shows the
    consent dialog first). If the TnC text changes later, `tnc_accepted`
    flips to false and the client must re-consent to enable again.
    """
    if request.method not in ("GET", "PUT"):
        return HttpResponse(status=405)
    err, user = _require_user(request)
    if err:
        return err

    tnc, tnc_hash = current_tnc()
    pref = FillyUserPreference.objects.filter(user=user).first()
    facility_available = _user_has_scribe_facility(user)

    if request.method == "PUT":
        enabled = bool(_body(request).get("enabled"))
        if enabled and not facility_available:
            return _error(
                "no_facility_quota",
                "None of your facilities have scribe enabled.",
                400,
            )
        if pref is None:
            pref = FillyUserPreference(user=user)
        pref.scribe_enabled = enabled
        if enabled:
            pref.tnc_hash = tnc_hash
            pref.tnc_accepted_date = timezone.now()
        pref.save()

    return JsonResponse(
        {
            "enabled": bool(pref and pref.scribe_enabled),
            "tnc": tnc,
            "tnc_accepted": bool(pref and pref.tnc_hash == tnc_hash),
            "tnc_accepted_date": pref.tnc_accepted_date.isoformat()
            if pref and pref.tnc_accepted_date
            else None,
            "facility_available": facility_available,
        }
    )


# ---------------------------------------------------------------------------
# Admin endpoints


@csrf_exempt
def quota_collection(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        return _list_quotas(request)
    if request.method == "POST":
        return _create_quota(request)
    return HttpResponse(status=405)


def _list_quotas(request: HttpRequest) -> JsonResponse:
    err, _user = _require_superuser(request)
    if err:
        return err

    queryset = FillyQuota.objects.select_related("user")
    facility_uuid = parse_facility_id(request.GET.get("facility_id"))
    if facility_uuid is not None:
        # Per-user rows within one facility.
        queryset = queryset.filter(facility_external_id=facility_uuid).exclude(
            user=None
        )
        username = (request.GET.get("username") or "").strip()
        if username:
            queryset = queryset.filter(user__username__icontains=username)
    else:
        # Facility-level rows.
        queryset = queryset.filter(user=None)
        facility_search = (request.GET.get("facility") or "").strip()
        if facility_search:
            try:
                from care.facility.models.facility import Facility

                matching_ids = Facility.objects.filter(
                    name__icontains=facility_search
                ).values_list("external_id", flat=True)[:200]
                queryset = queryset.filter(facility_external_id__in=list(matching_ids))
            except ImportError:
                pass

    ordering = request.GET.get("ordering") or "-created_date"
    if ordering not in ORDERINGS:
        ordering = "-created_date"
    queryset = queryset.order_by(ordering)

    try:
        limit = min(int(request.GET.get("limit", 10)), MAX_PAGE_SIZE)
        offset = max(int(request.GET.get("offset", 0)), 0)
    except ValueError:
        return _error("invalid_pagination", "limit/offset must be integers.", 400)

    count = queryset.count()
    page = list(queryset[offset : offset + limit])
    return JsonResponse(
        {
            "count": count,
            "results": [_quota_dict(q) for q in page],
        }
    )


def _create_quota(request: HttpRequest) -> JsonResponse:
    err, user = _require_superuser(request)
    if err:
        return err
    body = _body(request)

    facility_uuid = parse_facility_id(body.get("facility_external_id"))
    if facility_uuid is None:
        return _error(
            "facility_required", "A valid facility_external_id is required.", 400
        )
    try:
        from care.facility.models.facility import Facility

        if not Facility.objects.filter(external_id=facility_uuid).exists():
            return _error("facility_not_found", "Facility does not exist.", 400)
    except ImportError:
        pass

    if FillyQuota.objects.filter(
        facility_external_id=facility_uuid, user=None
    ).exists():
        return _error(
            "quota_exists", "A scribe quota already exists for this facility.", 400
        )

    quota = FillyQuota.objects.create(
        facility_external_id=facility_uuid,
        tokens=int(body.get("tokens") or 0),
        tokens_per_user=int(body.get("tokens_per_user") or 0),
        allow_scribe=bool(body.get("allow_scribe", True)),
        created_by=user,
    )
    return JsonResponse(_quota_dict(quota), status=201)


@csrf_exempt
def quota_detail(request: HttpRequest, external_id: str) -> HttpResponse:
    err, _user = _require_superuser(request)
    if err:
        return err

    quota = FillyQuota.objects.filter(external_id=external_id).first()
    if quota is None:
        return _error("not_found", "Quota not found.", 404)

    if request.method == "GET":
        return JsonResponse(_quota_dict(quota))

    if request.method == "PATCH":
        body = _body(request)
        for field in ("tokens", "tokens_per_user"):
            if field in body:
                setattr(quota, field, int(body[field]))
        if "allow_scribe" in body:
            quota.allow_scribe = bool(body["allow_scribe"])
        quota.save()
        return JsonResponse(_quota_dict(quota))

    if request.method == "DELETE":
        if quota.user_id is None:
            # Facility quota removal takes the per-user rows with it.
            FillyQuota.objects.filter(
                facility_external_id=quota.facility_external_id
            ).delete()
        else:
            quota.delete()
        return JsonResponse({"detail": "Quota deleted."})

    return HttpResponse(status=405)
