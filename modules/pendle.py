"""Read-only Pendle PT discovery and executable-size exit quote previews.

API metadata locates a market; contract reads verify its economic relationships.
No transaction is signed or submitted. LP and YT exposures need separate models.
"""
from datetime import datetime, timezone
from decimal import Decimal
import math
import time

import requests
from web3 import Web3

from . import fetcher
from .chains import get_chain

BASE = "https://api-v2.pendle.finance/core"
USDC = {
    "ethereum": "0xA0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "arbitrum": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    "optimism": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
}
EXAMPLE_MARKET = "0x34280882267ffa6383b363e278b027be083bbe3b"


def abi(name, outputs):
    return {"type": "function", "name": name, "stateMutability": "view",
            "inputs": [], "outputs": [{"name": "", "type": t} for t in outputs]}


def _get(path, **params):
    response = requests.get(BASE + path, params=params, timeout=fetcher.HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _positive(value, name):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"Missing or invalid {name}")
    return number


def find_market(address, chain):
    """Resolve exactly a market or PT address, including beyond the first page."""
    chain_id = get_chain(chain).chain_id
    for skip in range(0, 10_000, 100):
        page = _get("/v2/markets/all", chainId=chain_id, limit=100, skip=skip)
        rows = page.get("results", [])
        for row in rows:
            if row.get("chainId") != chain_id:
                continue
            pt = row.get("pt", "").split("-", 1)[-1]
            if address.lower() in (row["address"].lower(), pt.lower()):
                return _get(f"/v1/{chain_id}/markets/{row['address']}")
        if not rows or skip + len(rows) >= page.get("total", 0):
            break
    raise ValueError("No Pendle market/PT matches this address on the selected chain. "
                     "Wallet, SY, YT and LP holdings are not supported by the PT adapter.")


def verify_market(market, chain):
    """Pin all reads to one block and compare API relationships with contracts."""
    w3 = fetcher._get_web3(chain)
    block = w3.eth.block_number
    market_contract = w3.eth.contract(address=Web3.to_checksum_address(market["address"]),
        abi=[abi("readTokens", ["address", "address", "address"])])
    sy, pt, yt = market_contract.functions.readTokens().call(block_identifier=block)
    for field, actual in (("sy", sy), ("pt", pt), ("yt", yt)):
        if market[field]["address"].lower() != actual.lower():
            raise ValueError(f"API/contract mismatch for {field}")
    pt_contract = w3.eth.contract(address=pt, abi=[abi("decimals", ["uint8"]), abi("expiry", ["uint256"])])
    pt_decimals = pt_contract.functions.decimals().call(block_identifier=block)
    pt_expiry = pt_contract.functions.expiry().call(block_identifier=block)
    api_expiry = int(datetime.fromisoformat(market["expiry"].replace("Z", "+00:00")).timestamp())
    if pt_decimals != market["pt"]["decimals"] or pt_expiry != api_expiry:
        raise ValueError("API/contract mismatch for PT decimals or maturity")
    sy_contract = w3.eth.contract(address=sy, abi=[
        abi("getTokensOut", ["address[]"]),
        abi("assetInfo", ["uint8", "address", "uint8"]),
        abi("exchangeRate", ["uint256"]),
    ])
    outputs = sy_contract.functions.getTokensOut().call(block_identifier=block)
    _, accounting, _ = sy_contract.functions.assetInfo().call(block_identifier=block)
    rate = sy_contract.functions.exchangeRate().call(block_identifier=block) / 1e18
    _positive(rate, "SY exchange rate")
    underlying = market["underlyingAsset"]["address"].lower()
    if underlying not in [a.lower() for a in outputs]:
        raise ValueError("Underlying asset is not a verified SY redemption output")
    if accounting.lower() != market["accountingAsset"]["address"].lower():
        raise ValueError("API/contract mismatch for SY accounting asset")
    return {"block": block, "block_timestamp": int(w3.eth.get_block(block)["timestamp"]),
            "redemption_tokens": outputs, "exchange_rate": rate}


def snapshot(address, chain):
    config = get_chain(chain)
    try:
        market = find_market(address, chain)
        if market.get("chainId") != config.chain_id:
            raise ValueError("Market metadata belongs to another chain")
        evidence = verify_market(market, chain)
        _positive(market["pt"]["price"]["usd"], "PT price")
        # This adapter does not substitute a fabricated graph when RPC is unavailable.
        return {"source": "on-chain + Pendle API", "market": market,
                "evidence": evidence, "observed_at": time.time()}
    except Exception as exc:
        return {"source": "unavailable", "error": str(exc), "chain": chain}


def exit_quote(scenario, position_usd):
    """Quote sale of the USD-marked PT amount to native USDC; fees included.

    Gas is added by the decision model. Slippage tolerance is a transaction limit,
    not an additional expected cost. A successful preview is not a mined fill.
    """
    try:
        market = scenario["pendle_market"]
        chain = scenario["chain"]
        price = _positive(market["pt"]["price"]["usd"], "PT price")
        price_time = datetime.fromisoformat(market["pt"]["priceUpdatedAt"].replace("Z", "+00:00"))
        if abs((datetime.now(timezone.utc) - price_time).total_seconds()) > 900:
            raise ValueError("PT price is over 15 minutes old; refresh discovery")
        decimals = int(market["pt"]["decimals"])
        if not 0 <= decimals <= 36:
            raise ValueError("Unsupported token decimals")
        amount = int(Decimal(str(position_usd)) / Decimal(str(price)) * Decimal(10) ** decimals)
        if amount <= 0:
            raise ValueError("Position amount rounds to zero")
        output_token = USDC[chain]
        # Dummy receiver is only used to construct a read-only preview.
        response = requests.post(BASE + f"/v3/sdk/{get_chain(chain).chain_id}/convert",
            json={"receiver": "0x1111111111111111111111111111111111111111",
                  "slippage": 0.01, "enableAggregator": True,
                  "inputs": [{"token": market["pt"]["address"], "amount": str(amount)}],
                  "outputs": [output_token]}, timeout=20)
        if not response.ok:
            raise ValueError(f"Quote API {response.status_code}: {response.text[:300]}")
        payload = response.json()
        routes = payload.get("routes", [payload] if "outputs" in payload else [])
        candidates = [(route, output) for route in routes for output in route.get("outputs", [])
                      if output["token"].lower() == output_token.lower()]
        if not candidates:
            raise ValueError("No PT → USDC route returned")
        route, output = max(candidates, key=lambda item: int(item[1]["amount"]))
        output_units = _positive(output["amount"], "quoted output") / 1e6
        output_price = _positive(fetcher.get_coin_price(f"{chain}:{output_token.lower()}", max_age_seconds=900), "USDC price")
        output_usd = output_units * output_price
        input_usd = amount / 10 ** decimals * price
        return {"source": "Pendle quote preview", "observed_at": time.time(),
                "input_usd": input_usd, "output_usd": output_usd,
                "cost_usd": max(0.0, input_usd - output_usd), "amount_raw": str(amount),
                "output_token": output_token, "output_price_usd": output_price,
                "price_impact": route.get("data", {}).get("priceImpact"),
                "router": route.get("tx", {}).get("to"),
                "aggregator": route.get("data", {}).get("aggregatorType", "unresolved"),
                "gas_used": route.get("data", {}).get("gasUsed"),
                "note": "PT → USDC; swap/aggregator fees included; gas assumption added separately."}
    except Exception as exc:
        return {"source": "unavailable", "error": str(exc), "observed_at": time.time()}
