"""Light scikit-learn models that predict *inputs* to the deterministic EV decision.

The decision rule (EV_stay vs L_exit) stays in ``risk_engine``; these models only
estimate three internal variables:

1. dynamic exit slippage     — Ridge on polynomial features
2. oracle anomaly / staleness — IsolationForest on heartbeat metrics
3. asset depeg probability    — LogisticRegression on backing ratio + pool skew

IMPORTANT: no public labelled dataset of DEX executions / oracle incidents / depegs
is bundled, so all three models are fitted on *synthetic* data whose shape encodes
standard DeFi priors (quadratic price impact, 1h Chainlink heartbeat, depeg risk
rising as backing ratio falls and pools skew). They are illustrative priors, not
calibrated estimates. Swap ``_synthetic_*`` for real data to calibrate.

All fractions are 0..1 (0.015 == 1.5%).
"""
from __future__ import annotations

import math
import os
from functools import lru_cache
from typing import Callable

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures, StandardScaler

MODEL_VERSION = "1"
DEFAULT_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ml_cache")

# Training-domain bounds (inputs are clipped into these before prediction).
X1_RANGE = (1e-3, 1.0)            # position / pool liquidity
X2_RANGE = (0.01, 10.0)           # 24h volume / pool liquidity
VOL_RANGE = (0.005, 0.5)          # volatility index
HEARTBEAT_SECONDS = 3600.0        # typical Chainlink ETH/USD heartbeat


def _require_finite(**values: float) -> None:
    for name, v in values.items():
        if v is None or isinstance(v, bool):
            raise ValueError(f"{name} is missing")
        try:
            ok = math.isfinite(float(v))
        except (TypeError, ValueError):
            raise ValueError(f"{name} is not numeric: {v!r}") from None
        if not ok:
            raise ValueError(f"{name} must be finite, got {v!r}")


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))


# --------------------------------------------------------------------------- #
# Synthetic training data
# --------------------------------------------------------------------------- #
def _synthetic_slippage(rng: np.random.Generator, n: int = 20_000):
    x1 = 10 ** rng.uniform(-3, 0, n)                 # size / liquidity
    x2 = 10 ** rng.uniform(-2, 1, n)                 # volume / liquidity
    vol = rng.uniform(*VOL_RANGE, n)
    vol_mult = 1.0 + 6.0 * (vol - 0.05)              # volatility widens impact
    depth_mult = 1.0 / (1.0 + 0.1 * (x2 - 1.0))      # high turnover = deeper book
    slip = 0.5 * x1 ** 2 * np.clip(vol_mult, 0.2, None) * np.clip(depth_mult, 0.3, 3.0)
    slip = slip * rng.lognormal(0.0, 0.03, n)
    # Fit log(slippage) on [log x1, log x2, vol]: the quadratic law becomes linear in log x1.
    return np.column_stack([np.log(x1), np.log(x2), vol]), np.log(slip)


def _synthetic_oracle(rng: np.random.Generator, n: int = 4_000):
    age = np.minimum(rng.exponential(500, n), 3000)  # healthy feeds mostly update well within heartbeat
    dev = np.abs(rng.normal(0.0, 0.003, n))          # |deviation| vs reference
    hvol = rng.lognormal(math.log(0.03), 0.4, n)     # daily historical volatility
    return np.column_stack([age, dev, hvol])


def _synthetic_depeg(rng: np.random.Generator, n: int = 30_000):
    stressed = rng.random(n) < 0.25
    ratio = np.where(stressed, rng.uniform(0.93, 1.0, n), rng.normal(0.9995, 0.002, n))
    imb = np.clip(np.where(stressed, rng.beta(5, 2.5, n), rng.beta(30, 30, n)), 0, 1)
    z = -5.5 + 60.0 * (1.0 - ratio) + 4.0 * (imb - 0.5)
    p = 1.0 / (1.0 + np.exp(-z))
    return np.column_stack([ratio, imb]), (rng.random(n) < p).astype(int)


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
class DeFiRiskMLPipeline:
    def __init__(self, seed: int = 42, model_dir: str = DEFAULT_MODEL_DIR, use_cache: bool = True):
        self.seed = seed
        self.model_dir = model_dir
        self.use_cache = use_cache
        self.slippage_model = self._load_or_fit("slippage", self._fit_slippage)
        self.oracle_model = self._load_or_fit("oracle", self._fit_oracle)
        self.depeg_model = self._load_or_fit("depeg", self._fit_depeg)

    # -- fitting / caching ---------------------------------------------------
    def _load_or_fit(self, name: str, fit: Callable):
        path = os.path.join(self.model_dir, f"{name}_v{MODEL_VERSION}_s{self.seed}.joblib")
        if self.use_cache and os.path.exists(path):
            try:
                return joblib.load(path)
            except Exception:  # noqa: BLE001  (stale / incompatible cache → refit)
                pass
        model = fit()
        if self.use_cache:
            try:
                os.makedirs(self.model_dir, exist_ok=True)
                joblib.dump(model, path)
            except Exception:  # noqa: BLE001  (read-only FS is fine)
                pass
        return model

    def _fit_slippage(self):
        X, y = _synthetic_slippage(np.random.default_rng(self.seed))
        model = make_pipeline(StandardScaler(), PolynomialFeatures(degree=3, include_bias=False),
                              Ridge(alpha=1e-4))
        return model.fit(X, y)

    def _fit_oracle(self):
        X = _synthetic_oracle(np.random.default_rng(self.seed))
        return IsolationForest(n_estimators=300, contamination=0.01, random_state=self.seed).fit(X)

    def _fit_depeg(self):
        X, y = _synthetic_depeg(np.random.default_rng(self.seed))
        return make_pipeline(StandardScaler(), LogisticRegression(C=100.0, max_iter=1000)).fit(X, y)

    # -- 1. dynamic slippage ---------------------------------------------------
    def predict_dynamic_slippage(self, position_size_usd: float, pool_liquidity_usd: float,
                                 volume_24h: float, volatility_index: float = 0.05) -> float:
        """Predicted exit slippage as a fraction (0..1), excluding fees."""
        _require_finite(position_size_usd=position_size_usd, pool_liquidity_usd=pool_liquidity_usd,
                        volume_24h=volume_24h, volatility_index=volatility_index)
        if pool_liquidity_usd <= 0:
            raise ValueError("pool_liquidity_usd must be > 0")
        if position_size_usd < 0 or volume_24h < 0 or volatility_index < 0:
            raise ValueError("position, volume and volatility must be >= 0")

        x1 = position_size_usd / pool_liquidity_usd
        if x1 == 0:
            return 0.0
        x1c = float(np.clip(x1, *X1_RANGE))
        x2 = float(np.clip(volume_24h / pool_liquidity_usd, *X2_RANGE))
        vol = float(np.clip(volatility_index, *VOL_RANGE))

        log_slip = float(self.slippage_model.predict([[math.log(x1c), math.log(x2), vol]])[0])
        slip = math.exp(max(-50.0, min(log_slip, 5.0)))
        slip *= (x1 / x1c) ** 2               # outside the training domain: extrapolate quadratically
        return float(np.clip(slip, 0.0, 1.0))

    # -- 2. oracle anomaly -----------------------------------------------------
    def detect_oracle_anomaly(self, last_price_update_seconds: int, price_deviation_pct: float,
                              historical_volatility: float) -> dict:
        """Isolation-Forest score for a price feed's heartbeat behaviour.

        ``price_deviation_pct`` and ``historical_volatility`` are fractions.
        """
        _require_finite(last_price_update_seconds=last_price_update_seconds,
                        price_deviation_pct=price_deviation_pct,
                        historical_volatility=historical_volatility)
        if last_price_update_seconds < 0 or historical_volatility < 0:
            raise ValueError("update age and volatility must be >= 0")

        X = [[float(last_price_update_seconds), abs(float(price_deviation_pct)),
              float(historical_volatility)]]
        decision = float(self.oracle_model.decision_function(X)[0])   # < 0 → outlier
        forest_score = _sigmoid(-decision / 0.02)
        # Isolation trees cannot rank values beyond their training range (3.7k s and 10k s
        # score alike), so a heartbeat-excess term keeps "very late" above "late".
        heartbeat_score = _sigmoid((last_price_update_seconds - 1.1 * HEARTBEAT_SECONDS) / 500.0)
        # Same saturation applies to deviation: a >4% gap vs the reference is flagged outright.
        deviation_score = _sigmoid((abs(float(price_deviation_pct)) - 0.04) / 0.006)
        anomaly = max(forest_score, heartbeat_score, deviation_score)
        age_component = _sigmoid((last_price_update_seconds - 1.5 * HEARTBEAT_SECONDS) / 1200.0)
        stale_prob = float(np.clip(0.85 * anomaly + 0.15 * age_component, 0.0, 1.0))
        return {
            "anomaly_score": float(anomaly),
            "is_anomaly": bool(anomaly >= 0.5),
            "predicted_stale_probability": stale_prob,
        }

    # -- 3. depeg probability ---------------------------------------------------
    def predict_depeg_probability(self, current_ratio: float, pool_imbalance_ratio: float) -> float:
        """P(severe depeg). ``current_ratio`` = market price / backing value (1.0 = at peg);
        ``pool_imbalance_ratio`` = weight of the weak asset in the pool (0.5 = balanced)."""
        _require_finite(current_ratio=current_ratio, pool_imbalance_ratio=pool_imbalance_ratio)
        if current_ratio <= 0:
            raise ValueError("current_ratio must be > 0")
        ratio = float(np.clip(current_ratio, 0.5, 1.5))
        imb = float(np.clip(pool_imbalance_ratio, 0.0, 1.0))
        p = float(self.depeg_model.predict_proba([[ratio, imb]])[0, 1])
        return float(np.clip(p, 0.0, 1.0))


@lru_cache(maxsize=1)
def get_pipeline() -> DeFiRiskMLPipeline:
    """Process-wide singleton (models are fitted once, then cached on disk)."""
    return DeFiRiskMLPipeline()
