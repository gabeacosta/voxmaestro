"""Read-only Operator Hub voice projection for VOX-OPHUB-CONVERGENCE-004.

Consumes existing ``GET /api/v1/owner-workspace`` only. Never dispatches,
merges, mints grants, or invents running sessions / costs. ICM remains
canonical context elsewhere; this module speaks Ceinit/fleet evidence as
projected by Operator Hub without creating a second control plane.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

SCHEMA_EXPECTED = "operator_hub.owner_workspace.v1"

Intent = str

_INTENT_PATTERNS: list[tuple[Intent, re.Pattern[str]]] = [
    (
        "agents_running",
        re.compile(r"\b(which agents are running|who is running|agents running)\b", re.I),
    ),
    (
        "codex_task",
        re.compile(r"\b(what is codex working on|codex working|codex(?:'s)? (?:task|work))\b", re.I),
    ),
    (
        "active_tasks",
        re.compile(r"\b(active tasks|my tasks|show (?:me )?my active tasks|what(?:'s| is) (?:everyone|each) working)\b", re.I),
    ),
    (
        "agents_blocked",
        re.compile(r"\b(which agents are blocked|who is blocked|agents blocked|blockers?)\b", re.I),
    ),
    (
        "independently_verified",
        re.compile(
            r"\b(independently verified|what has been (?:independently )?verified|verified evidence)\b",
            re.I,
        ),
    ),
    (
        "unsupported_or_governance",
        re.compile(
            r"\b(merge|deploy|dispatch|approve and merge|accept and merge|run live|paid provider)\b",
            re.I,
        ),
    ),
]


def classify_owner_query(text: str) -> Intent:
    """Map spoken text to a bounded owner-workspace intent."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "unsupported_or_governance"
    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(cleaned):
            return intent
    return "unsupported_or_governance"


def workspace_is_stale(
    workspace: dict[str, Any],
    *,
    max_age_s: float = 300.0,
    now: datetime | None = None,
) -> bool:
    """True when fetched_at is missing or older than max_age_s."""
    raw = workspace.get("fetched_at")
    if not raw or not isinstance(raw, str):
        return True
    try:
        fetched = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - fetched).total_seconds() > max_age_s


def _agents(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    block = workspace.get("agents") or {}
    rows = block.get("agents") if isinstance(block, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict)]


def _fleet_blockers(workspace: dict[str, Any]) -> list[str]:
    fb = workspace.get("fleet_burn") or {}
    out: list[str] = []
    for item in fb.get("blockers") or []:
        out.append(str(item))
    for finding in fb.get("findings") or []:
        if isinstance(finding, dict) and finding.get("severity") in {"blocker", "BLOCKER"}:
            out.append(str(finding.get("message") or finding))
    for m in (workspace.get("commit_evidence_display") or {}).get("milestones") or []:
        if isinstance(m, dict):
            for b in m.get("blockers") or []:
                out.append(f"{m.get('id')}: {b}")
    return out


def render_spoken_answer(
    intent: Intent,
    workspace: dict[str, Any],
    *,
    stale: bool = False,
) -> str:
    """Produce a short spoken answer grounded only on workspace fields."""
    prefix = "Warning: this Operator Hub snapshot may be stale. " if stale else ""

    if intent == "unsupported_or_governance":
        return (
            prefix
            + "I cannot merge, deploy, or dispatch from voice. "
            "This interface is read-only; those actions need a separate owner grant."
        )

    if workspace.get("schema") != SCHEMA_EXPECTED and workspace.get("schema"):
        # Still answer, but disclose schema mismatch as uncertainty.
        prefix += "Workspace schema is unexpected, so treat details as uncertain. "

    agents = _agents(workspace)

    if intent == "agents_running":
        observed_running = [
            a
            for a in agents
            if str(a.get("running_state") or "").upper()
            in {"RUNNING", "ACTIVE", "BUSY"}
            and a.get("independently_observed_session") is True
        ]
        if observed_running:
            names = ", ".join(str(a.get("runtime")) for a in observed_running)
            return prefix + f"Independently observed running agents: {names}."
        installed = [
            str(a.get("runtime"))
            for a in agents
            if str(a.get("availability") or "").upper() == "INSTALLED"
        ]
        bits = [
            "No independently observed running sessions.",
            "Running state is unknown without independent session evidence.",
        ]
        if installed:
            bits.append("Installed agents in inventory: " + ", ".join(installed) + ".")
        else:
            bits.append("Installed status is also unknown or empty.")
        return prefix + " ".join(bits)

    if intent == "codex_task":
        codex = next((a for a in agents if str(a.get("runtime")) == "codex"), None)
        if codex is None:
            return prefix + "Codex is unknown in the owner workspace projection."
        task = codex.get("active_task")
        if task and codex.get("independently_observed_session") is True:
            return prefix + f"Codex is working on {task}."
        return (
            prefix
            + "Codex active task is unknown. "
            "No independently observed session evidence is available."
        )

    if intent == "active_tasks":
        observed = [
            f"{a.get('runtime')}: {a.get('active_task')}"
            for a in agents
            if a.get("active_task") and a.get("independently_observed_session") is True
        ]
        if observed:
            return prefix + "Independently observed active tasks: " + "; ".join(observed) + "."
        return (
            prefix
            + "No independently observed active tasks. "
            "Active work remains unknown without session receipts."
        )

    if intent == "agents_blocked":
        not_installed = [
            str(a.get("runtime"))
            for a in agents
            if str(a.get("availability") or "").upper() == "NOT_INSTALLED"
        ]
        blockers = _fleet_blockers(workspace)
        parts: list[str] = []
        if not_installed:
            parts.append("Not installed: " + ", ".join(not_installed) + ".")
        if blockers:
            parts.append("Fleet blockers: " + "; ".join(blockers[:5]) + ".")
        if not parts:
            parts.append(
                "No explicit agent blockers are recorded; "
                "blocked status is otherwise unknown."
            )
        return prefix + " ".join(parts)

    if intent == "independently_verified":
        display = workspace.get("commit_evidence_display") or {}
        independent = bool(display.get("independent_github_observation"))
        milestones = [
            m for m in (display.get("milestones") or []) if isinstance(m, dict)
        ]
        verified = [
            str(m.get("id") or m.get("pr") or "milestone")
            for m in milestones
            if m.get("evidence_ok") is True and m.get("verified_kind")
        ]
        if independent and verified:
            return (
                prefix
                + "Independently observed verification covers: "
                + ", ".join(verified)
                + "."
            )
        if verified and not independent:
            return (
                prefix
                + "Recorded verification markers exist for "
                + ", ".join(verified)
                + ", but independent GitHub observation is unknown."
            )
        return (
            prefix
            + "Independently verified work is unknown on this snapshot. "
            "Commit evidence status is "
            + str(display.get("status") or "UNKNOWN")
            + "."
        )

    return prefix + "I can only answer read-only owner workspace questions."


class OphubVoiceClient:
    """Loopback read-only client for owner-workspace spoken answers."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8899",
        timeout_s: float = 5.0,
        max_age_s: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_age_s = max_age_s
        self._pending_spoken: str | None = None

    def _fetch_json(self, url: str) -> dict[str, Any]:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("owner-workspace response is not an object")
        return payload

    def fetch_workspace(self) -> dict[str, Any]:
        url = f"{self.base_url}/api/v1/owner-workspace"
        data = self._fetch_json(url)
        data.setdefault("fetched_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        data.setdefault("source_url", url)
        return data

    def cancel_pending(self) -> None:
        """Invalidate any queued spoken payload after barge-in / cancel."""
        self._pending_spoken = None

    def pending_spoken(self) -> str | None:
        return self._pending_spoken

    def answer(self, spoken_query: str) -> dict[str, Any]:
        intent = classify_owner_query(spoken_query)
        try:
            workspace = self.fetch_workspace()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, RuntimeError) as exc:
            spoken = (
                "Operator Hub is unavailable, so I cannot verify agent state. "
                f"Connection error: {exc.__class__.__name__}."
            )
            self._pending_spoken = spoken
            return {
                "status": "DISCONNECTED",
                "intent": intent,
                "spoken": spoken,
                "workspace": None,
            }

        stale = workspace_is_stale(workspace, max_age_s=self.max_age_s)
        spoken = render_spoken_answer(intent, workspace, stale=stale)
        self._pending_spoken = spoken
        return {
            "status": "STALE" if stale else "OK",
            "intent": intent,
            "spoken": spoken,
            "workspace": {
                "schema": workspace.get("schema"),
                "ok": workspace.get("ok"),
                "fetched_at": workspace.get("fetched_at"),
                "source_url": workspace.get("source_url"),
            },
        }


def load_fixture(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("fixture must be an object")
    return data


FetchFn = Callable[[str], dict[str, Any]]
