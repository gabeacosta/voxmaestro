"""Fail-closed consequential tool authorization."""

from __future__ import annotations

import pytest

from voxmaestro.governance import (
    AuthorizationDenied,
    ExternalGrant,
    GovernedToolExecutor,
    grant_covers,
)


@pytest.mark.asyncio
async def test_consequential_tool_fails_closed_without_grant() -> None:
    async def leaf(tool_name, tool, params, context):
        return {"ok": True}

    governed = GovernedToolExecutor(
        leaf,
        consequential_tools=frozenset({"check_availability"}),
        grant=None,
        grant_loader=lambda: None,
    )
    with pytest.raises(AuthorizationDenied, match="no external Veynit grant"):
        await governed("check_availability", {}, {}, None)


@pytest.mark.asyncio
async def test_consequential_tool_dispatches_with_matching_grant() -> None:
    async def leaf(tool_name, tool, params, context):
        return {"slots": ["Thursday 3pm"]}

    grant = ExternalGrant(
        grant_id="gr_test",
        effect="tool.dispatch",
        scope="tool:check_availability",
        source="test",
    )
    governed = GovernedToolExecutor(
        leaf,
        consequential_tools=frozenset({"check_availability"}),
        grant=grant,
    )
    result = await governed("check_availability", {}, {}, None)
    assert result["slots"] == ["Thursday 3pm"]


def test_self_issued_grant_rejected() -> None:
    with pytest.raises(AuthorizationDenied, match="self-issued"):
        ExternalGrant.from_mapping(
            {
                "grant_id": "g1",
                "effect": "tool.dispatch",
                "scope": "*",
                "issuer": "self",
            },
            source="test",
        )


def test_grant_covers_tool_scope() -> None:
    grant = ExternalGrant("g", "tool.dispatch", "tool:book", "t")
    assert grant_covers(grant, tool_name="book", effect="tool.dispatch")
    assert not grant_covers(grant, tool_name="other", effect="tool.dispatch")
