"""Fail-closed consequential tool authorization for local Voice Maestro.

Models and conversation state may propose tool calls. They must not mint
authority. A consequential ``ToolExecutor`` requires an external Veynit-shaped
grant; absence of a grant fails closed. This module does not self-issue grants.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

ToolExecutor = Callable[[str, Mapping[str, Any], Mapping[str, Any], Any], Awaitable[Any]]


class AuthorizationDenied(PermissionError):
    """Raised when a consequential tool lacks an external grant."""


@dataclass(frozen=True)
class ExternalGrant:
    """Minimal external grant projection. Never constructed by the model path."""

    grant_id: str
    effect: str
    scope: str
    source: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], *, source: str) -> ExternalGrant:
        grant_id = str(raw.get("grant_id") or "").strip()
        effect = str(raw.get("effect") or "").strip()
        scope = str(raw.get("scope") or "").strip()
        if not grant_id or not effect or not scope:
            raise AuthorizationDenied("external grant is missing grant_id/effect/scope")
        if str(raw.get("issuer") or "").strip().lower() in {"self", "model", "runtime"}:
            raise AuthorizationDenied("self-issued grants are rejected")
        return cls(grant_id=grant_id, effect=effect, scope=scope, source=source)


def load_external_grant(
    *,
    path: str | None = None,
    env_var: str = "VEYNIT_TOOL_GRANT_JSON",
) -> Optional[ExternalGrant]:
    """Load a grant from ``VEYNIT_TOOL_GRANT_JSON`` or a JSON file path.

    Returns None when no grant material is present (caller must fail closed for
    consequential tools).
    """
    raw_env = os.environ.get(env_var)
    if raw_env:
        try:
            payload = json.loads(raw_env)
        except json.JSONDecodeError as exc:
            raise AuthorizationDenied(f"invalid {env_var}: {exc}") from exc
        if not isinstance(payload, dict):
            raise AuthorizationDenied(f"{env_var} must be a JSON object")
        return ExternalGrant.from_mapping(payload, source=f"env:{env_var}")

    grant_path = path or os.environ.get("VEYNIT_TOOL_GRANT_PATH")
    if not grant_path:
        return None
    text = Path(grant_path).read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise AuthorizationDenied("grant file must contain a JSON object")
    return ExternalGrant.from_mapping(payload, source=f"file:{grant_path}")


def grant_covers(grant: ExternalGrant, *, tool_name: str, effect: str) -> bool:
    """Return True when grant.effect matches and scope includes the tool."""
    if grant.effect != effect:
        return False
    if grant.scope in {"*", "tools:*"}:
        return True
    if grant.scope == f"tool:{tool_name}":
        return True
    if grant.scope.startswith("tools:") and tool_name in grant.scope.split(":", 1)[1].split(","):
        return True
    return False


class GovernedToolExecutor:
    """Wrap a leaf executor; consequential tools require an external grant."""

    def __init__(
        self,
        leaf: ToolExecutor,
        *,
        consequential_tools: frozenset[str],
        effect_name: str = "tool.dispatch",
        grant: Optional[ExternalGrant] = None,
        grant_loader: Callable[[], Optional[ExternalGrant]] | None = None,
    ) -> None:
        self._leaf = leaf
        self._consequential = consequential_tools
        self._effect_name = effect_name
        self._grant = grant
        self._grant_loader = grant_loader or load_external_grant

    async def __call__(
        self,
        tool_name: str,
        tool: Mapping[str, Any],
        params: Mapping[str, Any],
        context: Any,
    ) -> Any:
        if tool_name in self._consequential:
            grant = self._grant if self._grant is not None else self._grant_loader()
            if grant is None:
                raise AuthorizationDenied(
                    f"consequential tool {tool_name!r} blocked: no external Veynit grant"
                )
            if not grant_covers(grant, tool_name=tool_name, effect=self._effect_name):
                raise AuthorizationDenied(
                    f"consequential tool {tool_name!r} blocked: grant "
                    f"{grant.grant_id!r} does not cover effect={self._effect_name!r}"
                )
            # Independent verification of the leaf effect remains Ceinit-owned.
            # This wrapper only gates dispatch authority.
        return await self._leaf(tool_name, tool, params, context)
