"""Bound request bodies before any route reads them.

Uploads have their own streamed 1 MiB limit. Every other request body is a
small JSON command, so it must declare its length and stay under 64 KiB.
Requests without a declared length (chunked bodies) are refused with 411:
counting a stream inside the route would let FastAPI read it into memory
first.
"""

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

MAX_JSON_BODY_BYTES = 65_536
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _refusal(status: int, code: str, message: str) -> JSONResponse:
    """The API's usual error body."""
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status
    )


class BodySizeLimit:
    """ASGI middleware: check Content-Length of command requests up front."""

    def __init__(
        self,
        app: ASGIApp,
        limit: int = MAX_JSON_BODY_BYTES,
        exempt_paths: frozenset[str] = frozenset(),
    ) -> None:
        """Wrap the app; exempt paths enforce their own limits."""
        self._app = app
        self._limit = limit
        self._exempt = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Refuse an undeclared or oversized body; pass everything else on."""
        if (
            scope["type"] != "http"
            or scope["method"] not in _BODY_METHODS
            or scope["path"] in self._exempt
        ):
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        if declared is None and "chunked" in headers.get("transfer-encoding", ""):
            response = _refusal(
                411, "length_required", "Send the request with a Content-Length."
            )
        elif declared is not None and (
            not declared.isdigit() or int(declared) > self._limit
        ):
            response = _refusal(
                413, "too_large", f"The request body exceeds {self._limit} bytes."
            )
        else:
            await self._app(scope, receive, send)
            return
        await response(scope, receive, send)
