# Obsidian

https://obsidian-sentinel.streamlit.app

DeFi Risk Sentinel is a Streamlit-based risk-analysis dashboard for evaluating whether a DeFi position should be exited or held under stress conditions. It models dependency risk across settlement, yield, oracle, AMM, and lending layers, then compares the guaranteed cost of unwinding a position against the expected loss of staying in it.

This project was built for the EPFL Hackathon 2026 / Belem Capital Challenge and focuses on portfolio action decisions for risky on-chain positions.

## Project overview

The application helps portfolio managers answer a simple but critical question:

> Should we exit a DeFi position now, or stay and monitor?

It does this by combining:

- a five-layer dependency model of the position
- live on-chain and market data enrichment when available
- offline fallback scenarios if external APIs fail
- a stress test for oracle staleness and liquidity deterioration
- an expected-value decision engine that recommends the better action

The dashboard is designed to be easy to understand: the user can switch between preset scenarios, inspect a dependency tree, simulate stress conditions, and see how the recommendation changes as market assumptions worsen.

## What the app does

The app presents a single dashboard with:

- position selection (preset reference positions or a custom token/position contract address)
- live or mock scenario loading
- risk metrics for expected exit cost and expected loss of staying
- a dependency graph showing the five layers of the position
- an interactive stress test for:
  - oracle staleness in hours
  - AMM liquidity drop as a percentage
- a recommended action: STAY or EXIT

### Default scenarios

The repo includes two preset scenarios:

1. Pendle PT-eETH (Normal)
   - relatively healthy reference environment
   - deep liquidity, fresh oracle, low concentration risk

2. Compromised Vault / Fragile Oracle
   - high-risk scenario with stale pricing and thin AMM depth
   - elevated protocol concentration and depeg risk

There is also support for custom addresses, which start from the Pendle template and then enrich the scenario with live data if valid.

## Architecture

```text
Obsidian/
├── app.py                     # Streamlit application entry point
├── requirements.txt          # Python dependencies
├── LICENSE                   # Project license
├── README.md                 # Project documentation
├── modules/
│   ├── __init__.py           # Module package initializer
│   ├── dependencies.py       # Scenario loading, health rules, dependency graph
│   ├── fetcher.py            # Live RPC + DeFiLlama / DEX Screener fetchers
│   ├── mock_data.py          # Offline fallback scenarios and static market data
│   └── risk_engine.py        # EV model + decision logic
└── .venv/                    # local environment (if created by the user)
```

### Main modules

#### app.py

This is the dashboard entry point. It:

- sets page configuration for Streamlit
- loads the scenario for either a preset or a custom address
- lets the user tune stress assumptions
- computes exit and stay metrics
- renders the dependency graph and recommendation panels

#### modules/dependencies.py

Responsible for:

- scenario lookup and data enrichment
- regex validation for Ethereum-style addresses
- health classification for Oracle and AMM nodes
- construction of the dependency tree used in the visualization
- conversion into Streamlit Flow elements for rendering

#### modules/fetcher.py

This module implements live data fetchers for:

- Chainlink oracle heartbeat via public RPC (`latestRoundData`)
- DEX Screener token liquidity lookups
- DeFiLlama protocol TVL data
- helper functions for coin prices and yield pools

If a live call fails, the app falls back to the static mock data automatically.

#### modules/mock_data.py

Contains:

- preset scenario definitions
- mock market data used when the network is unavailable
- protocol TVL values used as fallback inputs

#### modules/risk_engine.py

Implements the decision model:

- exit loss decomposition
- stay loss decomposition
- oracle staleness logic
- recommendation engine
- layer-by-layer risk table used in the analysis panel

## Risk model

The application uses a compact expected-value framework.

### 1) Exit loss

The exit side is modeled as:

- slippage cost: quadratic in position size relative to liquidity
- fixed execution/base fee
- total exit cost = slippage + fee

In code, the model is approximately:

```python
slippage = min(1.0, (position_size_usd / pool_liquidity_usd) ** 2 * 0.5)
exit_total = min(1.0, slippage + base_fee_pct)
```

This means thin pools and large positions produce a much higher exit cost.

### 2) Stay loss

The stay side combines the risk of:

- oracle failure / stale pricing
- concentration risk in the protocol or market
- depeg risk in the underlying yield or collateral stack

The model uses weighted effects:

```python
oracle = 0.25 if stale else 0.002
conc = protocol_concentration_risk * 0.1
depeg = depeg_risk * 0.8
stay_total = min(1.0, oracle + conc + depeg)
```

### 3) Recommendation rule

The app compares:

- guaranteed exit loss in USD
- expected loss of staying in USD

and chooses the lower-cost action:

- `STAY` if the expected loss of staying is lower than the guaranteed exit loss
- `EXIT` otherwise

This is intentionally simple and interpretable for decision support.

## Dependency layers

Each position is represented as a five-layer dependency graph:

1. Settlement layer
2. Yield layer
3. Oracle layer
4. AMM layer
5. Lending layer

These layers are color-coded in the visualization:

- green = healthy
- yellow = warning
- red = critical

The AMM and Oracle nodes are recalculated live under user stress inputs, while other layers keep their baseline risk state.

## Live data behavior

The app supports optional live enrichment using public endpoints.

### Live sources

- Chainlink oracle data via public Ethereum RPC
- DEX Screener for token liquidity
- DefiLlama for protocol TVL

### Fallback strategy

If any live call fails or is rate-limited, the app silently falls back to the offline mock datasets in `modules/mock_data.py` and continues running. This makes the dashboard usable even in offline or restricted environments.

## Setup

### Prerequisites

- Python 3.10+ recommended
- pip
- Internet access for live data enrichment (optional)

### Install

```bash
cd /workspaces/Obsidian
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run the app

```bash
streamlit run app.py
```

Then open the local Streamlit URL shown in the terminal, usually:

```text
http://localhost:8501
```

## Usage

### Option 1: preset scenarios

Choose from the sidebar:

- `Preset Reference Positions`
- then select a scenario such as `Pendle PT-eETH (Normal)` or `Compromised Oracle Vault (High Risk)`

You can also enable:

- `Refresh with live RPC / API data`

This triggers live fetches for oracle freshness and liquidity, while falling back to mock data if needed.

### Option 2: custom contract address

Select:

- `Custom Contract Address`
- paste a valid Ethereum address such as `0x...`

The app validates the address format and then loads a scenario based on the Pendle template, enriching it with live data if possible.

### Stress testing

Use the sliders in the sidebar to simulate:

- oracle staleness in hours
- AMM liquidity drop percentage

As the sliders move:

- oracle node health changes
- AMM node health changes
- exit loss calculations update
- the decision recommendation may flip between STAY and EXIT

## Expected dashboard flow

The dashboard contains several sections:

1. Header and scenario summary
2. Controls sidebar
3. Core risk metrics
4. Dependency visualizer
5. Decision analysis and stress-breakdown tables
6. Recommendation panel with reasoning text

This creates a simple workflow:

- choose scenario
- stress the market assumptions
- inspect the dependency stack
- interpret the recommendation and expected value

## Key files and responsibilities

| File | Purpose |
| --- | --- |
| `app.py` | Streamlit UI and user interaction flow |
| `modules/dependencies.py` | Scenario logic, health grade rules, graph building |
| `modules/fetcher.py` | RPC and public API live reads |
| `modules/mock_data.py` | Scenario baseline and fallback datasets |
| `modules/risk_engine.py` | EV risk calculations and action recommendation |

## Dependencies

The project currently uses:

```text
streamlit
web3
requests
streamlit-flow-component
networkx
```

The app is designed to degrade gracefully when optional integrations are unavailable. In particular, `streamlit-flow-component` is optional from a runtime perspective, and the dashboard shows a warning if it is absent.

## Troubleshooting

### `streamlit-flow-component` missing

If the graph fails to render, install the dependencies again:

```bash
pip install -r requirements.txt
```

If the component still is not available, you may need to reinstall it in the active virtual environment.

### Live data not loading

This is expected in some environments. The app automatically falls back to mock data. The app will still function without external network access.

### Invalid address error

The app accepts an Ethereum-like address in the form:

```text
0x + 40 hex characters
```

For example:

```text
0x1234567890abcdef1234567890abcdef12345678
```

## Known limitations

- This is a decision-support dashboard, not a production-grade risk engine for regulated financial deployment.
- Live data is best-effort and depends on public API availability and rate limits.
- The model intentionally uses simplified heuristics so that the tradeoff between exit cost and stay risk is readable and explainable.
- Custom address mode starts from a Pendle PT-eETH template and enriches the scenario rather than building a fully new position-specific model from scratch.

## Summary

Obsidian is a compact but practical DeFi risk dashboard that turns dependency analysis into an actionable decision. It combines live chain data, fallback offline datasets, and an understandable expected-value model to help a user decide whether a position should be exited or held under stress.

It is especially useful for evaluating:

- oracle fragility
- AMM depth shocks
- concentration risk
- depeg risk
- position liquidation / unwind tradeoffs

## License

This project is distributed under the repository license specified in the root `LICENSE` file.
