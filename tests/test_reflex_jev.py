"""Deterministic contract gate for the Jev shadow adapter (v3).

    python -m pytest tests/test_reflex_jev.py -q

MUST be fully green before any real API key enters the acceptance path.
Verified green: 26 passed (Python 3.12, pytest 8.3) against v3.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import replace
from typing import Any, Mapping

import pytest

from voxmaestro.reflex.jev_backend import (
    QUESTION_CHOICE,
    QUESTION_NOUL,
    QUESTION_SCORE,
    DecisionRequest,
    DecisionTrace,
    JevProtocolError,
    JevQuestion,
    JevReflexBackend,
    _NoRedirect,
    make_jsonl_observer,
)

PINNED_MODEL = "jev-2026-09-21"


class FakeTransport:
    def __init__(self, body=None, *, error=None, provider="fake"):
        self.body = body
        self.error = error
        self.provider = provider
        self.calls = []

    def request_systemone(self, payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        assert self.body is not None
        return self.body


class CaptureObserver:
    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(event)


def failing_observer(_event):
    raise OSError("disk_full")


@pytest.fixture
def trace():
    return DecisionTrace(session_id="session-001", turn_id="turn-007",
                         question_version="reflex-v3", local_decision_id="local-42")


@pytest.fixture
def choice_question():
    return JevQuestion(kind=QUESTION_CHOICE, key="intent",
                       prompt="Select the best matching intent.",
                       options=("schedule", "faq", "other"))


@pytest.fixture
def score_question():
    return JevQuestion(kind=QUESTION_SCORE, key="risk",
                       prompt="Rate the materiality of this proposed action.",
                       levels=(("low", "No meaningful external consequence."),
                               ("medium", "Bounded reversible external consequence."),
                               ("high", "Material or difficult-to-reverse consequence.")))


@pytest.fixture
def noul_question():
    return JevQuestion(kind=QUESTION_NOUL, key="needs_tool",
                       prompt="Does this request require an external tool?",
                       threshold=0.50)


def valid_body(*, answers, model=PINNED_MODEL, usage=None):
    if usage is None:
        usage = {"input_tokens": 101, "output_tokens": 9}
    return {"model": model, "answers": dict(answers), "usage": usage}


def backend_for(transport, observer, *, max_state_chars=8000):
    return JevReflexBackend(transport, model=PINNED_MODEL,
                            max_state_chars=max_state_chars, observer=observer)


def request_for(trace, *questions, state="Customer wants to move tomorrow's appointment."):
    return DecisionRequest(state=state, questions=tuple(questions), trace=trace)


def assert_closed(verdicts):
    assert verdicts
    assert all(v.ok is False for v in verdicts)
    assert all(v.value is None for v in verdicts)
    assert all(v.confidence is None for v in verdicts)


@pytest.mark.parametrize(
    ("question", "answer", "expected_value", "expected_confidence"),
    [
        (JevQuestion(kind=QUESTION_CHOICE, key="intent", prompt="Choose intent.",
                     options=("schedule", "faq")),
         {"type": "choice", "choice": "schedule", "confidence": 0.91}, "schedule", 0.91),
        (JevQuestion(kind=QUESTION_SCORE, key="risk", prompt="Score risk.",
                     levels=(("low", "low"), ("medium", "medium"), ("high", "high"))),
         {"type": "score", "score": 1.63, "confidence": 0.82}, 1.63, 0.82),
        (JevQuestion(kind=QUESTION_NOUL, key="needs_tool", prompt="Requires external tool?"),
         {"type": "noul", "noul": 0.734}, 0.734, None),
    ],
)
def test_valid_jev_primitives_preserve_semantics(trace, question, answer, expected_value, expected_confidence):
    observer = CaptureObserver()
    transport = FakeTransport(valid_body(answers={question.key: answer}))
    backend = backend_for(transport, observer)
    verdicts = backend.decide(request_for(trace, question))
    assert len(verdicts) == 1
    verdict = verdicts[0]
    assert verdict.ok is True
    assert verdict.value == expected_value
    assert verdict.confidence == expected_confidence
    assert len(observer.events) == 1
    assert observer.events[0].model == PINNED_MODEL


def test_missing_sibling_closes_entire_batch(trace, choice_question, noul_question):
    observer = CaptureObserver()
    transport = FakeTransport(valid_body(answers={
        "intent": {"type": "choice", "choice": "schedule", "confidence": 0.95},
    }))
    backend = backend_for(transport, observer)
    verdicts = backend.decide(request_for(trace, choice_question, noul_question))
    assert len(verdicts) == 2
    assert_closed(verdicts)
    assert observer.events[0].ok is False
    assert "answer_key_mismatch" in observer.events[0].error


def test_malformed_sibling_closes_entire_batch(trace, choice_question, noul_question):
    observer = CaptureObserver()
    transport = FakeTransport(valid_body(answers={
        "intent": {"type": "choice", "choice": "schedule", "confidence": 0.95},
        "needs_tool": {"type": "noul", "noul": 1.7},
    }))
    backend = backend_for(transport, observer)
    verdicts = backend.decide(request_for(trace, choice_question, noul_question))
    assert_closed(verdicts)
    assert observer.events[0].ok is False
    assert "numeric_value_out_of_range" in observer.events[0].error


@pytest.mark.parametrize("exc", [
    RuntimeError("jev_timeout"), RuntimeError("jev_http_429"),
    RuntimeError("jev_http_503"), RuntimeError("jev_unreachable"),
])
def test_transport_failure_closes_batch_and_never_raises(trace, choice_question, exc):
    observer = CaptureObserver()
    transport = FakeTransport(error=exc)
    backend = backend_for(transport, observer)
    verdicts = backend.decide(request_for(trace, choice_question))
    assert_closed(verdicts)
    assert observer.events[0].ok is False


def test_returned_model_must_equal_pinned_model(trace, choice_question):
    observer = CaptureObserver()
    transport = FakeTransport(valid_body(model="jev-something-else", answers={
        "intent": {"type": "choice", "choice": "schedule", "confidence": 0.90}}))
    backend = backend_for(transport, observer)
    verdicts = backend.decide(request_for(trace, choice_question))
    assert_closed(verdicts)
    event = observer.events[0]
    assert event.ok is False
    assert "model_drift" in event.error


@pytest.mark.parametrize("bad_usage", [
    {"input_tokens": -1, "output_tokens": 4},
    {"input_tokens": 10.5, "output_tokens": 4},
    {"input_tokens": True, "output_tokens": 4},
    {"input_tokens": 10, "output_tokens": "wat"},
    "not-a-mapping",
])
def test_malformed_usage_preserves_valid_answers(trace, choice_question, bad_usage):
    observer = CaptureObserver()
    transport = FakeTransport(valid_body(answers={
        "intent": {"type": "choice", "choice": "schedule", "confidence": 0.88}},
        usage=bad_usage))
    backend = backend_for(transport, observer)
    verdicts = backend.decide(request_for(trace, choice_question))
    assert verdicts[0].ok is True
    assert verdicts[0].value == "schedule"
    event = observer.events[0]
    assert event.ok is True
    assert event.usage is None
    assert event.model == PINNED_MODEL


def test_persistence_failure_forfeits_successful_model_result(trace, choice_question):
    transport = FakeTransport(valid_body(answers={
        "intent": {"type": "choice", "choice": "schedule", "confidence": 0.96}}))
    backend = backend_for(transport, failing_observer)
    verdicts = backend.decide(request_for(trace, choice_question))
    assert_closed(verdicts)


def test_duplicate_question_key_is_rejected_pre_network(trace, choice_question):
    observer = CaptureObserver()
    transport = FakeTransport(body={})
    duplicate = replace(choice_question, prompt="Same key, different semantics.")
    backend = backend_for(transport, observer)
    verdicts = backend.decide(request_for(trace, choice_question, duplicate))
    assert_closed(verdicts)
    assert transport.calls == []
    assert "duplicate_question_key" in observer.events[0].error


def test_oversized_state_is_rejected_pre_network(trace, choice_question):
    observer = CaptureObserver()
    transport = FakeTransport(body={})
    backend = backend_for(transport, observer, max_state_chars=8)
    verdicts = backend.decide(request_for(trace, choice_question, state="123456789"))
    assert_closed(verdicts)
    assert transport.calls == []
    assert "state_too_large" in observer.events[0].error


def test_digest_covers_encoded_questions_and_jsonl_excludes_state_plaintext(tmp_path, trace, choice_question):
    log_path = tmp_path / "jev-shadow.jsonl"
    secret_state = "PRIVATE-CUSTOMER-STATE-9f38a7"
    observer = make_jsonl_observer(str(log_path))
    body = valid_body(answers={"intent": {"type": "choice", "choice": "schedule", "confidence": 0.91}})
    backend = backend_for(FakeTransport(body), observer)
    backend.decide(request_for(trace, choice_question, state=secret_state))
    changed_question = replace(choice_question, prompt="Select intent using the revised v4 semantic rule.")
    backend_2 = backend_for(FakeTransport(body), observer)
    backend_2.decide(request_for(replace(trace, turn_id="turn-008"), changed_question, state=secret_state))
    raw = log_path.read_text(encoding="utf-8")
    assert secret_state not in raw
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert len(records) == 2
    assert records[0]["request_hash"] != records[1]["request_hash"]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_handler_rejects_all_redirects(status):
    handler = _NoRedirect()
    request = urllib.request.Request("https://api.typesafe.ai/v1/systemone")
    with pytest.raises(JevProtocolError, match="unexpected_redirect"):
        handler.redirect_request(request, fp=None, code=status, msg="redirect",
                                 headers={}, newurl="https://other.example/systemone")


@pytest.mark.parametrize("provider", ["typesafe-native", "vercel-typesafe-compat"])
def test_provider_surfaces_share_identical_systemone_codec(trace, score_question, provider):
    observer = CaptureObserver()
    body = valid_body(answers={"risk": {"type": "score", "score": 1.63, "confidence": 0.84}})
    transport = FakeTransport(body, provider=provider)
    backend = backend_for(transport, observer)
    verdicts = backend.decide(request_for(trace, score_question))
    assert len(transport.calls) == 1
    payload = transport.calls[0]
    assert payload["model"] == PINNED_MODEL
    assert payload["questions"]["risk"]["type"] == "score"
    verdict = verdicts[0]
    assert verdict.ok is True
    assert verdict.value == 1.63
    assert verdict.confidence == 0.84
