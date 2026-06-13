from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


def normalize_symbol(inst_id: str, timeframe: str) -> str:
    if not inst_id.endswith("-USDT-SWAP"):
        raise ValueError(f"unsupported instrument: {inst_id}")
    base, quote, _ = inst_id.split("-")
    return f"{base}_{quote}_{quote}_{timeframe}.parquet"


def iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def summarize_symbol(history_dir: Path, inst_id: str, timeframe: str) -> dict[str, Any]:
    path = history_dir / normalize_symbol(inst_id, timeframe)
    if not path.exists():
        return {
            "inst_id": inst_id,
            "status": "missing",
            "rows_after": 0,
            "first_ts": "",
            "last_ts": "",
            "error": str(path),
        }

    parquet = pq.ParquetFile(path)
    rows = parquet.metadata.num_rows
    if rows <= 0 or parquet.num_row_groups <= 0:
        return {
            "inst_id": inst_id,
            "status": "empty",
            "rows_after": 0,
            "first_ts": "",
            "last_ts": "",
            "error": "",
        }

    first_group = parquet.read_row_group(0, columns=["ts"])
    last_group = parquet.read_row_group(parquet.num_row_groups - 1, columns=["ts"])
    first_ts = first_group.column("ts")[0].as_py()
    last_ts = last_group.column("ts")[-1].as_py()
    return {
        "inst_id": inst_id,
        "status": "passed",
        "rows_after": rows,
        "first_ts": iso(first_ts),
        "last_ts": iso(last_ts),
        "error": "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="+")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--history-dir", required=True)
    args = parser.parse_args()

    history_dir = Path(args.history_dir)
    timeframe = args.timeframe.strip().lower()
    payload = {
        "symbols": [
            summarize_symbol(history_dir, symbol, timeframe)
            for symbol in args.symbols
        ]
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
