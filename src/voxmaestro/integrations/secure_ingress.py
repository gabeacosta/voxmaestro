"""Single-process, fail-closed admission for read-only VoxMaestro browser preview.

Requires aiohttp for the optional HTTP/WebSocket server. This is NOT a
distributed admission store or Ceinit effect authority. Must be deployed
only behind a trusted external identity/session provider.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Optional
from urllib.parse import urlsplit

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{24,256}$")
_SESSION = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_SCOPE = "conversation:readonly"
_WS_PROTOCOL = "voxmaestro.v1"
_TICKET_PREFIX = "vsai.ticket."


@dataclass(frozen=True)
class _Ticket:
    principal: str
    origin: str
    expiry: float


class AdmissionRejected(Exception):
    """Admission denied. Do not reveal the reason to the browser."""


class MemoryVoiceAdmission:
    """Atomically redeem tickets in exactly one asyncio process.

    This store is intentionally NOT safe for multiple workers or host replicas.
    The safe failure mode after a process restart is refusing outstanding tickets.
    """

    def __init__(
        self,
        *,
        allowed_origins: set[str],
        ticket_ttl_seconds: int = 60,
        max_pending: int = 128,
        max_active_per_user: int = 1,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if not allowed_origins or any(
            urlsplit(o).scheme not in {"https", "http"}
            or not urlsplit(o).netloc or urlsplit(o).path
            or urlsplit(o).query or urlsplit(o).fragment or urlsplit(o).username
            for o in allowed_origins
        ):
            raise ValueError("Exact scheme/host origins required")
        if not (1 <= ticket_ttl_seconds <= 120):
            raise ValueError("Invalid ticket TTL")
        if max_pending < 1 or max_active_per_user < 1:
            raise ValueError("Invalid admission budget")
        self.allowed_origins = frozenset(allowed_origins)
        self.ticket_ttl_seconds = ticket_ttl_seconds
        self.max_pending = max_pending
        self.max_active_per_user = max_active_per_user
        self._clock = monotonic
        self._lock = asyncio.Lock()
        self._pending: dict[str, _Ticket] = {}
        self._active: dict[str, int] = {}

    def _require(self, principal: str, origin: str) -> None:
        if (
            not isinstance(principal, str) or not principal.strip()
            or len(principal) > 256 or origin not in self.allowed_origins
        ):
            raise AdmissionRejected()

    def _purge(self, now: float) -> None:
        for key, item in list(self._pending.items()):
            if item.expiry <= now:
                del self._pending[key]

    async def issue(self, principal: str, origin: str) -> tuple[str, datetime]:
        self._require(principal, origin)
        async with self._lock:
            self._purge(self._clock())
            if len(self._pending) >= self.max_pending:
                raise AdmissionRejected()
            token = secrets.token_urlsafe(32)
            self._pending[sha256(token.encode()).hexdigest()] = _Ticket(
                principal, origin, self._clock() + self.ticket_ttl_seconds
            )
        expiry = datetime.now(timezone.utc) + timedelta(seconds=self.ticket_ttl_seconds)
        return token, expiry

    async def redeem(self, token: str, *, principal: str, origin: str) -> None:
        self._require(principal, origin)
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise AdmissionRejected()
        async with self._lock:
            now = self._clock()
            self._purge(now)
            item = self._pending.pop(sha256(token.encode()).hexdigest(), None)
            if (
                item is None or item.expiry <= now
                or item.principal != principal or item.origin != origin
                or self._active.get(principal, 0) >= self.max_active_per_user
            ):
                raise AdmissionRejected()
            self._active[principal] = self._active.get(principal, 0) + 1

    async def release(self, principal: str) -> None:
        async with self._lock:
            remaining = self._active.get(principal, 0)
            if remaining <= 1:
                self._active.pop(principal, None)
            else:
                self._active[principal] = remaining - 1


def _readonly_adapter(adapter: Any) -> None:
    cfg = getattr(getattr(adapter, "runtime", None), "config", None)
    if not isinstance(cfg, dict):
        raise ValueError("Configured VoxMaestro runtime required")
    handoff = cfg.get("handoff") or {}
    if cfg.get("tools") or not isinstance(handoff, dict) or handoff.get("delivery"):
        raise ValueError("Voice ingress must have zero tools and handoff delivery")


def create_voice_app(
    *,
    adapter: Any,
    authenticator: Callable[[Any], Any],
    admission: MemoryVoiceAdmission,
    wss_url: str,
    max_messages: int = 16,
    max_session_seconds: int = 600,
    issue_speech: Callable[[str, str], str | None] | None = None,
) -> Any:
    """Provide an HTTP bootstrap route and WSS route with mandatory external auth.

    The authenticator must validate a genuine upstream user identity/session.
    NEVER trust user-controlled headers, unverified cookies or text claims.
    """
    from aiohttp import WSMsgType, web

    _readonly_adapter(adapter)
    parsed = urlsplit(wss_url)
    if (
        parsed.scheme != "wss" or not parsed.netloc or parsed.username
        or parsed.password or parsed.query or parsed.fragment
        or parsed.path != "/session"
    ):
        raise ValueError("Exact wss://host/session URL required")
    if not callable(authenticator):
        raise ValueError("Verified-session authenticator required")
    if max_messages < 1 or not (1 <= max_session_seconds <= 600):
        raise ValueError("Invalid session budget")

    app = web.Application(client_max_size=2048)

    async def identity(request: Any) -> str:
        value = authenticator(request)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise web.HTTPUnauthorized()
        return value

    def check_origin(request: Any) -> str:
        origin = request.headers.get("Origin", "")
        if origin not in admission.allowed_origins:
            raise web.HTTPForbidden()
        return origin

    async def bootstrap(request: Any) -> Any:
        origin = check_origin(request)
        principal = await identity(request)
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType()
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise web.HTTPBadRequest() from None
        if body != {}:
            raise web.HTTPBadRequest()
        try:
            token, expiry = await admission.issue(principal, origin)
        except AdmissionRejected:
            raise web.HTTPForbidden() from None
        return web.json_response(
            {
                "scope": _SCOPE,
                "ticket": token,
                "expiresAt": expiry.isoformat().replace("+00:00", "Z"),
                "wssUrl": wss_url,
            },
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    async def session(request: Any) -> Any:
        origin = check_origin(request)
        principal = await identity(request)
        raw = request.headers.get("Sec-WebSocket-Protocol", "")
        protocols = [p.strip() for p in raw.split(",") if p.strip()]
        tickets = [p[len(_TICKET_PREFIX):] for p in protocols if p.startswith(_TICKET_PREFIX)]
        if (
            len(protocols) != 2 or protocols[0] != _WS_PROTOCOL
            or len(tickets) != 1 or protocols[1] != _TICKET_PREFIX + tickets[0]
        ):
            raise web.HTTPForbidden()
        try:
            await admission.redeem(tickets[0], principal=principal, origin=origin)
        except AdmissionRejected:
            raise web.HTTPForbidden() from None
        ws = web.WebSocketResponse(
            protocols=(_WS_PROTOCOL,), max_msg_size=4096, heartbeat=20
        )
        session_id: Optional[str] = None
        try:
            await ws.prepare(request)
            message_count = 0
            deadline = time.monotonic() + max_session_seconds
            while not ws.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if msg.type in {
                    WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR
                }:
                    break
                if msg.type != WSMsgType.TEXT:
                    await ws.close(code=1008, message=b"text_only")
                    break
                message_count += 1
                if message_count > max_messages or len(msg.data.encode("utf-8")) > 4096:
                    await ws.close(code=1008, message=b"budget")
                    break
                try:
                    frame = json.loads(msg.data)
                except (ValueError, TypeError):
                    await ws.close(code=1008, message=b"invalid")
                    break
                if not isinstance(frame, dict):
                    await ws.close(code=1008, message=b"invalid")
                    break
                kind, provided = frame.get("type"), frame.get("sessionId")
                if (
                    not isinstance(provided, str) or not _SESSION.fullmatch(provided)
                    or any(
                        k not in {"type", "sessionId", "locale", "text"}
                        for k in frame
                    )
                ):
                    await ws.close(code=1008, message=b"invalid")
                    break
                if session_id is None:
                    if kind != "start" or frame.get("locale", "en") != "en":
                        await ws.close(code=1008, message=b"start_required")
                        break
                    session_id = provided
                elif provided != session_id or kind not in {"message", "end"}:
                    await ws.close(code=1008, message=b"session_mismatch")
                    break
                if kind == "message" and (
                    not isinstance(frame.get("text"), str)
                    or not frame["text"].strip() or len(frame["text"]) > 500
                ):
                    await ws.close(code=1008, message=b"invalid_message")
                    break
                async def deliver_events() -> None:
                    async for event in adapter.iter_events(frame):
                        if event.get("sessionId") not in {None, session_id}:
                            continue
                        if event.get("type") in {
                            "booking_confirm", "booking_verified", "effect_verified", "audio"
                        }:
                            continue
                        outbound = dict(event)
                        if (
                            issue_speech is not None
                            and outbound.get("type") == "response"
                            and isinstance(outbound.get("text"), str)
                            and isinstance(outbound.get("metadata"), dict)
                            and outbound["metadata"].get("final") is True
                        ):
                            audio_id = issue_speech(principal, outbound["text"])
                            if audio_id is not None:
                                outbound["speechId"] = audio_id
                        await ws.send_json(outbound)

                # A slow model/runtime turn must not evade the session budget
                # just because we stopped waiting on socket.receive().
                try:
                    await asyncio.wait_for(
                        deliver_events(),
                        timeout=max(0.001, deadline - time.monotonic()),
                    )
                except asyncio.TimeoutError:
                    await ws.close(code=1008, message=b"session_timeout")
                    break
                if kind == "end":
                    session_id = None
                    break
        finally:
            if session_id is not None:
                async for _ in adapter.iter_events(
                    {"type": "end", "sessionId": session_id}
                ):
                    pass
            await admission.release(principal)
            if not ws.closed:
                await ws.close()
        return ws

    app.router.add_post("/api/voice/bootstrap", bootstrap)
    app.router.add_get("/session", session)
    return app
