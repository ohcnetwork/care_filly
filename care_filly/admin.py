from django.contrib import admin

from .models import FillyHistory, FillyQuota, FillyUsage, FillyUserPreference


@admin.register(FillyQuota)
class FillyQuotaAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "facility_external_id",
        "tokens",
        "tokens_per_user",
        "allow_scribe",
        "deleted",
    )
    list_filter = ("deleted", "allow_scribe")
    search_fields = ("user__username", "facility_external_id")


@admin.register(FillyUsage)
class FillyUsageAdmin(admin.ModelAdmin):
    list_display = (
        "session_id",
        "user",
        "input_tokens",
        "output_tokens",
        "created_date",
    )
    search_fields = ("user__username", "session_id")


@admin.register(FillyHistory)
class FillyHistoryAdmin(admin.ModelAdmin):
    list_display = ("session_id", "user", "status", "started_at", "deleted")
    list_filter = ("deleted", "status")
    search_fields = ("user__username", "session_id")


@admin.register(FillyUserPreference)
class FillyUserPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "scribe_enabled", "tnc_accepted_date")
    search_fields = ("user__username",)
