from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from okx_signal_system.data.loader import SymbolData, closed_bars, load_all_symbols
from okx_signal_system.timeframe import timeframe_spec


@dataclass(frozen=True)
class QualityResult:
    inst_id: str
    rows: int
    first_ts: str
    last_ts: str
    missing_bars: int
    missing_ratio: float
    max_gap_hours: float
    duplicate_ts: int
    invalid_ohlc_rows: int
    non_positive_price_rows: int
    negative_volume_rows: int
    zero_volume_ratio: float
    open_rows: int
    status: str


def audit_symbol(data: SymbolData, *, expected_freq: str = "1h") -> QualityResult:
    df = data.frame.sort_values("ts").reset_index(drop=True)
    if expected_freq == "auto":
        expected_freq = str(df.get("timeframe", pd.Series(["1h"])).dropna().iloc[0] or "1h")
    expected = timeframe_spec(expected_freq).pandas_freq
    rows = len(df)
    if rows == 0:
        return QualityResult(data.inst_id, 0, "", "", 0, 1.0, 0.0, 0, 0, 0, 0, 0.0, 0, "failed")

    ts = pd.to_datetime(df["ts"], utc=True)
    first_ts = ts.iloc[0]
    last_ts = ts.iloc[-1]
    expected_index = pd.date_range(first_ts, last_ts, freq=expected, tz="UTC")
    unique_ts = pd.DatetimeIndex(ts.drop_duplicates())
    missing_bars = int(len(expected_index.difference(unique_ts)))
    missing_ratio = missing_bars / max(len(expected_index), 1)
    duplicate_ts = int(ts.duplicated().sum())
    diffs = ts.drop_duplicates().sort_values().diff().dropna()
    max_gap_hours = float(diffs.max().total_seconds() / 3600) if not diffs.empty else 0.0

    invalid_ohlc = (
        (df["high"] < df[["open", "close", "low"]].max(axis=1))
        | (df["low"] > df[["open", "close", "high"]].min(axis=1))
    )
    non_positive = (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    negative_volume = df["volume"] < 0
    zero_volume = df["volume"] == 0
    open_rows = rows - len(closed_bars(df))

    failed = (
        duplicate_ts > 0
        or missing_ratio > 0.02
        or invalid_ohlc.mean() > 0.0005
        or non_positive.any()
        or negative_volume.any()
        or zero_volume.mean() > 0.05
    )
    status = "failed" if failed else "passed"
    return QualityResult(
        inst_id=data.inst_id,
        rows=rows,
        first_ts=first_ts.isoformat(),
        last_ts=last_ts.isoformat(),
        missing_bars=missing_bars,
        missing_ratio=float(missing_ratio),
        max_gap_hours=max_gap_hours,
        duplicate_ts=duplicate_ts,
        invalid_ohlc_rows=int(invalid_ohlc.sum()),
        non_positive_price_rows=int(non_positive.sum()),
        negative_volume_rows=int(negative_volume.sum()),
        zero_volume_ratio=float(zero_volume.mean()),
        open_rows=int(open_rows),
        status=status,
    )


def audit_dataset(dataset: str = "okx_1h_extended", *, expected_freq: str = "auto") -> pd.DataFrame:
    results = [asdict(audit_symbol(symbol_data, expected_freq=expected_freq)) for symbol_data in load_all_symbols(dataset)]
    return pd.DataFrame(results).sort_values("inst_id").reset_index(drop=True)


def write_quality_report(output_path: str | Path, dataset: str = "okx_1h_extended", *, expected_freq: str = "auto") -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report = audit_dataset(dataset, expected_freq=expected_freq)
    report.to_csv(path, index=False, encoding="utf-8")
    return path
