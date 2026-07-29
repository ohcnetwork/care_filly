from django.contrib import admin

from .models import FillyQuota, FillySession, FillyUsage, FillyUserPreference


@admin.register(FillyQuota)
class FillyQuotaAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "facility",
        "tokens",
        "tokens_per_user",
        "allow_filly",
    )
    search_fields = ("user__username", "facility__name")


@admin.register(FillyUsage)
class FillyUsageAdmin(admin.ModelAdmin):
    list_display = (
        "session",
        "user",
        "input_tokens",
        "output_tokens",
        "created_date",
    )
    search_fields = ("user__username", "session__external_id")


@admin.register(FillySession)
class FillySessionAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "user",
        "facility",
        "status",
        "started_at",
        "deleted",
    )
    list_filter = ("deleted", "status")
    search_fields = ("user__username", "external_id")


@admin.register(FillyUserPreference)
class FillyUserPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "filly_enabled", "tnc_accepted_date")
    search_fields = ("user__username",)
