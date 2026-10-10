from unittest.mock import MagicMock, patch

import pytest

from modules import dependencies, fetcher
from modules.chains import CHAINS


def pair(chain, address, depth, pool="0x" + "2" * 40):
    return {"chainId": chain, "pairAddress": pool, "dexId": "uniswap",
            "liquidity": {"usd": depth}, "priceUsd": "3150",
            "baseToken": {"address": address, "symbol": "WETH"},
            "quoteToken": {"address": "0x" + "3" * 40, "symbol": "USDC"}}


@pytest.mark.parametrize("chain", list(CHAINS))
@patch("modules.fetcher.requests.get")
def test_discovery_uses_only_selected_chain_and_real_pool_tokens(mock_get, chain):
    address = CHAINS[chain].wrapped_eth
    other_chain = "optimism" if chain != "optimism" else "arbitrum"
    response = MagicMock()
    response.json.return_value = [pair(other_chain, address, 9_000_000), pair(chain, address, 700_000)]
    mock_get.return_value = response
    sc = dependencies.get_scenario(address, live=True, chain=chain)
    assert sc["discovery_complete"]
    assert sc["chain"] == chain
    assert sc["pool_liquidity_usd"] == 700_000
    assert sc["oracle_address"] == CHAINS[chain].eth_usd_feed
    assert any("3" * 40 in node["details"] for node in sc["dependencies"])
    assert not any(node["type"] == "Lending" for node in sc["dependencies"])
    if chain != "ethereum":
        assert {"l1", "sequencer"} <= {node["node_id"] for node in sc["dependencies"]}
    assert f"/tokens/v1/{chain}/" in mock_get.call_args.args[0]


@patch("modules.fetcher.requests.get")
def test_same_address_on_wrong_chain_is_not_treated_as_discovered(mock_get):
    address = CHAINS["optimism"].wrapped_eth
    mock_get.return_value.json.return_value = [pair("arbitrum", address, 10_000_000)]
    sc = dependencies.get_scenario(address, live=True, chain="optimism")
    assert not sc["discovery_complete"]
    assert sc["pool_liquidity_usd"] == 0
    assert "unavailable" in sc["data_source"]


@patch("modules.fetcher.requests.get", side_effect=ConnectionError("offline"))
def test_chain_discovery_never_invents_pool_data_on_failure(mock_get):
    sc = dependencies.get_scenario("0x" + "1" * 40, live=True, chain="arbitrum")
    assert not sc["discovery_complete"]
    assert sc["oracle_address"] is None
    assert "offline" in sc["discovery_error"]
    assert not any("Morpho" in node["label"] or "Ether.fi" in node["label"] for node in sc["dependencies"])


@patch("modules.fetcher._get_web3", side_effect=ConnectionError("offline"))
def test_live_monitor_reads_do_not_fall_back_to_demo_prices(mock_web3):
    result = fetcher.get_chainlink_price(CHAINS["arbitrum"].eth_usd_feed,
                                       chain="arbitrum", allow_mock=False)
    assert result["source"] == "unavailable"
    assert result["price"] is None
    mock_web3.assert_called_once_with("arbitrum")


@patch("modules.fetcher._get_web3")
def test_invalid_oracle_round_is_rejected(mock_web3):
    contract = mock_web3.return_value.eth.contract.return_value
    contract.functions.latestRoundData.return_value.call.return_value = (2, -100, 1, 1, 1)
    result = fetcher.get_chainlink_price(allow_mock=False)
    assert result["source"] == "unavailable"


def test_unverified_discovery_can_run_offline_without_network_calls():
    sc = dependencies.get_scenario("0x" + "1" * 40, live=False, chain="optimism")
    assert not sc["discovery_complete"]
    assert sc["pool_liquidity_usd"] == 0


def test_unknown_chain_is_rejected():
    with pytest.raises(ValueError, match="Unsupported chain"):
        dependencies.get_scenario("0x" + "1" * 40, chain="unknown")
