from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def normalize_symbol(inst_id: str) -> str:
    if not inst_id.endswith("-USDT-SWAP"):
        raise ValueError(f"unsupported instrument: {inst_id}")
    base, quote, _ = inst_id.split("-")
    return f"{base}_{quote}_{quote}_15m.parquet"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("--limit", type=int, default=260)
    parser.add_argument("--history-dir", required=True)
    args = parser.parse_args()

    limit = max(20, min(args.limit, 2000))
    path = Path(args.history_dir) / normalize_symbol(args.symbol)
    if not path.exists():
        raise FileNotFoundError(str(path))

    frame = pd.read_parquet(path).tail(limit).copy()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["ts", "open", "high", "low", "close"])

    candles = [
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

    payload = {
        "symbol": args.symbol,
        "path": str(path),
        "count": len(candles),
        "last_time": frame["ts"].max().isoformat() if not frame.empty else None,
        "candles": candles,
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
