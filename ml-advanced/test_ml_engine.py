"""Tests for modules/ml_engine.py and its integration in modules/risk_engine.py.

Run from the project root:  python -m pytest -q
"""
import math
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
warnings.filterwarnings("ignore")

from modules import risk_engine  # noqa: E402
from modules.ml_engine import DeFiRiskMLPipeline  # noqa: E402

NAN = float("nan")


@pytest.fixture(scope="module")
def pipeline():
    return DeFiRiskMLPipeline(use_cache=False)


@pytest.fixture(autouse=True)
def _shared_pipeline(monkeypatch, pipeline):
    """Make risk_engine reuse the module-scoped pipeline instead of refitting."""
    monkeypatch.setattr(risk_engine, "_get_ml", lambda: pipeline)


# --------------------------------------------------------------------------- #
# 1. Dynamic slippage
# --------------------------------------------------------------------------- #
def test_ml_slippage_prediction_scales_with_volatility(pipeline):
    vols = [0.02, 0.05, 0.1, 0.2, 0.3]
    preds = [pipeline.predict_dynamic_slippage(100_000, 700_000, 700_000, v) for v in vols]
    assert all(b > a for a, b in zip(preds, preds[1:])), preds
    assert preds[-1] > 2 * preds[0]


def test_ml_slippage_scales_with_size_and_tracks_quadratic_baseline(pipeline):
    small = pipeline.predict_dynamic_slippage(10_000, 700_000, 700_000, 0.05)
    large = pipeline.predict_dynamic_slippage(100_000, 700_000, 700_000, 0.05)
    assert large > small
    static = 0.5 * (100_000 / 700_000) ** 2
    assert large == pytest.approx(static, rel=0.15)      # neutral volume + default volatility
    assert pipeline.predict_dynamic_slippage(0, 700_000, 700_000) == 0.0
    assert pipeline.predict_dynamic_slippage(10 ** 7, 100_000, 100_000) == 1.0  # capped


# --------------------------------------------------------------------------- #
# 2. Oracle anomaly detection
# --------------------------------------------------------------------------- #
def test_oracle_anomaly_detection(pipeline):
    normal = pipeline.detect_oracle_anomaly(600, 0.002, 0.03)
    assert normal["is_anomaly"] is False and normal["anomaly_score"] < 0.2

    late = pipeline.detect_oracle_anomaly(3700, 0.003, 0.03)       # just past a 1h heartbeat
    very_late = pipeline.detect_oracle_anomaly(7200, 0.003, 0.03)
    for res in (late, very_late):
        assert res["is_anomaly"] is True and res["anomaly_score"] > 0.5
    assert very_late["anomaly_score"] > 0.9
    assert (normal["predicted_stale_probability"] < late["predicted_stale_probability"]
            < very_late["predicted_stale_probability"])
    assert set(very_late) == {"anomaly_score", "is_anomaly", "predicted_stale_probability"}


def test_oracle_anomaly_flags_large_deviation(pipeline):
    res = pipeline.detect_oracle_anomaly(600, 0.08, 0.03)
    assert res["is_anomaly"] is True


# --------------------------------------------------------------------------- #
# 3. Depeg probability
# --------------------------------------------------------------------------- #
def test_depeg_probability_monotonic_and_bounded(pipeline):
    at_peg = pipeline.predict_depeg_probability(1.0, 0.5)
    stressed = pipeline.predict_depeg_probability(0.97, 0.85)
    assert 0.0 <= at_peg < 0.02 < stressed <= 1.0
    assert pipeline.predict_depeg_probability(0.98, 0.5) > pipeline.predict_depeg_probability(0.995, 0.5)
    assert pipeline.predict_depeg_probability(0.99, 0.9) > pipeline.predict_depeg_probability(0.99, 0.5)


# --------------------------------------------------------------------------- #
# 4. Fallback safety
# --------------------------------------------------------------------------- #
def test_ml_engine_rejects_invalid_input(pipeline):
    with pytest.raises(ValueError):
        pipeline.predict_dynamic_slippage(100_000, 700_000, NAN, 0.05)
    with pytest.raises(ValueError):
        pipeline.predict_dynamic_slippage(100_000, 0, 1_000, 0.05)
    with pytest.raises(ValueError):
        pipeline.detect_oracle_anomaly(NAN, 0.01, 0.03)
    with pytest.raises(ValueError):
        pipeline.predict_depeg_probability(1.0, NAN)


def test_ml_fallback_safety():
    static = risk_engine.calculate_exit_loss(100_000, 700_000, use_ml=False)

    # NaN ML inputs → ML raises internally → deterministic result, no crash
    b = risk_engine.exit_loss_components(100_000, 700_000, volume_24h=NAN, volatility_index=NAN)
    assert b.used_ml is False and b.ml_slippage_pct is None
    assert b.total_pct == pytest.approx(static)

    # NaN oracle / depeg features → static oracle & depeg inputs
    s = risk_engine.stay_loss_components(
        False, 0.03, 0.00375,
        oracle_features={"last_price_update_seconds": NAN, "price_deviation_pct": 0.0,
                         "historical_volatility": 0.03},
        depeg_features={"current_ratio": NAN, "pool_imbalance_ratio": 0.5})
    assert not s.used_ml_oracle and not s.used_ml_depeg
    assert s.total_pct == pytest.approx(risk_engine.calculate_stay_loss(False, 0.03, 0.00375, use_ml=False))


class _BrokenPipeline:
    def predict_dynamic_slippage(self, *a, **k):
        raise RuntimeError("model exploded")

    def detect_oracle_anomaly(self, *a, **k):
        raise RuntimeError("model exploded")

    def predict_depeg_probability(self, *a, **k):
        raise RuntimeError("model exploded")


class _OutOfBoundsPipeline:
    def predict_dynamic_slippage(self, *a, **k):
        return 7.5

    def detect_oracle_anomaly(self, *a, **k):
        return {"anomaly_score": 3.0, "is_anomaly": True, "predicted_stale_probability": -1.0}

    def predict_depeg_probability(self, *a, **k):
        return NAN


@pytest.mark.parametrize("stub", [_BrokenPipeline(), _OutOfBoundsPipeline(), None])
def test_ml_fallback_on_model_failure_or_out_of_bounds(monkeypatch, stub):
    monkeypatch.setattr(risk_engine, "_get_ml", lambda: stub)
    oracle_f = {"last_price_update_seconds": 600, "price_deviation_pct": 0.001, "historical_volatility": 0.03}
    depeg_f = {"current_ratio": 0.999, "pool_imbalance_ratio": 0.5}

    exit_b = risk_engine.exit_loss_components(100_000, 700_000)
    stay_b = risk_engine.stay_loss_components(False, 0.03, 0.00375, oracle_f, depeg_f)
    assert exit_b.total_pct == pytest.approx(risk_engine.calculate_exit_loss(100_000, 700_000, use_ml=False))
    assert stay_b.total_pct == pytest.approx(risk_engine.calculate_stay_loss(False, 0.03, 0.00375, use_ml=False))

    out = risk_engine.evaluate_decision(100_000, exit_b.total_pct, stay_b.total_pct, exit_b, stay_b)
    assert out["decision"] in {"STAY", "EXIT"}
    assert out["ml_insights"]["ml_active"] is False
    assert all(math.isfinite(v) for v in (out["net_benefit_usd"], out["exit_loss_usd"], out["stay_loss_usd"]))


# --------------------------------------------------------------------------- #
# 5. Integration: deterministic rule preserved, ml_insights populated
# --------------------------------------------------------------------------- #
def test_evaluate_decision_includes_ml_insights_and_keeps_rule():
    exit_b = risk_engine.exit_loss_components(100_000, 700_000, volume_24h=700_000, volatility_index=0.05)
    stay_b = risk_engine.stay_loss_components(
        False, 0.03, 0.00375,
        {"last_price_update_seconds": 600, "price_deviation_pct": 0.001, "historical_volatility": 0.03},
        {"current_ratio": 0.9995, "pool_imbalance_ratio": 0.52})
    out = risk_engine.evaluate_decision(100_000, exit_b.total_pct, stay_b.total_pct, exit_b, stay_b)

    ins = out["ml_insights"]
    assert ins["ml_active"] is True
    assert ins["slippage"]["ml_pct"] is not None and ins["slippage"]["static_pct"] is not None
    assert 0.0 <= ins["oracle"]["anomaly_score"] <= 1.0
    assert 0.0 <= ins["depeg"]["ml_probability"] <= 1.0
    # Rule itself is unchanged: STAY iff |EV_stay| < |L_exit|
    assert out["decision"] == ("STAY" if stay_b.total_pct < exit_b.total_pct else "EXIT")


def test_stale_oracle_keeps_full_static_risk_under_ml():
    s = risk_engine.stay_loss_components(
        True, 0.0, 0.0,
        {"last_price_update_seconds": 10_800, "price_deviation_pct": 0.03, "historical_volatility": 0.12})
    assert s.oracle_pct == pytest.approx(risk_engine.ORACLE_STALE_PROB)
