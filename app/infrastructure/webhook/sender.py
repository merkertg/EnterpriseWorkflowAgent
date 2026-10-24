"""Send signed outbox messages with the standard library, to one fixed URL.

The URL is checked once, when the sender is built from settings. Proxies from
the environment are ignored and redirects are refused, so a receiver answer
cannot send the event somewhere else either.
"""

import http.client
import json
import urllib.request
from typing import Any
from urllib.error import HTTPError, URLError

from app.application.delivery import WebhookMessage, WebhookResponse
from app.config import require_loopback_url

# The receiver answers with a short JSON status; nothing more is read.
_MAX_RESPONSE_BYTES = 4096


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Return no new request, so urllib reports the 3xx answer as an error."""

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        """Never follow a redirect."""
        return None


def _duplicate(raw: bytes) -> bool | None:
    """Read the receiver's ``{"status": "duplicate"}`` flag, if it sent one."""
    try:
        answer = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(answer, dict) or "status" not in answer:
        return None
    return bool(answer["status"] == "duplicate")


class UrllibWebhookSender:
    """Implements the WebhookSender port over HTTP."""

    def __init__(self, url: str) -> None:
        """Validate the loopback URL and build an opener without proxies."""
        self._url = require_loopback_url(url)
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _RefuseRedirects()
        )

    @property
    def url(self) -> str:
        """The only destination of this sender."""
        return self._url

    def send(self, message: WebhookMessage, timeout_seconds: float) -> WebhookResponse:
        """POST the body; map every failure to a short code instead of raising."""
        request = urllib.request.Request(
            self._url, data=message.body, headers=message.headers, method="POST"
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                status = int(response.status)
                raw = response.read(_MAX_RESPONSE_BYTES)
        except HTTPError as error:
            status = error.code
            error.close()
            if 300 <= status < 400:
                return WebhookResponse(status, "redirect_refused")
            return WebhookResponse(status, f"http_{status}")
        except TimeoutError:
            return WebhookResponse(None, "timeout")
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                return WebhookResponse(None, "timeout")
            return WebhookResponse(None, "connection_error")
        except (OSError, http.client.HTTPException):
            return WebhookResponse(None, "connection_error")
        if not 200 <= status < 300:
            return WebhookResponse(status, f"http_{status}")
        return WebhookResponse(status, None, _duplicate(raw))
