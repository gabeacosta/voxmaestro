"""Calibrated L0 mode: confidence comes from label logprobs, never from generated text.

Fake inference here proves wiring and fail-closed behavior only. It is not model
admission and says nothing about real calibration quality.
"""
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from voxmaestro.workers.calibration import CalibrationArtifact
from voxmaestro.workers.intent_worker import UNKNOWN, IntentWorker

REQUEST = {"text": "Do you have anything Thursday?",
           "intents": [{"id": "availability_question", "description": "asks about slots"},
                       "human_request", "unknown"]}


def top(**probs):
    return [{"token": token, "logprob": math.log(p)} for token, p in probs.items()]


def body(top_logprobs, content="A"):
    return {"choices": [{"message": {"content": content},
                         "logprobs": {"content": [{"token": content, "logprob": -0.1,
                                                   "top_logprobs": top_logprobs}]}}]}


@pytest.fixture
def upstream():
    state = SimpleNamespace(calls=[], body=body(top(A=0.9, B=0.05, C=0.03)))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            state.calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            raw = json.dumps(state.body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.endpoint = f"http://127.0.0.1:{server.server_port}/v1"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(2)


def cal(temperature=1.0, model="fixed-model", min_label_mass=0.5):
    return CalibrationArtifact(model=model, temperature=temperature,
                               min_label_mass=min_label_mass, fitted_n=10, eval_n=10)


def worker(upstream, **kw):
    kw.setdefault("calibration", cal())
    return IntentWorker(endpoint=upstream.endpoint, model="fixed-model", **kw)


def test_requests_one_scored_label_token_and_maps_letters_to_intents(upstream):
    result = worker(upstream).classify(REQUEST)
    assert result["intent"] == "availability_question"
    assert result["confidence"] == pytest.approx(0.9 / 0.98)
    sent = upstream.calls[0]
    assert sent["max_tokens"] == 1 and sent["logprobs"] is True and sent["top_logprobs"] == 10
    assert sent["temperature"] == 0
    options = json.loads(sent["messages"][1]["content"])["options"]
    assert options == {"A": "availability_question: asks about slots",
                       "B": "human_request", "C": "unknown"}


def test_fitted_temperature_changes_reported_confidence(upstream):
    hot = worker(upstream, calibration=cal(temperature=3.0)).classify(REQUEST)
    assert hot["intent"] == "availability_question"
    assert hot["confidence"] < 0.9 / 0.98


def test_low_calibrated_confidence_abstains(upstream):
    upstream.body = body(top(A=0.45, B=0.40, C=0.10))
    assert worker(upstream, min_confidence=0.6).classify(REQUEST) == UNKNOWN


def test_unknown_as_top_option_abstains(upstream):
    upstream.body = body(top(C=0.95, A=0.03))
    assert worker(upstream).classify(REQUEST) == UNKNOWN


@pytest.mark.parametrize("bad_body", [
    {"choices": [{"message": {"content": "A"}}]},                       # no logprobs at all
    {"choices": [{"message": {"content": "A"}, "logprobs": {"content": []}}]},
    body([]),                                                             # empty top-k
    body(top(Sure=0.8, A=0.1)),                                           # mostly off-label
    body([{"token": "A", "logprob": 0.2}]),                               # impossible logprob
    {"choices": [{"message": {"content": "A", "tool_calls": [{}]},
                  "logprobs": {"content": [{"top_logprobs": top(A=0.9)}]}}]},
])
def test_fails_closed_on_unscoreable_responses(upstream, bad_body):
    upstream.body = bad_body
    assert worker(upstream).classify(REQUEST) == UNKNOWN


def test_too_many_options_for_letter_labels_fails_closed(upstream):
    many = {"text": "hi", "intents": [f"intent_{i}" for i in range(27)]}
    assert worker(upstream).classify(many) == UNKNOWN
    assert upstream.calls == []


def test_artifact_pinned_to_other_model_is_refused_at_startup(upstream):
    with pytest.raises(ValueError):
        worker(upstream, calibration=cal(model="other-model"))


@pytest.mark.parametrize("k", [0, 21, True, 2.0])
def test_top_logprobs_bounds(upstream, k):
    with pytest.raises(ValueError):
        worker(upstream, top_logprobs=k)


def test_score_raw_works_without_artifact_and_default_mode_is_unchanged(upstream):
    plain = IntentWorker(endpoint=upstream.endpoint, model="fixed-model")
    raw = plain.score_raw(REQUEST)
    assert set(raw) == {"availability_question", "human_request", "unknown"}
    assert plain.calibration_digest is None
    assert worker(upstream).calibration_digest == cal().digest
