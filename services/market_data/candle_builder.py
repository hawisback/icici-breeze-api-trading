"""Real-time candle builder aggregating ticks into 1m, 5m, and 15m OHLCV bars.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from libs.contracts.models import Candle, Quote
from libs.market_time import IST

logger = logging.getLogger(__name__)


class CandleBuilder:
    """Builds and finalizes OHLCV bars from incoming quote streams."""

    def __init__(self, interval_minutes: int = 1) -> None:
        self.interval_minutes = interval_minutes
        self._current_candles: dict[str, dict] = {}
        self._last_quotes: dict[str, Quote] = {}

    def _get_bar_start(self, dt: datetime) -> datetime:
        minute = (dt.minute // self.interval_minutes) * self.interval_minutes
        return dt.replace(minute=minute, second=0, microsecond=0)

    def process_quote(self, quote: Quote) -> Optional[Candle]:
        """Update current bar with quote. If a bar boundary is crossed, return completed candle."""
        bar_start = self._get_bar_start(quote.timestamp)
        inst_id = quote.instrument_id
        active = self._current_candles.get(inst_id)
        previous = self._last_quotes.get(inst_id)
        if previous and quote.timestamp <= previous.timestamp:
            return None
        same_feed = previous is not None and previous.source == quote.source
        same_day = previous is not None and previous.timestamp.astimezone(IST).date() == quote.timestamp.astimezone(IST).date()
        volume_delta = max(0, quote.volume - previous.volume) if same_feed and same_day else 0
        self._last_quotes[inst_id] = quote
        if active and active["source"] != quote.source:
            active = None

        completed_candle: Optional[Candle] = None

        if active and active["start_time"] != bar_start:
            # Bar completed!
            completed_candle = Candle(
                instrument_id=inst_id,
                interval=f"{self.interval_minutes}m",
                start_time=active["start_time"],
                end_time=active["start_time"] + timedelta(minutes=self.interval_minutes),
                open=active["open"],
                high=active["high"],
                low=active["low"],
                close=active["close"],
                volume=active["volume"],
                open_interest=active["open_interest"],
                source=active["source"],
            )
            active = None

        if active is None:
            self._current_candles[inst_id] = {
                "start_time": bar_start,
                "open": quote.last_price,
                "high": quote.last_price,
                "low": quote.last_price,
                "close": quote.last_price,
                "volume": volume_delta,
                "open_interest": quote.open_interest,
                "source": quote.source,
            }
        else:
            active["high"] = max(active["high"], quote.last_price)
            active["low"] = min(active["low"], quote.last_price)
            active["close"] = quote.last_price
            active["volume"] += volume_delta
            active["open_interest"] = quote.open_interest

        return completed_candle
