"""VOX-OPHUB-CONVERGENCE-004 — contract tests before implementation.

Covers interruption of consequential tools, disconnected OpHub, stale data,
and grounded spoken answers. No fabricated running/cost claims.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from voxmaestro.ophub_voice import (
    OphubVoiceClient,
    classify_owner_query,
    render_spoken_answer,
    workspace_is_stale,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ophub_owner_workspace_fixture.json"


@pytest.fixture
def workspace() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_classify_five_spoken_intents() -> None:
    assert classify_owner_query("Which agents are running?") == "agents_running"
    assert classify_owner_query("What is Codex working on?") == "codex_task"
    assert classify_owner_query("Show my active tasks.") == "active_tasks"
    assert classify_owner_query("Which agents are blocked?") == "agents_blocked"
    assert classify_owner_query("What has been independently verified?") == "independently_verified"
    assert classify_owner_query("please merge the pull request now") == "unsupported_or_governance"


def test_running_agents_speak_unknown_without_independent_session(workspace: dict) -> None:
    text = render_spoken_answer("agents_running", workspace)
    assert "unknown" in text.lower()
    assert "running" not in text.lower() or "no independently observed running" in text.lower()
    # Must not invent live sessions
    assert "codex is running" not in text.lower()


def test_codex_task_unknown_without_active_task(workspace: dict) -> None:
    text = render_spoken_answer("codex_task", workspace)
    assert "unknown" in text.lower()
    assert "working on" not in text.lower() or "unknown" in text.lower()


def test_active_tasks_grounded(workspace: dict) -> None:
    text = render_spoken_answer("active_tasks", workspace)
    assert "unknown" in text.lower() or "no independently observed active tasks" in text.lower()


def test_blocked_agents_from_fleet_and_install(workspace: dict) -> None:
    text = render_spoken_answer("agents_blocked", workspace)
    # antigravity not installed in fixture — may be mentioned as not installed
    assert "antigravity" in text.lower() or "not installed" in text.lower() or "blocker" in text.lower() or "unknown" in text.lower()


def test_independently_verified_does_not_invent_github_observation(workspace: dict) -> None:
    text = render_spoken_answer("independently_verified", workspace)
    assert "unknown" in text.lower() or "not independently" in text.lower() or "recorded" in text.lower()
    assert "independently verified all" not in text.lower()


def test_governance_blocks_dispatch_intents(workspace: dict) -> None:
    text = render_spoken_answer("unsupported_or_governance", workspace)
    assert "cannot" in text.lower() or "not authorized" in text.lower() or "read-only" in text.lower()
    assert "dispatched" not in text.lower()


def test_disconnected_client_speaks_unavailable() -> None:
    client = OphubVoiceClient(base_url="http://127.0.0.1:1", timeout_s=0.2)

    def boom(_url: str) -> dict:
        raise ConnectionError("refused")

    client._fetch_json = boom  # type: ignore[method-assign]
    answer = client.answer("Which agents are running?")
    assert answer["status"] == "DISCONNECTED"
    assert "unavailable" in answer["spoken"].lower() or "cannot" in answer["spoken"].lower()
    assert answer["spoken"].lower().count("running") == 0 or "cannot" in answer["spoken"].lower()


def test_stale_workspace_is_flagged(workspace: dict) -> None:
    stale = copy.deepcopy(workspace)
    stale["fetched_at"] = "2020-01-01T00:00:00Z"
    assert workspace_is_stale(stale, max_age_s=60) is True
    text = render_spoken_answer("agents_running", stale, stale=True)
    assert "stale" in text.lower() or "outdated" in text.lower()


def test_interruption_does_not_queue_stale_action(workspace: dict) -> None:
    """Cancelled turn must not keep a prior spoken action payload."""
    client = OphubVoiceClient(base_url="http://127.0.0.1:8899", timeout_s=1.0)
    client._fetch_json = lambda _u: workspace  # type: ignore[method-assign]
    first = client.answer("Which agents are running?")
    client.cancel_pending()
    assert client.pending_spoken() is None
    assert first["spoken"]
    # After cancel, answer again is a fresh turn — no stale merge/dispatch residue
    second = client.answer("please merge everything")
    assert "not authorized" in second["spoken"].lower() or "cannot" in second["spoken"].lower()
    assert "ACCEPT_AND_MERGE" not in second["spoken"]
