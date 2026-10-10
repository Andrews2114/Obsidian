"""DeFi Risk Sentinel — On-Chain Dependency & Portfolio Action Engine."""
import hashlib
import math

import streamlit as st

from modules import dependencies as deps
from modules import mock_data, risk_engine

st.set_page_config(page_title="DeFi Risk Sentinel", page_icon="🛡️", layout="wide")


def esc(text: str) -> str:
    """Escape $ so Streamlit markdown doesn't treat dollar amounts as LaTeX."""
    return text.replace("$", "\\$")


@st.cache_data(ttl=300, show_spinner="Fetching on-chain / market data…")
def load_scenario(key: str, live: bool) -> dict:
    return deps.get_scenario(key, live=live)


def render_flow(nodes, edges, key: str):
    """Render with streamlit-flow-component (supports both 1.x state API and the older API)."""
    try:
        from streamlit_flow import streamlit_flow
        from streamlit_flow.layouts import TreeLayout
    except ImportError:
        st.warning("`streamlit-flow-component` is not installed — run `pip install -r requirements.txt`.")
        return
    try:
        from streamlit_flow.state import StreamlitFlowState
        streamlit_flow(key, StreamlitFlowState(nodes, edges),
                       layout=TreeLayout(direction="down"), fit_view=True, height=450)
    except ImportError:  # older component versions
        streamlit_flow(key, nodes, edges, layout=TreeLayout(direction="down"),
                       fit_view=True, height=450)


# ------------------------------------------------------------------ Header
st.title("DeFi Risk Sentinel — On-Chain Dependency & Portfolio Action Engine")
st.caption("EPFL Hackathon 2026 | Belem Capital Challenge")

# ----------------------------------------------------------------- Sidebar
with st.sidebar:
    st.header("Controls")
    mode = st.radio("Position source", ["Preset Reference Positions", "Custom Contract Address"])

    scenario, live = None, False
    if mode == "Preset Reference Positions":
        preset = st.selectbox("Preset", list(mock_data.PRESET_LABELS))
        live = st.checkbox("Refresh with live RPC / API data", value=False,
                           help="Falls back to offline mock data automatically if a call fails.")
        scenario = load_scenario(mock_data.PRESET_LABELS[preset], live)
    else:
        address = st.text_input("Token / position contract address", placeholder="0x…")
        if deps.ADDRESS_RE.match(address.strip()):
            scenario = load_scenario(address.strip(), True)
        else:
            if address.strip():
                st.error("Invalid address — expected 0x followed by 40 hex characters.")
            st.info("Showing the PT-eETH reference position until a valid address is entered.")
            scenario = load_scenario("pendle_pt_eeth", False)

    position_size = st.number_input("Position Size ($ USD)", min_value=1_000.0, value=100_000.0,
                                    step=10_000.0, format="%.0f")

    st.subheader("Interactive Stress Test")
    default_stale = float(min(12.0, math.ceil(scenario["oracle_stale_hours"] * 2) / 2))
    stale_hours = st.slider("Simulate Oracle Staleness (Hours)", 0.0, 12.0, default_stale, 0.5,
                            key=f"stale_{scenario['key']}")
    liq_drop = st.slider("Simulate AMM Liquidity Drop (%)", 0, 90, 0, key=f"drop_{scenario['key']}")

# ------------------------------------------------------------------- Model
eff_liquidity = scenario["pool_liquidity_usd"] * (1 - liq_drop / 100)
exit_b = risk_engine.exit_loss_components(position_size, eff_liquidity)
stay_b = risk_engine.stay_loss_components(
    risk_engine.is_oracle_stale(stale_hours),
    scenario["protocol_concentration_risk"], scenario["depeg_risk"])
result = risk_engine.evaluate_decision(position_size, exit_b.total_pct, stay_b.total_pct)

nodes = deps.build_dependency_tree(scenario, position_size, stale_hours, liq_drop)

# ------------------------------------------------------------- Metrics row
st.caption(f"**{scenario['name']}** · baseline status: {scenario['default_status']} · "
           f"data source: {scenario['data_source']}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Position Value", f"${position_size:,.0f}")
c2.metric("Guaranteed Exit Cost", f"${result['exit_loss_usd']:,.0f}", f"{exit_b.total_pct:.2%}",
          delta_color="off")
c3.metric("Expected Loss of Staying", f"${result['stay_loss_usd']:,.0f}", f"{stay_b.total_pct:.2%}",
          delta_color="off")
with c4:
    st.caption("Recommendation")
    if result["decision"] == "EXIT":
        st.error("🚨 ACTION REQUIRED: EXIT NOW")
    else:
        st.success("✅ DECISION: STAY IN POSITION")

# --------------------------------------------------------- Dependency graph
st.subheader("Interactive Dependency Visualizer")
flow_nodes, flow_edges = deps.to_flow_elements(nodes)
flow_key = "flow-" + hashlib.md5(
    (scenario["key"] + "".join(n.health_status for n in nodes)).encode()).hexdigest()[:10]
render_flow(flow_nodes, flow_edges, flow_key)
st.caption("🟩 healthy · 🟨 warning · 🟥 critical — oracle and AMM nodes react to the stress sliders.")

# ---------------------------------------------------------------- Analysis
st.subheader("Decision Analysis & Stress Test Breakdown")
left, right = st.columns(2)

with left:
    st.markdown("**Exit Trade-off Analysis**")
    st.table([
        {"Component": "Effective pool liquidity", "Value": f"${eff_liquidity:,.0f}"},
        {"Component": "Position / liquidity", "Value": f"{position_size / eff_liquidity:.1%}"
         if eff_liquidity > 0 else "n/a"},
        {"Component": "Slippage (quadratic)",
         "Value": f"{exit_b.slippage_pct:.2%}  (${exit_b.slippage_pct * position_size:,.0f})"},
        {"Component": "Execution / base fee",
         "Value": f"{exit_b.fee_pct:.2%}  (${exit_b.fee_pct * position_size:,.0f})"},
        {"Component": "Total exit cost",
         "Value": f"{exit_b.total_pct:.2%}  (${result['exit_loss_usd']:,.0f})"},
    ])

with right:
    st.markdown("**Dependency Risk Exposure**")
    st.table(risk_engine.layer_risk_table(nodes, stay_b, exit_b))
    st.caption(f"Total expected loss of staying: {stay_b.total_pct:.2%} "
               f"(oracle {stay_b.oracle_pct:.2%} + concentration {stay_b.concentration_pct:.2%} "
               f"+ depeg {stay_b.depeg_pct:.2%})")

with st.container(border=True):
    st.markdown("### Portfolio Manager Recommendation")
    st.markdown(f"**Decision: {result['decision']}** — expected advantage of this action: "
                f"**${result['net_benefit_usd']:,.0f}**".replace("$", "\\$"))
    st.markdown(esc(result["reasoning"]))
