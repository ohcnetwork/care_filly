"""Quota management endpoints (CARE EMR viewset pattern).

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
- DELETE v1/quota/<external_id>   -> facility rows take their user rows along
                                     (soft delete, same as CARE's destroy)
"""

from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework.response import Response

from care.emr.api.viewsets.base import EMRModelViewSet
from care.facility.models.facility import Facility
from care.utils.pagination.care_pagination import CareLimitOffsetPagination
from care_filly.api.exceptions import FillyAPIError, filly_exception_handler
from care_filly.models import FillyQuota, FillyUserPreference
from care_filly.quota import current_tnc, parse_facility_id
from care_filly.resources.quota import (
    FillyQuotaCreateSpec,
    FillyQuotaReadSpec,
    FillyQuotaUpdateSpec,
    facility_name,
)

logger = logging.getLogger("care_filly")

ORDERINGS = {"created_date", "-created_date", "tokens", "-tokens"}


class FillyQuotaPagination(CareLimitOffsetPagination):
    default_limit = 10
    max_limit = 100


class FillyQuotaViewSet(EMRModelViewSet):
    database_model = FillyQuota
    pydantic_model = FillyQuotaCreateSpec
    pydantic_update_model = FillyQuotaUpdateSpec
    pydantic_read_model = FillyQuotaReadSpec
    pagination_class = FillyQuotaPagination

    def get_exception_handler(self):
        return filly_exception_handler

    # -- authorization (admin CRUD is superuser-only) -----------------

    def _require_superuser(self) -> None:
        if not getattr(self.request.user, "is_superuser", False):
            raise FillyAPIError("forbidden", "Superuser access required", 403)

    def authorize_create(self, instance) -> None:
        self._require_superuser()

    def authorize_update(self, request_obj, model_instance) -> None:
        self._require_superuser()

    def authorize_destroy(self, instance) -> None:
        self._require_superuser()

    def authorize_retrieve(self, model_instance) -> None:
        self._require_superuser()

    # -- admin queryset / list filters ---------------------------------

    def get_queryset(self):
        self._require_superuser()
        queryset = FillyQuota.objects.select_related("user", "created_by")
        if self.action != "list":
            return queryset

        facility_uuid = parse_facility_id(self.request.GET.get("facility_id"))
        if facility_uuid is not None:
            # Per-user rows within one facility.
            queryset = queryset.filter(facility_external_id=facility_uuid).exclude(
                user=None
            )
            username = (self.request.GET.get("username") or "").strip()
            if username:
                queryset = queryset.filter(user__username__icontains=username)
        else:
            # Facility-level rows.
            queryset = queryset.filter(user=None)
            facility_search = (self.request.GET.get("facility") or "").strip()
            if facility_search:
                matching_ids = Facility.objects.filter(
                    name__icontains=facility_search
                ).values_list("external_id", flat=True)[:200]
                queryset = queryset.filter(facility_external_id__in=list(matching_ids))

        ordering = self.request.GET.get("ordering") or "-created_date"
        if ordering not in ORDERINGS:
            ordering = "-created_date"
        return queryset.order_by(ordering)

    # -- admin create / destroy validation ------------------------------

    def clean_create_data(self, request_data):
        facility_uuid = parse_facility_id(request_data.get("facility_external_id"))
        if facility_uuid is None:
            raise FillyAPIError(
                "facility_required", "A valid facility_external_id is required."
            )
        return request_data

    def validate_data(self, instance, model_obj=None) -> None:
        if model_obj is not None:  # update — nothing to validate
            return
        facility_uuid = instance.facility_external_id
        if not Facility.objects.filter(external_id=facility_uuid).exists():
            raise FillyAPIError("facility_not_found", "Facility does not exist.")
        if FillyQuota.objects.filter(
            facility_external_id=facility_uuid, user=None
        ).exists():
            raise FillyAPIError(
                "quota_exists", "A scribe quota already exists for this facility."
            )

    def perform_destroy(self, instance) -> None:
        if instance.user_id is None:
            # Facility quota removal takes the per-user rows with it
            # (explicitly — soft delete does not cascade).
            FillyQuota.objects.filter(
                facility_external_id=instance.facility_external_id
            ).update(deleted=True, modified_date=timezone.now())
        else:
            super().perform_destroy(instance)

    # -- user-facing endpoints ------------------------------------------

    def my(self, request):
        facility_uuid = parse_facility_id(request.GET.get("facility_id"))
        if facility_uuid is None:
            raise FillyAPIError("facility_required", "A valid facility_id is required.")

        tnc, tnc_hash = current_tnc()
        facility_quota = FillyQuota.objects.filter(
            facility_external_id=facility_uuid, user=None
        ).first()
        user_quota = FillyQuota.objects.filter(
            facility_external_id=facility_uuid, user=request.user
        ).first()

        name = facility_name(facility_uuid)
        quotas = [
            FillyQuotaReadSpec.serialize(q, facility_name=name).to_json()
            for q in (facility_quota, user_quota)
            if q is not None
        ]
        return Response(
            {
                "quotas": quotas,
                "tnc": tnc,
                "tnc_accepted": bool(user_quota and user_quota.tnc_hash == tnc_hash),
            }
        )

    def accept_tnc(self, request):
        facility_uuid = parse_facility_id(request.data.get("facility_id"))
        if facility_uuid is None:
            raise FillyAPIError("facility_required", "A valid facility_id is required.")

        _tnc, tnc_hash = current_tnc()
        user_quota = FillyQuota.objects.filter(
            facility_external_id=facility_uuid, user=request.user
        ).first()
        if user_quota and user_quota.tnc_hash == tnc_hash:
            return Response({"detail": "Terms and Conditions already accepted."})

        if user_quota is None:
            facility_quota = FillyQuota.objects.filter(
                facility_external_id=facility_uuid, user=None
            ).first()
            if facility_quota is None:
                raise FillyAPIError(
                    "no_facility_quota", "Facility does not have a quota."
                )
            FillyQuota.objects.create(
                user=request.user,
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

        return Response({"detail": "Terms and Conditions accepted successfully."})

    def preference(self, request):
        """Per-user scribe opt-in, managed from the user's profile page.

        Enabling records acceptance of the current TnC (the client shows
        the consent dialog first). If the TnC text changes later,
        `tnc_accepted` flips to false and the client must re-consent.
        """
        tnc, tnc_hash = current_tnc()
        pref = FillyUserPreference.objects.filter(user=request.user).first()
        facility_available = _user_has_scribe_facility(request.user)

        if request.method == "PUT":
            enabled = bool(request.data.get("enabled"))
            if enabled and not facility_available:
                raise FillyAPIError(
                    "no_facility_quota",
                    "None of your facilities have scribe enabled.",
                )
            if pref is None:
                pref = FillyUserPreference(user=request.user)
            pref.scribe_enabled = enabled
            if enabled:
                pref.tnc_hash = tnc_hash
                pref.tnc_accepted_date = timezone.now()
            pref.save()

        return Response(
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


def _user_has_scribe_facility(user) -> bool:
    """True when any facility the user belongs to has scribe enabled.

    Used by the profile page: there is no point offering the scribe
    opt-in to a user none of whose facilities have a scribe quota.
    """
    from care.emr.models.organization import FacilityOrganizationUser

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
