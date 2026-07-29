"""Error handling that keeps the plugin's public error contract.

The scribe frontend reads errors as ``{"error": {"code", "message"}}``.
``FillyAPIError`` produces exactly that shape; everything else falls
through to CARE's ``emr_exception_handler``.
"""

from __future__ import annotations

from rest_framework.response import Response

from care.emr.api.viewsets.base import emr_exception_handler


class FillyAPIError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def filly_exception_handler(exc, context):
    if isinstance(exc, FillyAPIError):
        return Response(
            {"error": {"code": exc.code, "message": exc.message}},
            status=exc.status,
        )
    return emr_exception_handler(exc, context)
