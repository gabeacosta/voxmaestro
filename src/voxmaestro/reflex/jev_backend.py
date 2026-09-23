"""Remote Jev shadow adapter for the reflex layer — v3 (review-hardened)."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

_logger = logging.getLogger(__name__)

QUESTION_CHOICE = "choice"
QUESTION_SCORE = "score"
QUESTION_NOUL = "noul"


class JevProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class JevQuestion:
    kind: str
    key: str
    prompt: str
    options: tuple = ()
    levels: tuple = ()
    threshold: Optional[float] = None


@dataclass(frozen=True)
class DecisionTrace:
    session_id: str
    turn_id: str
    question_version: str
    local_decision_id: Optional[str] = None

    def as_record(self) -> dict:
        record = {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "question_version": self.question_version,
        }
        if self.local_decision_id is not None:
            record["local_decision_id"] = self.local_decision_id
        return record


@dataclass(frozen=True)
class DecisionRequest:
    state: str
    questions: tuple
    trace: DecisionTrace


@dataclass(frozen=True)
class JevVerdict:
    key: str
    kind: str
    value: Any
    confidence: Optional[float]
    latency_ms: float
    ok: bool


@dataclass(frozen=True)
class ResponseUsage:
    input_tokens: Optional[int]
    output_tokens: Optional[int]


@dataclass(frozen=True)
class ShadowObservation:
    request_hash: Optional[str]
    trace: DecisionTrace
    model: str
    usage: Optional[ResponseUsage]
    verdicts: tuple
    ok: bool
    total_latency_ms: float
    error: Optional[str]
    requested_model: Optional[str] = None


ShadowObserver = Callable[[ShadowObservation], None]


def make_jsonl_observer(path: str) -> ShadowObserver:
    def observe(observation: ShadowObservation) -> None:
        record = {
            "request_hash": observation.request_hash,
            "trace": observation.trace.as_record(),
            "model": observation.model,
            "requested_model": observation.requested_model,
            "usage": (
                None
                if observation.usage is None
                else {
                    "input_tokens": observation.usage.input_tokens,
                    "output_tokens": observation.usage.output_tokens,
                }
            ),
            "ok": observation.ok,
            "total_latency_ms": round(observation.total_latency_ms, 2),
            "error": observation.error,
            "verdicts": [
                {
                    "key": v.key,
                    "kind": v.kind,
                    "value": v.value,
                    "confidence": v.confidence,
                    "latency_ms": round(v.latency_ms, 2),
                }
                for v in observation.verdicts
            ],
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    return observe


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise JevProtocolError(f"unexpected_redirect:{code}")


@dataclass(frozen=True)
class JevHttpConfig:
    api_key: str
    endpoint: str = "https://api.typesafe.ai/v1/systemone"
    timeout_s: float = 0.8
    provider: str = "typesafe"

    def __post_init__(self) -> None:
        if urllib.parse.urlparse(self.endpoint).scheme != "https":
            raise ValueError("jev_endpoint_must_be_https")
        if self.provider not in ("typesafe", "vercel-typesafe"):
            raise ValueError(f"unknown_jev_provider:{self.provider}")


TYPESAFE_NATIVE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
VERCEL_TYPESAFE_ENDPOINT = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"


class JevHttpTransport:
    def __init__(self, config: JevHttpConfig) -> None:
        self._config = config
        self._opener = urllib.request.build_opener(_NoRedirect())

    def request_systemone(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        http_request = urllib.request.Request(
            self._config.endpoint,
            data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(http_request, timeout=self._config.timeout_s) as response:
                raw_body = response.read()
        except JevProtocolError:
            raise
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"jev_http_{exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"jev_unreachable:{exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise RuntimeError("jev_timeout") from exc
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JevProtocolError("invalid_json") from exc
        if not isinstance(body, Mapping):
            raise JevProtocolError("body_not_mapping")
        return body


def _finite_number(value: Any, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise JevProtocolError("boolean_is_not_numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise JevProtocolError("invalid_numeric_value") from exc
    if not math.isfinite(number):
        raise JevProtocolError("non_finite_numeric_value")
    if not minimum <= number <= maximum:
        raise JevProtocolError("numeric_value_out_of_range")
    return number


def _nonnegative_int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise JevProtocolError("invalid_token_count")
    if value < 0:
        raise JevProtocolError("negative_token_count")
    return value


class JevReflexBackend:
    def __init__(
        self,
        transport,
        *,
        model: str,
        max_state_chars: int = 8000,
        observer: ShadowObserver,
        allow_model_alias: bool = False,
    ) -> None:
        if model.endswith("-latest") and not allow_model_alias:
            raise ValueError("resolved_model_pin_required")
        if observer is None:
            raise ValueError("shadow_observer_required")
        self._transport = transport
        self._model = model
        self._max_state_chars = max_state_chars
        self._observer = observer
        self._allow_model_alias = allow_model_alias

    def decide(self, request: DecisionRequest) -> tuple:
        start = time.monotonic()
        error: Optional[str] = None
        verdicts: tuple = ()
        digest: Optional[str] = None
        usage: Optional[ResponseUsage] = None
        returned_model: Optional[str] = None
        try:
            if len(request.state) > self._max_state_chars:
                raise JevProtocolError("state_too_large")
            questions = self._encode_questions(request.questions)
            digest = hashlib.sha256(
                json.dumps(
                    {"model": self._model, "state": request.state, "questions": questions},
                    sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            payload = {"model": self._model, "state": request.state, "questions": questions}
            t0 = time.monotonic()
            body = self._transport.request_systemone(payload)
            batch_latency_ms = (time.monotonic() - t0) * 1000.0
            returned_model, usage = self._decode_response_metadata(body)
            verdicts = self._decode_batch(request.questions, body, batch_latency_ms)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            verdicts = tuple(
                self._closed_verdict(q, (time.monotonic() - start) * 1000.0)
                for q in request.questions
            )
        elapsed_ms = (time.monotonic() - start) * 1000.0
        observation = ShadowObservation(
            request_hash=digest,
            trace=request.trace,
            model=returned_model or self._model,
            usage=usage,
            verdicts=verdicts,
            ok=error is None,
            total_latency_ms=elapsed_ms,
            error=error,
            requested_model=self._model,
        )
        if not self._persist(observation):
            return tuple(self._closed_verdict(q, elapsed_ms) for q in request.questions)
        return verdicts

    def _encode_questions(self, questions: Sequence[JevQuestion]) -> Mapping[str, Any]:
        encoded: dict = {}
        for question in questions:
            if question.key in encoded:
                raise JevProtocolError(f"duplicate_question_key:{question.key}")
            body: dict = {"type": question.kind, "instructions": question.prompt}
            if question.kind == QUESTION_CHOICE:
                if not question.options:
                    raise JevProtocolError(f"choice_without_options:{question.key}")
                if len(set(question.options)) != len(question.options):
                    raise JevProtocolError(f"duplicate_choice_option:{question.key}")
                body["criteria"] = {str(option): None for option in question.options}
            elif question.kind == QUESTION_SCORE:
                if not 2 <= len(question.levels) <= 10:
                    raise JevProtocolError(f"invalid_score_levels:{question.key}")
                body["criteria"] = [
                    {"label": label, "description": description}
                    for label, description in question.levels
                ]
            elif question.kind == QUESTION_NOUL:
                if question.threshold is not None and not 0.0 <= question.threshold <= 1.0:
                    raise JevProtocolError(f"invalid_noul_threshold:{question.key}")
            else:
                raise JevProtocolError(f"unknown_question_kind:{question.kind}")
            encoded[question.key] = body
        if not encoded:
            raise JevProtocolError("empty_questions")
        return encoded

    def _decode_response_metadata(
        self,
        body: Mapping[str, Any],
    ) -> tuple[str, Optional[ResponseUsage]]:
        returned_model = body.get("model")
        if not isinstance(returned_model, str) or not returned_model:
            raise JevProtocolError("missing_or_invalid_model")
        if not self._allow_model_alias and returned_model != self._model:
            raise JevProtocolError(
                f"model_drift:requested={self._model}:returned={returned_model}"
            )
        raw_usage = body.get("usage")
        if not isinstance(raw_usage, Mapping):
            return returned_model, None
        try:
            usage = ResponseUsage(
                input_tokens=_nonnegative_int_or_none(raw_usage.get("input_tokens")),
                output_tokens=_nonnegative_int_or_none(raw_usage.get("output_tokens")),
            )
        except JevProtocolError:
            usage = None
        return returned_model, usage

    def _decode_batch(self, questions, body: Mapping[str, Any], batch_latency_ms: float) -> tuple:
        answers = body.get("answers")
        if not isinstance(answers, Mapping):
            raise JevProtocolError("answers_not_mapping")
        if set(answers.keys()) != {q.key for q in questions}:
            raise JevProtocolError("answer_key_mismatch")
        verdicts = tuple(
            self._decode_verdict(q, answers[q.key], batch_latency_ms) for q in questions
        )
        if any(not v.ok for v in verdicts):
            raise JevProtocolError("partial_invalid_batch")
        return verdicts

    def _decode_verdict(self, question: JevQuestion, answer, batch_latency_ms: float) -> JevVerdict:
        if not isinstance(answer, Mapping):
            raise JevProtocolError(f"answer_not_mapping:{question.key}")
        if answer.get("type") != question.kind:
            raise JevProtocolError(f"answer_type_mismatch:{question.key}")
        if question.kind == QUESTION_CHOICE:
            value = answer.get("choice")
            if value not in question.options:
                raise JevProtocolError(f"choice_out_of_contract:{question.key}")
            confidence = _finite_number(answer.get("confidence"), minimum=0.0, maximum=1.0)
        elif question.kind == QUESTION_SCORE:
            value = _finite_number(answer.get("score"), minimum=0.0, maximum=float(len(question.levels) - 1))
            confidence = _finite_number(answer.get("confidence"), minimum=0.0, maximum=1.0)
        elif question.kind == QUESTION_NOUL:
            value = _finite_number(answer.get("noul"), minimum=0.0, maximum=1.0)
            confidence = None
        else:
            raise JevProtocolError(f"unsupported_question_kind:{question.kind}")
        return JevVerdict(key=question.key, kind=question.kind, value=value,
                          confidence=confidence, latency_ms=batch_latency_ms, ok=True)

    def _closed_verdict(self, question: JevQuestion, latency_ms: float) -> JevVerdict:
        return JevVerdict(key=question.key, kind=question.kind, value=None,
                          confidence=None, latency_ms=latency_ms, ok=False)

    def _persist(self, observation: ShadowObservation) -> bool:
        try:
            self._observer(observation)
            return True
        except Exception as exc:
            _logger.warning("jev observation forfeited, persistence failed (request_hash=%s): %s",
                            observation.request_hash, exc)
            return False


ShadowDispatch = Callable[[DecisionRequest], None]


def hand_off_shadow(request: DecisionRequest, dispatch: ShadowDispatch) -> None:
    try:
        dispatch(request)
    except Exception as exc:
        _logger.warning("jev shadow dispatch failed: %s", exc)


def jev_backend_from_config(config: Mapping[str, Any]) -> JevReflexBackend:
    provider = str(config.get("provider", "typesafe"))
    endpoints = {
        "typesafe": TYPESAFE_NATIVE_ENDPOINT,
        "vercel-typesafe": VERCEL_TYPESAFE_ENDPOINT,
    }
    if provider not in endpoints:
        raise ValueError(f"unknown_jev_provider:{provider}")
    http_config = JevHttpConfig(
        api_key=str(config["api_key"]),
        endpoint=str(config.get("endpoint", endpoints[provider])),
        timeout_s=float(config.get("timeout_s", 0.8)),
        provider=provider,
    )
    if not config.get("audit_log"):
        raise ValueError("audit_log_required")
    return JevReflexBackend(
        JevHttpTransport(http_config),
        model=str(config["model"]),
        max_state_chars=int(config.get("max_state_chars", 8000)),
        observer=make_jsonl_observer(str(config["audit_log"])),
        allow_model_alias=bool(config.get("allow_model_alias", False)),
    )
