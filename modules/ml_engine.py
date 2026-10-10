"""Experimental advisory models trained on synthetic DeFi priors.

These estimates are not calibrated forecasts and must not replace the
user-supplied probability or severity in the graph-based decision model.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

VOL_RANGE = (0.005, 0.5)
HEARTBEAT_SECONDS = 3600.0


def _require_finite(**values: float) -> None:
    for name, value in values.items():
        if value is None or isinstance(value, bool):
            raise ValueError(f"{name} is missing")
        try:
            finite = math.isfinite(float(value))
        except (TypeError, ValueError):
            raise ValueError(f"{name} is not numeric: {value!r}") from None
        if not finite:
            raise ValueError(f"{name} must be finite, got {value!r}")


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, value))))


def _synthetic_slippage(rng: np.random.Generator, size: int = 20_000):
    ratio = 10 ** rng.uniform(-3, 0, size)
    turnover = 10 ** rng.uniform(-2, 1, size)
    volatility = rng.uniform(*VOL_RANGE, size)
    volatility_multiplier = 1.0 + 6.0 * (volatility - 0.05)
    depth_multiplier = 1.0 / (1.0 + 0.1 * (turnover - 1.0))
    slippage = (
        0.5 * ratio**2
        * np.clip(volatility_multiplier, 0.2, None)
        * np.clip(depth_multiplier, 0.3, 3.0)
        * rng.lognormal(0.0, 0.03, size)
    )
    return np.column_stack((np.log(ratio), np.log(turnover), volatility)), np.log(slippage)


def _synthetic_oracle(rng: np.random.Generator, size: int = 4_000):
    age = np.minimum(rng.exponential(500, size), 3000)
    deviation = np.abs(rng.normal(0.0, 0.003, size))
    historical_volatility = rng.lognormal(math.log(0.03), 0.4, size)
    return np.column_stack((age, deviation, historical_volatility))


def _synthetic_depeg(rng: np.random.Generator, size: int = 30_000):
    stressed = rng.random(size) < 0.25
    ratio = np.where(stressed, rng.uniform(0.93, 1.0, size), rng.normal(0.9995, 0.002, size))
    imbalance = np.clip(
        np.where(stressed, rng.beta(5, 2.5, size), rng.beta(30, 30, size)), 0, 1
    )
    probability = 1.0 / (1.0 + np.exp(-(-5.5 + 60.0 * (1.0 - ratio) + 4.0 * (imbalance - 0.5))))
    return np.column_stack((ratio, imbalance)), (rng.random(size) < probability).astype(int)


class DeFiRiskMLPipeline:
    def __init__(self, seed: int = 42):
        self.seed = seed
        self.slippage_model = self._fit_slippage()
        self.oracle_model = self._fit_oracle()
        self.depeg_model = self._fit_depeg()

    def _fit_slippage(self):
        features, target = _synthetic_slippage(np.random.default_rng(self.seed))
        model = make_pipeline(
            StandardScaler(),
            PolynomialFeatures(degree=3, include_bias=False),
            Ridge(alpha=1e-4),
        )
        return model.fit(features, target)

    def _fit_oracle(self):
        features = _synthetic_oracle(np.random.default_rng(self.seed))
        return IsolationForest(
            n_estimators=300, contamination=0.01, random_state=self.seed
        ).fit(features)

    def _fit_depeg(self):
        features, target = _synthetic_depeg(np.random.default_rng(self.seed))
        model = make_pipeline(StandardScaler(), LogisticRegression(C=100.0, max_iter=1000))
        return model.fit(features, target)

    def predict_dynamic_slippage(
        self,
        position_size_usd: float,
        pool_liquidity_usd: float,
        volume_24h: float,
        volatility_index: float = 0.05,
    ) -> float:
        """Return an advisory slippage estimate as a fraction, excluding fees."""
        _require_finite(
            position_size_usd=position_size_usd,
            pool_liquidity_usd=pool_liquidity_usd,
            volume_24h=volume_24h,
            volatility_index=volatility_index,
        )
        if pool_liquidity_usd <= 0:
            raise ValueError("pool_liquidity_usd must be > 0")
        if position_size_usd < 0 or volume_24h < 0 or volatility_index < 0:
            raise ValueError("position, volume and volatility must be >= 0")

        size_ratio = position_size_usd / pool_liquidity_usd
        if size_ratio == 0:
            return 0.0
        clipped_ratio = float(np.clip(size_ratio, 1e-3, 1.0))
        turnover = float(np.clip(volume_24h / pool_liquidity_usd, 0.01, 10.0))
        volatility = float(np.clip(volatility_index, *VOL_RANGE))
        log_slippage = float(
            self.slippage_model.predict(
                [[math.log(clipped_ratio), math.log(turnover), volatility]]
            )[0]
        )
        slippage = math.exp(max(-50.0, min(log_slippage, 5.0)))
        slippage *= (size_ratio / clipped_ratio) ** 2
        return float(np.clip(slippage, 0.0, 1.0))

    def detect_oracle_anomaly(
        self,
        last_price_update_seconds: int,
        price_deviation_pct: float,
        historical_volatility: float,
    ) -> dict:
        """Return an advisory anomaly score and stale probability, all in [0, 1]."""
        _require_finite(
            last_price_update_seconds=last_price_update_seconds,
            price_deviation_pct=price_deviation_pct,
            historical_volatility=historical_volatility,
        )
        if last_price_update_seconds < 0 or historical_volatility < 0:
            raise ValueError("update age and volatility must be >= 0")

        features = [[
            float(last_price_update_seconds),
            abs(float(price_deviation_pct)),
            float(historical_volatility),
        ]]
        decision = float(self.oracle_model.decision_function(features)[0])
        forest_score = _sigmoid(-decision / 0.02)
        heartbeat_score = _sigmoid(
            (last_price_update_seconds - 1.1 * HEARTBEAT_SECONDS) / 500.0
        )
        deviation_score = _sigmoid((abs(float(price_deviation_pct)) - 0.04) / 0.006)
        anomaly = max(forest_score, heartbeat_score, deviation_score)
        age_component = _sigmoid(
            (last_price_update_seconds - 1.5 * HEARTBEAT_SECONDS) / 1200.0
        )
        stale_probability = float(np.clip(0.85 * anomaly + 0.15 * age_component, 0.0, 1.0))
        return {
            "anomaly_score": float(anomaly),
            "is_anomaly": bool(anomaly >= 0.5),
            "predicted_stale_probability": stale_probability,
        }

    def predict_depeg_probability(
        self, current_ratio: float, pool_imbalance_ratio: float
    ) -> float:
        """Return synthetic-prior P(depeg) for an explicitly provided peg ratio."""
        _require_finite(
            current_ratio=current_ratio,
            pool_imbalance_ratio=pool_imbalance_ratio,
        )
        if current_ratio <= 0:
            raise ValueError("current_ratio must be > 0")
        ratio = float(np.clip(current_ratio, 0.5, 1.5))
        imbalance = float(np.clip(pool_imbalance_ratio, 0.0, 1.0))
        probability = float(
            self.depeg_model.predict_proba([[ratio, imbalance]])[0, 1]
        )
        return float(np.clip(probability, 0.0, 1.0))


@lru_cache(maxsize=1)
def get_pipeline() -> DeFiRiskMLPipeline:
    """Fit the models once per app process."""
    return DeFiRiskMLPipeline()
