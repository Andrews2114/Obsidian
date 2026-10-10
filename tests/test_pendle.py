import copy
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from modules import pendle, discovery, impact

MARKET, PT, SY, YT, UNDERLYING, ACCOUNTING = ["0x" + digit * 40 for digit in "123456"]


@pytest.fixture
def market():
    def token(address, symbol):
        return {"address": address, "symbol": symbol, "decimals": 18, "protocol": "Yield provider",
                "price": {"usd": 2}, "priceUpdatedAt": datetime.now(timezone.utc).isoformat()}
    return {"address": MARKET, "chainId": 1, "expiry": "2027-12-30T00:00:00.000Z",
        "pt": token(PT, "PT"), "sy": token(SY, "SY"), "yt": token(YT, "YT"),
        "underlyingAsset": token(UNDERLYING, "Yield token"),
        "accountingAsset": token(ACCOUNTING, "Accounting token"),
        "liquidity": {"usd": 5_000_000, "acc": 2_500_000}}


def test_pt_lookup_paginates_and_rejects_other_chains(monkeypatch, market):
    calls = []
    def get(path, **params):
        calls.append((path, params))
        if path.startswith("/v1/"):
            return market
        if params["skip"] == 0:
            return {"total": 101, "results": [{"chainId": 42161, "address": MARKET,
                "pt": f"42161-{PT}"}] * 100}
        return {"total": 101, "results": [{"chainId": 1, "address": MARKET, "pt": f"1-{PT}"}]}
    monkeypatch.setattr(pendle, "_get", get)
    assert pendle.find_market(PT.upper(), "ethereum") == market
    assert calls[1][1]["skip"] == 100
    assert calls[-1][0] == f"/v1/1/markets/{MARKET}"


def test_contract_relationships_are_verified_at_one_block(monkeypatch, market):
    w3, mc, sy, pt = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    w3.eth.block_number = 123
    w3.eth.get_block.return_value = {"timestamp": 1700000000}
    w3.eth.contract.side_effect = [mc, pt, sy]
    mc.functions.readTokens.return_value.call.return_value = (SY, PT, YT)
    pt.functions.decimals.return_value.call.return_value = 18
    pt.functions.expiry.return_value.call.return_value = int(datetime.fromisoformat(market["expiry"].replace("Z", "+00:00")).timestamp())
    sy.functions.getTokensOut.return_value.call.return_value = [UNDERLYING]
    sy.functions.assetInfo.return_value.call.return_value = (0, ACCOUNTING, 18)
    sy.functions.exchangeRate.return_value.call.return_value = 1_100_000_000_000_000_000
    monkeypatch.setattr(pendle.fetcher, "_get_web3", lambda chain: w3)
    r = pendle.verify_market(market, "ethereum")
    assert r["block"] == 123 and r["exchange_rate"] == 1.1
    for fn in [mc.functions.readTokens, pt.functions.decimals, pt.functions.expiry, sy.functions.getTokensOut,
               sy.functions.assetInfo, sy.functions.exchangeRate]:
        fn.return_value.call.assert_called_once_with(block_identifier=123)
    w3.eth.contract.side_effect = [mc, pt, sy]
    mc.functions.readTokens.return_value.call.return_value = (SY, UNDERLYING, YT)
    with pytest.raises(ValueError, match="mismatch"):
        pendle.verify_market(market, "ethereum")


def test_no_fake_graph_on_rpc_failure(monkeypatch, market):
    monkeypatch.setattr(pendle, "find_market", lambda *a: market)
    def unavailable(*a):
        raise ConnectionError("offline RPC")
    monkeypatch.setattr(pendle, "verify_market", unavailable)
    sc = discovery.discover_pendle(PT, "ethereum")
    assert not sc["discovery_complete"]
    assert "offline RPC" in sc["discovery_error"]
    assert not any(n["type"] == "Lending" for n in sc["dependencies"])


def test_real_response_shape_builds_backing_graph_and_liquidity(monkeypatch, market):
    monkeypatch.setattr(pendle, "snapshot", lambda *a: {"source": "on-chain + Pendle API",
        "market": market, "evidence": {"block": 123, "exchange_rate": 1.1}, "observed_at": 1700000000})
    sc = discovery.discover_pendle(MARKET, "ethereum", oracle_address=pendle.USDC["ethereum"])
    assert sc["discovery_complete"] and sc["pool_liquidity_usd"] == 5_000_000
    assert sc["token_address"] == PT
    assert impact.propagate(sc, "yield", .3)["path"] == ["yield", "sy", "asset", "position"]
    assert impact.propagate(sc, "oracle", 1)["loss_fraction"] == 0


def test_quote_uses_raw_decimals_best_route_and_actual_usdc_mark(monkeypatch, market):
    market["pt"]["decimals"] = 6
    response = MagicMock()
    response.ok = True
    def route(amount):
        return {"outputs": [{"token": pendle.USDC["ethereum"], "amount": str(amount)}],
                "data": {"aggregatorType": "Example", "priceImpact": -.03}, "tx": {"to": MARKET}}
    response.json.return_value = {"routes": [route(96_000_000_000), route(97_000_000_000)]}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(pendle.requests, "post", post)
    monkeypatch.setattr(pendle.fetcher, "get_coin_price", lambda key, **kw: .99)
    q = pendle.exit_quote({"pendle_market": market, "chain": "ethereum"}, 100_000)
    assert post.call_args.kwargs["json"]["inputs"][0]["amount"] == "50000000000"
    assert q["cost_usd"] == pytest.approx(100_000 - 97_000 * .99)
    assert q["output_price_usd"] == .99
    assert q["source"] == "Pendle quote preview"


@pytest.mark.parametrize("problem", ["stale", "no_routes", "api_failure", "missing_usdc_price"])
def test_bad_quote_data_is_unavailable_not_a_demo_estimate(monkeypatch, market, problem):
    market = copy.deepcopy(market)
    response = MagicMock()
    response.ok = problem != "api_failure"
    response.status_code = 503
    response.text = "No route"
    response.json.return_value = {"routes": [] if problem == "no_routes" else [{
        "outputs": [{"token": pendle.USDC["ethereum"], "amount": "97000000000"}]}]}
    monkeypatch.setattr(pendle.requests, "post", lambda *a, **kw: response)
    monkeypatch.setattr(pendle.fetcher, "get_coin_price", lambda key, **kw: None if problem == "missing_usdc_price" else 1)
    if problem == "stale":
        market["pt"]["priceUpdatedAt"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    q = pendle.exit_quote({"pendle_market": market, "chain": "ethereum"}, 100_000)
    assert q["source"] == "unavailable" and "cost_usd" not in q


def test_generic_token_pool_does_not_claim_wrapper_discovery_is_complete(monkeypatch):
    monkeypatch.setattr(discovery.fetcher, "get_token_liquidity", lambda *a, **kw: {
        "source": "live", "pair_address": MARKET, "top_dex": "dex", "observed_at": 1,
        "top_pair_liquidity_usd": 1_000_000, "base_token": {"address": PT, "symbol": "PT"},
        "quote_token": {"address": ACCOUNTING, "symbol": "USD"}})
    sc = discovery.discover_token(PT, "ethereum")
    assert not sc["discovery_complete"]
    assert "unresolved" in sc["discovery_error"]
