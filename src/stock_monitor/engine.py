"""Application service that joins storage, rules, and deduplication."""

from __future__ import annotations

from .models import EvaluationResult, RuleConfig
from .rules import evaluate
from .storage import SQLiteRepository


class MonitorEngine:
    def __init__(self, repository: SQLiteRepository) -> None:
        self.repository = repository

    def evaluate_symbol(self, symbol: str, interval: str = "1m", config: RuleConfig | None = None) -> EvaluationResult:
        config = config or RuleConfig()
        bars = self.repository.load_closed_bars(symbol, interval)
        trading_date = bars.iloc[-1]["trading_date"] if not bars.empty else None
        previous_close = None if trading_date is None else self.repository.get_reference_close(symbol, trading_date)
        result = evaluate(symbol, interval, bars, previous_close=previous_close, config=config)
        accepted = []
        suppressed = 0
        for alert in result.alerts:
            if self.repository.insert_alert_if_new(alert, config.cooldown_seconds):
                accepted.append(alert)
            else:
                suppressed += 1
        result.alerts = accepted
        result.diagnostics["suppressed_alerts"] = suppressed
        return result

