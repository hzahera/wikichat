"""Security helpers: web UI password, rate limit, safe headers, secret masking in logs."""
from __future__ import annotations

import base64
import ipaddress
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Only our own files: no CDN, no Google Fonts, no inline scripts, no framing.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def ensure_private_dir(path: Path) -> None:
    """Create a folder only the current user can read (wiki content may be internal)."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


class SecretFilter(logging.Filter):
    """Replace secret values with *** in every log line, in case one slips into an error."""

    def __init__(self, values: list[str]):
        super().__init__()
        self.values = sorted(set(values), key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        if self.values:
            msg = record.getMessage()
            masked = msg
            for v in self.values:
                masked = masked.replace(v, "***")
            if masked != msg:
                record.msg, record.args = masked, None
        return True


def install_secret_filter(values: list[str]) -> None:
    f = SecretFilter(values)
    root = logging.getLogger()
    for h in root.handlers:
        h.addFilter(f)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "wikichat"):
        logging.getLogger(name).addFilter(f)


class SecurityMiddleware(BaseHTTPMiddleware):
    """Password check (HTTP Basic), rate limit for the API, and security headers."""

    LIMITED = ("/api/chat", "/api/search")

    def __init__(self, app, password: str = "", rate_per_minute: int = 0):
        super().__init__(app)
        self.password = password
        self.rate = rate_per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    def _authorized(self, request: Request) -> bool:
        if not self.password:
            return True
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
        except Exception:  # noqa: BLE001
            return False
        _, _, given = decoded.partition(":")
        return secrets.compare_digest(given.encode(), self.password.encode())

    def _allowed(self, request: Request) -> bool:
        if not self.rate or not request.url.path.startswith(self.LIMITED):
            return True
        key = request.client.host if request.client else "?"
        now, q = time.monotonic(), self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.rate:
            return False
        q.append(now)
        if len(self.hits) > 10_000:  # keep memory bounded
            self.hits.clear()
        return True

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == "/healthz":
            response = await call_next(request)
        elif not self._authorized(request):
            response = Response("Login required", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="WikiChat", charset="UTF-8"'})
        elif not self._allowed(request):
            response = JSONResponse({"detail": "Too many requests. Please wait a minute."}, status_code=429)
        else:
            response = await call_next(request)
        for k, v in HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        else:  # always check for a newer UI after an update
            response.headers.setdefault("Cache-Control", "no-cache")
        return response
