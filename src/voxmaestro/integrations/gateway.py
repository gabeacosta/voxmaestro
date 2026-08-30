"""WebSocket gateway between browser frames and ``WebSessionAdapter``.

Dependency-free: works with any connection object supporting
``async for message in conn`` (str or bytes) and ``await conn.send(data)``
(websockets, aiohttp, Starlette, or a test fake).

Frame protocol v0 (all server frames are JSON text):

- client -> server text: ``start`` / ``message`` / ``end`` (existing contract)
- client -> server binary: mic PCM frame (requires ``asr=AsrIngress(...)``)
- server -> client: ``greeting`` / ``response`` / ``error`` /
  ``transcript_partial`` as JSON; ``audio`` events carry base64 PCM in
  ``pcmB64`` (v0 keeps one text frame type; binary audio can land with the
  LiveKit transport path)

One voice session per connection: the sessionId from ``start`` binds mic
frames until ``end``.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Optional

from voxmaestro.asr import AsrIngress
from voxmaestro.integrations.web_session import WebSessionAdapter


class WebSocketGateway:
    """Serve one browser connection through a WebSessionAdapter."""

    def __init__(
        self,
        adapter: WebSessionAdapter,
        *,
        asr: Optional[AsrIngress] = None,
        mic_sample_rate: int = 24000,
    ) -> None:
        """Bind the session adapter and optional ASR ingress."""
        self.adapter = adapter
        self.asr = asr
        self.mic_sample_rate = mic_sample_rate

    async def handle(self, conn: Any) -> None:
        """Process frames until the connection closes."""
        voice_session: Optional[str] = None
        async for raw in conn:
            if isinstance(raw, (bytes, bytearray)):
                voice_session = await self._on_pcm(conn, bytes(raw), voice_session)
                continue
            voice_session = await self._on_text(conn, raw, voice_session)

    async def _on_text(
        self, conn: Any, raw: Any, voice_session: Optional[str]
    ) -> Optional[str]:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            await self._send(conn, self._error("bad_json", "Invalid JSON frame"))
            return voice_session
        if not isinstance(message, dict):
            await self._send(
                conn, self._error("bad_frame", "Frame must be a JSON object")
            )
            return voice_session

        mtype = message.get("type")
        session_id = str(message.get("sessionId") or "")

        if mtype == "start" and self.asr is not None and session_id:
            self.asr.open(session_id, language=str(message.get("locale") or "en")[:2])
            voice_session = session_id

        if mtype == "end" and self.asr is not None and voice_session is not None:
            for out in self.asr.flush(voice_session):
                await self._dispatch_asr(conn, out)

        async for event in self.adapter.iter_events(message):
            await self._send(conn, event)

        if mtype == "end":
            if self.asr is not None and voice_session is not None:
                self.asr.close(voice_session)
            voice_session = None
        return voice_session

    async def _on_pcm(
        self, conn: Any, pcm: bytes, voice_session: Optional[str]
    ) -> Optional[str]:
        if self.asr is None:
            await self._send(
                conn,
                self._error("audio_not_enabled", "This gateway has no ASR ingress"),
            )
            return voice_session
        if voice_session is None:
            await self._send(
                conn,
                self._error("no_session", "Start a session before sending audio"),
            )
            return voice_session
        for out in self.asr.accept(voice_session, pcm, self.mic_sample_rate):
            await self._dispatch_asr(conn, out)
        return voice_session

    async def _dispatch_asr(self, conn: Any, out: dict) -> None:
        if out["type"] == "message":
            async for event in self.adapter.iter_events(out):
                await self._send(conn, event)
        else:
            await self._send(conn, out)

    async def _send(self, conn: Any, event: dict) -> None:
        if event.get("type") == "audio":
            event = dict(event)
            event["pcmB64"] = base64.b64encode(event.pop("pcm")).decode("ascii")
        await conn.send(json.dumps(event))

    @staticmethod
    def _error(code: str, text: str) -> dict:
        return {"type": "error", "text": text, "metadata": {"code": code}}
