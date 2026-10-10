"""Offline fallback datasets.

All percentages are stored as fractions (0.015 == 1.5%).
The ``reference_*`` fields are the headline figures of each scenario at the
default $100k position size; the app recomputes live numbers from the model
parameters (pool liquidity, oracle staleness, concentration, depeg risk).
"""

PRESET_LABELS = {
    "Pendle PT-eETH (Normal)": "pendle_pt_eeth",
    "Compromised Oracle Vault (High Risk)": "compromised_vault",
}

# Public Chainlink ETH/USD mainnet aggregator (used for live oracle reads).
CHAINLINK_ETH_USD = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"

SCENARIOS = {
    # ------------------------------------------------------------------ #
    # Scenario 1 — Pendle PT-eETH (healthy)
    # ------------------------------------------------------------------ #
    "pendle_pt_eeth": {
        "chain": "ethereum",
        "key": "pendle_pt_eeth",
        "fallback_key": "pendle_pt_eeth",
        "name": "Pendle PT-eETH",
        "position_value_usd": 100_000,
        "default_status": "Healthy (Low Risk)",
        "reference_exit_slippage_pct": 0.015,
        "reference_stay_loss_pct": 0.008,
        # Model inputs
        "pool_liquidity_usd": 700_000,        # PT/SY AMM depth
        "oracle_stale_hours": 0.0,
        "protocol_concentration_risk": 0.03,  # 0..1
        "depeg_risk": 0.00375,                # 0..1 probability
        # Optional live-data hooks
        "oracle_address": CHAINLINK_ETH_USD,
        "token_address": None,
        "tvl_slugs": {"Yield": "ether.fi-stake", "AMM": "pendle", "Lending": "morpho-blue"},
        "dependencies": [
            {"layer": 1, "type": "Settlement", "label": "Ethereum Mainnet",
             "health_status": "healthy",
             "details": "Settlement layer. Finality and liveness nominal."},
            {"layer": 2, "type": "Yield", "label": "Ether.fi (eETH)",
             "health_status": "healthy",
             "details": "Underlying liquid-restaking yield source; eETH trades at peg."},
            {"layer": 3, "type": "Oracle", "label": "Chainlink / RedStone ETH Oracle",
             "health_status": "healthy",
             "details": "Redundant price feeds, fresh heartbeat."},
            {"layer": 4, "type": "AMM", "label": "Pendle PT/SY AMM Pool",
             "health_status": "healthy",
             "details": "Deep liquidity venue for PT exit; position is a small share of the pool."},
            {"layer": 5, "type": "Lending", "label": "Morpho Blue (Collateral Market)",
             "health_status": "healthy",
             "details": "PT accepted as collateral; low protocol concentration."},
        ],
    },
    # ------------------------------------------------------------------ #
    # Scenario 2 — Compromised vault / fragile oracle
    # ------------------------------------------------------------------ #
    "compromised_vault": {
        "chain": "arbitrum",
        "key": "compromised_vault",
        "fallback_key": "compromised_vault",
        "name": "Compromised Vault / Fragile Oracle",
        "position_value_usd": 100_000,
        "default_status": "High Risk / Stale Oracle",
        "reference_exit_slippage_pct": 0.11,
        "reference_stay_loss_pct": 0.325,
        "pool_liquidity_usd": 200_000,
        "oracle_stale_hours": 3.0,
        "protocol_concentration_risk": 0.5,
        "depeg_risk": 0.03125,
        "oracle_address": None,
        "token_address": None,
        "tvl_slugs": {},
        "dependencies": [
            {"layer": 1, "type": "Settlement", "label": "Arbitrum One",
             "health_status": "healthy",
             "details": "L2 settlement; sequencer operating normally."},
            {"layer": 2, "type": "Yield", "label": "Protocol X Staked Vault",
             "health_status": "critical",
             "details": "Vault flagged as compromised; single-operator upgrade keys."},
            {"layer": 3, "type": "Oracle", "label": "Single-Source Custom Price Feed",
             "health_status": "critical",
             "details": "No redundancy; last update more than 2 hours ago."},
            {"layer": 4, "type": "AMM", "label": "Low-Liquidity AMM Pool ($200k TVL)",
             "health_status": "critical",
             "details": "Thin pool; exit size is a large share of total TVL."},
            {"layer": 5, "type": "Lending", "label": "Secondary Yield Market",
             "health_status": "warning",
             "details": "Small secondary market, concentrated depositors."},
        ],
    },
}

# Offline replacements for each live fetcher, keyed by scenario key.
MOCK_MARKET_DATA = {
    "pendle_pt_eeth": {
        "oracle": {"price": 3150.0, "staleness_hours": 0.2},
        "liquidity": {"top_pair_liquidity_usd": 700_000, "total_liquidity_usd": 1_050_000,
                      "top_dex": "pendle", "pair_count": 3},
    },
    "compromised_vault": {
        "oracle": {"price": 1.0, "staleness_hours": 3.0},
        "liquidity": {"top_pair_liquidity_usd": 200_000, "total_liquidity_usd": 200_000,
                      "top_dex": "unknown-amm", "pair_count": 1},
    },
}

MOCK_PROTOCOL_TVL = {
    "ether.fi-stake": 6_500_000_000,
    "pendle": 4_000_000_000,
    "morpho-blue": 5_000_000_000,
}
