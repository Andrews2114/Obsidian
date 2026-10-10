"""Expected-value (EV) risk model: exit slippage vs. expected loss of staying.

All percentages are fractions (0.015 == 1.5%).
"""
from __future__ import annotations

from dataclasses import dataclass

STALE_THRESHOLD_HOURS = 2.0
ORACLE_STALE_PROB = 0.25
ORACLE_OK_PROB = 0.002
CONCENTRATION_WEIGHT = 0.1
DEPEG_WEIGHT = 0.8


@dataclass
class ExitBreakdown:
    slippage_pct: float
    fee_pct: float
    total_pct: float


@dataclass
class StayBreakdown:
    oracle_pct: float
    concentration_pct: float
    depeg_pct: float
    total_pct: float


def is_oracle_stale(staleness_hours: float) -> bool:
    return staleness_hours >= STALE_THRESHOLD_HOURS


# --------------------------------------------------------------------------- #
# 1. Exit loss
# --------------------------------------------------------------------------- #
def exit_loss_components(position_size_usd: float, pool_liquidity_usd: float,
                         base_fee_pct: float = 0.005) -> ExitBreakdown:
    """Slippage = (size / liquidity)^2 * 0.5, capped at 100%."""
    if pool_liquidity_usd <= 0:
        return ExitBreakdown(1.0, base_fee_pct, 1.0)
    slippage = min(1.0, (position_size_usd / pool_liquidity_usd) ** 2 * 0.5)
    return ExitBreakdown(slippage, base_fee_pct, min(1.0, slippage + base_fee_pct))


def calculate_exit_loss(position_size_usd: float, pool_liquidity_usd: float,
                        base_fee_pct: float = 0.005) -> float:
    """Total exit loss L_exit = slippage % + base fee %."""
    return exit_loss_components(position_size_usd, pool_liquidity_usd, base_fee_pct).total_pct


# --------------------------------------------------------------------------- #
# 2. Stay loss
# --------------------------------------------------------------------------- #
def stay_loss_components(oracle_stale: bool, protocol_concentration_risk: float,
                         depeg_risk: float) -> StayBreakdown:
    oracle = ORACLE_STALE_PROB if oracle_stale else ORACLE_OK_PROB
    conc = protocol_concentration_risk * CONCENTRATION_WEIGHT
    depeg = depeg_risk * DEPEG_WEIGHT
    return StayBreakdown(oracle, conc, depeg, min(1.0, oracle + conc + depeg))


def calculate_stay_loss(oracle_stale: bool, protocol_concentration_risk: float,
                        depeg_risk: float) -> float:
    """EV_stay = oracle failure + concentration exploit + depeg risk."""
    return stay_loss_components(oracle_stale, protocol_concentration_risk, depeg_risk).total_pct


# --------------------------------------------------------------------------- #
# 3. Decision
# --------------------------------------------------------------------------- #
def evaluate_decision(position_size_usd: float, exit_loss_pct: float, stay_loss_pct: float) -> dict:
    """STAY if |EV_stay| < |L_exit| else EXIT.

    ``net_benefit_usd`` is the expected dollar advantage of the recommended
    action over the alternative (always >= 0).
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
    }


# --------------------------------------------------------------------------- #
# Layer-by-layer exposure table (for the dashboard)
# --------------------------------------------------------------------------- #
def layer_risk_table(nodes, stay: StayBreakdown, exit_: ExitBreakdown) -> list:
    """Map the EV components onto the 5 dependency layers."""
    rows = []
    for n in nodes:
        if n.type == "Yield":
            risk, note = stay.depeg_pct, "Asset depeg risk x 0.8"
        elif n.type == "Oracle":
            risk, note = stay.oracle_pct, "Oracle failure (25% if stale, else 0.2%)"
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
