from __future__ import annotations

import pytest

from voxmaestro.tts.crosstalk import detect_crosstalk


def _by_id(findings, session_id: str):
    return next(f for f in findings if f.session_id == session_id)


def test_no_crosstalk_when_each_session_matches_its_own_text() -> None:
    assignments = {
        "s1": "Thanks for calling Rivera Plumbing, this is Alma speaking.",
        "s2": "Your appointment is Thursday at three thirty.",
    }
    transcripts = {
        "s1": "Thanks for calling Rivera Plumbing, this is Alma speaking.",
        "s2": "Your appointment is Thursday at three thirty.",
    }
    findings = detect_crosstalk(assignments, transcripts)
    assert all(not f.crosstalk and not f.inconclusive for f in findings)


def test_crosstalk_detected_when_transcript_matches_a_different_session() -> None:
    assignments = {
        "s1": "Thanks for calling Rivera Plumbing, this is Alma speaking.",
        "s2": "Your appointment is Thursday at three thirty.",
    }
    # s1's captured audio actually contains s2's line -- a real content leak.
    transcripts = {
        "s1": "Your appointment is Thursday at three thirty.",
        "s2": "Your appointment is Thursday at three thirty.",
    }
    findings = detect_crosstalk(assignments, transcripts)
    s1 = _by_id(findings, "s1")
    s2 = _by_id(findings, "s2")
    assert s1.crosstalk is True
    assert s1.best_other_session_id == "s2"
    assert s2.crosstalk is False  # s2's own audio is correct
    assert s2.inconclusive is False


def test_garbled_transcript_is_inconclusive_not_a_verdict() -> None:
    assignments = {
        "s1": "Thanks for calling Rivera Plumbing, this is Alma speaking.",
        "s2": "Your appointment is Thursday at three thirty.",
    }
    transcripts = {"s1": "asdf mumble garbage", "s2": "Your appointment is Thursday at three thirty."}
    findings = detect_crosstalk(assignments, transcripts)
    s1 = _by_id(findings, "s1")
    assert s1.inconclusive is True
    assert s1.crosstalk is False  # never a positive verdict when inconclusive


def test_missing_transcript_is_inconclusive() -> None:
    assignments = {"s1": "hello there", "s2": "goodbye now"}
    findings = detect_crosstalk(assignments, {"s2": "goodbye now"})  # s1 never transcribed
    s1 = _by_id(findings, "s1")
    assert s1.inconclusive is True
    assert s1.crosstalk is False


def test_weak_match_everywhere_is_not_flagged_as_crosstalk() -> None:
    """A session whose own match is merely mediocre (ASR noise) must not be
    flagged just because a different session's text scores marginally
    higher -- the margin must be real, not noise-sized."""
    assignments = {"s1": "Do you have anything available this week?", "s2": "Su cita es el jueves a las tres y media."}
    transcripts = {"s1": "Do you have anything available this weekend?", "s2": "Su cita es el jueves a las tres y media."}
    findings = detect_crosstalk(assignments, transcripts)
    s1 = _by_id(findings, "s1")
    assert s1.crosstalk is False


def test_three_way_crosstalk_identifies_the_actual_source() -> None:
    assignments = {
        "s1": "Let me check that.",
        "s2": "Déjame revisar.",
        "s3": "Juan Gabriel Acosta, A-C-O-S-T-A.",
    }
    transcripts = {
        "s1": "Let me check that.",
        "s2": "Juan Gabriel Acosta, A-C-O-S-T-A.",  # s2's audio actually contains s3's line
        "s3": "Juan Gabriel Acosta, A-C-O-S-T-A.",
    }
    findings = detect_crosstalk(assignments, transcripts)
    s2 = _by_id(findings, "s2")
    assert s2.crosstalk is True
    assert s2.best_other_session_id == "s3"


def test_requires_at_least_two_sessions() -> None:
    with pytest.raises(ValueError, match="at least two"):
        detect_crosstalk({"s1": "only one"}, {"s1": "only one"})
