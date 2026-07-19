"""Truthful, per-call VoxMaestro runtime.

This module establishes the v0.2 execution boundary without breaking the alpha
``VoxMaestro`` API in ``conductor.py``. Shared configuration is immutable;
mutable callbacks and conversation state belong to one ``CallSession``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .conductor import (
    CallPhase,
    ConversationContext,
    SchemaLoader,
    StateMachine,
    ToolCallResult,
    TransitionResult,
)

logger = logging.getLogger("voxmaestro.runtime")


class RuntimeConfigurationError(RuntimeError):
    """Execution required an adapter that was not configured."""


ToolExecutor = Callable[
    [str, Mapping[str, Any], Mapping[str, Any], ConversationContext], Awaitable[Any]
]
HandoffExecutor = Callable[
    [Mapping[str, Any], Mapping[str, Any], ConversationContext], Awaitable[Any]
]
IntentClassifier = Callable[[str, ConversationContext], Awaitable[str]]
FillerCallback = Callable[[dict[str, Any]], Awaitable[None]]
TransferCallback = Callable[[dict[str, Any]], Awaitable[None]]
MetricCallback = Callable[[str, float, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class RuntimeToolResult:
    """A tool result that distinguishes real, failed, and simulated work."""

    tool_name: str
    success: bool
    data: Any = None
    error: Optional[str] = None
    latency_ms: float = 0
    simulated: bool = False


class RuntimeToolBridge:
    """Execute tools without ever inventing external success."""

    def __init__(
        self,
        config: Mapping[str, Any],
        executor: Optional[ToolExecutor] = None,
        *,
        dry_run: bool = False,
    ):
        self.tools = config.get("tools", {})
        self.executor = executor
        self.dry_run = dry_run

    async def execute(
        self,
        tool_name: str,
        context: ConversationContext,
        on_filler: Optional[FillerCallback] = None,
    ) -> RuntimeToolResult:
        tool = self.tools.get(tool_name)
        if not tool:
            return RuntimeToolResult(tool_name, False, error=f"Unknown tool: {tool_name}")

        context.phase = CallPhase.FILLER_PLAYING
        filler = tool.get("filler")
        if filler and on_filler:
            await on_filler(dict(filler))

        context.phase = CallPhase.TOOL_PENDING
        started_at = time.monotonic()
        timeout_ms = tool.get("timeout_ms", 3000)

        try:
            data = await asyncio.wait_for(
                self._execute(tool_name, tool, context),
                timeout=timeout_ms / 1000,
            )
            latency_ms = (time.monotonic() - started_at) * 1000
            context.phase = CallPhase.ACTIVE

            if isinstance(data, Mapping) and data.get("__voxmaestro_simulated__"):
                simulated_data = dict(data)
                simulated_data.pop("__voxmaestro_simulated__", None)
                context.tool_results[tool_name] = simulated_data
                return RuntimeToolResult(
                    tool_name,
                    False,
                    data=simulated_data,
                    error="Tool execution was simulated",
                    latency_ms=latency_ms,
                    simulated=True,
                )

            context.tool_results[tool_name] = data
            return RuntimeToolResult(tool_name, True, data=data, latency_ms=latency_ms)
        except asyncio.TimeoutError:
            context.phase = CallPhase.ACTIVE
            return RuntimeToolResult(
                tool_name,
                False,
                error=f"Timeout after {timeout_ms}ms",
                latency_ms=(time.monotonic() - started_at) * 1000,
            )
        except Exception as error:  # External adapter boundary.
            context.phase = CallPhase.ACTIVE
            logger.exception("[%s] Tool '%s' failed", context.call_id, tool_name)
            return RuntimeToolResult(
                tool_name,
                False,
                error=str(error),
                latency_ms=(time.monotonic() - started_at) * 1000,
            )

    async def _execute(
        self,
        tool_name: str,
        tool: Mapping[str, Any],
        context: ConversationContext,
    ) -> Any:
        params = {
            key: context.metadata.get(key)
            for key in tool.get("params_from_context", [])
        }
        if self.executor:
            return await self.executor(tool_name, tool, params, context)
        if self.dry_run:
            return {
                "__voxmaestro_simulated__": True,
                "tool": tool_name,
                "endpoint": tool.get("endpoint"),
                "params": params,
            }
        raise RuntimeConfigurationError(
            f"Tool '{tool_name}' requires a ToolExecutor. "
            "Pass tool_executor=... or explicitly set dry_run=True."
        )


class RuntimeHandoff:
    """Perform handoff with truthful delivery receipts."""

    def __init__(
        self,
        config: Mapping[str, Any],
        executor: Optional[HandoffExecutor] = None,
        *,
        dry_run: bool = False,
    ):
        self.config = config
        self.handoff = config.get("handoff", {})
        self.executor = executor
        self.dry_run = dry_run

    async def execute(
        self,
        context: ConversationContext,
        *,
        on_filler: Optional[FillerCallback] = None,
        on_transfer: Optional[TransferCallback] = None,
    ) -> dict[str, Any]:
        phases = self.config.get("states", {}).get("handoff", {}).get("phases", {})
        context.phase = CallPhase.HANDOFF_BRIDGE

        filler_text = phases.get("bridge", {}).get(
            "filler", "Let me connect you with someone."
        )
        if on_filler:
            await on_filler({"text": filler_text})

        payload = self._payload(context)
        receipts = []
        for delivery in self.handoff.get("delivery", []):
            channel = delivery.get("channel", "unknown")
            if self.executor:
                try:
                    result = await self.executor(delivery, payload, context)
                    receipts.append(
                        {"channel": channel, "status": "delivered", "result": result}
                    )
                except Exception as error:  # External adapter boundary.
                    receipts.append(
                        {"channel": channel, "status": "failed", "error": str(error)}
                    )
            elif self.dry_run:
                receipts.append({"channel": channel, "status": "simulated"})
            else:
                receipts.append(
                    {
                        "channel": channel,
                        "status": "not_delivered",
                        "error": "No HandoffExecutor configured",
                    }
                )

        context.phase = CallPhase.HANDOFF_TEARDOWN
        teardown = phases.get("teardown", {})
        teardown_data = {
            "payload": payload,
            "transcript": (
                context.conversation_history if teardown.get("save_transcript") else None
            ),
            "irelop_score": (
                context.irelop_score if teardown.get("save_irelop_score") else None
            ),
            "save_to": teardown.get("save_to"),
        }
        if on_transfer:
            await on_transfer(teardown_data)
        context.phase = CallPhase.EXITED

        return {
            "phases_completed": ["decision", "bridge", "teardown"],
            "reason": context.handoff_reason or "caller_request",
            "payload": payload,
            "delivery": receipts,
            "teardown": teardown_data,
        }

    def _payload(self, context: ConversationContext) -> dict[str, Any]:
        fields = {
            "caller_phone": context.caller_phone,
            "irelop_score": (
                context.irelop_score.get("total") if context.irelop_score else None
            ),
            "irelop_breakdown": context.irelop_score,
            "intent_history": list(context.intent_history),
            "qualification_progress": context.metadata.get("qualification_progress"),
            "conversation_summary": context.metadata.get("conversation_summary"),
            "handoff_reason": context.handoff_reason,
            "caller_sentiment": context.metadata.get("sentiment"),
            "call_duration_seconds": round(context.duration_seconds),
            "transcript_url": context.metadata.get("transcript_url"),
        }
        return {name: fields.get(name) for name in self.handoff.get("payload", [])}


class CallSession:
    """One isolated call with its own state and callbacks."""

    def __init__(
        self,
        runtime: "VoxMaestroRuntime",
        context: ConversationContext,
        *,
        on_filler: Optional[FillerCallback] = None,
        on_transfer: Optional[TransferCallback] = None,
        on_metric: Optional[MetricCallback] = None,
    ):
        self.runtime = runtime
        self.context = context
        self.on_filler = on_filler
        self.on_transfer = on_transfer
        self.on_metric = on_metric

    async def process_turn(
        self, caller_text: str, intent: Optional[str] = None
    ) -> dict[str, Any]:
        return await self.runtime._process_turn(
            self.context,
            caller_text,
            intent=intent,
            on_filler=self.on_filler,
            on_transfer=self.on_transfer,
            on_metric=self.on_metric,
        )


class VoxMaestroRuntime:
    """Shared runtime that creates isolated per-call sessions."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        tool_executor: Optional[ToolExecutor] = None,
        handoff_executor: Optional[HandoffExecutor] = None,
        intent_classifier: Optional[IntentClassifier] = None,
        dry_run: bool = False,
    ):
        SchemaLoader._validate(config)
        self.config = config
        self.state_machine = StateMachine(config)
        self.tools = RuntimeToolBridge(config, tool_executor, dry_run=dry_run)
        self.handoff = RuntimeHandoff(config, handoff_executor, dry_run=dry_run)
        self.intent_classifier = intent_classifier
        self.guardrails = config.get("guardrails", {})

    @classmethod
    def from_yaml(cls, path: str | Path, **kwargs: Any) -> "VoxMaestroRuntime":
        return cls(SchemaLoader.load(path), **kwargs)

    def start_call(
        self,
        call_id: str,
        caller_phone: str = "",
        *,
        on_filler: Optional[FillerCallback] = None,
        on_transfer: Optional[TransferCallback] = None,
        on_metric: Optional[MetricCallback] = None,
        **metadata: Any,
    ) -> CallSession:
        context = ConversationContext(call_id=call_id, caller_phone=caller_phone)
        context.metadata.update(metadata)
        return CallSession(
            self,
            context,
            on_filler=on_filler,
            on_transfer=on_transfer,
            on_metric=on_metric,
        )

    async def _process_turn(
        self,
        context: ConversationContext,
        caller_text: str,
        *,
        intent: Optional[str],
        on_filler: Optional[FillerCallback],
        on_transfer: Optional[TransferCallback],
        on_metric: Optional[MetricCallback],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "response_text": None,
            "filler": None,
            "tool_result": None,
            "generation_context": None,
            "state": context.current_state,
            "action": None,
        }
        if context.phase is CallPhase.EXITED:
            result["action"] = "ignored"
            return result

        resolved_intent = intent or await self._classify(caller_text, context)
        context.add_turn("caller", caller_text, intent=resolved_intent)
        transition = self.state_machine.evaluate_transition(context, resolved_intent)
        origin_state = context.current_state
        should_handoff = transition.new_state == "handoff" or transition.trigger == "handoff"

        if transition.tool_to_fire:
            result["filler"] = transition.filler
            self.state_machine.apply_transition(context, transition)
            tool_result = await self.tools.execute(
                transition.tool_to_fire, context, on_filler=on_filler
            )
            result["tool_result"] = tool_result
            result["generation_context"] = self.generation_context(context)
            if on_metric:
                await on_metric(
                    "tool_call_latency_ms",
                    tool_result.latency_ms,
                    {
                        "tool": transition.tool_to_fire,
                        "success": tool_result.success,
                        "simulated": tool_result.simulated,
                    },
                )
            if not tool_result.success and not tool_result.simulated:
                failure = self.tools.tools[transition.tool_to_fire].get("on_failure", {})
                result["response_text"] = failure.get(
                    "message", "I'm sorry, I'm having trouble with that."
                )
                should_handoff = failure.get("trigger") == "handoff"
            if not should_handoff:
                return_to = self.config["states"].get("tool_call", {}).get(
                    "return_to", "previous"
                )
                return_state = origin_state if return_to == "previous" else return_to
                self.state_machine.apply_transition(
                    context, TransitionResult(new_state=return_state)
                )
        else:
            self.state_machine.apply_transition(context, transition)

        if transition.trigger == "graceful_exit":
            result["action"] = "exit"
            result["response_text"] = self.config["states"].get("exit", {}).get(
                "farewell_message", "Goodbye!"
            )
            context.phase = CallPhase.EXITED
        elif should_handoff:
            if context.current_state != "handoff":
                self.state_machine.apply_transition(context, TransitionResult("handoff"))
            context.handoff_reason = (
                "max_turns_escalation"
                if transition.trigger == "max_turns_escalation"
                else f"intent:{resolved_intent}"
            )
            result["handoff"] = await self.handoff.execute(
                context, on_filler=on_filler, on_transfer=on_transfer
            )
            result["action"] = "handoff"

        result["state"] = context.current_state
        return result

    async def _classify(self, text: str, context: ConversationContext) -> str:
        if self.intent_classifier:
            return await self.intent_classifier(text, context)
        return "unknown"

    @staticmethod
    def generation_context(context: ConversationContext) -> dict[str, Any]:
        return {
            "call_id": context.call_id,
            "state": context.current_state,
            "previous_state": context.previous_state,
            "intent_history": list(context.intent_history),
            "tool_results": dict(context.tool_results),
            "conversation_history": list(context.conversation_history),
            "metadata": dict(context.metadata),
        }
