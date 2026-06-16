from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def normalize_symbol(inst_id: str, timeframe: str) -> str:
    if not inst_id.endswith("-USDT-SWAP"):
        raise ValueError(f"unsupported instrument: {inst_id}")
    base, quote, _ = inst_id.split("-")
    return f"{base}_{quote}_{quote}_{timeframe}.parquet"


def frame_to_candles(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    frame = frame.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["ts", "open", "high", "low", "close"])
    return [
        {
            "time": int(row.ts.timestamp()),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume) if pd.notna(row.volume) else None,
        }
        for row in frame.itertuples(index=False)
    ]


def timeframe_minutes(timeframe: str) -> int:
    if timeframe.endswith("m"):
        return int(timeframe[:-1])
    if timeframe.endswith("h"):
        return int(timeframe[:-1]) * 60
    if timeframe.endswith("d"):
        return int(timeframe[:-1]) * 1440
    return 15


def fetch_recent_from_okx(symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    from okx_signal_system.exchange.candles import okx_candles_to_frame
    from okx_signal_system.exchange.okx import get_candles

    raw = get_candles(symbol, bar=timeframe, limit=min(limit, 300))
    confirmed = [row for row in raw if len(row) < 9 or str(row[8]) == "1"]
    frame = okx_candles_to_frame(confirmed)
    if frame.empty:
        return frame
    frame["symbol"] = symbol
    frame["timeframe"] = timeframe
    frame["is_closed"] = True
    return frame.sort_values("ts").drop_duplicates("ts", keep="last")


def read_parquet_tail(path: Path, limit: int) -> pd.DataFrame:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        parquet = pq.ParquetFile(path)
        tables = []
        rows = 0
        for idx in range(parquet.num_row_groups - 1, -1, -1):
            table = parquet.read_row_group(idx)
            tables.append(table)
            rows += table.num_rows
            if rows >= limit:
                break
        if not tables:
            return pd.DataFrame()
        table = pa.concat_tables(list(reversed(tables)), promote_options="default")
        return table.to_pandas().tail(limit).copy()
    except Exception:
        return pd.read_parquet(path).tail(limit).copy()


def needs_recent_refresh(frame: pd.DataFrame, timeframe: str) -> bool:
    if frame.empty or "ts" not in frame.columns:
        return True
    last_ts = pd.to_datetime(frame["ts"], utc=True).max()
    stale_after = pd.Timedelta(minutes=timeframe_minutes(timeframe) * 3)
    return pd.Timestamp.now(tz="UTC") - last_ts > stale_after


def merge_recent_if_stale(path: Path, frame: pd.DataFrame, symbol: str, timeframe: str, limit: int) -> tuple[pd.DataFrame, str, str | None]:
    if frame.empty or "ts" not in frame.columns:
        try:
            recent = fetch_recent_from_okx(symbol, timeframe, limit)
            return recent, "okx_recent", None
        except Exception as exc:
            return frame, "local_stale", str(exc)

    last_ts = pd.to_datetime(frame["ts"], utc=True).max()
    stale_after = pd.Timedelta(minutes=timeframe_minutes(timeframe) * 3)
    if pd.Timestamp.now(tz="UTC") - last_ts <= stale_after:
        return frame, "local", None

    try:
        recent = fetch_recent_from_okx(symbol, timeframe, min(max(limit, 300), 300))
        if recent.empty:
            return frame, "local_stale", "okx_recent_empty"
        merged = pd.concat([frame, recent], ignore_index=True)
        merged["ts"] = pd.to_datetime(merged["ts"], utc=True)
        merged = merged.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_parquet(path, index=False)
        return merged, "local_okx_merged", None
    except Exception as exc:
        return frame, "local_stale", str(exc)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("--limit", type=int, default=260)
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--history-dir")
    parser.add_argument("--dataset")
    args = parser.parse_args()

    timeframe = args.timeframe.strip().lower()
    limit = max(20, min(args.limit, 60000))
    if args.history_dir:
        history_dir = Path(args.history_dir)
    else:
        from okx_signal_system.paths import find_lightweight_history

        history_dir = find_lightweight_history(args.dataset or f"okx_{timeframe}_extended")
    path = history_dir / normalize_symbol(args.symbol, timeframe)
    source = "local"
    warning = None
    if path.exists():
        tail_limit = min(max(limit, 300), 60000)
        frame = read_parquet_tail(path, tail_limit)
        if needs_recent_refresh(frame, timeframe):
            full_frame = pd.read_parquet(path).copy()
            frame, source, warning = merge_recent_if_stale(path, full_frame, args.symbol, timeframe, limit)
        frame = frame.tail(limit).copy()
    else:
        try:
            frame = fetch_recent_from_okx(args.symbol, timeframe, limit)
            source = "okx_recent"
        except Exception as exc:
            frame = pd.DataFrame()
            source = "okx_recent_failed"
            warning = str(exc)

    candles = frame_to_candles(frame)

    payload = {
        "symbol": args.symbol,
        "timeframe": timeframe,
        "source": source,
        "path": str(path),
        "count": len(candles),
        "last_time": frame["ts"].max().isoformat() if not frame.empty else None,
        "warning": warning,
        "candles": candles,
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
