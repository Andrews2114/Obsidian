"""5-layer dependency graph construction + streamlit-flow conversion."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Optional, Union

from . import fetcher, mock_data

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

HEALTH_COLORS = {"healthy": "#d4edda", "warning": "#fff3cd", "critical": "#f8d7da"}
HEALTH_BORDERS = {"healthy": "#28a745", "warning": "#e0a800", "critical": "#dc3545"}


@dataclass
class DependencyNode:
    node_id: str
    label: str
    layer: int
    type: str            # Settlement | Yield | Oracle | AMM | Lending
    health_status: str   # healthy | warning | critical
    details: str


# --------------------------------------------------------------------------- #
# Scenario loading (preset or custom address, optional live enrichment)
# --------------------------------------------------------------------------- #
def _enrich_live(sc: dict) -> dict:
    """Overlay live RPC / API data on a scenario; silently keeps mock values on failure."""
    sources = []

    if sc.get("oracle_address"):
        r = fetcher.get_chainlink_price(sc["oracle_address"], fallback_key=sc["fallback_key"])
        sc["oracle_stale_hours"] = r["staleness_hours"]
        sources.append(f"oracle:{r['source']}")

    if sc.get("token_address"):
        r = fetcher.get_token_liquidity(sc["token_address"], fallback_key=sc["fallback_key"])
        sc["pool_liquidity_usd"] = r["top_pair_liquidity_usd"]
        sources.append(f"liquidity:{r['source']}")

    lending_tvl = None
    for dep_type, slug in sc.get("tvl_slugs", {}).items():
        r = fetcher.get_protocol_tvl(slug)
        sources.append(f"tvl[{slug}]:{r['source']}")
        for dep in sc["dependencies"]:
            if dep["type"] == dep_type and r["tvl_usd"]:
                dep["details"] += f" TVL ~ ${r['tvl_usd'] / 1e6:,.0f}M ({r['source']})."
        if dep_type == "Lending" and r["tvl_usd"]:
            lending_tvl = r["tvl_usd"]

    if lending_tvl:
        # Heuristic: smaller venues are more concentrated. 0 at >= $2B TVL, floor at 1%.
        sc["protocol_concentration_risk"] = max(0.01, min(1.0, (2e9 - lending_tvl) / 2e9) * 0.5)

    sc["data_source"] = "live: " + ", ".join(sources) if sources else "mock"
    return sc


def get_scenario(position_address_or_preset: str, live: bool = False) -> dict:
    key = position_address_or_preset.strip()
    key = mock_data.PRESET_LABELS.get(key, key)

    if key in mock_data.SCENARIOS:
        sc = copy.deepcopy(mock_data.SCENARIOS[key])
        sc["data_source"] = "mock"
        return _enrich_live(sc) if live else sc

    if ADDRESS_RE.match(key):
        # Custom contract: start from the Pendle template, then enrich with live data.
        sc = copy.deepcopy(mock_data.SCENARIOS["pendle_pt_eeth"])
        sc["key"] = key
        sc["name"] = f"Custom position {key[:6]}…{key[-4:]}"
        sc["token_address"] = key
        sc["data_source"] = "mock-template"
        return _enrich_live(sc)

    raise ValueError(f"Unknown preset or invalid address: {position_address_or_preset!r}")


# --------------------------------------------------------------------------- #
# Health rules (re-evaluated under stress)
# --------------------------------------------------------------------------- #
def _oracle_health(stale_hours: float) -> str:
    if stale_hours >= 2.0:
        return "critical"
    return "warning" if stale_hours >= 1.0 else "healthy"


def _amm_health(position_usd: float, liquidity_usd: float) -> str:
    if liquidity_usd <= 0:
        return "critical"
    ratio = position_usd / liquidity_usd
    if ratio > 0.4:
        return "critical"
    return "warning" if ratio > 0.2 else "healthy"


# --------------------------------------------------------------------------- #
# Tree construction
# --------------------------------------------------------------------------- #
def build_dependency_tree(position_address_or_preset: Union[str, dict],
                          position_size_usd: Optional[float] = None,
                          oracle_stale_hours: Optional[float] = None,
                          liquidity_drop_pct: float = 0.0) -> list:
    """Return the 5 DependencyNodes of a position (Layer 1 → Layer 5).

    Oracle and AMM node health is recomputed from the stress inputs; other
    layers keep their baseline health from the scenario.
    """
    sc = (position_address_or_preset if isinstance(position_address_or_preset, dict)
          else get_scenario(position_address_or_preset))
    size = position_size_usd if position_size_usd is not None else sc["position_value_usd"]
    stale = sc["oracle_stale_hours"] if oracle_stale_hours is None else oracle_stale_hours
    eff_liq = sc["pool_liquidity_usd"] * (1 - liquidity_drop_pct / 100)

    nodes = []
    for d in sc["dependencies"]:
        health, details = d["health_status"], d["details"]
        if d["type"] == "Oracle":
            health = _oracle_health(stale)
            details += f" Staleness: {stale:.1f}h."
        elif d["type"] == "AMM":
            health = _amm_health(size, eff_liq)
            details += f" Effective depth: ${eff_liq:,.0f}."
        nodes.append(DependencyNode(f"L{d['layer']}", d["label"], d["layer"], d["type"], health, details))
    return sorted(nodes, key=lambda n: n.layer)


# --------------------------------------------------------------------------- #
# streamlit-flow conversion
# --------------------------------------------------------------------------- #
def to_flow_elements(nodes: list):
    """Convert DependencyNodes into StreamlitFlowNode / StreamlitFlowEdge lists."""
    from streamlit_flow.elements import StreamlitFlowEdge, StreamlitFlowNode

    flow_nodes, flow_edges = [], []
    for n in nodes:
        text = f"L{n.layer} · {n.type}\n{n.label}"
        style = {
            "background": HEALTH_COLORS[n.health_status],
            "border": f"2px solid {HEALTH_BORDERS[n.health_status]}",
            "color": "#212529",
            "borderRadius": "8px",
            "width": 280,
            "fontSize": "13px",
            "padding": "6px",
            "textAlign": "center",
        }
        # 'label' (older component versions) and 'content' (newer) both supplied.
        flow_nodes.append(StreamlitFlowNode(
            id=n.node_id, pos=(0, (n.layer - 1) * 120),
            data={"label": text, "content": text}, style=style))

    for a, b in zip(nodes, nodes[1:]):
        flow_edges.append(StreamlitFlowEdge(id=f"{a.node_id}-{b.node_id}",
                                            source=a.node_id, target=b.node_id, animated=True))
    return flow_nodes, flow_edges
