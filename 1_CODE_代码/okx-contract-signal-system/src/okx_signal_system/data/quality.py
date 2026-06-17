from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from okx_signal_system.data.loader import MISSING_REQUIRED_IS_CLOSED_COLUMN, SymbolData, load_all_symbols
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
    non_tail_open_rows: int = 0
    invalid_numeric_rows: int = 0
    invalid_timestamp_rows: int = 0
    timestamp_boundary_rows: int = 0
    irregular_interval_rows: int = 0
    internal_gap_count: int = 0
    max_gap_bars: int = 0
    symbol_mismatch_rows: int = 0
    timeframe_mismatch_rows: int = 0
    invalid_quote_volume_rows: int = 0
    error_code: str = ""


def _is_closed_value(value: object) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"true", "1", "yes"}


def _open_row_metrics(df: pd.DataFrame) -> tuple[int, int, bool]:
    if "is_closed" not in df.columns:
        return 0, 0, False
    closed_mask = df["is_closed"].map(_is_closed_value)
    open_mask = ~closed_mask
    open_rows = int(open_mask.sum())
    if open_rows == 0:
        return 0, 0, False
    last_is_open = bool(open_mask.iloc[-1])
    non_tail_open_rows = open_rows - (1 if last_is_open else 0)
    return open_rows, int(non_tail_open_rows), last_is_open


def _time_gap_metrics(ts: pd.Series, *, expected_minutes: int) -> tuple[int, float, float, int, int]:
    valid_ts = pd.to_datetime(ts, utc=True, errors="coerce").dropna()
    if valid_ts.empty:
        return 0, 0.0, 0, 0, 0

    first_ts = valid_ts.iloc[0]
    last_ts = valid_ts.iloc[-1]
    expected_index = pd.date_range(
        first_ts,
        last_ts,
        freq=pd.Timedelta(minutes=expected_minutes),
        tz="UTC",
    )
    unique_ts = pd.DatetimeIndex(valid_ts.drop_duplicates())
    missing_bars = int(len(expected_index.difference(unique_ts)))
    missing_ratio = missing_bars / max(len(expected_index), 1)

    diffs = valid_ts.drop_duplicates().sort_values().diff().dropna()
    expected_seconds = expected_minutes * 60
    gap_sizes: list[int] = []
    irregular_interval_rows = 0
    for diff in diffs:
        diff_seconds = diff.total_seconds()
        ratio = diff_seconds / expected_seconds
        if not np.isclose(ratio, 1.0):
            irregular_interval_rows += 1
        if ratio > 1.0:
            gap_sizes.append(max(1, int(round(ratio)) - 1))

    max_gap_hours = float(diffs.max().total_seconds() / 3600) if not diffs.empty else 0.0
    return missing_bars, float(missing_ratio), max_gap_hours, irregular_interval_rows, max(gap_sizes, default=0)


def _timestamp_boundary_rows(ts: pd.Series, *, expected_minutes: int) -> int:
    valid_ts = pd.to_datetime(ts, utc=True, errors="coerce").dropna()
    if valid_ts.empty:
        return 0
    interval = pd.Timedelta(minutes=expected_minutes)
    return int((valid_ts.dt.floor(interval) != valid_ts).sum())


def _timeframe_mismatch_rows(values: pd.Series, expected_freq: str) -> int:
    expected_key = timeframe_spec(expected_freq).key
    mismatches = 0
    for value in values:
        if pd.isna(value):
            mismatches += 1
            continue
        try:
            if timeframe_spec(str(value)).key != expected_key:
                mismatches += 1
        except ValueError:
            mismatches += 1
    return mismatches


def audit_symbol(
    data: SymbolData,
    *,
    expected_freq: str = "1h",
    allow_runtime_open_tail: bool = False,
) -> QualityResult:
    df = data.frame.copy()
    missing_required_is_closed = "is_closed" not in df.columns
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.sort_values("ts", na_position="last").reset_index(drop=True)
    rows = len(df)
    if rows == 0:
        return QualityResult(data.inst_id, 0, "", "", 0, 1.0, 0.0, 0, 0, 0, 0, 0.0, 0, "failed")
    if expected_freq == "auto":
        expected_freq = str(df.get("timeframe", pd.Series(["1h"])).dropna().iloc[0] or "1h")
    spec = timeframe_spec(expected_freq)

    ts = df["ts"]
    valid_ts = ts.dropna()
    first_ts = valid_ts.iloc[0] if not valid_ts.empty else None
    last_ts = valid_ts.iloc[-1] if not valid_ts.empty else None
    invalid_timestamp_rows = int(ts.isna().sum())
    duplicate_ts = int(ts.dropna().duplicated().sum())
    open_rows, non_tail_open_rows, last_is_open = _open_row_metrics(df)
    allowed_tail_open = allow_runtime_open_tail and open_rows == 1 and last_is_open
    check_df = df.iloc[:-1].copy() if allowed_tail_open else df.copy()

    missing_bars, missing_ratio, max_gap_hours, irregular_interval_rows, max_gap_bars = _time_gap_metrics(
        check_df["ts"],
        expected_minutes=spec.minutes,
    )
    timestamp_boundary_rows = _timestamp_boundary_rows(check_df["ts"], expected_minutes=spec.minutes)
    internal_gap_count = int(irregular_interval_rows if max_gap_bars > 0 else 0)

    numeric_cols = ["open", "high", "low", "close", "volume"]
    if "quote_volume" in check_df.columns:
        numeric_cols.append("quote_volume")
    numeric = check_df[numeric_cols].apply(pd.to_numeric, errors="coerce")
    numeric_float = numeric.astype("float64")
    invalid_numeric = pd.DataFrame(
        ~np.isfinite(numeric_float.to_numpy()),
        index=numeric_float.index,
        columns=numeric_float.columns,
    )
    invalid_numeric_rows = invalid_numeric.any(axis=1)

    price_cols = ["open", "high", "low", "close"]
    invalid_ohlc = (
        (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1))
        | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1))
    ).fillna(False)
    non_positive = (numeric[price_cols] <= 0).any(axis=1)
    negative_volume = numeric["volume"] < 0
    zero_volume = numeric["volume"] == 0
    if "quote_volume" in numeric.columns:
        invalid_quote_volume = invalid_numeric["quote_volume"] | (numeric["quote_volume"] < 0).fillna(False)
    else:
        invalid_quote_volume = pd.Series(False, index=check_df.index)

    if "symbol" in df.columns:
        symbol_mismatch_rows = int((df["symbol"].astype("string").str.strip() != data.inst_id).fillna(True).sum())
    else:
        symbol_mismatch_rows = 0
    timeframe_mismatch_rows = _timeframe_mismatch_rows(df["timeframe"], spec.key) if "timeframe" in df.columns else 0

    failed = (
        missing_required_is_closed
        or
        invalid_timestamp_rows > 0
        or timestamp_boundary_rows > 0
        or duplicate_ts > 0
        or irregular_interval_rows > 0
        or missing_ratio > 0.02
        or int(invalid_numeric_rows.sum()) > 0
        or invalid_ohlc.any()
        or non_positive.any()
        or negative_volume.any()
        or zero_volume.mean() > 0.05
        or (open_rows > 0 if not allow_runtime_open_tail else (open_rows > 1 or non_tail_open_rows > 0))
        or symbol_mismatch_rows > 0
        or timeframe_mismatch_rows > 0
        or int(invalid_quote_volume.sum()) > 0
    )
    status = "failed" if failed else "passed"
    return QualityResult(
        inst_id=data.inst_id,
        rows=rows,
        first_ts=first_ts.isoformat() if first_ts is not None else "",
        last_ts=last_ts.isoformat() if last_ts is not None else "",
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
        non_tail_open_rows=int(non_tail_open_rows),
        invalid_numeric_rows=int(invalid_numeric_rows.sum()),
        invalid_timestamp_rows=invalid_timestamp_rows,
        timestamp_boundary_rows=timestamp_boundary_rows,
        irregular_interval_rows=int(irregular_interval_rows),
        internal_gap_count=internal_gap_count,
        max_gap_bars=int(max_gap_bars),
        symbol_mismatch_rows=symbol_mismatch_rows,
        timeframe_mismatch_rows=timeframe_mismatch_rows,
        invalid_quote_volume_rows=int(invalid_quote_volume.sum()),
        error_code=MISSING_REQUIRED_IS_CLOSED_COLUMN if missing_required_is_closed else "",
    )


def audit_dataset(
    dataset: str = "okx_15m_extended",
    *,
    expected_freq: str = "auto",
    allow_runtime_open_tail: bool = False,
) -> pd.DataFrame:
    results = [
        asdict(
            audit_symbol(
                symbol_data,
                expected_freq=expected_freq,
                allow_runtime_open_tail=allow_runtime_open_tail,
            )
        )
        for symbol_data in load_all_symbols(dataset)
    ]
    return pd.DataFrame(results).sort_values("inst_id").reset_index(drop=True)


def write_quality_report(
    output_path: str | Path,
    dataset: str = "okx_15m_extended",
    *,
    expected_freq: str = "auto",
    allow_runtime_open_tail: bool = False,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report = audit_dataset(
        dataset,
        expected_freq=expected_freq,
        allow_runtime_open_tail=allow_runtime_open_tail,
    )
    report.to_csv(path, index=False, encoding="utf-8")
    return path
