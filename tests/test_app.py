from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app.py"


def slider(app, label):
    return next(widget for widget in app.slider if widget.label == label)


def button(app, label):
    return next(widget for widget in app.button if widget.label == label)


def new_app():
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not app.exception
    return app


def test_staleness_and_liquidity_sliders_update_metrics_and_badge():
    app = new_app()
    assert any("STAY" in item.value for item in app.success)
    slider(app, "Simulate AMM Liquidity Drop (%)").set_value(30).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == "Estimated Exit Cost").value == "$2,632"
    slider(app, "Simulate Oracle Staleness (Hours)").set_value(2.0).run()
    assert any("EXIT" in item.value for item in app.error)
    slider(app, "Simulate AMM Liquidity Drop (%)").set_value(100).run()
    assert any("REVIEW" in item.value for item in app.warning)
    assert next(m for m in app.metric if m.label == "Estimated Exit Cost").value == "Unavailable"


@pytest.mark.parametrize("move,alert", [(3.0, False), (-3.0, False), (3.5, True), (-3.5, True)])
def test_simulated_logs_trigger_popup_only_above_three_percent(move, alert):
    app = new_app()
    slider(app, "Simulated oracle price change (%)").set_value(move).run()
    button(app, "Publish simulated oracle log").click().run()
    assert not app.exception
    assert bool(app.session_state.oracle_monitor.incident) is alert
    assert any("Oracle moved" in item.value for item in app.warning) is alert
    if alert:
        assert any("EXIT" in item.value for item in app.error)
        button(app, "Acknowledge incident").click().run()
        assert app.session_state.oracle_monitor.incident
        assert not any("Oracle moved" in item.value for item in app.warning)
        button(app, "Reset monitor / clear incident").click().run()
        assert app.session_state.oracle_monitor.incident is None
        assert any("STAY" in item.value for item in app.success)


@patch("modules.fetcher.get_chainlink_price")
def test_live_polling_processes_new_round_and_deduplicates(mock_price):
    import time
    now = time.time()
    mock_price.return_value = {"source": "on-chain", "price": 100, "updated_at": now,
                              "round_id": "1", "staleness_hours": 0.0}
    app = new_app()
    next(r for r in app.radio if r.label == "Event source").set_value("Live oracle polling").run()
    assert not app.exception
    assert app.session_state.oracle_monitor.previous_price == 100
    mock_price.return_value = {"source": "on-chain", "price": 95, "updated_at": now + 1,
                              "round_id": "2", "staleness_hours": 0.0}
    app.run()
    assert not app.exception
    assert app.session_state.oracle_monitor.incident
    assert any("Oracle moved" in item.value for item in app.warning)
    app.run()
    assert len(app.session_state.oracle_monitor.history) == 2


@patch("modules.fetcher.get_chainlink_price", return_value={"source": "unavailable", "price": None, "error": "offline"})
def test_live_failure_shows_review_without_triggering_incident(mock_price):
    app = new_app()
    next(r for r in app.radio if r.label == "Event source").set_value("Live oracle polling").run()
    assert not app.exception
    assert app.session_state.oracle_monitor.incident is None
    assert any("REVIEW" in item.value for item in app.warning)


@pytest.mark.parametrize("chain", ["arbitrum", "optimism"])
@patch("modules.fetcher.get_token_liquidity")
def test_chain_selection_discovers_l2_and_resets_incident_scope(mock_pool, chain):
    from modules.chains import CHAINS
    import time

    def pool(address, **kwargs):
        return {"source": "live", "top_pair_liquidity_usd": 700_000,
                "pair_address": "0x" + "2" * 40, "top_dex": "uniswap",
                "base_token": {"address": address, "symbol": "WETH"},
                "quote_token": {"address": "0x" + "3" * 40, "symbol": "USDC"},
                "observed_at": time.time()}

    mock_pool.side_effect = pool
    app = new_app()
    slider(app, "Simulated oracle price change (%)").set_value(-5.0).run()
    button(app, "Publish simulated oracle log").click().run()
    assert app.session_state.oracle_monitor.incident
    next(r for r in app.radio if r.label == "Position source").set_value("Discover Token Dependencies").run()
    next(s for s in app.selectbox if s.label == "Blockchain").set_value(chain).run()
    assert not app.exception
    assert app.session_state.oracle_monitor.incident is None
    assert chain in app.session_state.monitor_scope
    assert any(CHAINS[chain].name in item.value for item in app.caption)


def test_active_failure_and_portfolio_scaling_change_recommendation():
    app = new_app()
    probability = next(w for w in app.number_input if w.label == "Failure probability over this horizon (%)")
    probability.set_value(10).run()
    slider(app, "Additional loss if failure happens (%)").set_value(30).run()
    assert next(m for m in app.metric if m.label == "Modelled Loss of Staying").value == "$3,000"
    assert any("EXIT" in item.value for item in app.error)
    next(c for c in app.checkbox if c.label == "Selected failure is already active").check().run()
    assert not app.exception
    assert any("REVIEW" in item.value for item in app.warning)


@patch("modules.fetcher.get_token_liquidity")
def test_benchmark_alert_does_not_invent_a_loss_to_native_weth(mock_pool):
    from modules.chains import CHAINS
    import time
    address = CHAINS["ethereum"].wrapped_eth
    mock_pool.return_value = {"source": "live", "top_pair_liquidity_usd": 700_000,
        "pair_address": "0x" + "2" * 40, "top_dex": "uniswap",
        "base_token": {"address": address, "symbol": "WETH"},
        "quote_token": {"address": "0x" + "3" * 40, "symbol": "USDC"}, "observed_at": time.time()}
    app = new_app()
    next(r for r in app.radio if r.label == "Position source").set_value("Discover Token Dependencies").run()
    before = next(m for m in app.metric if m.label == "Modelled Loss of Staying").value
    slider(app, "Simulated oracle price change (%)").set_value(-5.0).run()
    button(app, "Publish simulated oracle log").click().run()
    assert app.session_state.oracle_monitor.incident
    assert next(m for m in app.metric if m.label == "Modelled Loss of Staying").value == before
    assert any("no proven capital-loss path" in item.value for item in app.caption)


@patch("modules.pendle.exit_quote")
@patch("modules.pendle.snapshot")
def test_pendle_ui_discovery_quote_failure_and_graph_state_persistence(mock_snapshot, mock_quote):
    from datetime import datetime, timezone
    market_address, pt, sy, yt, underlying, accounting = ["0x" + digit * 40 for digit in "123456"]
    def token(address, symbol):
        return {"address": address, "symbol": symbol, "decimals": 18, "protocol": "Provider",
                "price": {"usd": 2}, "priceUpdatedAt": datetime.now(timezone.utc).isoformat()}
    mock_snapshot.return_value = {"source": "on-chain + Pendle API", "observed_at": 1,
        "evidence": {"block": 123, "exchange_rate": 1.1}, "market": {
        "address": market_address, "chainId": 1, "expiry": "2027-12-30T00:00:00Z",
        "pt": token(pt, "PT"), "sy": token(sy, "SY"), "yt": token(yt, "YT"),
        "underlyingAsset": token(underlying, "Yield asset"), "accountingAsset": token(accounting, "Underlying"),
        "liquidity": {"usd": 5_000_000}}}
    mock_quote.return_value = {"source": "Pendle quote preview", "cost_usd": 1000,
        "observed_at": 1, "router": market_address, "aggregator": "Example",
        "output_token": accounting, "output_price_usd": 1}
    app = new_app()
    next(r for r in app.radio if r.label == "Position source").set_value("Discover Pendle PT Dependencies").run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == "Estimated Exit Cost").value == "$1,050"
    assert any("Yield asset → SY → PT → Standalone PT holding" in item.value for item in app.markdown)
    timestamp = app.session_state.flow_state.timestamp
    app.run()
    assert app.session_state.flow_state.timestamp == timestamp
    slider(app, "Simulate AMM Liquidity Drop (%)").set_value(100).run()
    assert not app.exception
    assert next(m for m in app.metric if m.label == "Estimated Exit Cost").value == "Unavailable"
    assert any("REVIEW" in item.value for item in app.warning)
