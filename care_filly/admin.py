from django.contrib import admin

from .models import MedispeakSession


@admin.register(MedispeakSession)
class MedispeakSessionAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "user",
        "facility",
        "medispeak_session_id",
        "created_date",
    )
    search_fields = ("user__username", "medispeak_session_id")
