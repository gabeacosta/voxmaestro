"""Post-hoc confidence calibration for the L0 intent slot.

The intent worker's default ``confidence`` is a number the model writes into its
JSON answer. Nothing makes that number a probability, so a ``min_confidence``
gate over it is a threshold over generated text. This module replaces it with a
probability read from the model's own next-token distribution over the legal
option labels, rescaled by a temperature fitted on labeled turns.

Pure functions plus one frozen artifact. Stdlib only; no model access here.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA = "voxmaestro.intent-calibration/v0"
LABEL_SCHEME = "letters-v0"
LABELS = tuple(chr(ord("A") + i) for i in range(26))
T_MIN, T_MAX = 0.05, 20.0


def _logsumexp(values: Sequence[float]) -> float:
    top = max(values)
    return top + math.log(sum(math.exp(v - top) for v in values))


def label_logprobs(top_logprobs: Sequence[Mapping[str, Any]],
                   labels: Sequence[str]) -> dict[str, float]:
    """Collect logprobs for option labels from one position's top-k list.

    Tokenizers may emit ``"A"`` or ``" A"``; both count toward label ``A``.
    Labels absent from the top-k are absent from the result, never guessed.
    """
    wanted = set(labels)
    seen: dict[str, list[float]] = {}
    for entry in top_logprobs:
        token, logprob = entry.get("token"), entry.get("logprob")
        if (not isinstance(token, str) or isinstance(logprob, bool)
                or not isinstance(logprob, (int, float)) or not math.isfinite(logprob)
                or logprob > 0):
            raise ValueError("malformed top_logprobs entry")
        key = token.strip()
        if key in wanted:
            seen.setdefault(key, []).append(float(logprob))
    return {key: _logsumexp(values) for key, values in seen.items()}


def option_probabilities(logprobs: Mapping[str, float], *, temperature: float = 1.0,
                         min_label_mass: float = 0.5) -> dict[str, float] | None:
    """Temperature-scaled distribution over the options the model actually ranked.

    Returns ``None`` (fail closed) when the raw probability mass on legal labels
    is below ``min_label_mass``: the model was mostly trying to say something
    that is not an option, so no option probability is trustworthy.
    """
    if not logprobs:
        return None
    if not (math.isfinite(temperature) and T_MIN <= temperature <= T_MAX):
        raise ValueError("temperature outside bounds")
    if sum(math.exp(lp) for lp in logprobs.values()) < min_label_mass:
        return None
    scaled = {key: lp / temperature for key, lp in logprobs.items()}
    norm = _logsumexp(list(scaled.values()))
    return {key: math.exp(value - norm) for key, value in scaled.items()}


def negative_log_likelihood(samples: Sequence[tuple[Mapping[str, float], str]],
                            temperature: float) -> float:
    """Mean NLL of gold options. Callers must drop samples whose gold is unranked."""
    total = 0.0
    for logprobs, gold in samples:
        scaled = {key: lp / temperature for key, lp in logprobs.items()}
        total -= scaled[gold] - _logsumexp(list(scaled.values()))
    return total / len(samples)


def fit_temperature(samples: Sequence[tuple[Mapping[str, float], str]]) -> float:
    """Golden-section search on log T. NLL is unimodal in T for this family."""
    usable = [(lp, gold) for lp, gold in samples if gold in lp]
    if len(usable) < 2:
        raise ValueError("need at least two samples with a ranked gold option")
    lo, hi = math.log(T_MIN), math.log(T_MAX)
    ratio = (math.sqrt(5) - 1) / 2
    a, b = hi - ratio * (hi - lo), lo + ratio * (hi - lo)
    fa = negative_log_likelihood(usable, math.exp(a))
    fb = negative_log_likelihood(usable, math.exp(b))
    for _ in range(80):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - ratio * (hi - lo)
            fa = negative_log_likelihood(usable, math.exp(a))
        else:
            lo, a, fa = a, b, fb
            b = lo + ratio * (hi - lo)
            fb = negative_log_likelihood(usable, math.exp(b))
    return math.exp((lo + hi) / 2)


def expected_calibration_error(confidences: Sequence[float], correct: Sequence[bool],
                               bins: int = 10) -> float:
    """Equal-width-bin ECE over top-1 confidence."""
    if len(confidences) != len(correct) or not confidences:
        raise ValueError("need equal-length, non-empty inputs")
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for conf, ok in zip(confidences, correct):
        buckets[min(int(conf * bins), bins - 1)].append((conf, ok))
    n = len(confidences)
    return sum(
        len(b) / n * abs(sum(c for c, _ in b) / len(b) - sum(ok for _, ok in b) / len(b))
        for b in buckets if b
    )


@dataclass(frozen=True)
class CalibrationArtifact:
    """Pinned to one model identity; a worker refuses it for any other model."""

    model: str
    temperature: float
    min_label_mass: float
    fitted_n: int
    eval_n: int
    metrics: Mapping[str, Any] = field(default_factory=dict)
    schema: str = SCHEMA
    label_scheme: str = LABEL_SCHEME

    def __post_init__(self) -> None:
        if self.schema != SCHEMA or self.label_scheme != LABEL_SCHEME:
            raise ValueError("unsupported calibration schema or label scheme")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model identity required")
        for name, value, lo, hi in (("temperature", self.temperature, T_MIN, T_MAX),
                                    ("min_label_mass", self.min_label_mass, 0.0, 1.0)):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not lo <= value <= hi):
                raise ValueError(f"{name} outside bounds")
        for name in ("fitted_n", "eval_n"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative int")

    def to_json(self) -> str:
        data = asdict(self)
        data["metrics"] = dict(self.metrics)
        return json.dumps(data, sort_keys=True, indent=2)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @classmethod
    def from_json(cls, raw: str) -> CalibrationArtifact:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {
                "model", "temperature", "min_label_mass", "fitted_n", "eval_n",
                "metrics", "schema", "label_scheme"}:
            raise ValueError("calibration artifact has unexpected keys")
        if not isinstance(data["metrics"], dict):
            raise ValueError("metrics must be an object")
        return cls(**data)
