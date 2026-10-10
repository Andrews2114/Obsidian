"""Discover chain-qualified token/pool relationships from real DEX metadata.

Scope: standalone token exposure and its deepest observed exit pool. This
adapter does not invent yield protocols, collateral venues or borrowing positions.
"""
import time

from . import fetcher
from .chains import get_chain


def discover_pendle(address, chain, *, live=True, oracle_address=None):
    """Discover a standalone PT holding, never infer a lending position."""
    from . import pendle
    sc = discover_token(address, chain, live=False, oracle_address=oracle_address)
    sc["adapter"] = "pendle_pt"
    data = pendle.snapshot(address, chain) if live else {"source": "unavailable", "error": "Live discovery is disabled"}
    if data["source"] == "unavailable":
        sc.update(discovery_complete=False, discovery_error=data["error"],
                  data_source="Pendle discovery unavailable", model_note="No verified Pendle graph is available.")
        return sc
    market, evidence = data["market"], data["evidence"]
    liquidity = market.get("liquidity", (market.get("details") or {}).get("liquidity", 0))
    if isinstance(liquidity, dict):
        liquidity = liquidity.get("usd", 0)
    nodes = [n for n in sc["dependencies"] if n["type"] in ("Settlement", "Infrastructure")]

    def add(key, layer, kind, token, parents=(), exits=(), monitors=(), detail=""):
        nodes.append({"node_id": key, "layer": layer, "type": kind,
            "label": token.get("symbol", key), "health_status": "unknown",
            "details": f"{token.get('address', '')} on {chain}. {detail}",
            "capital_dependencies": parents, "exit_dependencies": exits,
            "monitor_dependencies": monitors, "depends_on": parents + exits + monitors})

    accounting, underlying = market["accountingAsset"], market["underlyingAsset"]
    separate = accounting["address"].lower() != underlying["address"].lower()
    if separate:
        add("accounting", 2, "Asset", accounting, ("chain",),
            detail="SY.assetInfo() verified accounting asset; its price is not assumed to be $1.")
    add("yield", 3, "Yield", underlying, ("accounting",) if separate else ("chain",),
        detail=f"Verified SY redemption output. Yield provider: {underlying.get('protocol') or market['pt'].get('protocol', 'unresolved')} (API metadata). Internal protocol dependencies are not recursively resolved.")
    add("sy", 4, "Yield", market["sy"], ("yield",),
        detail=f"market.readTokens(), SY.getTokensOut(), SY.assetInfo() at block {evidence['block']}. Exchange rate {evidence['exchange_rate']:.6f} accounting units/SY.")
    add("asset", 5, "Asset", market["pt"], ("sy",),
        detail=f"Verified market.readTokens() PT. Maturity {market['expiry']}; price supplied by Pendle API.")
    add("pool", 6, "AMM", {"symbol": "Pendle PT/SY market", "address": market["address"]},
        exits=("chain", "sy", "asset"), detail="Verified PT/SY market. Liquidity is API data, not an executable exit quote.")
    if oracle_address:
        add("oracle", 3, "Oracle", {"symbol": "User-configured price monitor", "address": oracle_address},
            monitors=("chain",), detail="Benchmark only. No evidence that this standalone holding consumes the feed.")
    add("position", 7, "Position", {"symbol": "Standalone PT holding", "address": market["pt"]["address"]},
        parents=("asset",), exits=("pool",), monitors=("oracle",) if oracle_address else (),
        detail="Unlevered PT exposure. Lending, wallet balance and borrowing positions are not inferred.")
    sc.update(name=market["pt"]["symbol"], token_address=market["pt"]["address"],
        pendle_market=market, dependencies=nodes, oracle_address=oracle_address,
        oracle_price=None, pool_liquidity_usd=float(liquidity or 0),
        data_source=data["source"], observed_at=data["observed_at"], block=evidence["block"],
        discovery_complete=True, discovery_error=None, adapter="pendle_pt",
        model_note="Verified PT → SY → yield asset relationships. Scope: standalone PT; no lending/borrow discovery. Failure probabilities and loss severity are user assumptions; full provider internals are unresolved.")
    return sc


def discover_token(address: str, chain: str, *, live: bool = True,
                   oracle_address: str | None = None) -> dict:
    config = get_chain(chain)
    data = (fetcher.get_token_liquidity(address, chain=chain) if live
            else {"source": "unavailable", "error": "Live discovery is disabled"})
    wrapped_eth = address.lower() == config.wrapped_eth.lower()
    feed = oracle_address or (config.eth_usd_feed if wrapped_eth else None)
    nodes = []

    def node(node_id, layer, kind, label, details, parents=(), health="unknown", role="capital"):
        nodes.append({"node_id": node_id, "layer": layer, "type": kind,
                      "label": label, "details": details, "depends_on": parents,
                      "capital_dependencies": parents if role == "capital" else (),
                      "exit_dependencies": parents if role == "exit" else (),
                      "monitor_dependencies": parents if role == "monitor" else (),
                      "health_status": health})

    if chain != "ethereum":
        node("l1", 0, "Settlement", "Ethereum settlement",
             "Configured L2 settlement dependency; liveness is not monitored.")
        node("sequencer", 1, "Infrastructure", f"{config.name} sequencer",
             "Sequencer availability is not monitored by this adapter.", ("l1",))
    node("chain", 1, "Settlement", config.name,
         f"Chain ID {config.chain_id}; explorer {config.explorer}.",
         ("l1", "sequencer") if chain != "ethereum" else ())

    base, quote = data.get("base_token", {}), data.get("quote_token", {})
    asset = base if base.get("address", "").lower() == address.lower() else quote
    symbol = asset.get("symbol") or ("WETH" if wrapped_eth else address[:10])
    node("asset", 2, "Asset", symbol, f"Token {address} on {chain}.", ("chain",))
    other = quote if asset is base else base
    pool_found = (data.get("source") == "live" and data.get("pair_address")
                  and other.get("address") and asset.get("address"))
    if pool_found:
        node("quote", 2, "Asset", other.get("symbol", "Pool counter-asset"),
             f"DEX metadata: {other['address']} on {chain}.", ("chain",))
    if feed:
        node("oracle", 3, "Oracle", "Configured price monitor",
             f"Feed {feed}. {'ETH/USD benchmark' if wrapped_eth and not oracle_address else 'User-configured feed'}; "
             "monitoring input, not proof that the pool consumes this oracle.", ("chain",), role="monitor")
    node("pool", 4, "AMM", f"{data.get('top_dex', 'Unknown')} exit pool",
         (f"Pool {data['pair_address']}; observed at {data['observed_at']:.0f} UTC epoch."
          if pool_found else f"Unresolved: {data.get('error', 'Incomplete pool metadata')}"),
         ("chain", "asset", "quote") if pool_found else ("chain", "asset"), role="exit")
    node("position", 5, "Position", f"{symbol} exposure",
         "Standalone token holding; no lending or yield relationship inferred.",
         ("asset",))
    nodes[-1]["exit_dependencies"] = ("pool",)
    nodes[-1]["monitor_dependencies"] = ("oracle",) if feed else ()
    nodes[-1]["depends_on"] += ("pool",) + (("oracle",) if feed else ())
    complete = bool(pool_found and wrapped_eth)
    error = (None if complete else data.get("error") or
             "A pool was found, but this token's backing/yield dependencies are unresolved. Use the Pendle PT adapter for Pendle positions.")
    ml_features = {
        name: data[name] for name in ("volume_24h_usd", "volatility_index")
        if data.get(name) is not None
    }
    return {
        "key": address, "chain": chain, "name": f"Custom position {address[:6]}…{address[-4:]} ({symbol})",
        "fallback_key": "pendle_pt_eeth", "token_address": address,
        "oracle_address": feed, "oracle_price": 3150.0 if wrapped_eth else None,
        "oracle_stale_hours": 0.0, "pool_liquidity_usd": data.get("top_pair_liquidity_usd", 0.0),
        "protocol_concentration_risk": 0.0, "depeg_risk": 0.0,
        "position_value_usd": 100_000, "default_status": "Discovered exposure" if pool_found else "Unresolved exposure",
        "data_source": f"discovery:{data['source']} ({chain})", "dependencies": nodes,
        "discovery_complete": complete, "discovery_error": error, "adapter": "token",
        "observed_at": data.get("observed_at", time.time()), "tvl_slugs": {},
        "ml_features": ml_features,
        "ml_feature_source": "DEX Screener 24h pair data" if ml_features else "neutral model defaults",
        "model_note": "Token/pool metadata discovery; supported exposure is configured native WETH. Other tokens require a protocol adapter. "
                      "Price monitors are not capital dependencies. Exit depth estimates and incident probabilities are assumptions.",
    }
