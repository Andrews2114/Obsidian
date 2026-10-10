"""Regression tests: only a dependency of the exit route may block the exit.

Toy graph:  chain -> yield -> position          (capital / backing)
            chain -> pool -> router -> position  (exit route)
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import impact  # noqa: E402


def _scenario():
    def node(key, capital=(), exit_=()):
        return {"node_id": key, "label": key.title(), "type": "X",
                "capital_dependencies": capital, "exit_dependencies": exit_,
                "monitor_dependencies": ()}
    return {"dependencies": [
        node("chain"),
        node("yield", capital=("chain",)),
        node("pool", exit_=("chain",)),
        node("router", exit_=("pool",)),
        node("position", capital=("yield",), exit_=("router",)),
    ]}


def _evaluate(target, confirmed, exit_usd=1_000.0, severity=0.5):
    return impact.evaluate(_scenario(), 100_000, 1_000_000, target, 1.0, severity, 7,
                           exit_usd, incident_confirmed=confirmed)


def test_backing_loss_does_not_block_exit_route():
    res = impact.propagate(_scenario(), "yield", 0.5)
    assert res["loss_fraction"] == 0.5
    assert res["exit_dependency_affected"] is False


def test_confirmed_backing_failure_is_exit_not_review():
    res = _evaluate("yield", confirmed=True)
    assert res["decision"] == "EXIT"          # expected loss $50k > $1k exit cost
    assert res["review_reasons"] == []


def test_exit_dependency_failure_still_blocks_route():
    for target in ("pool", "router"):
        res = impact.propagate(_scenario(), target, 0.5)
        assert res["exit_dependency_affected"] is True


def test_confirmed_exit_route_failure_requires_review():
    res = _evaluate("router", confirmed=True)
    assert res["decision"] == "REVIEW"
    assert any("exit route" in r for r in res["review_reasons"])


def test_assumed_exit_route_failure_does_not_force_review():
    # A hypothetical (unconfirmed) event leaves today's route available.
    assert _evaluate("router", confirmed=False)["decision"] == "STAY"   # no backing loss


def test_chain_failure_reaches_both_backing_and_exit_route():
    res = impact.propagate(_scenario(), "chain", 0.4)
    assert res["loss_fraction"] == pytest.approx(0.4)
    assert res["exit_dependency_affected"] is True