from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from okx_signal_system.config import load_config
from okx_signal_system.shadow_ensemble import (
    ShadowEnsembleService,
    ShadowEnsembleStore,
    load_shadow_ensemble_config,
)


def runtime_filename(symbol: str) -> str:
    normalized = symbol.replace("-", "_").replace("_SWAP", "").upper()
    if normalized.count("USDT") == 1:
        normalized = f"{normalized}_USDT"
    return f"{normalized}_15m.parquet"


async def run_check(*, write_runtime_output: bool = False) -> dict[str, object]:
    base = load_config("base.yaml")
    symbols = [str(item) for item in base.get("data", {}).get("symbols", [])]
    cache_dir = ROOT / "outputs" / "runtime_cache" / "lightweight_history" / "okx_15m_extended"
    if not cache_dir.is_dir():
        raise FileNotFoundError(f"runtime cache directory not found: {cache_dir}")

    config = load_shadow_ensemble_config()

    async def loader(symbol: str, limit: int) -> pd.DataFrame:
        path = cache_dir / runtime_filename(symbol)
        if not path.is_file():
            raise FileNotFoundError(path)
        frame = await asyncio.to_thread(pd.read_parquet, path)
        return frame.tail(limit).reset_index(drop=True)

    if write_runtime_output:
        service = ShadowEnsembleService(candle_loader=loader, config=config)
        result = await service.scan(symbols)
        status_path = service.status_path
        database_path = service.store.path
    else:
        with tempfile.TemporaryDirectory(prefix="okx-shadow-check-") as temp:
            temp_dir = Path(temp)
            isolated = replace(
                config,
                status_file=str(temp_dir / "shadow_status.json"),
                sqlite_file=str(temp_dir / "shadow.sqlite3"),
            )
            store = ShadowEnsembleStore(temp_dir / "shadow.sqlite3")
            service = ShadowEnsembleService(candle_loader=loader, config=isolated, store=store)
            result = await service.scan(symbols)
            status_path = service.status_path
            database_path = service.store.path
            payload = {
                "status": result.status,
                "latest_closed_4h": result.latest_closed_4h,
                "eligible_symbols": result.eligible_symbols,
                "new_signal_count": len(result.new_observations),
                "pending_entry_count": result.pending_entry_count,
                "active_count": result.active_count,
                "closed_count": result.closed_count,
                "skipped_symbols": list(result.skipped_symbols),
                "temporary_status_written": status_path.is_file(),
                "temporary_database_written": database_path.is_file(),
            }
            return payload

    return {
        "status": result.status,
        "latest_closed_4h": result.latest_closed_4h,
        "eligible_symbols": result.eligible_symbols,
        "new_signal_count": len(result.new_observations),
        "pending_entry_count": result.pending_entry_count,
        "active_count": result.active_count,
        "closed_count": result.closed_count,
        "skipped_symbols": list(result.skipped_symbols),
        "status_path": str(status_path),
        "database_path": str(database_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the frozen shadow ensemble against local 15m runtime cache.")
    parser.add_argument(
        "--write-runtime-output",
        action="store_true",
        help="Write the normal outputs/shadow_ensemble files instead of temporary smoke-test files.",
    )
    args = parser.parse_args()
    payload = asyncio.run(run_check(write_runtime_output=args.write_runtime_output))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "running" else 2


if __name__ == "__main__":
    raise SystemExit(main())
