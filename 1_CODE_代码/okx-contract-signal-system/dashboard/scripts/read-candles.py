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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("--limit", type=int, default=260)
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--history-dir", required=True)
    args = parser.parse_args()

    timeframe = args.timeframe.strip().lower()
    limit = max(20, min(args.limit, 60000))
    path = Path(args.history_dir) / normalize_symbol(args.symbol, timeframe)
    source = "local"
    if path.exists():
        frame = pd.read_parquet(path).tail(limit).copy()
    else:
        frame = fetch_recent_from_okx(args.symbol, timeframe, limit)
        source = "okx_recent"

    candles = frame_to_candles(frame)

    payload = {
        "symbol": args.symbol,
        "timeframe": timeframe,
        "source": source,
        "path": str(path),
        "count": len(candles),
        "last_time": frame["ts"].max().isoformat() if not frame.empty else None,
        "candles": candles,
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
