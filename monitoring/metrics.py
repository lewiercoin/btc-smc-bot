from __future__ import annotations

from dataclasses import dataclass, field

SIGNALS_GENERATED = "signals_generated"
TRADES_OPENED = "trades_opened"
TRADES_CLOSED = "trades_closed"
GOVERNANCE_VETOES = "governance_vetoes"
RISK_BLOCKS = "risk_blocks"
ERRORS_TOTAL = "errors_total"
CYCLE_DURATION_MS = "cycle_duration_ms"


@dataclass(slots=True)
class MetricsRegistry:
    counters: dict[str, int] = field(default_factory=dict)
    gauges: dict[str, float] = field(default_factory=dict)

    def inc(self, name: str, value: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + value

    def set_gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def snapshot(self) -> dict[str, dict[str, int | float]]:
        return {
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
        }
