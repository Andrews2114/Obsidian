# Obsidian — DeFi Risk Sentinel

Obsidian is a Streamlit-based risk-analysis prototype for DeFi positions. It helps analysts reason about hidden dependency chains, simulate a failure scenario, and compare the expected loss from holding a position against the cost of exiting it.

The app is designed as an interactive decision aid rather than a financial advisor or automated execution system. It works with a small set of supported discovery paths, uses graph-based reasoning, and gives a transparent breakdown of what is and is not proven.

Hosted demo: https://obsidian-sentinel.streamlit.app

## Why this project exists

DeFi positions often hide behind a stack of dependencies: underlying yield assets, liquidity venues, oracles, settlement layers, and exit routes. A position may look healthy on paper while being exposed to a single weak link somewhere deeper in the stack.

Obsidian makes that structure visible by:

- discovering supported dependency graphs for a token or Pendle PT position;
- showing the dependency path from a failure source to the user holding;
- propagating a modeled loss through capital edges;
- comparing expected additional loss with a portfolio-level exit quote;
- surfacing unresolved or unsupported relationships as REVIEW rather than pretending they are fully understood.

## Key capabilities

- Supported discovery modes:
  - preset reference scenarios;
  - token dependency discovery on Ethereum, Arbitrum, and Optimism;
  - direct Pendle PT/market dependency discovery on a selected chain;
- Graph-based impact model with capital, exit, and monitor edges;
- Scenario-based analysis using analyst input for event probability and severity;
- Portfolio-aware loss metrics measured against total NAV;
- PT-to-USDC exit quote preview with gas / execution overhead assumptions;
- Oracle incident monitoring with stale-feed and alert logic;
- Optional advisory ML indicators based on synthetic priors only.

## Supported position sources

### 1. Preset reference positions
These are demonstration graphs that are intentionally predefined. They help illustrate the dashboard and the risk model, but they are not live wallet positions and should not be treated as verified holdings.

### 2. Discover Token Dependencies
This mode accepts an EVM address and chain. It attempts to identify a token's deepest observed pool and build a limited dependency graph. This is a constrained prototype; a token may have a pool without the app actually understanding its full underlying yield stack.

### 3. Discover Pendle PT Dependencies
This mode resolves a Pendle market or PT on a given chain, verifies the relationship between the market, PT, SY, and underlying accounting asset, and estimates a PT-to-USDC exit route preview. It uses a block-pinned verification approach and rejects unsupported or unverified dependencies instead of inventing extra graph edges.

## Quick start

Requirements: Python 3.10+ recommended.

On Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address=127.0.0.1 --server.port=8502
```

On macOS/Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py --server.address=127.0.0.1 --server.port=8502
```

Then open the app at http://127.0.0.1:8502.

Optional ML indicators:

```bash
python -m pip install -r requirements-ml.txt
```

If the optional ML package set is not installed, the app still runs and the advisory indicators remain unavailable.

## Running the app

The main dashboard is driven by `app.py` and exposes a sidebar with the main controls:

- position source and chain;
- USD position size and portfolio NAV;
- event probability and additional loss severity;
- exit cost / gas assumptions;
- liquidity stress and oracle staleness simulation;
- oracle incident monitoring.

For a realistic Pendle workflow:

1. Open the app.
2. Select Discover Pendle PT Dependencies.
3. Choose Ethereum and leave live data enabled.
4. Use the sample market address or input a supported Pendle PT/market address.
5. Inspect the graph, the evidence panel, and the modeled exit route.
6. Set the event probability, severity, and exit assumptions.
7. Review the stay vs exit recommendation, the path to the holding, and the break-even probability.

## Model assumptions and decision logic

Obsidian is built around a transparent but intentionally narrow model:

- One event is modeled at a time.
- Severity is defined as an additional loss from today’s mark, not a repeated charge on a price move already baked into the current valuation.
- Capital edges propagate the modeled haircut toward the position.
- Exit edges determine whether a sale route is usable.
- Monitor edges represent observations, not proof of economic loss.
- If a dependency is unsupported or route data is missing, the app intentionally falls back to REVIEW instead of making up a conclusion.

The core equations are:

```text
conditional additional loss = position value × propagated additional haircut
expected additional loss    = event probability × conditional loss
portfolio loss fraction     = expected loss / total portfolio NAV
exit cost                   = quoted output value gap + gas / execution overhead assumption
break-even probability      = exit cost / conditional loss
```

A shared event reaching a holding by more than one path is not double-counted; the model applies the maximum relevant haircut along the relevant capital path. This is a simplified unlevered assumption, not a full correlated-loss or leverage model.

## Data sources and verification

The app integrates a mix of real market data and deterministic graph logic:

- Chain configuration and RPC metadata: `modules/chains.py`
- Pendle market and PT resolution: `modules/pendle.py`
- Graph construction and dependency discovery: `modules/discovery.py`
- Dependency and evidence presentation: `modules/dependencies.py`
- Impact and scenario evaluation: `modules/impact.py`
- API and oracle fetches: `modules/fetcher.py`
- Incident monitoring and alert logic: `modules/incidents.py`
- Preset scenarios and offline reference cases: `modules/mock_data.py`
- Legacy heuristics and fallback logic: `modules/risk_engine.py`
- Optional synthetic advisory indicators: `modules/ml_engine.py`

This means:

- real RPC reads and API fetches are used where available;
- a discovered relationship is only as good as the evidence behind it;
- unsupported or partially resolved stacks remain explicitly flagged rather than silently assumed.

## Architecture overview

```text
app.py
├── Streamlit dashboard and controls
├── scenario loading and quote caching
├── graph rendering and incident polling
└── risk-model result presentation

modules/
├── chains.py              # supported chains and feed metadata
├── pendle.py             # Pendle discovery, verification, pricing preview
├── discovery.py          # token and PT dependency adapters
├── dependencies.py       # graph-to-UI translation and evidence sections
├── impact.py             # propagation, NAV weighting, exit analysis
├── fetcher.py            # Chainlink, DEX, and API data fetches
├── incidents.py         # strict oracle alert logic and state tracking
├── mock_data.py         # preset demo graphs and fallback references
├── risk_engine.py       # legacy demonstration heuristics
├── ml_engine.py         # optional synthetic advisory indicators
└── __init__.py
```

## Example workflow

1. Launch the app.
2. Select a position source.
3. Provide a supported address and chain.
4. Review the dependency graph and evidence.
5. Choose a failure mode, probability, and severity.
6. Inspect the path to the holding and the recommendation.
7. Compare holding expected loss with the modeled exit cost.

## Monitoring and alerts

Oracle monitoring can be configured in two modes:

- simulated log subscription;
- live polling against the configured feed.

Alerts are strict and conservative:

- only deviations greater than 3% trigger an alert;
- exact ±3% does not alert;
- duplicate or unchanged observations do not create a new breach;
- feed or chain changes reset monitoring scope;
- a monitor trigger does not by itself prove an economic capital loss unless a real capital edge connects the oracle to the holding.

## Limitations and scope

This is a working prototype and not a complete DeFi risk engine. The project intentionally narrows scope to make the model transparent and auditable.

Current limitations include:

- no full wallet-level balance or debt analysis;
- no general automatic coverage of all DeFi wrappers or lending markets;
- no leveraged or correlated multi-position cascade modeling;
- no calibrated historical incident forecasting;
- no execution guarantees or transaction signing.

The app should be used as an analyst support tool, not as a final trading or risk-control system.

## Testing

Install test dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Then run the suite:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q
```

The test set covers chain scoping, contract validation, asset discovery rules, quote selection, stale-price handling, graph propagation, monitoring logic, and portfolio weighting. Optional ML tests are skipped unless the ML requirements are installed.

## References

- Pendle Market contracts: https://docs.pendle.finance/pendle-v2-dev/Contracts/PendleMarket
- StandardizedYield documentation: https://docs.pendle.finance/pendle-v2-dev/Contracts/StandardizedYield
- Pendle API overview: https://docs.pendle.finance/pendle-v2-dev/Backend/ApiOverview
- Chainlink data feeds: https://docs.chain.link/data-feeds
- DEX Screener API: https://docs.dexscreener.com/api/reference
- Streamlit fragments: https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment

## License

See the LICENSE file in this repository.
