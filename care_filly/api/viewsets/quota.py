"""Quota management endpoints.

User-facing:
- GET  v1/quota/my?facility_id=   -> {quotas, tnc, tnc_accepted}
- POST v1/quota/accept-tnc        -> accepts TnC, auto-creates the user quota
- GET  v1/preferences/filly       -> per-user filly opt-in + TnC state
- PUT  v1/preferences/filly       -> enable (accepts current TnC) / disable

Admin (requires ``can_manage_filly_quota`` in the target facility, or CARE
superuser for cross-facility listing):
- GET    v1/quota                 -> facility-level quotas (or per-user rows
                                     of one facility with ?facility_id=)
- POST   v1/quota                 -> create a facility quota
- GET    v1/quota/<external_id>
- PATCH  v1/quota/<external_id>
- DELETE v1/quota/<external_id>   -> facility rows cascade their user rows
"""

import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone

from care.security.authorization import AuthorizationController

from care_filly.api.common import authenticate, body, error, resolve_facility
from care_filly.models import FillyQuota, FillyUserPreference, used_tokens
from care_filly.quota import current_tnc

logger = logging.getLogger("care_filly")

MAX_PAGE_SIZE = 100
ORDERINGS = {"created_date", "-created_date", "tokens", "-tokens"}


def _require_user(request: HttpRequest):
    err, user = authenticate(request)
    if err:
        return err, None
    return None, user


def _can_manage(user, facility) -> bool:
    return AuthorizationController.call("can_manage_filly_quota", user, facility)


def _user_dict(user) -> dict | None:
    if user is None:
        return None
    return {
        "username": user.username,
        "first_name": getattr(user, "first_name", ""),
        "last_name": getattr(user, "last_name", ""),
    }


def _quota_dict(q: FillyQuota) -> dict:
    return {
        "external_id": str(q.external_id),
        "user": _user_dict(q.user),
        "facility_external_id": str(q.facility.external_id),
        "facility_name": q.facility.name,
        "tokens": q.tokens,
        "tokens_per_user": q.tokens_per_user,
        "used": used_tokens(q.facility_id, q.user_id),
        "allow_filly": q.allow_filly,
        "tnc_accepted_date": q.tnc_accepted_date.isoformat()
        if q.tnc_accepted_date
        else None,
        "created_by": _user_dict(q.created_by),
        "created_date": q.created_date.isoformat() if q.created_date else None,
        "modified_date": q.modified_date.isoformat() if q.modified_date else None,
    }


# ---------------------------------------------------------------------------
# User-facing endpoints


def my_quota(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return HttpResponse(status=405)
    err, user = _require_user(request)
    if err:
        return err

    facility = resolve_facility(request.GET.get("facility_id"))
    if facility is None:
        return error("facility_required", "A valid facility_id is required.", 400)

    tnc, tnc_hash = current_tnc()
    facility_quota = FillyQuota.objects.filter(facility=facility, user=None).first()
    user_quota = FillyQuota.objects.filter(facility=facility, user=user).first()

    quotas = [_quota_dict(q) for q in (facility_quota, user_quota) if q is not None]
    return JsonResponse(
        {
            "quotas": quotas,
            "tnc": tnc,
            "tnc_accepted": bool(user_quota and user_quota.tnc_hash == tnc_hash),
        }
    )


def accept_tnc(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponse(status=405)
    err, user = _require_user(request)
    if err:
        return err

    facility = resolve_facility(body(request).get("facility_id"))
    if facility is None:
        return error("facility_required", "A valid facility_id is required.", 400)

    tnc, tnc_hash = current_tnc()
    user_quota = FillyQuota.objects.filter(facility=facility, user=user).first()
    if user_quota and user_quota.tnc_hash == tnc_hash:
        return JsonResponse({"detail": "Terms and Conditions already accepted."})

    if user_quota is None:
        facility_quota = FillyQuota.objects.filter(facility=facility, user=None).first()
        if facility_quota is None:
            return error("no_facility_quota", "Facility does not have a quota.", 400)
        FillyQuota.objects.create(
            user=user,
            facility=facility,
            tokens=facility_quota.tokens_per_user,
            tnc_hash=tnc_hash,
            tnc_accepted_date=timezone.now(),
            created_by=user,
            updated_by=user,
        )
    else:
        user_quota.tnc_hash = tnc_hash
        user_quota.tnc_accepted_date = timezone.now()
        user_quota.save(
            update_fields=["tnc_hash", "tnc_accepted_date", "modified_date"]
        )

    return JsonResponse({"detail": "Terms and Conditions accepted successfully."})


def _user_has_filly_facility(user) -> bool:
    """True when any facility the user belongs to has filly enabled."""
    from care.emr.models.organization import FacilityOrganizationUser

    facility_ids = FacilityOrganizationUser.objects.filter(user=user).values_list(
        "organization__facility_id", flat=True
    )
    return FillyQuota.objects.filter(
        facility_id__in=list(facility_ids),
        user=None,
        allow_filly=True,
    ).exists()


def filly_preference(request: HttpRequest) -> HttpResponse:
    """Per-user filly opt-in, managed from the user's profile page."""
    if request.method not in ("GET", "PUT"):
        return HttpResponse(status=405)
    err, user = _require_user(request)
    if err:
        return err

    tnc, tnc_hash = current_tnc()
    pref = FillyUserPreference.objects.filter(user=user).first()
    facility_available = _user_has_filly_facility(user)

    if request.method == "PUT":
        enabled = bool(body(request).get("enabled"))
        if enabled and not facility_available:
            return error(
                "no_facility_quota",
                "None of your facilities have filly enabled.",
                400,
            )
        if pref is None:
            pref = FillyUserPreference(user=user, created_by=user)
        pref.filly_enabled = enabled
        if enabled:
            pref.tnc_hash = tnc_hash
            pref.tnc_accepted_date = timezone.now()
        pref.updated_by = user
        pref.save()

    return JsonResponse(
        {
            "enabled": bool(pref and pref.filly_enabled),
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


def quota_collection(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        return _list_quotas(request)
    if request.method == "POST":
        return _create_quota(request)
    return HttpResponse(status=405)


def _list_quotas(request: HttpRequest) -> JsonResponse:
    err, user = _require_user(request)
    if err:
        return err

    queryset = FillyQuota.objects.select_related("user", "facility")
    facility = resolve_facility(request.GET.get("facility_id"))
    if facility is not None:
        if not _can_manage(user, facility):
            return error("forbidden", "You cannot manage this facility's quota.", 403)
        # Per-user rows within one facility.
        queryset = queryset.filter(facility=facility).exclude(user=None)
        username = (request.GET.get("username") or "").strip()
        if username:
            queryset = queryset.filter(user__username__icontains=username)
    else:
        # Cross-facility listing is superuser-only.
        if not getattr(user, "is_superuser", False):
            return error("forbidden", "Superuser access required.", 403)
        queryset = queryset.filter(user=None)
        facility_search = (request.GET.get("facility") or "").strip()
        if facility_search:
            queryset = queryset.filter(facility__name__icontains=facility_search)

    ordering = request.GET.get("ordering") or "-created_date"
    if ordering not in ORDERINGS:
        ordering = "-created_date"
    queryset = queryset.order_by(ordering)

    try:
        limit = min(int(request.GET.get("limit", 10)), MAX_PAGE_SIZE)
        offset = max(int(request.GET.get("offset", 0)), 0)
    except ValueError:
        return error("invalid_pagination", "limit/offset must be integers.", 400)

    count = queryset.count()
    page = list(queryset[offset : offset + limit])
    return JsonResponse({"count": count, "results": [_quota_dict(q) for q in page]})


def _create_quota(request: HttpRequest) -> JsonResponse:
    err, user = _require_user(request)
    if err:
        return err
    b = body(request)

    facility = resolve_facility(b.get("facility_external_id"))
    if facility is None:
        return error(
            "facility_required", "A valid facility_external_id is required.", 400
        )
    if not _can_manage(user, facility):
        return error("forbidden", "You cannot manage this facility's quota.", 403)

    if FillyQuota.objects.filter(facility=facility, user=None).exists():
        return error(
            "quota_exists", "A filly quota already exists for this facility.", 400
        )

    quota = FillyQuota.objects.create(
        facility=facility,
        tokens=int(b.get("tokens") or 0),
        tokens_per_user=int(b.get("tokens_per_user") or 0),
        allow_filly=bool(b.get("allow_filly", True)),
        created_by=user,
        updated_by=user,
    )
    return JsonResponse(_quota_dict(quota), status=201)


def quota_detail(request: HttpRequest, external_id: str) -> HttpResponse:
    err, user = _require_user(request)
    if err:
        return err

    quota = (
        FillyQuota.objects.select_related("user", "facility")
        .filter(external_id=external_id)
        .first()
    )
    if quota is None:
        return error("not_found", "Quota not found.", 404)
    if not _can_manage(user, quota.facility):
        return error("forbidden", "You cannot manage this facility's quota.", 403)

    if request.method == "GET":
        return JsonResponse(_quota_dict(quota))

    if request.method == "PATCH":
        b = body(request)
        for field in ("tokens", "tokens_per_user"):
            if field in b:
                setattr(quota, field, int(b[field]))
        if "allow_filly" in b:
            quota.allow_filly = bool(b["allow_filly"])
        quota.updated_by = user
        quota.save()
        return JsonResponse(_quota_dict(quota))

    if request.method == "DELETE":
        quota.delete()
        return JsonResponse({"deleted": 1})

    return HttpResponse(status=405)
