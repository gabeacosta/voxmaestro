"""Private Maya V3 staging through Tailscale Serve, never Funnel.

Identity is the Tailscale-User-Login header inserted by Tailscale Serve.
The service MUST bind to 127.0.0.1 only, preventing remote clients from
forging this header by bypassing Serve. Requests from other local processes
on the Mac remain a trusted-host limitation.

Not usable as an Internet-facing identity provider or an effect gateway.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from voxmaestro.integrations.secure_ingress import (
    MemoryVoiceAdmission,
    create_voice_app,
)
from voxmaestro.integrations.private_speech import PrivateSpeechClips, speaker_probe

_LOGIN = re.compile(r"^[A-Za-z0-9_+@.:-]{3,200}$")
_REQUIRED = (
    "index.html",
    "styles.css",
    "demo.js",
    "voxmaestro-voice.mjs",
    "voxmaestro-voice-ui.mjs",
    "assets/maya-hero.webp",
)
_MIME = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".webp": "image/webp",
}


def check_tailnet_origin(origin: str) -> str:
    if not isinstance(origin, str):
        raise ValueError("An explicit tailnet HTTPS origin is required")
    url = urlsplit(origin)
    if (
        url.scheme != "https" or not url.hostname or not url.hostname.endswith(".ts.net")
        or url.port != 10000 or url.username or url.password
        or url.path or url.query or url.fragment
    ):
        raise ValueError("Use an exact https://<machine>.<tailnet>.ts.net:10000 origin")
    return origin


def stage_site_files(site: Path) -> dict[str, Path]:
    base = site.expanduser().resolve(strict=True)
    if not base.is_dir():
        raise ValueError("Maya site is not a directory")
    files = {}
    for name in _REQUIRED:
        resolved = (base / name).resolve(strict=True)
        if not resolved.is_file() or not resolved.is_relative_to(base):
            raise ValueError("Missing or escaping Maya site asset: " + name)
        files[name] = resolved
    html = files["index.html"].read_text(encoding="utf-8")
    for marker in ("voxmaestro-voice-ui.mjs", "id=\"microphone\"", "noindex"):
        if marker not in html:
            raise ValueError("Maya V3 browser voice source is missing: " + marker)
    return files


def make_tailnet_stage(
    *,
    adapter: Any,
    site_dir: Path,
    origin: str,
    allowed_logins: set[str],
    admission: MemoryVoiceAdmission | None = None,
    speech_clips: PrivateSpeechClips | None = None,
) -> Any:
    """Create the full site, bootstrap and WSS under one tailnet TLS origin."""
    from aiohttp import web

    checked_origin = check_tailnet_origin(origin)
    normalized_logins = {v.lower() for v in allowed_logins}
    if not normalized_logins or any(not _LOGIN.fullmatch(x) for x in normalized_logins):
        raise ValueError("Explicit allowed Tailscale user login(s) required")
    files = stage_site_files(site_dir)
    registry = admission or MemoryVoiceAdmission(allowed_origins={checked_origin})
    speech = speech_clips or PrivateSpeechClips()

    def authenticated(request: Any) -> str | None:
        # X-Forwarded-* and user-supplied identity headers are NOT a login.
        # Serve inserts this header after stripping client-supplied copies.
        if request.remote not in {"127.0.0.1", "::1"}:
            return None
        login = request.headers.get("Tailscale-User-Login", "")
        if not _LOGIN.fullmatch(login) or login.lower() not in normalized_logins:
            return None
        return login.lower()

    app = create_voice_app(
        adapter=adapter,
        authenticator=authenticated,
        admission=registry,
        wss_url="wss://" + urlsplit(checked_origin).netloc + "/session",
        max_messages=16,
        max_session_seconds=180,
        issue_speech=speech.issue,
    )
    csp = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self' "
        + "wss://" + urlsplit(checked_origin).netloc + "; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'none'"
    )

    async def file_view(request: Any) -> Any:
        if not authenticated(request):
            raise web.HTTPUnauthorized()
        key = request.path.lstrip("/") or "index.html"
        if key not in files:
            raise web.HTTPNotFound()
        path = files[key]
        result = web.FileResponse(path, headers={
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex, nofollow, noarchive",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": csp,
            "Permissions-Policy": "microphone=(self)",
        })
        result.content_type = _MIME[path.suffix]
        return result

    async def audio_test(request: Any) -> Any:
        if not authenticated(request):
            raise web.HTTPUnauthorized()
        return web.Response(
            body=speaker_probe(), content_type="audio/wav",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def audio_clip(request: Any) -> Any:
        principal = authenticated(request)
        if not principal:
            raise web.HTTPUnauthorized()
        data = await speech.audio(principal, request.match_info["speech_id"])
        if not data:
            raise web.HTTPNotFound()
        headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Accept-Ranges": "bytes",
        }
        # Safari frequently requests WAV using byte ranges. Bound and validate
        # Range rather than treating audio playback as a text-only API.
        spec = request.headers.get("Range")
        if spec:
            import re
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", spec)
            if not match or (not match[1] and not match[2]):
                raise web.HTTPRequestRangeNotSatisfiable()
            start = int(match[1]) if match[1] else max(0, len(data) - int(match[2]))
            end = int(match[2]) if match[2] else len(data) - 1
            if match[1] == "":
                end = len(data) - 1
            if start >= len(data) or end < start:
                raise web.HTTPRequestRangeNotSatisfiable()
            end = min(end, len(data) - 1)
            headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            return web.Response(
                status=206, body=data[start:end + 1],
                content_type="audio/wav", headers=headers,
            )
        return web.Response(body=data, content_type="audio/wav", headers=headers)

    app.router.add_get("/api/voice/audio-test.wav", audio_test)
    app.router.add_get("/api/voice/audio/{speech_id}", audio_clip)
    app.router.add_get("/", file_view)
    for filename in _REQUIRED:
        app.router.add_get("/" + filename, file_view, name="maya-" + filename.replace("/", "-"))
    # The route above carries no asset variable. Exact route identity determines
    # the file and prevents arbitrary file-system path resolution.
    return app
