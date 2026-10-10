"""Protocol-aware and reference graph loading plus streamlit-flow conversion."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Optional, Union

from . import fetcher, mock_data
from .chains import get_chain
from .incidents import exceeds_deviation_threshold

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

HEALTH_COLORS = {"healthy": "#d4edda", "warning": "#fff3cd", "critical": "#f8d7da", "unknown": "#e9ecef"}
HEALTH_BORDERS = {"healthy": "#28a745", "warning": "#e0a800", "critical": "#dc3545", "unknown": "#6c757d"}


@dataclass
class DependencyNode:
    node_id: str
    label: str
    layer: int
    type: str            # Settlement | Yield | Oracle | AMM | Lending
    health_status: str   # healthy | warning | critical
    details: str
    depends_on: tuple[str, ...] | None = None
    dependency_roles: dict[str, str] | None = None


# --------------------------------------------------------------------------- #
# Scenario loading (preset or custom address, optional live enrichment)
# --------------------------------------------------------------------------- #
def _enrich_live(sc: dict) -> dict:
    """Enrich an explicitly predefined reference graph; retain source labels."""
    sources = []

    if sc.get("oracle_address"):
        r = fetcher.get_chainlink_price(sc["oracle_address"], fallback_key=sc["fallback_key"],
                                       chain=sc.get("chain", "ethereum"))
        sc["oracle_stale_hours"] = r["staleness_hours"]
        sc["oracle_price"] = r["price"]
        sources.append(f"oracle:{r['source']}")

    if sc.get("token_address"):
        r = fetcher.get_token_liquidity(sc["token_address"], fallback_key=sc["fallback_key"],
                                      chain=sc.get("chain", "ethereum"))
        if r["source"] == "live":
            sc["pool_liquidity_usd"] = r["top_pair_liquidity_usd"]
        sources.append(f"liquidity:{r['source']}")

    for dep_type, slug in sc.get("tvl_slugs", {}).items():
        r = fetcher.get_protocol_tvl(slug)
        sources.append(f"tvl[{slug}]:{r['source']}")
        for dep in sc["dependencies"]:
            if dep["type"] == dep_type and r["tvl_usd"]:
                dep["details"] += f" TVL ~ ${r['tvl_usd'] / 1e6:,.0f}M ({r['source']})."

    sc["data_source"] = "live: " + ", ".join(sources) if sources else "mock"
    return sc


def get_scenario(position_address_or_preset: str, live: bool = False, *,
                 chain: str = "ethereum", oracle_address: str | None = None,
                 adapter: str = "token") -> dict:
    get_chain(chain)
    key = position_address_or_preset.strip()
    key = mock_data.PRESET_LABELS.get(key, key)

    if key in mock_data.SCENARIOS:
        sc = copy.deepcopy(mock_data.SCENARIOS[key])
        sc["data_source"] = "mock"
        sc["adapter"] = "reference"
        # Reference edges describe different economic channels, not a made-up linear chain.
        reference = {"Settlement": ((), ()), "Yield": (("L1",), ()),
                     "Oracle": (("L1",), ()), "AMM": ((), ("L1", "L2")),
                     "Lending": (("L2", "L3"), ("L4",))}
        for dep in sc["dependencies"]:
            capital, exits = reference[dep["type"]]
            dep.update(node_id=f"L{dep['layer']}", capital_dependencies=capital,
                       exit_dependencies=exits, monitor_dependencies=(), depends_on=capital + exits)
        sc["dependencies"].append({"node_id": "position", "layer": 6, "type": "Position",
            "label": "Reference collateral holding", "health_status": "unknown",
            "details": "Demonstration only; collateral, debt and venue relationships are predefined.",
            "capital_dependencies": ("L5",), "exit_dependencies": ("L4",),
            "monitor_dependencies": (), "depends_on": ("L5", "L4")})
        return _enrich_live(sc) if live else sc

    if ADDRESS_RE.match(key):
        from .discovery import discover_token, discover_pendle
        if oracle_address and not ADDRESS_RE.fullmatch(oracle_address):
            raise ValueError("Invalid oracle address")
        if adapter not in ("token", "pendle_pt"):
            raise ValueError("Unsupported discovery adapter")
        discover = discover_pendle if adapter == "pendle_pt" else discover_token
        return discover(key, chain, live=live, oracle_address=oracle_address)

    raise ValueError(f"Unknown preset or invalid address: {position_address_or_preset!r}")


# --------------------------------------------------------------------------- #
# Health rules (re-evaluated under stress)
# --------------------------------------------------------------------------- #
def _oracle_health(stale_hours: float, deviation_pct: float = 0.0) -> str:
    if stale_hours >= 2.0 or exceeds_deviation_threshold(deviation_pct):
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
                          liquidity_drop_pct: float = 0.0,
                          oracle_deviation_pct: float = 0.0) -> list:
    """Return dependency nodes for the selected adapter's graph.

    Oracle and AMM node health is recomputed from the stress inputs; other
    layers keep their baseline health from the scenario. Discovery evidence
    and dependency health are separate; verified links can have unknown health.
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
            health = (_oracle_health(stale, oracle_deviation_pct)
                      if health != "unknown" or stale > 0 or oracle_deviation_pct else "unknown")
            details += f" Staleness: {stale:.1f}h. Incident deviation: {oracle_deviation_pct:+.2f}%."
        elif d["type"] == "AMM":
            health = (_amm_health(size, eff_liq) if sc.get("discovery_complete", True)
                      else "unknown")
            details += f" Effective depth: ${eff_liq:,.0f}."
        nodes.append(DependencyNode(d.get("node_id", f"L{d['layer']}"), d["label"], d["layer"],
                                    d["type"], health, details,
                                    tuple(d["depends_on"]) if "depends_on" in d else None,
                                    {parent: role for role in ("capital", "exit", "monitor")
                                     for parent in d.get(role + "_dependencies", ())}))
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

    for index, node in enumerate(nodes):
        parents = node.depends_on
        if parents is None:
            parents = (nodes[index - 1].node_id,) if index else ()
        for parent in parents:
            role = (node.dependency_roles or {}).get(parent, "capital")
            flow_edges.append(StreamlitFlowEdge(id=f"{parent}-{node.node_id}",
                source=parent, target=node.node_id, animated=role == "capital", label=role,
                style={"stroke": {"capital": "#dc3545", "exit": "#337ab7", "monitor": "#6c757d"}[role],
                       "strokeDasharray": "0" if role == "capital" else "5 5"}))
    return flow_nodes, flow_edges
