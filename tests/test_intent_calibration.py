"""Pure calibration math; no model, no network."""
import math
import random

import pytest

from voxmaestro.workers.calibration import (
    CalibrationArtifact,
    expected_calibration_error,
    fit_temperature,
    label_logprobs,
    option_probabilities,
)


def test_label_logprobs_merges_leading_space_tokens_and_ignores_off_label():
    top = [{"token": "A", "logprob": math.log(0.3)}, {"token": " A", "logprob": math.log(0.2)},
           {"token": "B", "logprob": math.log(0.1)}, {"token": "Sure", "logprob": math.log(0.4)}]
    got = label_logprobs(top, ["A", "B", "C"])
    assert set(got) == {"A", "B"}  # C was never ranked; it is absent, not guessed
    assert math.exp(got["A"]) == pytest.approx(0.5)


@pytest.mark.parametrize("entry", [{"token": "A"}, {"token": "A", "logprob": 0.5},
                                   {"token": "A", "logprob": float("nan")},
                                   {"token": 1, "logprob": -1.0},
                                   {"token": "A", "logprob": True}])
def test_label_logprobs_rejects_malformed_entries(entry):
    with pytest.raises(ValueError):
        label_logprobs([entry], ["A"])


def test_option_probabilities_normalizes_and_temperature_flattens():
    lp = {"x": math.log(0.6), "y": math.log(0.3)}
    sharp = option_probabilities(lp, temperature=1.0)
    flat = option_probabilities(lp, temperature=4.0)
    assert sum(sharp.values()) == pytest.approx(1.0)
    assert sharp["x"] == pytest.approx(2 / 3)
    assert 0.5 < flat["x"] < sharp["x"]


def test_option_probabilities_fails_closed_when_model_is_mostly_off_label():
    assert option_probabilities({"x": math.log(0.3)}, min_label_mass=0.5) is None
    assert option_probabilities({}, min_label_mass=0.0) is None


def _synthetic(true_t, n, seed):
    """Logits drawn, gold sampled from softmax(logits / true_t); report raw logits."""
    rng = random.Random(seed)
    samples = []
    for _ in range(n):
        logits = {k: rng.gauss(0, 3) for k in "abcd"}
        norm = math.log(sum(math.exp(v / true_t) for v in logits.values()))
        probs = {k: math.exp(v / true_t - norm) for k, v in logits.items()}
        gold = rng.choices(list(probs), weights=list(probs.values()))[0]
        samples.append((logits, gold))
    return samples


@pytest.mark.parametrize("true_t", [0.7, 1.0, 2.5])
def test_fit_temperature_recovers_generating_temperature(true_t):
    fitted = fit_temperature(_synthetic(true_t, 4000, seed=7))
    assert fitted == pytest.approx(true_t, rel=0.12)


def test_fit_temperature_ignores_unranked_gold_and_needs_data():
    samples = _synthetic(1.0, 200, seed=1) + [({"a": -0.1}, "z")] * 50
    assert fit_temperature(samples) == pytest.approx(1.0, rel=0.3)
    with pytest.raises(ValueError):
        fit_temperature([({"a": -0.1}, "z")])


def test_calibration_lowers_ece_on_overconfident_scores():
    samples = _synthetic(2.5, 3000, seed=3)  # raw logits are overconfident by 2.5x
    fit, held = samples[:1500], samples[1500:]
    t = fit_temperature(fit)

    def ece_at(temp):
        confs, oks = [], []
        for lp, gold in held:
            probs = option_probabilities(lp, temperature=temp, min_label_mass=0.0)
            top = max(probs, key=probs.get)
            confs.append(probs[top])
            oks.append(top == gold)
        return expected_calibration_error(confs, oks)

    assert ece_at(t) < ece_at(1.0) / 2


def test_ece_bounds():
    assert expected_calibration_error([0.9] * 10, [True] * 9 + [False]) == pytest.approx(0.0)
    assert expected_calibration_error([0.99] * 4, [False] * 4) == pytest.approx(0.99)
    with pytest.raises(ValueError):
        expected_calibration_error([], [])


def _artifact(**kw):
    base = dict(model="m", temperature=1.7, min_label_mass=0.5, fitted_n=100, eval_n=100)
    return CalibrationArtifact(**{**base, **kw})


def test_artifact_roundtrip_and_digest_is_stable():
    art = _artifact(metrics={"ece_after": 0.03})
    again = CalibrationArtifact.from_json(art.to_json())
    assert again == art and again.digest == art.digest
    assert _artifact(temperature=1.8).digest != art.digest


@pytest.mark.parametrize("bad", [dict(temperature=0.0), dict(temperature=float("nan")),
                                 dict(temperature=99.0), dict(min_label_mass=1.5),
                                 dict(fitted_n=-1), dict(model=" "), dict(schema="other/v9"),
                                 dict(temperature=True)])
def test_artifact_rejects_out_of_contract_values(bad):
    with pytest.raises(ValueError):
        _artifact(**bad)


def test_artifact_from_json_rejects_extra_keys():
    raw = _artifact().to_json().replace('"eval_n"', '"extra": 1, "eval_n"')
    with pytest.raises(ValueError):
        CalibrationArtifact.from_json(raw)
