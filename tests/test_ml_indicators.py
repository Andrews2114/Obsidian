import pytest

pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from modules.ml_engine import DeFiRiskMLPipeline


@pytest.fixture(scope="module")
def pipeline():
    return DeFiRiskMLPipeline()


def test_slippage_estimate_is_bounded_and_increases_with_position_size(pipeline):
    small = pipeline.predict_dynamic_slippage(10_000, 700_000, 700_000)
    large = pipeline.predict_dynamic_slippage(100_000, 700_000, 700_000)

    assert 0 <= small < large <= 1


def test_oracle_anomaly_score_increases_with_staleness(pipeline):
    current = pipeline.detect_oracle_anomaly(600, 0.002, 0.03)
    stale = pipeline.detect_oracle_anomaly(7_200, 0.002, 0.03)

    assert 0 <= current["anomaly_score"] < stale["anomaly_score"] <= 1
    assert current["predicted_stale_probability"] < stale["predicted_stale_probability"]


def test_depeg_estimate_is_bounded_and_rejects_invalid_inputs(pipeline):
    assert 0 <= pipeline.predict_depeg_probability(0.9995, 0.52) <= 1

    with pytest.raises(ValueError):
        pipeline.predict_depeg_probability(0, 0.5)
