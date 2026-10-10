"""Process simulated log events and live oracle observations identically.

The baseline is the preceding valid observation of the same feed and source.
Alerts latch until explicitly reset; acknowledging the popup does not clear risk.
"""
from dataclasses import dataclass, field
from math import isfinite

DEVIATION_THRESHOLD_PCT = 3.0


def exceeds_deviation_threshold(deviation_pct: float) -> bool:
    # A tiny tolerance prevents a floating-point representation of exactly 3%
    # from firing a strictly greater-than-3% trigger.
    return abs(deviation_pct) > DEVIATION_THRESHOLD_PCT + 1e-9


@dataclass
class OracleMonitor:
    scope: str
    previous_price: float | None = None
    last_event_id: str | None = None
    incident: dict | None = None
    history: list[dict] = field(default_factory=list)

    def observe(self, sample: dict) -> dict | None:
        """Ignore fallback data, duplicate/out-of-order events and invalid prices."""
        if sample.get("source") not in {"on-chain", "simulated"}:
            return None
        try:
            price = float(sample["price"])
            timestamp = float(sample["updated_at"])
        except (KeyError, TypeError, ValueError):
            return None
        if not isfinite(price) or price <= 0 or not isfinite(timestamp) or timestamp <= 0:
            return None
        event_id = str(sample.get("round_id", sample["updated_at"]))
        if event_id == self.last_event_id:
            return None
        if self.history and timestamp < self.history[-1]["updated_at"]:
            return None
        previous = self.previous_price
        deviation = 100 * (price - previous) / previous if previous else 0.0
        observation = {"event_id": event_id, "price": price, "previous_price": previous,
                       "deviation_pct": deviation, "updated_at": timestamp,
                       "source": sample["source"]}
        self.last_event_id = event_id
        self.previous_price = price
        self.history = (self.history + [observation])[-25:]
        if previous is not None and exceeds_deviation_threshold(deviation):
            self.incident = {**observation, "scope": self.scope}
            return self.incident
        return None

    def reset(self):
        self.incident = None
