"""Replaceable market-data provider boundary and deterministic CSV replay."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal, Protocol

import pandas as pd

from .models import Bar, ReferenceClose
from .data_sources.common import volume_in_shares


class MarketDataProvider(Protocol):
    """A vendor adapter must emit normalized domain objects, never raw SDK rows."""

    def bars(self, symbol: str, interval: str) -> Iterable[Bar]: ...

    def reference_closes(self, symbol: str) -> Iterable[ReferenceClose]: ...


class CSVReplayProvider:
    """Read a deterministic replay fixture without network or vendor coupling."""

    def __init__(
        self,
        bars_path: str | Path,
        reference_closes_path: str | Path | None = None,
        volume_unit: Literal["shares", "lots"] = "shares",
    ) -> None:
        self.bars_path = Path(bars_path)
        self.reference_closes_path = None if reference_closes_path is None else Path(reference_closes_path)
        self.volume_unit = volume_unit

    def bars(self, symbol: str, interval: str = "1m") -> Iterable[Bar]:
        frame = pd.read_csv(self.bars_path)
        if "symbol" in frame:
            frame = frame[frame["symbol"].str.upper() == symbol.upper()]
        if "interval" in frame:
            frame = frame[frame["interval"] == interval]
        frame = frame.sort_values("timestamp")
        for row in frame.to_dict(orient="records"):
            row = {key: (None if pd.isna(value) else value) for key, value in row.items()}
            row["symbol"] = symbol
            row["interval"] = interval
            row["volume"] = volume_in_shares(row.get("volume"), "volume", self.volume_unit)
            row["source"] = "csv"
            yield Bar.model_validate(row)

    def reference_closes(self, symbol: str) -> Iterable[ReferenceClose]:
        if self.reference_closes_path is None:
            return
        frame = pd.read_csv(self.reference_closes_path)
        if "symbol" in frame:
            frame = frame[frame["symbol"].str.upper() == symbol.upper()]
        for row in frame.to_dict(orient="records"):
            row = {key: (None if pd.isna(value) else value) for key, value in row.items()}
            row["symbol"] = symbol
            row["source"] = "csv"
            yield ReferenceClose.model_validate(row)
