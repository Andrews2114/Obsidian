# Obsidian — DeFi Risk Sentinel

Streamlit prototype that discovers supported DeFi dependencies, propagates a failure through the holding's backing, and compares its assumed expected loss with an exit cost.

Hosted reference demo: https://obsidian-sentinel.streamlit.app. Local changes require redeployment to appear there.

## Run locally

On Windows:

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address=127.0.0.1 --server.port=8502
~~~

On macOS/Linux use python3 to create the virtual environment and .venv/bin/python to run it. Python 3.10+ is supported.

## View real dependency discovery

1. Open http://127.0.0.1:8502/ and select **Discover Pendle PT Dependencies**.
2. Select Ethereum and leave **Fetch live pool data** enabled.
3. Use the included market example: **0x34280882267ffa6383b363e278b027be083bbe3b**, or enter a Pendle PT address on the selected chain. A market address selects its PT exposure, not its LP shares.
4. Inspect **Dependency evidence and scope**. Contract addresses, evidence block, backing relationships, exit relationships and monitoring inputs are shown separately.
5. Select **Dependency that fails**, its incident type, probability over the chosen horizon, and additional loss severity.
6. Read **How the Failure Reaches Your Position**, portfolio loss, the quoted PT → USDC exit cost and the break-even probability.

The market example currently represents PT-stETH-30DEC2027. Its backing path is PT → SY-stETH → wstETH → stETH. Names and prices come from current API responses; maturity is verified on-chain. PT is not assumed to be worth $1.

Discovery resolves a matching market/PT through the paginated Pendle market catalogue, restricted to the selected chain. It checks market.readTokens(), PT.decimals(), PT.expiry(), SY.getTokensOut(), SY.assetInfo() and SY.exchangeRate() at one block. The yield provider's name is API metadata; the relationships are verified contract reads. Failed verification produces REVIEW rather than a made-up Pendle graph.

**Discover Token Dependencies** selects a token's deepest observed pool on Ethereum, Arbitrum or Optimism. WETH exposure is a configured supported case. A pool match for another token does not prove its backing/yield stack is understood: that exposure remains unresolved and requires a protocol adapter. L2 settlement and sequencer relationships are configuration, not an automatic bridge or liveness audit.

**Preset Reference Positions** are predefined demonstration graphs. Even with live price/TVL enrichment, these are not discovered holdings and do not establish a real Morpho position.

**Refresh data** clears the 60-second discovery and 30-second exit-quote caches.

## Challenge compliance audit

The implementation is a working, narrow prototype, not complete coverage of arbitrary DeFi positions.

| Requirement | Current status | Evidence and remaining scope |
| --- | --- | --- |
| Automatically uncover hidden dependencies | Partial, supported Pendle PT and WETH scope | Resolves verified PT/SY/yield/accounting-asset relationships and real pools. Lending, debt balances, arbitrary wrappers and protocol-internal dependencies remain unsupported. |
| Estimate security, market and technical incident impact | Conditional scenario model implemented; calibration incomplete | A selected event's assumed haircut propagates through backing links. Probability and severity are analyst inputs, not estimates learned from incident history. Temporary outages can have zero backing loss while still blocking exit. |
| Explain failure propagation | Implemented for discovered backing and exit links | Typed graph, affected-node table and explicit path to the holding. A price-monitor alert has no backing-loss effect without an economic path. |
| Compare staying with exiting, including costs | Implemented for supported PT sale previews | Finite-horizon expected additional loss vs PT → USDC quote including swap/aggregator fees and a separate gas budget. Missing quotes or active route impairment require REVIEW. Other modes use an explicitly uncalibrated depth proxy. |
| Quantify portfolio impact | Implemented for one holding within a portfolio NAV | Reports conditional/expected loss and exit cost as fractions of total NAV. Multiple holdings, debt, liquidation cascades and correlated events are not aggregated. |
| Working implementation with real data | Implemented and integration-checked | Live Pendle metadata, block-pinned RPC evidence, PT sale quote, USDC price, Chainlink observations and DEX metadata. Public endpoint availability still affects usability. |

At the start of this repair, custom discovery only located a token pool. The holding-loss calculation did not use the graph, preset graphs were arranged as a misleading linear stack, there was no holding horizon or portfolio NAV, and a stale/deviating oracle imposed a fixed 25% loss. The graph component also recreated timestamped state on every rerun, which could trigger repeated redraws. These issues have been addressed in the active dashboard.

## Model and design choices

Edges have three roles:

- **capital**: backing/value impairment can reach the holding;
- **exit**: needed to execute the modelled route;
- **monitor**: observational input, not proof of an economic dependency.

The display points from a dependency toward its dependent. Red solid edges are backing links; blue dashed edges are exit links; grey dashed edges are monitoring links. A verified relationship does not establish that the dependency is healthy.

The current model considers one event at a time:

~~~text
conditional additional loss = current position value × propagated additional haircut
expected additional loss    = event probability over the chosen horizon × conditional loss
portfolio loss fraction     = loss dollars / total portfolio NAV
exit cost                   = current marked input value − quoted output value + gas budget
break-even probability      = exit cost / conditional additional loss
~~~

A shared event arriving through two backing paths is counted once, using the maximum transmitted haircut instead of summing the paths. This is a transparent 1:1 backing assumption for an unlevered exposure. It is not a general weighted loss cascade or leverage model.

For example, a $100,000 holding in a $1 million portfolio with a 30% conditional haircut has a $30,000 conditional loss, or 3% of NAV. At an assumed 10% event probability over seven days, its expected loss is $3,000, or 0.3% of NAV. An $1,100 exit cost is 0.11% of NAV. The break-even event probability is about 3.67%.

Severity always describes an **additional future loss from today's mark**. A price movement that already reduced today's mark is not charged again. A confirmed selected failure has a 100% event probability in the dashboard; its remaining-loss severity is still an assumption.

The PT adapter requests a read-only Pendle convert preview for the amount implied by the current USD mark and verified token decimals. It selects the returned route with the greatest USDC output, marks USDC at a live price rather than assuming a dollar peg, and rejects price marks older than 15 minutes. Swap/aggregator fees are already in the quoted output and are not added twice. The gas budget defaults to $50 and can be changed; it is not an RPC gas estimate. Route construction does not sign or submit a trade, guarantee a mined fill, verify the user's balance/approvals, or prove every external route venue's safety.

A missing or blocked route produces REVIEW. A future hypothetical failure does not by itself prevent selling today. An already active failure that reaches an exit dependency requires a usable route reassessment.

The optional **Show experimental ML indicators** panel fits lightweight models on synthetic priors and displays advisory slippage, oracle-anomaly and (where explicit peg inputs exist) depeg estimates. These are not calibrated forecasts, do not infer a security-incident probability, and never change the graph-based decision or the analyst-supplied event probability/severity. Install the optional packages with `pip install -r requirements-ml.txt`; the normal app remains usable without them.

## Stress testing and incident alerts

Liquidity-drop (0–100%) and oracle-staleness sliders update the badge, metrics and graph without a submit button.

For Pendle, the liquidity stress model is a **proxy**: request a current-pool quote at trade size divided by the remaining liquidity fraction, then scale the resulting cost down to the original holding. This assumes uniformly reduced depth; it is not Pendle's exact time-aware AMM simulation. A 100% liquidity drop makes the route unavailable. The unstressed PT cost is a real current quote preview.

Token/reference exit costs use the legacy depth proxy: 0.5 × (position / pool liquidity)² plus an assumed 0.5% fee, capped at 100%. This is not executable trading depth.

Oracle staleness and >3% alerts select the visible oracle probability/severity assumptions only when the graph contains an economic oracle dependency. That oracle scenario replaces the currently selected event; it is not added to it. A benchmark feed on a standalone PT or WETH holding still triggers an alert but does not invent an oracle-induced capital loss.

For the simulated event demo:

1. Enable **Oracle incident alerts** and select **Simulated log subscription**.
2. Set the simulated price change to -5% and click **Publish simulated oracle log**.
3. A banner, toast and modal show the recommendation under the current graph and assumptions.
4. **Acknowledge incident** closes the modal; **Reset monitor / clear incident** clears the latched incident.

Exactly +3% or -3% does not alert. Each new observation is compared with the preceding valid observation; unchanged updates and duplicate rounds do not create another breach. Feed/chain/source switches reset the monitoring scope.

Live Chainlink monitoring polls every five seconds while the browser session is active. The first valid round establishes a baseline. Invalid or failed reads are never replaced with simulated incident prices. This is session polling, not a persistent background subscription. A Chainlink round timestamp alone does not establish the appropriate feed-specific heartbeat policy.

## Next refinements, in priority order

1. Add actual lending adapters: wallet/market positions, collateral, debt, LLTV/LTV and liquidation penalties, plus the actual consumed oracle contract. A collateral-token match alone is not evidence the user has borrowed.
2. Expand provider internals and bridge dependencies using verified adapter interfaces, with explicit coverage/confidence and unresolved nodes.
3. Replace default probabilities/haircuts with documented, time-horizon-specific incident evidence, uncertainty ranges and sensitivity analysis. Include opportunity cost and shared portfolio exposures.
4. Replace the Pendle liquidity proxy with a protocol-specific reserve-state simulation; estimate gas from a funded-wallet simulation and validate alternative routes under a dependency failure.

## Architecture

| File | Responsibility |
| --- | --- |
| app.py | Dashboard, assumptions, portfolio metrics, persistent graph state, alerts and polling |
| modules/chains.py | Chain IDs, RPC URLs, WETH and price-feed configuration |
| modules/pendle.py | Paginated PT/market resolution, block-pinned verification and sale quote previews |
| modules/discovery.py | Typed dependency graphs and supported discovery scope |
| modules/dependencies.py | Reference graphs, health display and flow conversion |
| modules/impact.py | Backing-loss propagation, exit availability, finite-horizon comparison and NAV weighting |
| modules/fetcher.py | Chainlink, DEX Screener and DeFiLlama data |
| modules/incidents.py | Strict >3% detection, validation, deduplication and latched incidents |
| modules/mock_data.py | Explicit reference examples |
| modules/risk_engine.py | Legacy demonstration heuristics and depth proxy; no longer the dashboard's holding-loss model |
| modules/ml_engine.py | Optional synthetic-prior indicators; advisory only |

## Tests

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -B -m pytest -q
~~~

Tests cover paginated address resolution, chain isolation, contract/metadata mismatch, block-pinned evidence, real API response shapes, token decimal conversion, quote route selection, stale/missing prices, no fallback graphs, shared-event double counting, monitor versus backing dependencies, unavailable/blocked exits, portfolio weighting, probability boundaries, dynamic metrics, alerts and graph-state persistence. Optional ML tests are skipped unless `requirements-ml.txt` is installed. External services are mocked in automated tests; real integration checks require network access.

References: [Pendle market contracts](https://docs.pendle.finance/pendle-v2-dev/Contracts/PendleMarket), [SY contracts and pricing](https://docs.pendle.finance/pendle-v2-dev/Contracts/StandardizedYield), [Pendle API overview](https://docs.pendle.finance/pendle-v2-dev/Backend/ApiOverview), [Chainlink](https://docs.chain.link/data-feeds), [DEX Screener](https://docs.dexscreener.com/api/reference), [Streamlit fragments](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment).

## License

See LICENSE.
