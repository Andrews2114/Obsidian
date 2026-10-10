import pytest

from modules import impact
from modules.dependencies import get_scenario


def diamond():
    return {"discovery_complete": True, "dependencies": [
        {"node_id": "chain", "label": "Chain", "capital_dependencies": ()},
        {"node_id": "yield", "label": "Yield", "capital_dependencies": ("chain",)},
        {"node_id": "sy", "label": "SY", "capital_dependencies": ("yield",)},
        {"node_id": "pt", "label": "PT", "capital_dependencies": ("yield", "sy")},
        {"node_id": "pool", "label": "Pool", "exit_dependencies": ("chain", "pt")},
        {"node_id": "benchmark", "label": "Price monitor", "monitor_dependencies": ("chain",)},
        {"node_id": "position", "label": "Holding", "capital_dependencies": ("pt",),
         "exit_dependencies": ("pool",), "monitor_dependencies": ("benchmark",)},
    ]}


def test_shared_failure_is_counted_once_and_weighted_by_portfolio_nav():
    r = impact.evaluate(diamond(), 100_000, 1_000_000, "yield", .1, .3, 7, 1_100)
    assert r["conditional_loss_usd"] == 30_000  # Two paths must not produce $60k.
    assert r["stay_loss_usd"] == 3_000
    assert r["conditional_portfolio_loss_pct"] == .03
    assert r["portfolio_loss_pct"] == .003
    assert r["exit_portfolio_pct"] == .0011
    assert r["decision"] == "EXIT"
    assert r["break_even_probability"] == pytest.approx(1100 / 30_000)
    assert r["path"][0] == "yield" and r["path"][-1] == "position"


def test_pool_failure_blocks_exit_without_inventing_loss_of_pt_backing():
    r = impact.evaluate(diamond(), 100_000, 1_000_000, "pool", 1, 1, 7, 1_100,
                        incident_confirmed=True)
    assert r["stay_loss_usd"] == 0
    assert r["exit_dependency_affected"]
    assert r["decision"] == "REVIEW"
    outage = impact.evaluate(diamond(), 100_000, 1_000_000, "pool", 1, 0, 7, 1_100,
                             incident_kind="Technical failure", incident_confirmed=True)
    assert outage["decision"] == "REVIEW" and outage["stay_loss_usd"] == 0


def test_monitor_feed_has_no_economic_loss_path():
    r = impact.evaluate(diamond(), 100_000, 1_000_000, "benchmark", 1, 1, 7, 1_100)
    assert r["stay_loss_usd"] == 0
    assert not r["exit_dependency_affected"]
    assert r["path"] == []
    assert r["break_even_probability"] is None


def test_future_failure_does_not_automatically_block_todays_exit():
    r = impact.evaluate(diamond(), 100_000, 1_000_000, "chain", .5, .8, 7, 1_100)
    assert r["decision"] == "EXIT"
    active = impact.evaluate(diamond(), 100_000, 1_000_000, "chain", .5, .8, 7, 1_100,
                             incident_confirmed=True)
    assert active["decision"] == "REVIEW"


@pytest.mark.parametrize("complete,cost", [(False, 100), (True, None)])
def test_incomplete_discovery_or_quote_requires_review(complete, cost):
    sc = diamond()
    sc["discovery_complete"] = complete
    r = impact.evaluate(sc, 100_000, 1_000_000, "yield", 1, .8, 7, cost)
    assert r["decision"] == "REVIEW"
    assert r["net_benefit_usd"] is None


@pytest.mark.parametrize("probability", [-1, 1.01, float("nan"), float("inf")])
def test_invalid_probabilities_are_rejected(probability):
    with pytest.raises(ValueError):
        impact.evaluate(diamond(), 100_000, 1_000_000, "yield", probability, .8, 7, 100)


def test_graph_validation_rejects_cycles_and_missing_edges():
    sc = diamond()
    sc["dependencies"][0]["capital_dependencies"] = ("position",)
    with pytest.raises(ValueError, match="cycle"):
        impact.propagate(sc, "yield", .3)
    sc["dependencies"][0]["capital_dependencies"] = ("missing",)
    with pytest.raises(ValueError, match="Unresolved"):
        impact.propagate(sc, "yield", .3)


def test_tie_does_not_recommend_unnecessary_exit_and_nav_includes_position():
    r = impact.evaluate(diamond(), 100_000, 1_000_000, "yield", .1, .3, 7, 3_000)
    assert r["decision"] == "STAY"
    with pytest.raises(ValueError, match="portfolio NAV"):
        impact.evaluate(diamond(), 100_000, 99_999, "yield", .1, .3, 7, 3_000)


def test_reference_oracle_only_reaches_lending_not_a_fictitious_amm_consumer():
    sc = get_scenario("pendle_pt_eeth")
    r = impact.propagate(sc, "L3", .5)
    assert r["path"] == ["L3", "L5", "position"]
    assert r["node_losses"]["L4"] == 0
