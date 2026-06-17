from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Literal

import pandas as pd

from okx_signal_system.exchange.okx import OKXInstrument
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.timeframe import SUPPORTED_TIMEFRAMES, normalize_timeframe


OHLCV_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]
OPTIONAL_COLUMNS = ["symbol", "timeframe", "quote_volume", "is_closed"]
REQUIRED_METADATA_COLUMNS = ["symbol", "timeframe", "is_closed"]
MISSING_REQUIRED_IS_CLOSED_COLUMN = "MISSING_REQUIRED_IS_CLOSED_COLUMN"
MISSING_REQUIRED_STRUCTURE_COLUMNS = "MISSING_REQUIRED_STRUCTURE_COLUMNS"
STRICT_DATA_ROLES = {"formal_history", "runtime_cache", "research", "runtime"}
DataRole = Literal["formal_history", "runtime_cache", "research", "runtime", "raw_ingest"]


@dataclass(frozen=True)
class SymbolData:
    inst_id: str
    source_path: Path
    frame: pd.DataFrame


def file_symbol_to_inst_id(path: Path) -> str:
    stem = path.stem
    for suffix in ["_1h", "_15m", "_5m", "_1d", "_1m"]:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return OKXInstrument.from_symbol(stem).inst_id


def file_timeframe(path: Path, default: str = "1h") -> str:
    stem = path.stem.lower()
    for spec in SUPPORTED_TIMEFRAMES.values():
        if stem.endswith(f"_{spec.file_suffix}"):
            return spec.key
    return normalize_timeframe(default)


def _strict_role(data_role: DataRole) -> bool:
    return data_role in STRICT_DATA_ROLES


def _raise_missing_required_metadata(inst_id: str, missing: list[str]) -> None:
    if missing == ["is_closed"]:
        raise ValueError(f"{inst_id} {MISSING_REQUIRED_IS_CLOSED_COLUMN}")
    raise ValueError(f"{inst_id} {MISSING_REQUIRED_STRUCTURE_COLUMNS}: {missing}")


def list_parquet_files(dataset: str = "okx_15m_extended") -> list[Path]:
    root = find_lightweight_history(dataset)
    return sorted(path for path in root.glob("*.parquet") if ".tmp" not in path.name)


def normalize_ohlcv(
    frame: pd.DataFrame,
    *,
    inst_id: str,
    timeframe: str = "1h",
    data_role: DataRole = "formal_history",
) -> pd.DataFrame:
    df = frame.copy()
    timeframe = normalize_timeframe(timeframe)
    if "time" in df.columns and "ts" not in df.columns:
        df = df.rename(columns={"time": "ts"})
    missing = [col for col in OHLCV_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"{inst_id} missing OHLCV columns: {missing}")
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    missing_metadata = [col for col in REQUIRED_METADATA_COLUMNS if col not in df.columns]
    if _strict_role(data_role):
        if missing_metadata:
            if "is_closed" in missing_metadata:
                raise ValueError(f"{inst_id} {MISSING_REQUIRED_IS_CLOSED_COLUMN}")
            _raise_missing_required_metadata(inst_id, missing_metadata)
        null_metadata = [col for col in REQUIRED_METADATA_COLUMNS if df[col].isna().any()]
        if null_metadata:
            _raise_missing_required_metadata(inst_id, null_metadata)
    else:
        if "is_closed" not in df.columns:
            df["is_closed"] = True
        if "symbol" not in df.columns:
            df["symbol"] = inst_id
        if "timeframe" not in df.columns:
            df["timeframe"] = timeframe
        else:
            df["timeframe"] = df["timeframe"].fillna(timeframe)
    ordered = [*OHLCV_COLUMNS, *[c for c in OPTIONAL_COLUMNS if c in df.columns]]
    rest = [c for c in df.columns if c not in ordered]
    return df[[*ordered, *rest]].sort_values("ts").reset_index(drop=True)


def load_symbol_file(path: Path, *, data_role: DataRole = "formal_history") -> SymbolData:
    inst_id = file_symbol_to_inst_id(path)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            raw = pd.read_parquet(path)
            break
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
    else:
        assert last_error is not None
        raise last_error
    frame = normalize_ohlcv(raw, inst_id=inst_id, timeframe=file_timeframe(path), data_role=data_role)
    return SymbolData(inst_id=inst_id, source_path=path, frame=frame)


def load_all_symbols(dataset: str = "okx_15m_extended") -> list[SymbolData]:
    return [load_symbol_file(path) for path in list_parquet_files(dataset)]


def closed_bars(frame: pd.DataFrame) -> pd.DataFrame:
    if "is_closed" not in frame.columns:
        raise ValueError(MISSING_REQUIRED_IS_CLOSED_COLUMN)
    closed = frame["is_closed"]
    if pd.api.types.is_bool_dtype(closed):
        mask = closed.fillna(False)
    else:
        mask = closed.astype("string").str.strip().str.lower().isin({"true", "1", "yes"})
    return frame[mask].reset_index(drop=True)
