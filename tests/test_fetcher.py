from unittest.mock import patch, MagicMock
import pytest
from modules.fetcher import (
    get_token_liquidity, 
    get_protocol_tvl, 
    get_coin_price, 
    get_yield_pools,
    get_chainlink_price
)

# --- MOCKS PARA RESPUESTAS EXITOSAS DE RED ---

@patch('modules.fetcher.requests.get')
def test_get_token_liquidity_live_success(mock_get):
    """Cubre líneas 50-52 y 67-68: Respuesta exitosa con pares válidos de DEX Screener."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "pairs": [
            {"liquidity": {"usd": 600000}, "dexId": "uniswap", "priceUsd": "3200.50"},
            {"liquidity": {"usd": 400000}, "dexId": "sushiswap", "priceUsd": "3200.00"}
        ]
    }
    mock_get.return_value = mock_response

    res = get_token_liquidity("0xTokenAddress")
    assert res["source"] == "live"
    assert res["top_pair_liquidity_usd"] == 600000.0
    assert res["total_liquidity_usd"] == 1000000.0
    assert res["pair_count"] == 2


@patch("modules.fetcher.requests.get")
def test_get_token_liquidity_exposes_finite_ml_features(mock_get):
    token_address = "0x" + "1" * 40
    response = MagicMock()
    response.json.return_value = {"pairs": [{
        "chainId": "ethereum",
        "baseToken": {"address": token_address, "symbol": "TOK"},
        "quoteToken": {"address": "0x" + "2" * 40, "symbol": "USD"},
        "liquidity": {"usd": 600_000},
        "dexId": "uniswap",
        "priceUsd": "3200.50",
        "volume": {"h24": "120000"},
        "priceChange": {"h24": "-4.5"},
    }]}
    mock_get.return_value = response

    result = get_token_liquidity(token_address, chain="ethereum")

    assert result["volume_24h_usd"] == 120_000
    assert result["volatility_index"] == pytest.approx(0.045)

@patch('modules.fetcher.requests.get')
def test_get_token_liquidity_live_no_pairs(mock_get):
    """Cubre el bloque ValueError dentro del try si la API responde pero no trae pares."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"pairs": []}
    mock_get.return_value = mock_response

    res = get_token_liquidity("0xTokenAddress")
    assert res["source"] == "mock"  # Cae en el except y activa el fallback

@patch('modules.fetcher.requests.get')
def test_get_protocol_tvl_live_success(mock_get):
    """Cubre líneas 117 y 131-136: Respuesta exitosa con registros históricos en DefiLlama."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "tvl": [{"totalLiquidityUSD": 5000000000.0}]
    }
    mock_get.return_value = mock_response

    res = get_protocol_tvl("pendle")
    assert res["source"] == "live"
    assert res["tvl_usd"] == 5000000000.0

@patch('modules.fetcher.requests.get')
def test_get_coin_price_success_and_fail(mock_get):
    """Cubre líneas 141-147: Ambos caminos (éxito y excepción) para precios de monedas."""
    # Camino 1: Éxito
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"coins": {"ethereum:0x123": {"price": 3500.0}}}
    mock_get.return_value = mock_response
    assert get_coin_price("ethereum:0x123") == 3500.0

    # Camino 2: Excepción / Fallo
    mock_get.side_effect = Exception("Network Down")
    assert get_coin_price("ethereum:0x123") is None

@patch('modules.fetcher.requests.get')
def test_get_yield_pools_success_and_fail(mock_get):
    """Cubre líneas finales: Filtrado nominal y captura del except en bloques de rendimiento."""
    # Camino 1: Filtrado exitoso
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {"project": "pendle", "symbol": "PT-eETH"},
            {"project": "aave", "symbol": "aUSDC"}
        ]
    }
    mock_get.return_value = mock_response
    res = get_yield_pools("pendle", symbol_contains="eeth")
    assert len(res) == 1
    assert res[0]["project"] == "pendle"

    # Camino 2: Excepción / Fallo
    mock_get.side_effect = Exception("Timeout")
    assert get_yield_pools("pendle") == []

@patch('modules.fetcher._get_web3')
def test_get_chainlink_price_fail_path(mock_get_w3):
    """Asegura que el bloque except de get_chainlink_price se ejecute completamente."""
    mock_get_w3.side_effect = Exception("RPC Fail")
    res = get_chainlink_price()
    assert res["source"] == "mock"
    assert "RPC Fail" in res["error"]

@patch('modules.fetcher._get_web3')
def test_get_chainlink_price_on_chain_success(mock_get_w3):
    """Simula una respuesta exitosa de un contrato Chainlink cubriendo el bloque try de oráculos."""
    # Estructura de mocks para emular: w3.eth.contract().functions.latestRoundData().call()
    mock_w3 = MagicMock()
    mock_contract = MagicMock()
    
    # Simulamos latestRoundData() -> devuelve (roundId, answer, startedAt, updatedAt, answeredInRound)
    mock_contract.functions.latestRoundData.return_value.call.return_value = (1, 300000000000, 0, 1600000000, 1)
    # Simulamos decimals() -> devuelve 8 decimales tradicionales de Chainlink
    mock_contract.functions.decimals.return_value.call.return_value = 8
    
    mock_w3.eth.contract.return_value = mock_contract
    mock_get_w3.return_value = mock_w3
    
    res = get_chainlink_price()
    assert res["source"] == "on-chain"
    assert res["price"] == 3000.0


@patch('modules.fetcher.requests.get')
def test_exit_asset_marks_reject_stale_missing_or_invalid_prices(mock_get):
    import time
    response = MagicMock()
    mock_get.return_value = response
    coin = "ethereum:0x123"
    for item in ({"price": 1, "timestamp": time.time() - 901},
                 {"price": 1}, {"price": float("nan"), "timestamp": time.time()},
                 {"price": 0, "timestamp": time.time()}):
        response.json.return_value = {"coins": {coin: item}}
        assert get_coin_price(coin, max_age_seconds=900) is None
    response.json.return_value = {"coins": {coin: {"price": .99, "timestamp": time.time()}}}
    assert get_coin_price(coin, max_age_seconds=900) == .99
