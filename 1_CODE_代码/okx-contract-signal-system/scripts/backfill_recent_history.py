from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

import pandas as pd

from okx_signal_system.config import load_config, project_paths
from okx_signal_system.exchange.candles import okx_candles_to_frame
from okx_signal_system.exchange.okx import get_candles
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.timeframe import timeframe_spec


@dataclass
class BackfillResult:
    inst_id: str
    rows_before: int
    rows_after: int
    added_rows: int
    first_ts: str
    last_ts: str
    requests: int
    status: str
    error: str = ""


def _inst_to_filename(inst_id: str, suffix: str) -> str:
    normalized = inst_id.replace("-SWAP", "").replace("-", "_").upper()
    parts = normalized.split("_")
    if len(parts) >= 2:
        base, quote = parts[0], parts[1]
        return f"{base}_{quote}_{quote}_{suffix}.parquet"
    return f"{normalized}_USDT_{suffix}.parquet"


def _read_frame(path: Path, *, inst_id: str, timeframe: str) -> pd.DataFrame:
    if path.exists():
        df = pd.read_parquet(path)
    else:
        df = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "quote_volume"])
    if df.empty:
        return df
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "quote_volume" not in df.columns:
        df["quote_volume"] = pd.NA
    if "symbol" not in df.columns:
        df["symbol"] = inst_id
    if "timeframe" not in df.columns:
        df["timeframe"] = timeframe
    if "is_closed" not in df.columns:
        df["is_closed"] = True
    return df.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)


def _confirmed_frame(raw_bars: list[list], *, inst_id: str, timeframe: str) -> pd.DataFrame:
    confirmed = [row for row in raw_bars if len(row) < 9 or str(row[8]) == "1"]
    df = okx_candles_to_frame(confirmed)
    if df.empty:
        return df
    df["symbol"] = inst_id
    df["timeframe"] = timeframe
    df["is_closed"] = True
    return df


def _get_candles_with_retry(
    inst_id: str,
    *,
    timeframe: str,
    limit: int,
    after: int | None = None,
    retries: int = 5,
) -> list[list]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            kwargs = {"after": after} if after is not None else {}
            return get_candles(inst_id, bar=timeframe, limit=limit, **kwargs)
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            wait = min(30.0, 2.0 * (attempt + 1))
            print(f"{inst_id}: request failed, retry {attempt + 1}/{retries} in {wait:.0f}s: {exc}", flush=True)
            time.sleep(wait)
    raise last_error or RuntimeError("unknown candle request error")


def _merge_save(path: Path, existing: pd.DataFrame, new_frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [existing, *[df for df in new_frames if df is not None and not df.empty]]
    merged = pd.concat(frames, ignore_index=True) if frames else existing
    if merged.empty:
        return merged
    merged["ts"] = pd.to_datetime(merged["ts"], utc=True)
    merged = merged.dropna(subset=["ts", "open", "high", "low", "close"])
    merged = merged.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(path, index=False)
    return merged


def backfill_symbol(
    inst_id: str,
    *,
    dataset: str,
    timeframe: str,
    years: float,
    limit: int,
    sleep_seconds: float,
    save_every: int,
    retries: int,
) -> BackfillResult:
    spec = timeframe_spec(timeframe)
    data_dir = find_lightweight_history(dataset)
    path = data_dir / _inst_to_filename(inst_id, spec.file_suffix)
    existing = _read_frame(path, inst_id=inst_id, timeframe=spec.key)
    rows_before = len(existing)
    cutoff = pd.Timestamp.now(tz="UTC") - timedelta(days=365 * years)

    requests = 0
    pending: list[pd.DataFrame] = []

    try:
        raw_latest = _get_candles_with_retry(inst_id, timeframe=spec.key, limit=limit, retries=retries)
        requests += 1
        latest_df = _confirmed_frame(raw_latest, inst_id=inst_id, timeframe=spec.key)
        if not latest_df.empty:
            pending.append(latest_df)
            existing = _merge_save(path, existing, pending)
            pending = []

        cursor = (
            pd.Timestamp.now(tz="UTC")
            if existing.empty
            else pd.to_datetime(existing["ts"].min(), utc=True)
        )
        while cursor > cutoff:
            cursor_ms = int(cursor.timestamp() * 1000)

            raw = _get_candles_with_retry(
                inst_id,
                timeframe=spec.key,
                limit=limit,
                after=cursor_ms,
                retries=retries,
            )
            requests += 1
            older = _confirmed_frame(raw, inst_id=inst_id, timeframe=spec.key)
            if older.empty:
                break

            older = older[older["ts"] < cursor]
            older = older[older["ts"] >= cutoff]
            if older.empty:
                break

            pending.append(older)
            cursor = pd.to_datetime(older["ts"].min(), utc=True)
            if len(pending) >= save_every:
                existing = _merge_save(path, existing, pending)
                pending = []
                first = pd.to_datetime(existing["ts"].min(), utc=True)
                print(f"{inst_id}: saved {len(existing)} rows, first={first.isoformat()}", flush=True)

            time.sleep(sleep_seconds)

        if pending:
            existing = _merge_save(path, existing, pending)

        if existing.empty:
            first_ts = ""
            last_ts = ""
        else:
            first_ts = pd.to_datetime(existing["ts"].min(), utc=True).isoformat()
            last_ts = pd.to_datetime(existing["ts"].max(), utc=True).isoformat()
        return BackfillResult(
            inst_id=inst_id,
            rows_before=rows_before,
            rows_after=len(existing),
            added_rows=max(0, len(existing) - rows_before),
            first_ts=first_ts,
            last_ts=last_ts,
            requests=requests,
            status="passed",
        )
    except Exception as exc:
        if pending:
            existing = _merge_save(path, existing, pending)
        first_ts = "" if existing.empty else pd.to_datetime(existing["ts"].min(), utc=True).isoformat()
        last_ts = "" if existing.empty else pd.to_datetime(existing["ts"].max(), utc=True).isoformat()
        return BackfillResult(
            inst_id=inst_id,
            rows_before=rows_before,
            rows_after=len(existing),
            added_rows=max(0, len(existing) - rows_before),
            first_ts=first_ts,
            last_ts=last_ts,
            requests=requests,
            status="failed",
            error=str(exc),
        )


def _symbols_from_config() -> list[str]:
    cfg = load_config("base.yaml")
    return list(cfg.get("data", {}).get("symbols", []))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="okx_15m_extended")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--years", type=float, default=3.0)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--sleep-seconds", type=float, default=0.12)
    parser.add_argument("--save-every", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--symbols", nargs="*", default=None)
    args = parser.parse_args()

    symbols = args.symbols or _symbols_from_config()
    if not symbols:
        raise SystemExit("no symbols configured")

    results: list[BackfillResult] = []
    workers = max(1, int(args.workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for idx, inst_id in enumerate(symbols, 1):
            print(f"[{idx}/{len(symbols)}] queued {inst_id}", flush=True)
            futures[
                pool.submit(
                    backfill_symbol,
                    inst_id,
                    dataset=args.dataset,
                    timeframe=args.timeframe,
                    years=args.years,
                    limit=args.limit,
                    sleep_seconds=args.sleep_seconds,
                    save_every=args.save_every,
                    retries=args.retries,
                )
            ] = inst_id

        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps(asdict(result), ensure_ascii=False), flush=True)

    output = project_paths().output_dir / f"{args.timeframe}_backfill_{int(args.years)}y_report.json"
    output.write_text(json.dumps([asdict(item) for item in results], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report={output}", flush=True)


if __name__ == "__main__":
    main()
