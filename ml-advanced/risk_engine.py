"""Expected-value (EV) risk model: exit slippage vs. expected loss of staying.

The decision rule (|EV_stay| < |L_exit| → STAY) is deterministic and unchanged.
ML (``ml_engine``) only *predicts three inputs* to it — slippage, oracle risk and
depeg probability — and every ML call falls back to the original static formula if
the model is unavailable, the inputs are invalid (NaN, negative…) or the prediction
is out of bounds.

All percentages are fractions (0.015 == 1.5%).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

try:  # scikit-learn is optional at runtime: no ML → pure deterministic model
    from . import ml_engine
except Exception:  # noqa: BLE001
    ml_engine = None

STALE_THRESHOLD_HOURS = 2.0
ORACLE_STALE_PROB = 0.25
ORACLE_OK_PROB = 0.002
CONCENTRATION_WEIGHT = 0.1
DEPEG_WEIGHT = 0.8

_ml_failed = False


def _get_ml():
    """Return the shared ML pipeline, or None if unavailable (never raises)."""
    global _ml_failed
    if ml_engine is None or _ml_failed:
        return None
    try:
        return ml_engine.get_pipeline()
    except Exception:  # noqa: BLE001
        _ml_failed = True
        return None


def _is_prob(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x) and 0.0 <= x <= 1.0


@dataclass
class ExitBreakdown:
    slippage_pct: float
    fee_pct: float
    total_pct: float
    static_slippage_pct: Optional[float] = None   # quadratic baseline
    ml_slippage_pct: Optional[float] = None       # ML prediction (None if unused / failed)
    used_ml: bool = False


@dataclass
class StayBreakdown:
    oracle_pct: float
    concentration_pct: float
    depeg_pct: float
    total_pct: float
    static_oracle_pct: Optional[float] = None
    static_depeg_pct: Optional[float] = None
    oracle_anomaly: Optional[dict] = None         # ML detector output
    ml_depeg_probability: Optional[float] = None  # ML probability (before x0.8 loss weight)
    used_ml_oracle: bool = False
    used_ml_depeg: bool = False


def is_oracle_stale(staleness_hours: float) -> bool:
    return staleness_hours >= STALE_THRESHOLD_HOURS


# --------------------------------------------------------------------------- #
# 1. Exit loss
# --------------------------------------------------------------------------- #
def _static_slippage(position_size_usd: float, pool_liquidity_usd: float) -> float:
    if pool_liquidity_usd <= 0:
        return 1.0
    return min(1.0, (position_size_usd / pool_liquidity_usd) ** 2 * 0.5)


def exit_loss_components(position_size_usd: float, pool_liquidity_usd: float,
                         base_fee_pct: float = 0.005, volume_24h: Optional[float] = None,
                         volatility_index: float = 0.05, use_ml: bool = True) -> ExitBreakdown:
    """Exit loss = slippage + base fee.

    Slippage is ML-predicted (volume- and volatility-aware) when possible, else the
    quadratic formula ``0.5 * (size / liquidity)^2``. ``volume_24h`` defaults to the
    pool's liquidity (neutral turnover) when unknown.
    """
    if not (math.isfinite(position_size_usd) and math.isfinite(pool_liquidity_usd)):
        raise ValueError("position_size_usd and pool_liquidity_usd must be finite numbers")

    static = _static_slippage(position_size_usd, pool_liquidity_usd)
    slippage, ml_value, used = static, None, False

    pipeline = _get_ml() if use_ml and pool_liquidity_usd > 0 else None
    if pipeline is not None:
        try:
            vol_usd = pool_liquidity_usd if volume_24h is None else volume_24h
            predicted = pipeline.predict_dynamic_slippage(
                position_size_usd, pool_liquidity_usd, vol_usd, volatility_index)
            if _is_prob(predicted):
                ml_value, slippage, used = float(predicted), float(predicted), True
        except Exception:  # noqa: BLE001  (invalid input / model error → deterministic)
            pass

    return ExitBreakdown(slippage, base_fee_pct, min(1.0, slippage + base_fee_pct),
                         static_slippage_pct=static, ml_slippage_pct=ml_value, used_ml=used)


def calculate_exit_loss(position_size_usd: float, pool_liquidity_usd: float,
                        base_fee_pct: float = 0.005, volume_24h: Optional[float] = None,
                        volatility_index: float = 0.05, use_ml: bool = True) -> float:
    """Total exit loss L_exit = slippage % + base fee %."""
    return exit_loss_components(position_size_usd, pool_liquidity_usd, base_fee_pct,
                                volume_24h, volatility_index, use_ml).total_pct


# --------------------------------------------------------------------------- #
# 2. Stay loss
# --------------------------------------------------------------------------- #
def stay_loss_components(oracle_stale: bool, protocol_concentration_risk: float, depeg_risk: float,
                         oracle_features: Optional[dict] = None,
                         depeg_features: Optional[dict] = None,
                         use_ml: bool = True) -> StayBreakdown:
    """EV components of staying.

    ``oracle_features``: {last_price_update_seconds, price_deviation_pct, historical_volatility}
    ``depeg_features``:  {current_ratio, pool_imbalance_ratio}
    When provided (and ML is healthy) they replace the static oracle / depeg inputs.
    """
    static_oracle = ORACLE_STALE_PROB if oracle_stale else ORACLE_OK_PROB
    static_depeg = depeg_risk * DEPEG_WEIGHT
    oracle, depeg = static_oracle, static_depeg
    anomaly, ml_depeg, used_o, used_d = None, None, False, False

    pipeline = _get_ml() if use_ml else None
    if pipeline is not None:
        if oracle_features:
            try:
                res = pipeline.detect_oracle_anomaly(**oracle_features)
                p_stale = res["predicted_stale_probability"]
                if _is_prob(p_stale) and _is_prob(res["anomaly_score"]):
                    if oracle_stale:                 # a confirmed stale feed keeps the full 25%
                        p_stale = 1.0
                    oracle = ORACLE_OK_PROB + (ORACLE_STALE_PROB - ORACLE_OK_PROB) * p_stale
                    anomaly, used_o = res, True
            except Exception:  # noqa: BLE001
                pass
        if depeg_features:
            try:
                p = pipeline.predict_depeg_probability(**depeg_features)
                if _is_prob(p):
                    ml_depeg, depeg, used_d = float(p), float(p) * DEPEG_WEIGHT, True
            except Exception:  # noqa: BLE001
                pass

    conc = protocol_concentration_risk * CONCENTRATION_WEIGHT
    return StayBreakdown(oracle, conc, depeg, min(1.0, oracle + conc + depeg),
                         static_oracle_pct=static_oracle, static_depeg_pct=static_depeg,
                         oracle_anomaly=anomaly, ml_depeg_probability=ml_depeg,
                         used_ml_oracle=used_o, used_ml_depeg=used_d)


def calculate_stay_loss(oracle_stale: bool, protocol_concentration_risk: float, depeg_risk: float,
                        oracle_features: Optional[dict] = None,
                        depeg_features: Optional[dict] = None, use_ml: bool = True) -> float:
    """EV_stay = oracle failure + concentration exploit + depeg risk."""
    return stay_loss_components(oracle_stale, protocol_concentration_risk, depeg_risk,
                                oracle_features, depeg_features, use_ml).total_pct


# --------------------------------------------------------------------------- #
# 3. Decision
# --------------------------------------------------------------------------- #
def _ml_insights(exit_b: Optional[ExitBreakdown], stay_b: Optional[StayBreakdown]) -> dict:
    ins: dict = {"ml_active": False}
    if exit_b is not None:
        ins["slippage"] = {
            "ml_pct": exit_b.ml_slippage_pct,
            "static_pct": exit_b.static_slippage_pct,
            "delta_pct": (exit_b.ml_slippage_pct - exit_b.static_slippage_pct
                          if exit_b.ml_slippage_pct is not None
                          and exit_b.static_slippage_pct is not None else None),
            "used_ml": exit_b.used_ml,
        }
        ins["ml_active"] |= exit_b.used_ml
    if stay_b is not None:
        a = stay_b.oracle_anomaly or {}
        ins["oracle"] = {
            "anomaly_score": a.get("anomaly_score"),
            "is_anomaly": a.get("is_anomaly"),
            "predicted_stale_probability": a.get("predicted_stale_probability"),
            "static_risk_pct": stay_b.static_oracle_pct,
            "scaled_risk_pct": stay_b.oracle_pct,
            "used_ml": stay_b.used_ml_oracle,
        }
        ins["depeg"] = {
            "ml_probability": stay_b.ml_depeg_probability,
            "static_probability": (stay_b.static_depeg_pct / DEPEG_WEIGHT
                                   if stay_b.static_depeg_pct is not None else None),
            "ml_loss_contribution_pct": stay_b.depeg_pct if stay_b.used_ml_depeg else None,
            "static_loss_contribution_pct": stay_b.static_depeg_pct,
            "used_ml": stay_b.used_ml_depeg,
        }
        ins["ml_active"] |= stay_b.used_ml_oracle or stay_b.used_ml_depeg
    return ins


def evaluate_decision(position_size_usd: float, exit_loss_pct: float, stay_loss_pct: float,
                      exit_breakdown: Optional[ExitBreakdown] = None,
                      stay_breakdown: Optional[StayBreakdown] = None) -> dict:
    """STAY if |EV_stay| < |L_exit| else EXIT (deterministic rule).

    ``net_benefit_usd`` is the expected dollar advantage of the recommended action over
    the alternative (always >= 0). Pass the breakdowns to populate ``ml_insights``.
    """
    exit_usd = abs(exit_loss_pct) * position_size_usd
    stay_usd = abs(stay_loss_pct) * position_size_usd
    stay = abs(stay_loss_pct) < abs(exit_loss_pct)
    decision = "STAY" if stay else "EXIT"
    net_benefit = abs(exit_usd - stay_usd)

    if stay:
        reasoning = (
            f"The expected loss from staying ({stay_loss_pct:.2%}, about ${stay_usd:,.0f}) is lower than "
            f"the guaranteed cost of exiting ({exit_loss_pct:.2%}, about ${exit_usd:,.0f}). "
            f"Holding preserves roughly ${net_benefit:,.0f} of expected value, so keep the position and "
            f"monitor oracle freshness and pool depth."
        )
    else:
        reasoning = (
            f"The expected loss from staying ({stay_loss_pct:.2%}, about ${stay_usd:,.0f}) exceeds "
            f"the guaranteed cost of exiting ({exit_loss_pct:.2%}, about ${exit_usd:,.0f}). "
            f"Exiting now saves roughly ${net_benefit:,.0f} of expected value, so unwind before "
            f"dependency risk is realised."
        )
    return {
        "decision": decision,
        "net_benefit_usd": net_benefit,
        "reasoning": reasoning,
        "exit_loss_usd": exit_usd,
        "stay_loss_usd": stay_usd,
        "ml_insights": _ml_insights(exit_breakdown, stay_breakdown),
    }


# --------------------------------------------------------------------------- #
# Layer-by-layer exposure table (for the dashboard)
# --------------------------------------------------------------------------- #
def layer_risk_table(nodes, stay: StayBreakdown, exit_: ExitBreakdown) -> list:
    """Map the EV components onto the 5 dependency layers."""
    rows = []
    for n in nodes:
        if n.type == "Yield":
            risk = stay.depeg_pct
            note = "Asset depeg probability (ML) x 0.8" if stay.used_ml_depeg else "Asset depeg risk x 0.8"
        elif n.type == "Oracle":
            risk = stay.oracle_pct
            note = ("Oracle failure scaled by ML stale probability" if stay.used_ml_oracle
                    else "Oracle failure (25% if stale, else 0.2%)")
        elif n.type == "Lending":
            risk, note = stay.concentration_pct, "TVL concentration exploit risk x 0.1"
        elif n.type == "AMM":
            risk, note = None, f"Drives exit slippage ({exit_.slippage_pct:.2%}), not EV of staying"
        else:
            risk, note = None, "Base layer — not modelled"
        rows.append({
            "Layer": f"L{n.layer} {n.type}",
            "Dependency": n.label,
            "Health": n.health_status.upper(),
            "Risk contribution": f"{risk:.2%}" if risk is not None else "—",
            "Basis": note,
        })
    return rows
