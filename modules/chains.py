"""Chain-qualified connections: an address alone is not a position identity."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Chain:
    key: str
    name: str
    chain_id: int
    rpc_urls: tuple[str, ...]
    wrapped_eth: str
    eth_usd_feed: str
    explorer: str


CHAINS = {
    "ethereum": Chain("ethereum", "Ethereum Mainnet", 1,
                      ("https://eth.llamarpc.com", "https://ethereum-rpc.publicnode.com"),
                      "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
                      "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419", "https://etherscan.io"),
    "arbitrum": Chain("arbitrum", "Arbitrum One", 42161,
                      ("https://arb1.arbitrum.io/rpc", "https://arbitrum-one-rpc.publicnode.com"),
                      "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
                      "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612", "https://arbiscan.io"),
    "optimism": Chain("optimism", "Optimism", 10,
                      ("https://mainnet.optimism.io", "https://optimism-rpc.publicnode.com"),
                      "0x4200000000000000000000000000000000000006",
                      "0x13e3Ee699D1909E989722E753853AE30b17e08c5", "https://optimistic.etherscan.io"),
}


def get_chain(key: str = "ethereum") -> Chain:
    try:
        return CHAINS[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported chain: {key}") from exc
