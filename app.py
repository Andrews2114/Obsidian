"""Obsidian: multi-chain dependencies, stress controls and oracle alerts."""
import hashlib
import math
import time
import streamlit as st
from modules import dependencies as deps, fetcher, mock_data, risk_engine, impact, pendle, theme
from modules.chains import CHAINS, get_chain
from modules.incidents import OracleMonitor

st.set_page_config(page_title="Obsidian - DeFi Risk Sentinel", page_icon="🛡️", layout="wide")


def esc(text):
    return text.replace("$", "\\$")


@st.cache_data(ttl=60, show_spinner="Fetching dependencies on the selected chain…")
def load_scenario(key, live, chain="ethereum", oracle="", adapter="token"):
    return deps.get_scenario(key, live=live, chain=chain, oracle_address=oracle or None, adapter=adapter)


@st.cache_data(ttl=30, show_spinner="Getting a read-only PT → USDC exit quote…")
def load_quote(scenario, size, liquidity_drop):
    if liquidity_drop >= 100:
        return {"source": "unavailable", "error": "Simulated route has no liquidity"}
    factor = 1 - liquidity_drop / 100
    quote = pendle.exit_quote(scenario, size / factor)
    if quote.get("cost_usd") is not None:
        quote["cost_usd"] *= factor
    if liquidity_drop:
        quote["source"] = "Liquidity stress proxy based on a live quote" if quote.get("cost_usd") is not None else "unavailable"
        quote["note"] = "Stress proxy: quote a trade enlarged by 1/(1-liquidity drop), then scale cost back down. Assumes uniformly reduced depth; not a simulation of Pendle's exact time-aware curve."
    return quote


def render_flow(nodes, key):
    try:
        from streamlit_flow import streamlit_flow
        from streamlit_flow.layouts import TreeLayout
        from streamlit_flow.state import StreamlitFlowState
        flow_nodes, flow_edges = deps.to_flow_elements(nodes)
    except ImportError:
        st.warning("Install streamlit-flow-component to show the graph; evidence is listed below.")
        return
    # The component sends its layout back to Python. Recreating timestamped
    # state on every rerun causes a frontend/Python update loop.
    if st.session_state.get("flow_model_key") != key:
        st.session_state.flow_model_key = key
        st.session_state.flow_state = StreamlitFlowState(flow_nodes, flow_edges)
    st.session_state.flow_state = streamlit_flow(key, st.session_state.flow_state,
        layout=TreeLayout(direction="down", node_node_spacing=30),
        fit_view=True, height=700, show_minimap=True)


@st.dialog("Oracle incident detected")
def incident_popup(incident, result):
    st.warning(f"Oracle moved {incident['deviation_pct']:+.2f}% from the preceding valid update (>3%).")
    st.write(f"Source: {incident['source']} · Chain: {chain_config.name}")
    st.metric("Recommendation under current assumptions", result["decision"])
    st.markdown(esc(result["reasoning"]))
    st.caption("Acknowledging closes this popup. The incident stays active until the monitor is reset.")
    if st.button("Acknowledge incident", key="ack_incident"):
        st.rerun()


# st.title("Obsidian - DeFi Risk Sentinel")
# st.caption("EPFL Hackathon 2026 | Belem Capital Challenge")

with st.sidebar:
    st.header("Controls")
    mode = st.radio("Position source", ["Preset Reference Positions", "Discover Token Dependencies", "Discover Pendle PT Dependencies"])
    if mode == "Preset Reference Positions":
        preset = st.selectbox("Preset", list(mock_data.PRESET_LABELS))
        live = st.checkbox("Refresh with live RPC / API data", value=False)
        if st.button("Refresh data"):
            load_scenario.clear()
            load_quote.clear()
        scenario = load_scenario(mock_data.PRESET_LABELS[preset], live)
        chain = scenario.get("chain", "ethereum")
        st.caption("Reference graph: relationships are predefined.")
    else:
        chain = st.selectbox("Blockchain", list(CHAINS), format_func=lambda key: CHAINS[key].name)
        config = get_chain(chain)
        adapter = "pendle_pt" if mode == "Discover Pendle PT Dependencies" else "token"
        default_address = pendle.EXAMPLE_MARKET if adapter == "pendle_pt" and chain == "ethereum" else config.wrapped_eth
        address = st.text_input("Pendle market or PT address" if adapter == "pendle_pt" else "Token address",
            value=default_address, key=f"address_{chain}_{adapter}").strip()
        oracle = st.text_input("Optional price feed address", key=f"feed_{chain}",
            help="WETH defaults to this chain's ETH/USD benchmark. Other tokens need a compatible feed.").strip()
        live = st.checkbox("Fetch live pool data", value=True)
        if st.button("Refresh data"):
            load_scenario.clear()
            load_quote.clear()
        if not deps.ADDRESS_RE.fullmatch(address) or (oracle and not deps.ADDRESS_RE.fullmatch(oracle)):
            st.error("Expected an EVM address: 0x followed by 40 hexadecimal characters.")
            st.stop()
        scenario = load_scenario(address, live, chain, oracle, adapter)
        st.caption("Pendle verifies market → PT/SY and SY → yield/accounting asset links on-chain. Token discovery supports native WETH; other backing dependencies require an adapter.")

    chain_config = get_chain(chain)
    st.caption(f"{chain_config.name} · chain ID {chain_config.chain_id}")
    position_size = st.number_input("Position Size ($ USD)", min_value=1_000.0,
                                    value=100_000.0, step=10_000.0, format="%.0f")
    portfolio_nav = st.number_input("Total portfolio NAV ($ USD)", min_value=1_000.0, value=1_000_000.0,
                                   step=100_000.0, format="%.0f")
    if portfolio_nav < position_size:
        st.error("Portfolio NAV must include the entire position value.")
        st.stop()
    scope_key = f"{chain}:{scenario.get('adapter')}:{scenario['key']}:{scenario.get('oracle_address')}"
    theme.section("Failure Scenario Assumptions","...")
    horizon = st.number_input("Decision horizon (days)", min_value=1, max_value=365, value=7)
    dependency_selector_slot = st.empty()
    event_kind = st.selectbox("Incident type", ["Security incident", "Market disruption", "Technical failure"])
    probability = st.number_input("Failure probability over this horizon (%)", min_value=0.0,
        max_value=100.0, value=1.0, step=0.5) / 100
    severity = st.slider("Additional loss if failure happens (%)", 0, 100, 80) / 100
    gas_usd = st.number_input("Gas / execution overhead assumption ($)", min_value=0.0,
                             value=50.0, step=10.0)
    confirmed = st.checkbox("Selected failure is already active", value=False)
    st.caption("Probability and additional loss are your assumptions. Technical outages may have zero backing loss; use severity to model only an assumed impairment. This model handles one event at a time.")
    st.subheader("Interactive Stress Test")
    default_stale = float(min(12.0, math.ceil(scenario["oracle_stale_hours"] * 2) / 2))
    stale_hours = st.slider("Simulate Oracle Staleness (Hours)", 0.0, 12.0, default_stale, 0.5,
                            key=f"stale_{scope_key}")
    liq_drop = st.slider("Simulate AMM Liquidity Drop (%)", 0, 100, 0, key=f"drop_{scope_key}")
    st.caption("The recommendation recalculates whenever a slider changes.")
    ml_enabled = st.checkbox(
        "Show experimental ML indicators",
        value=False,
        help="Displays synthetic-prior model estimates for comparison only. They do not change the scenario decision.",
    )
    with st.expander("Oracle failure assumptions"):
        oracle_probability = st.number_input("Loss-event probability after oracle alert (%)", 0.0, 100.0, 50.0) / 100
        oracle_severity = st.number_input("Additional loss if oracle event happens (%)", 0.0, 100.0, 50.0) / 100
        st.caption("Used only for a graph with an economic oracle dependency. An alert is an observation, not proof of loss. This scenario replaces the selected event; it is not added to it.")
    st.subheader("Oracle Incident Alerts")
    monitor_enabled = st.checkbox("Enable oracle incident alerts", value=True)
    monitor_mode = st.radio("Event source", ["Simulated log subscription", "Live oracle polling"])
    publish = False
    simulated_move = 0.0
    if monitor_mode == "Simulated log subscription":
        simulated_move = st.slider("Simulated oracle price change (%)", -30.0, 30.0, 0.0, 0.5)
        st.caption("The slider sets price relative to the starting simulated price. Alerts compare consecutive published updates.")
        publish = st.button("Publish simulated oracle log", disabled=not monitor_enabled)
    else:
        st.caption("Polls the selected feed every 5 seconds while this browser session is active.")
    reset_monitor = st.button("Reset monitor / clear incident")

# Keep live, simulated, and different chains/feeds in separate event scopes.
monitor_scope = f"{scope_key}:{monitor_mode}"
if st.session_state.get("monitor_scope") != monitor_scope or reset_monitor:
    baseline = scenario.get("oracle_price") or (
        mock_data.MOCK_MARKET_DATA[scenario["fallback_key"]]["oracle"]["price"]
        if mode == "Preset Reference Positions" else 1.0)
    st.session_state.monitor_scope = monitor_scope
    st.session_state.oracle_monitor = OracleMonitor(
        monitor_scope, previous_price=baseline if monitor_mode == "Simulated log subscription" else None)
    st.session_state.simulated_baseline = baseline
    st.session_state.latest_live_sample = None
    st.session_state.live_monitor_error = None
    st.session_state.live_stale = False
    st.session_state.shown_incident = None

monitor = st.session_state.oracle_monitor
if publish:
    sequence = st.session_state.get("simulation_sequence", 0) + 1
    st.session_state.simulation_sequence = sequence
    monitor.observe({"price": st.session_state.simulated_baseline * (1 + simulated_move / 100),
        "updated_at": time.time(), "round_id": f"simulated-{sequence}", "source": "simulated"})

incident = monitor.incident if monitor_enabled else None
deviation = incident["deviation_pct"] if incident else 0.0
effective_stale = stale_hours
live_sample = st.session_state.latest_live_sample
if monitor_enabled and monitor_mode == "Live oracle polling" and live_sample:
    effective_stale = max(stale_hours, (time.time() - live_sample["updated_at"]) / 3600)
eff_liquidity = scenario["pool_liquidity_usd"] * (1 - liq_drop / 100)
exit_b = risk_engine.exit_loss_components(position_size, eff_liquidity)
quote = None
exit_cost = None
exit_source = "Uncalibrated depth proxy / assumed 0.5% fee"
if scenario.get("adapter") == "pendle_pt" and scenario.get("discovery_complete"):
    quote = load_quote(scenario, position_size, liq_drop)
    exit_source = quote["source"]
    if quote.get("cost_usd") is not None:
        exit_cost = quote["cost_usd"] + gas_usd
        # The quote supplies real execution dependencies, not backing dependencies.
        # FIX: only attach the exit chain to nodes that actually exist in this graph;
        # a hardcoded missing id (e.g. "chain") made graph() raise "Unresolved graph edge".
        existing_ids = {n["node_id"] for n in scenario["dependencies"]}
        parents = tuple(key for key in ("pool", "chain") if key in existing_ids)
        for key, kind, label, detail in [
            ("router", "Infrastructure", "Pendle execution router", quote.get("router", "unresolved")),
            ("aggregator", "Venue", quote.get("aggregator", "Unresolved aggregator"), "Quote provider; internal venues are not fully expanded."),
            ("exit_asset", "Asset", "USDC exit proceeds", quote["output_token"]),
        ]:
            scenario["dependencies"].append({"node_id": key, "layer": 6, "type": kind,
                "label": label, "health_status": "unknown", "details": str(detail),
                "capital_dependencies": (), "exit_dependencies": parents,
                "monitor_dependencies": (), "depends_on": parents})
            parents = (key,)
        position_node = next(n for n in scenario["dependencies"] if n["node_id"] == "position")
        position_node["exit_dependencies"] = parents
        position_node["depends_on"] = tuple(position_node["capital_dependencies"]) + parents + tuple(position_node["monitor_dependencies"])
elif eff_liquidity > 0:
    exit_cost = exit_b.total_pct * position_size + gas_usd

by_id = {n["node_id"]: n for n in scenario["dependencies"]}
shock_targets = [key for key in by_id if key != "position"]
if not shock_targets:  # FIX: avoid IndexError on a graph that only contains the position
    st.error("This graph has no dependency other than the position itself, so there is nothing to stress.")
    st.stop()
preferred = next((key for key in shock_targets if by_id[key]["type"] == "Yield"),
                 "asset" if "asset" in shock_targets else shock_targets[0])
with dependency_selector_slot.container():
    shock_target = st.selectbox("Dependency that fails", shock_targets,
        index=shock_targets.index(preferred), format_func=lambda key: by_id[key]["label"], key=f"target_{scope_key}")
model_target, model_probability, model_severity = shock_target, probability, severity
if confirmed:
    model_probability = 1.0
oracle_target = next((n["node_id"] for n in scenario["dependencies"] if n["type"] == "Oracle"), None)
oracle_event = risk_engine.is_oracle_stale(effective_stale) or incident is not None
oracle_is_economic = bool(oracle_target and impact.propagate(scenario, oracle_target, 1)["loss_fraction"] > 0)
if oracle_event and oracle_is_economic:
    model_target, model_probability, model_severity = oracle_target, oracle_probability, oracle_severity
result = impact.evaluate(scenario, position_size, portfolio_nav, model_target, model_probability,
    model_severity, horizon, exit_cost, exit_source=exit_source, incident_kind=event_kind,
    incident_confirmed=confirmed and model_target == shock_target)
review_reasons = list(result["review_reasons"])
if monitor_enabled and monitor_mode == "Live oracle polling":
    if not scenario.get("oracle_address"):
        review_reasons.append("No compatible oracle feed is configured.")
    elif not live_sample or st.session_state.live_monitor_error:
        review_reasons.append("Live oracle observations are unavailable; fallback prices do not trigger alerts.")
if review_reasons:
    result.update(decision="REVIEW", net_benefit_usd=None,
                  reasoning=result["reasoning"] + " " + " ".join(review_reasons))

ml_indicators = None
ml_import_error = None
ml_runtime_error = None
if ml_enabled:
    # FIX: indicators are advisory, so any failure must degrade to a notice
    # instead of taking down the whole dashboard.
    try:
        from modules.ml_engine import get_pipeline
        pipeline = get_pipeline()
        features = scenario.get("ml_features", {})
        volume_24h = features.get("volume_24h_usd")
        if volume_24h is None:
            volume_24h = eff_liquidity
        ml_indicators = {
            "slippage": (
                pipeline.predict_dynamic_slippage(
                    position_size, eff_liquidity, volume_24h,
                    features.get("volatility_index", 0.05),
                )
                if eff_liquidity > 0 else None
            ),
            "oracle": pipeline.detect_oracle_anomaly(
                int(effective_stale * 3600),
                # FIX: preset features carry a fixed deviation; an active alert must still register.
                max(features.get("price_deviation_pct", 0.0), abs(deviation) / 100),
                features.get("historical_volatility", 0.03),
            ),
            "depeg": (
                pipeline.predict_depeg_probability(
                    features["backing_ratio"], features["pool_imbalance_ratio"],
                )
                if "backing_ratio" in features and "pool_imbalance_ratio" in features
                else None
            ),
            "feature_source": scenario.get("ml_feature_source", "neutral model defaults"),
        }
    except ImportError as exc:
        ml_import_error = str(exc)
    except Exception as exc:  # noqa: BLE001
        ml_runtime_error = str(exc)
nodes = deps.build_dependency_tree(scenario, position_size, effective_stale, liq_drop, deviation)
for node in nodes:
    # FIX: a zero-probability event should not colour the graph as an affected path.
    if node.node_id in result["affected_nodes"] and model_severity > 0 and model_probability > 0:
        node.health_status = "critical" if confirmed else "warning"
theme.masthead(chain_config.name, scenario["data_source"], scenario["name"])
st.caption(scenario.get("model_note", "Reference graph and market inputs are demonstration data."))
if scenario.get("adapter") == "reference":
    st.info("Reference demo: relationships are predefined. Select Discover Pendle PT Dependencies to verify a real position.")
if review_reasons:
    st.warning(esc(result["reasoning"]))  # FIX: escape $ so amounts are not rendered as LaTeX
if incident:
    st.warning(f"Active oracle incident: {deviation:+.2f}% movement ({incident['source']}). Reset monitor to clear.")
if oracle_event:
    st.caption("Oracle stress uses the visible oracle probability/severity assumptions." if oracle_is_economic else
               "This feed is a monitoring benchmark. Its alert has no proven capital-loss path into this holding.")

theme.verdict(result, exit_cost, horizon)
c1, c2, c3 = st.columns(3)
c1.metric("Position Value", "$" + f"{position_size:,.0f}", f"{position_size / portfolio_nav:.2%} of NAV", delta_color="off")
c2.metric("Estimated Exit Cost", "$" + f"{exit_cost:,.0f}" if exit_cost is not None else "Unavailable",
          f"{exit_cost / position_size:.2%}" if exit_cost is not None else None, delta_color="off")
c3.metric("Modelled Loss of Staying", "$" + f"{result['stay_loss_usd']:,.0f}",
          f"{result['portfolio_loss_pct']:.2%} of portfolio NAV", delta_color="off")
# with c4:
#     st.caption("Recommendation")
#     if result["decision"] == "EXIT":
#         st.error("🚨 ACTION: EXIT")
#     elif result["decision"] == "REVIEW":
#         st.warning("⚠️ REVIEW REQUIRED")
#     else:
#         st.success("✅ DECISION: STAY")

st.subheader("Interactive Dependency Visualizer")
flow_key = "flow-" + hashlib.md5((scope_key + repr([(n.node_id, n.health_status, n.details, n.depends_on) for n in nodes])).encode()).hexdigest()[:10]
render_flow(nodes, flow_key)
st.caption("Arrows run from dependency to dependent. Yellow: assumed failure propagation; red: selected active failure / oracle alert; grey: health unverified. A verified relationship does not imply a healthy protocol.")
with st.expander("Dependency evidence and scope"):
    st.table([{"Dependency": n["label"], "Type": n["type"], "Evidence / limitations": n["details"],
        "Backing dependencies": ", ".join(n.get("capital_dependencies", ())),
        "Exit dependencies": ", ".join(n.get("exit_dependencies", ())),
        "Monitor dependencies": ", ".join(n.get("monitor_dependencies", ()))} for n in scenario["dependencies"]])

st.subheader("How the Failure Reaches Your Position")
st.write(esc(result["trace"]))  # FIX: node labels may contain "$" (e.g. "$200k TVL")
st.table([{"Dependency": n.label,
    "Additional loss if event happens": f"{result['node_losses'].get(n.node_id, 0):.2%}",
    "Channel": "Backing impaired" if result["node_losses"].get(n.node_id, 0) else "Exit / monitoring / unaffected"}
    for n in nodes])
if result["exit_dependency_affected"]:
    st.caption("This event also reaches an exit dependency. An assumed future event leaves today's route available; marking it already active requires route review.")
st.caption("Loss passes through backing links once. Multiple paths from the same event are not added together. Severity is an additional loss from today's mark, so a realised price drop is not charged again.")

st.subheader("Decision Analysis & Stress Test Breakdown")
left, right = st.columns(2)
with left:
    st.table([
        {"Component": "Decision horizon", "Value": f"{horizon} days"},
        {"Component": "Assumed loss-event probability", "Value": f"{model_probability:.2%}"},
        {"Component": "Assumed additional loss severity", "Value": f"{model_severity:.2%}"},
        {"Component": "Loss if event happens", "Value": "$" + f"{result['conditional_loss_usd']:,.0f}"},
        {"Component": "Conditional portfolio loss", "Value": f"{result['conditional_portfolio_loss_pct']:.2%}"},
        {"Component": "Expected portfolio loss", "Value": f"{result['portfolio_loss_pct']:.2%}"},
        {"Component": "Exit cost as share of portfolio", "Value": f"{result['exit_portfolio_pct']:.2%}" if exit_cost is not None else "Unavailable"},
    ])
with right:
    st.table([
        {"Component": "Effective pool liquidity", "Value": "$" + f"{eff_liquidity:,.0f}"},
        {"Component": "Simulated liquidity drop", "Value": f"{liq_drop}%"},
        {"Component": "Exit cost source", "Value": exit_source},
        {"Component": "Gas assumption", "Value": "$" + f"{gas_usd:,.0f}"},
        {"Component": "Effective oracle staleness", "Value": f"{effective_stale:.2f} hours"},
        {"Component": "Active oracle deviation", "Value": f"{deviation:+.2f}%"},
    ])
    if quote:
        st.caption(quote.get("note", quote.get("error", "")))
        if quote.get("cost_usd") is not None:
            st.caption(f"Quote fetched at epoch {quote['observed_at']:.0f}; swap fees included. USDC marked at USD {quote['output_price_usd']:.6f}. Gas is a user budget, not an RPC estimate.")
    else:
        st.caption("Depth proxy: 0.5 × (position / liquidity)² + assumed 0.5% fee, capped at 100%. This is uncalibrated, not an executable quote.")
    threshold = result["break_even_probability"]
    if threshold is not None:
        st.caption(f"Break-even event probability over {horizon} days: {threshold:.2%}." +
                   (" Exit is never cheaper under this severity." if threshold > 1 else " Above it, the assumed expected loss exceeds exit cost."))
    else:
        st.caption("No break-even probability: no backing-loss path or no usable exit quote.")

with st.container(border=True):
    st.markdown("### Portfolio Manager Recommendation")
    st.write(f"Decision: {result['decision']}")
    if result["net_benefit_usd"] is not None:
        st.markdown(esc("Modelled advantage: $" + f"{result['net_benefit_usd']:,.0f}"))
    st.markdown(esc(result["reasoning"]))
    st.caption("One-event scenario comparison for an unlevered exposure. These probabilities are analyst inputs, not calibrated forecasts. Lending liquidation, correlated incidents, foregone yield and a multi-position portfolio are outside this model.")

if ml_enabled:
    with st.expander("Experimental ML indicators (advisory only)", expanded=True):
        st.warning(
            "These models are trained on synthetic priors, not historical incident or execution data. "
            "They never change the graph-based recommendation or its user-supplied probability/severity."
        )
        if ml_import_error:
            st.info(
                f"ML indicators could not load ({ml_import_error}). Install optional packages with "
                "`pip install -r requirements-ml.txt` to enable them."
            )
        elif ml_runtime_error:
            st.info(f"ML indicators are unavailable for this input ({ml_runtime_error}). "
                    "The recommendation above is unaffected.")
        elif ml_indicators:
            st.caption(f"Feature inputs: {ml_indicators['feature_source']}.")
            slippage = ml_indicators["slippage"]
            st.metric(
                "ML exit-slippage indicator",
                f"{slippage:.2%}" if slippage is not None else "Unavailable (no usable liquidity)",
            )
            oracle = ml_indicators["oracle"]
            first, second = st.columns(2)
            first.metric("Oracle anomaly score", f"{oracle['anomaly_score']:.2f}")
            second.metric("Modelled stale probability", f"{oracle['predicted_stale_probability']:.2%}")
            if ml_indicators["depeg"] is not None:
                st.metric("Synthetic-prior depeg indicator", f"{ml_indicators['depeg']:.2%}")


@st.fragment(run_every="5s")
def poll_oracle():
    """Uncached polling; failed reads never become oracle incident events."""
    if not monitor_enabled or monitor_mode != "Live oracle polling":
        return
    feed = scenario.get("oracle_address")
    if not feed:
        st.info("Configure a compatible feed to enable live observations.")
        return
    sample = fetcher.get_chainlink_price(feed, chain=chain, allow_mock=False)
    error = sample.get("error") if sample["source"] != "on-chain" else None
    error_changed = bool(error) != bool(st.session_state.live_monitor_error)
    st.session_state.live_monitor_error = error
    if error:
        st.warning("Oracle polling unavailable: " + error)
        if error_changed:
            st.rerun()
        return
    previous_event = monitor.last_event_id
    monitor.observe(sample)
    st.session_state.latest_live_sample = sample
    is_stale = risk_engine.is_oracle_stale(sample["staleness_hours"])
    stale_changed = is_stale != st.session_state.live_stale
    st.session_state.live_stale = is_stale
    st.caption(f"Live oracle: USD {sample['price']:,.4f} · round {sample['round_id']} · every 5 seconds")
    if previous_event != monitor.last_event_id or error_changed or stale_changed:
        st.rerun()


poll_oracle()

from datetime import datetime, timezone

if monitor.history:
    with st.expander("Oracle event history (latest 25 observations)"):
        formatted_history = []
        for item in monitor.history:
            formatted_item = item.copy()
            ts = formatted_item.get("updated_at")
            if isinstance(ts, (int, float)) and ts > 0:
                # Convierte el timestamp Unix a hora legible
                formatted_item["updated_at"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            formatted_history.append(formatted_item)
            
        st.table(formatted_history)
