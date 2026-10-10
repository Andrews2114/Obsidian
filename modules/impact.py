"""One-event, finite-horizon scenario model with typed dependency propagation.

Probabilities and conditional haircuts are supplied by the analyst, not inferred
from TVL, health colours, stale timestamps or an alert threshold. The model is
for an unlevered holding; it does not claim to model loan liquidation cascades.
"""
import math

import networkx as nx


def _finite(value, name, minimum=0, maximum=None):
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"Invalid {name}")


def graph(scenario, channel="capital"):
    nodes = {n["node_id"]: n for n in scenario["dependencies"]}
    if len(nodes) != len(scenario["dependencies"]) or "position" not in nodes:
        raise ValueError("Graph needs unique IDs and a position node")
    g = nx.DiGraph()
    g.add_nodes_from(nodes)
    for key, node in nodes.items():
        for parent in node.get(channel + "_dependencies", ()):
            if parent not in nodes:
                raise ValueError(f"Unresolved graph edge: {parent} → {key}")
            g.add_edge(parent, key)
    if not nx.is_directed_acyclic_graph(g):
        raise ValueError("Dependency graph contains a cycle")
    return g, nodes


def propagate(scenario, target, severity):
    """A conditional loss passes through capital edges once per node.

    The haircut applies to the whole exposure along verified backing links.
    Taking the maximum through converging branches avoids counting the same
    event twice. Separate independent incidents are deliberately not summed.
    """
    _finite(severity, "conditional loss severity", maximum=1)
    capital, nodes = graph(scenario)
    if target not in nodes:
        raise ValueError("Incident dependency is not in the graph")
    loss = {key: 0.0 for key in nodes}
    paths = {target: [target]}
    loss[target] = severity
    for key in nx.topological_sort(capital):
        if key == target:
            continue
        parents = list(capital.predecessors(key))
        if parents:
            parent = max(parents, key=lambda p: loss[p])
            if loss[parent] > 0:
                loss[key] = loss[parent]
                paths[key] = paths[parent] + [key]
    affected = {target} | {key for key, value in loss.items() if value > 0}
    # An incident can obstruct an exit even when it does not impair backing.
    exits, _ = graph(scenario, "exit")
    blocked = any(nx.has_path(exits, key, "position") for key in affected)
    path = paths.get("position", [])
    return {"loss_fraction": loss["position"], "node_losses": loss,
            "affected_nodes": affected, "exit_dependency_affected": blocked,
            "path": path, "path_labels": [nodes[key]["label"] for key in path]}


def evaluate(scenario, position_usd, portfolio_usd, target, probability, severity,
             horizon_days, exit_usd=None, *, exit_source="unavailable",
             incident_kind="Security incident", incident_confirmed=False):
    """Compare future incremental conditional loss with available exit cost.

    Severity describes additional loss from the current mark, not an already
    realised price move. probability applies to exactly the displayed horizon.
    A confirmed incident that blocks the modelled route prevents an EXIT badge.
    """
    _finite(position_usd, "position value", minimum=0.01)
    _finite(portfolio_usd, "portfolio NAV", minimum=position_usd)
    _finite(probability, "event probability", maximum=1)
    _finite(horizon_days, "horizon", minimum=1)
    if exit_usd is not None:
        _finite(exit_usd, "exit cost")
    impact = propagate(scenario, target, severity)
    conditional = impact["loss_fraction"] * position_usd
    expected = probability * conditional
    review = []
    if not scenario.get("discovery_complete", True):
        review.append("Discovery is incomplete: " + str(scenario.get("discovery_error", "unresolved dependencies")))
    if exit_usd is None:
        review.append("No usable exit cost is available.")
    if incident_confirmed and impact["exit_dependency_affected"]:
        review.append("The active incident affects the modelled exit route; obtain a new usable quote.")
    decision = "REVIEW" if review else ("EXIT" if expected > exit_usd else "STAY")
    break_even = exit_usd / conditional if exit_usd is not None and conditional > 0 else None
    trace = (" → ".join(impact["path_labels"]) if impact["path"] else
             "No capital dependency path reaches this holding; this event does not imply a direct backing loss.")
    reasoning = (f"{incident_kind} scenario over {horizon_days:g} days: "
        f"{probability:.2%} assumed probability × ${conditional:,.0f} conditional additional loss "
        f"= ${expected:,.0f} expected loss ({expected / portfolio_usd:.2%} of portfolio NAV). ")
    if exit_usd is not None:
        reasoning += f"Exit cost is ${exit_usd:,.0f} ({exit_usd / portfolio_usd:.2%} of NAV; {exit_source}). "
    reasoning += " ".join(review) if review else f"{decision} has the lower modelled incremental cost for this single scenario."
    return {**impact, "decision": decision, "reasoning": reasoning,
            "stay_loss_usd": expected, "conditional_loss_usd": conditional,
            "stay_loss_pct": expected / position_usd,
            "portfolio_loss_pct": expected / portfolio_usd,
            "conditional_portfolio_loss_pct": conditional / portfolio_usd,
            "exit_loss_usd": exit_usd, "exit_portfolio_pct": exit_usd / portfolio_usd if exit_usd is not None else None,
            "break_even_probability": break_even, "trace": trace,
            "net_benefit_usd": abs(expected - exit_usd) if decision != "REVIEW" else None,
            "review_reasons": review}
