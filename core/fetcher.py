"""On-chain (web3) and market-data (DefiLlama / DEX Screener) fetchers.

Every public function returns a dict with a ``source`` key:
``"on-chain"`` / ``"live"`` when real data was fetched, ``"mock"`` when the
call failed (or was rate-limited) and the offline dataset was used instead.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

from . import mock_data

HTTP_TIMEOUT = 8
RPC_ENDPOINTS = ["https://eth.llamarpc.com", "https://cloudflare-eth.com"]

CHAINLINK_ABI = [
    {
        "inputs": [], "name": "latestRoundData", "stateMutability": "view", "type": "function",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
    },
    {
        "inputs": [], "name": "decimals", "stateMutability": "view", "type": "function",
        "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
    },
]


def _mock(fallback_key: str) -> dict:
    return mock_data.MOCK_MARKET_DATA.get(fallback_key, mock_data.MOCK_MARKET_DATA["pendle_pt_eeth"])


def _get_web3():
    from web3 import Web3  # imported lazily so the app still loads without web3

    last_err = None
    for url in RPC_ENDPOINTS:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": HTTP_TIMEOUT}))
            if w3.is_connected():
                return w3
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise ConnectionError(f"No public RPC reachable: {last_err}")


# --------------------------------------------------------------------------- #
# Oracle
# --------------------------------------------------------------------------- #
def get_chainlink_price(oracle_address: str = mock_data.CHAINLINK_ETH_USD,
                        fallback_key: str = "pendle_pt_eeth") -> dict:
    """Read ``latestRoundData()`` from a Chainlink aggregator via public RPC."""
    try:
        from web3 import Web3

        w3 = _get_web3()
        feed = w3.eth.contract(address=Web3.to_checksum_address(oracle_address), abi=CHAINLINK_ABI)
        _, answer, _, updated_at, _ = feed.functions.latestRoundData().call()
        decimals = feed.functions.decimals().call()
        return {
            "price": answer / 10 ** decimals,
            "updated_at": int(updated_at),
            "staleness_hours": max(0.0, (time.time() - updated_at) / 3600),
            "source": "on-chain",
        }
    except Exception as exc:  # noqa: BLE001
        m = _mock(fallback_key)["oracle"]
        return {"price": m["price"], "updated_at": None,
                "staleness_hours": m["staleness_hours"], "source": "mock", "error": str(exc)}


# --------------------------------------------------------------------------- #
# DEX liquidity
# --------------------------------------------------------------------------- #
def get_token_liquidity(token_address: str, fallback_key: str = "pendle_pt_eeth") -> dict:
    """Liquidity depth for a token from DEX Screener."""
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
                         timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        pairs = [p for p in (r.json().get("pairs") or []) if (p.get("liquidity") or {}).get("usd")]
        if not pairs:
            raise ValueError("DEX Screener returned no pairs with liquidity")
        top = max(pairs, key=lambda p: p["liquidity"]["usd"])
        return {
            "top_pair_liquidity_usd": float(top["liquidity"]["usd"]),
            "total_liquidity_usd": float(sum(p["liquidity"]["usd"] for p in pairs)),
            "top_dex": top.get("dexId", "unknown"),
            "pair_count": len(pairs),
            "price_usd": float(top["priceUsd"]) if top.get("priceUsd") else None,
            "source": "live",
        }
    except Exception as exc:  # noqa: BLE001
        m = dict(_mock(fallback_key)["liquidity"])
        m.update({"source": "mock", "error": str(exc)})
        return m


# --------------------------------------------------------------------------- #
# Protocol TVL
# --------------------------------------------------------------------------- #
def get_protocol_tvl(protocol_slug: str) -> dict:
    """Current TVL for a protocol from DefiLlama."""
    try:
        r = requests.get(f"https://api.llama.fi/protocol/{protocol_slug}", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        series = r.json().get("tvl") or []
        if not series:
            raise ValueError("No TVL series returned")
        return {"slug": protocol_slug, "tvl_usd": float(series[-1]["totalLiquidityUSD"]),
                "source": "live"}
    except Exception as exc:  # noqa: BLE001
        return {"slug": protocol_slug,
                "tvl_usd": float(mock_data.MOCK_PROTOCOL_TVL.get(protocol_slug, 0.0)),
                "source": "mock", "error": str(exc)}


# --------------------------------------------------------------------------- #
# Extra DefiLlama helpers (prices / yields)
# --------------------------------------------------------------------------- #
def get_coin_price(coin_id: str) -> Optional[float]:
    """``coin_id`` like ``ethereum:0x...``. Returns USD price or None."""
    try:
        r = requests.get(f"https://coins.llama.fi/prices/current/{coin_id}", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return float(r.json()["coins"][coin_id]["price"])
    except Exception:  # noqa: BLE001
        return None


def get_yield_pools(project: str, symbol_contains: str = "") -> list:
    """Filter DefiLlama's yield pools by project (e.g. ``pendle``)."""
    try:
        r = requests.get("https://yields.llama.fi/pools", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return [p for p in r.json().get("data", [])
                if p.get("project") == project and symbol_contains.lower() in p.get("symbol", "").lower()]
    except Exception:  # noqa: BLE001
        return []
