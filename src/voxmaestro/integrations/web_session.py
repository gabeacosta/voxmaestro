"""Transport-neutral browser session adapter for ``VoxMaestroRuntime``.

This module maps the existing browser ``start`` / ``message`` / ``end`` contract
onto isolated VoxMaestro ``CallSession`` instances. It deliberately does not own
a WebSocket server, model runtime, or external-effect credentials.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Optional

from voxmaestro.runtime import CallSession, VoxMaestroRuntime

GenerationAdapter = Callable[
    [str, Mapping[str, Any], Mapping[str, Any]], Awaitable[str]
]


@dataclass
class _WebSession:
    call: CallSession
    queue: asyncio.Queue[dict[str, Any]]
    lock: asyncio.Lock


class WebSessionAdapter:
    """Map browser session events onto truthful, isolated runtime sessions.

    The adapter preserves the current public browser message types:
    ``greeting``, ``response``, ``booking_confirm``, and ``error``. This v0
    implementation never emits ``booking_confirm`` because a model/tool return is
    not sufficient proof that a consequential booking effect occurred.
    """

    def __init__(
        self,
        runtime: VoxMaestroRuntime,
        *,
        generation_adapter: Optional[GenerationAdapter] = None,
        greeting_text: str = "What can I help you with?",
    ):
        self.runtime = runtime
        self.generation_adapter = generation_adapter
        self.greeting_text = greeting_text
        self._sessions: dict[str, _WebSession] = {}

    @property
    def active_session_ids(self) -> tuple[str, ...]:
        return tuple(self._sessions)

    def context_for(self, session_id: str):
        session = self._sessions.get(session_id)
        return session.call.context if session else None

    async def iter_events(
        self, message: Mapping[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        """Consume one normalized browser event and yield client events."""
        event_type = message.get("type")
        session_id = str(message.get("sessionId") or "").strip()

        if not session_id:
            yield self._error("missing_session_id", "sessionId is required")
            return

        if event_type == "start":
            if session_id in self._sessions:
                yield self._error(
                    "session_exists",
                    "A session with this sessionId already exists",
                    session_id,
                )
                return
            self._start(session_id, message)
            yield {
                "type": "greeting",
                "text": self.greeting_text,
                "sessionId": session_id,
                "metadata": {"phase": "started"},
            }
            return

        if event_type == "end":
            self._sessions.pop(session_id, None)
            return

        if event_type != "message":
            yield self._error(
                "unsupported_event",
                f"Unsupported event type: {event_type!r}",
                session_id,
            )
            return

        session = self._sessions.get(session_id)
        if session is None:
            yield self._error(
                "unknown_session",
                "Start the session before sending messages",
                session_id,
            )
            return

        text = str(message.get("text") or "").strip()
        if not text:
            yield self._error("empty_message", "Message text is required", session_id)
            return

        async with session.lock:
            async for event in self._process_message(session_id, session, text):
                yield event

    def _start(self, session_id: str, message: Mapping[str, Any]) -> None:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def on_filler(filler: dict[str, Any]) -> None:
            await queue.put({"kind": "filler", "payload": dict(filler)})

        async def on_transfer(teardown: dict[str, Any]) -> None:
            await queue.put({"kind": "handoff_teardown", "payload": dict(teardown)})

        metadata = {
            key: value
            for key, value in message.items()
            if key not in {"type", "sessionId", "text", "callerPhone"}
        }
        call = self.runtime.start_call(
            session_id,
            caller_phone=str(message.get("callerPhone") or ""),
            on_filler=on_filler,
            on_transfer=on_transfer,
            **metadata,
        )
        self._sessions[session_id] = _WebSession(
            call=call,
            queue=queue,
            lock=asyncio.Lock(),
        )

    async def _process_message(
        self,
        session_id: str,
        session: _WebSession,
        text: str,
    ) -> AsyncIterator[dict[str, Any]]:
        turn_task = asyncio.create_task(session.call.process_turn(text))
        queue_task: Optional[asyncio.Task[dict[str, Any]]] = asyncio.create_task(
            session.queue.get()
        )

        while True:
            wait_set: set[asyncio.Task[Any]] = {turn_task}
            if queue_task is not None:
                wait_set.add(queue_task)
            done, _ = await asyncio.wait(wait_set, return_when=asyncio.FIRST_COMPLETED)

            if queue_task is not None and queue_task in done:
                internal = queue_task.result()
                event = self._map_internal_event(session_id, internal)
                if event is not None:
                    yield event
                queue_task = (
                    None if turn_task.done() else asyncio.create_task(session.queue.get())
                )

            if turn_task in done:
                result = turn_task.result()
                if queue_task is not None and not queue_task.done():
                    queue_task.cancel()
                    await asyncio.gather(queue_task, return_exceptions=True)
                break

        while not session.queue.empty():
            event = self._map_internal_event(session_id, session.queue.get_nowait())
            if event is not None:
                yield event

        async for event in self._final_events(session_id, session, text, result):
            yield event

    @staticmethod
    def _map_internal_event(
        session_id: str, internal: Mapping[str, Any]
    ) -> Optional[dict[str, Any]]:
        if internal.get("kind") != "filler":
            return None
        filler = internal.get("payload") or {}
        text = str(filler.get("text") or "").strip()
        if not text:
            return None
        return {
            "type": "response",
            "text": text,
            "sessionId": session_id,
            "metadata": {"phase": "filler", "final": False},
        }

    async def _final_events(
        self,
        session_id: str,
        session: _WebSession,
        caller_text: str,
        result: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        if result.get("action") == "ignored":
            yield self._error(
                "session_closed",
                "This conversation has already ended",
                session_id,
            )
            return

        tool_result = result.get("tool_result")
        if tool_result is not None and tool_result.simulated:
            yield self._error(
                "simulated_effect",
                "The requested action was simulated; no external effect was completed",
                session_id,
                metadata={"tool": tool_result.tool_name, "simulated": True},
            )
            return

        if result.get("action") == "handoff":
            handoff = result.get("handoff") or {}
            deliveries = list(handoff.get("delivery") or [])
            statuses = [delivery.get("status") for delivery in deliveries]
            delivered = any(status == "delivered" for status in statuses)
            fallback = (
                "I've passed this to a person with the conversation context."
                if delivered
                else "I couldn't complete that here, and the automatic handoff was not delivered."
            )
            yield {
                "type": "response",
                "text": result.get("response_text") or fallback,
                "sessionId": session_id,
                "metadata": {
                    "phase": "handoff",
                    "final": True,
                    "handoffDelivered": delivered,
                    "deliveryStatuses": statuses,
                },
            }
            return

        if result.get("action") == "exit":
            yield {
                "type": "response",
                "text": result.get("response_text") or "Thanks for stopping by.",
                "sessionId": session_id,
                "metadata": {"phase": "exit", "final": True},
            }
            return

        if tool_result is not None and not tool_result.success:
            yield {
                "type": "response",
                "text": result.get("response_text")
                or "I couldn't verify that action, so I won't claim it completed.",
                "sessionId": session_id,
                "metadata": {
                    "phase": "tool_failure",
                    "final": True,
                    "tool": tool_result.tool_name,
                },
            }
            return

        if self.generation_adapter is None:
            yield self._error(
                "generation_unconfigured",
                "No generation adapter is configured",
                session_id,
            )
            return

        context = self.runtime.generation_context(session.call.context)
        generation_config = self.runtime.config.get("generation", {})
        try:
            response_text = await self.generation_adapter(
                caller_text,
                context,
                generation_config,
            )
        except Exception as error:
            yield self._error(
                "generation_failed",
                str(error),
                session_id,
            )
            return

        response_text = str(response_text or "").strip()
        if not response_text:
            yield self._error(
                "empty_generation",
                "Generation adapter returned no response",
                session_id,
            )
            return

        # Keep assistant context without advancing caller-turn/state counters.
        session.call.context.conversation_history.append(
            {
                "role": "assistant",
                "content": response_text,
                "intent": None,
                "timestamp": time.time(),
            }
        )
        intent = (
            session.call.context.intent_history[-1]
            if session.call.context.intent_history
            else None
        )
        yield {
            "type": "response",
            "text": response_text,
            "sessionId": session_id,
            "metadata": {
                "phase": "final",
                "final": True,
                "state": session.call.context.current_state,
                "intent": intent,
                "presentationHint": self._presentation_hint(intent),
            },
        }

    @staticmethod
    def _presentation_hint(intent: Optional[str]) -> Optional[str]:
        hints = {
            "greeting": "understand",
            "service_question": "prove",
            "price_question": "prove",
            "pricing_question": "prove",
            "availability_question": "act",
            "schedule_appointment": "act",
            "booking_request": "act",
            "human_request": "confirm",
        }
        return hints.get(intent)

    @staticmethod
    def _error(
        code: str,
        text: str,
        session_id: Optional[str] = None,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "type": "error",
            "text": text,
            "metadata": {"code": code, **dict(metadata or {})},
        }
        if session_id:
            event["sessionId"] = session_id
        return event
