import pytest

from modules.incidents import OracleMonitor
from modules.risk_engine import stay_loss_components


def sample(price, event=1, source="simulated", timestamp=None):
    return {"price": price, "round_id": str(event), "updated_at": timestamp or event,
            "source": source}


@pytest.mark.parametrize("price,alert", [(103, False), (97, False), (103.01, True), (96.99, True)])
def test_strict_three_percent_threshold_in_both_directions(price, alert):
    monitor = OracleMonitor("arbitrum:feed:simulation", previous_price=100)
    assert bool(monitor.observe(sample(price))) is alert


def test_duplicates_do_not_realert_and_acknowledgement_does_not_clear_incident():
    monitor = OracleMonitor("ethereum:feed", previous_price=100)
    alert = monitor.observe(sample(95))
    assert alert
    assert monitor.observe(sample(95)) is None
    assert len(monitor.history) == 1
    assert monitor.incident == alert
    monitor.observe(sample(95.1, event=2))
    assert monitor.incident == alert
    monitor.reset()
    assert monitor.incident is None


@pytest.mark.parametrize("source", ["mock", "unavailable", "live"])
def test_failed_or_unverified_reads_cannot_seed_live_baseline(source):
    monitor = OracleMonitor("ethereum:real-feed")
    assert monitor.observe(sample(100, source=source)) is None
    assert monitor.previous_price is None
    assert monitor.observe(sample(80, event=2, source="on-chain")) is None
    assert monitor.previous_price == 80


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), None])
def test_invalid_prices_are_ignored(price):
    monitor = OracleMonitor("feed", previous_price=100)
    assert monitor.observe(sample(price)) is None
    assert monitor.previous_price == 100


def test_older_events_and_separate_chains_do_not_cross_contaminate():
    eth = OracleMonitor("ethereum:feed")
    arb = OracleMonitor("arbitrum:feed")
    eth.observe(sample(100, timestamp=10, source="on-chain"))
    assert eth.observe(sample(50, event=2, timestamp=9, source="on-chain")) is None
    assert eth.previous_price == 100
    assert arb.observe(sample(50, source="on-chain")) is None


def test_deviation_updates_risk_without_double_counting_staleness():
    healthy = stay_loss_components(False, 0.03, 0.00375)
    deviating = stay_loss_components(False, 0.03, 0.00375, -4)
    both = stay_loss_components(True, 0.03, 0.00375, -4)
    assert deviating.total_pct > healthy.total_pct
    assert both.total_pct == deviating.total_pct
