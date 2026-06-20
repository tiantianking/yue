from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from okx_signal_system.config import load_config, project_paths
from okx_signal_system.exchange.candles import okx_candles_to_frame
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.timeframe import timeframe_spec

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

BASE_URL = "https://www.okx.com"
DEFAULT_PROXY = "http://127.0.0.1:1088"


@dataclass
class SymbolBackfillReport:
    inst_id: str
    path: str
    rows_before: int
    rows_after: int
    target_start_utc: str
    target_end_utc: str
    first_in_target_utc: str
    last_in_target_utc: str
    pages: int
    downloaded_rows: int
    target_rows: int
    missing_rows: int
    duplicate_rows: int
    bad_step_rows: int
    status: str
    error: str = ""


def _tcp_port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _proxy_dict() -> dict[str, str] | None:
    configured = os.environ.get("OKX_REST_PROXY", "").strip()
    if configured.lower() in {"0", "false", "off", "none"}:
        return None
    proxy = configured or (DEFAULT_PROXY if _tcp_port_open("127.0.0.1", 1088) else "")
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _inst_to_filename(inst_id: str, suffix: str) -> str:
    normalized = inst_id.replace("-SWAP", "").replace("-", "_").upper()
    parts = normalized.split("_")
    if len(parts) >= 2:
        base, quote = parts[0], parts[1]
        return f"{base}_{quote}_{quote}_{suffix}.parquet"
    return f"{normalized}_USDT_{suffix}.parquet"


def _read_frame(path: Path, *, inst_id: str, timeframe: str) -> pd.DataFrame:
    columns = [
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "symbol",
        "timeframe",
        "is_closed",
    ]
    if path.exists():
        df = pd.read_parquet(path)
    else:
        df = pd.DataFrame(columns=columns)
    if df.empty:
        return df
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["symbol"] = inst_id
    df["timeframe"] = timeframe
    df["is_closed"] = True
    return df[columns].sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)


def _confirmed_frame(raw_bars: list[list[Any]], *, inst_id: str, timeframe: str) -> pd.DataFrame:
    confirmed = [row for row in raw_bars if len(row) < 9 or str(row[8]) == "1"]
    df = okx_candles_to_frame(confirmed)
    if df.empty:
        return df
    df["symbol"] = inst_id
    df["timeframe"] = timeframe
    df["is_closed"] = True
    return df


def _request_history(
    session: requests.Session,
    *,
    inst_id: str,
    okx_bar: str,
    after_ms: int,
    limit: int,
    retries: int,
) -> list[list[Any]]:
    params = {"instId": inst_id, "bar": okx_bar, "after": str(after_ms), "limit": str(limit)}
    url = BASE_URL + "/api/v5/market/history-candles"
    proxies = _proxy_dict()
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=20, proxies=proxies)
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") == "0":
                return body.get("data", [])
            raise RuntimeError(f"OKX code={body.get('code')} msg={body.get('msg')}")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= retries:
                break
            wait = min(45.0, 1.5 * (attempt + 1))
            time.sleep(wait)
    raise RuntimeError(f"{inst_id}: OKX request failed after retries: {last_error}")


def _merge_save(path: Path, existing: pd.DataFrame, frames: list[pd.DataFrame]) -> pd.DataFrame:
    valid_frames = [frame for frame in frames if frame is not None and not frame.empty]
    merged = pd.concat([existing, *valid_frames], ignore_index=True) if valid_frames else existing
    if merged.empty:
        return merged
    merged["ts"] = pd.to_datetime(merged["ts"], utc=True)
    merged = merged.dropna(subset=["ts", "open", "high", "low", "close"])
    merged = merged.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(path, index=False)
    return merged


def _target_window(start: str, end: str, timezone: str, minutes: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize(timezone)
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize(timezone)
    if len(end.strip()) == 10:
        end_ts = end_ts + pd.Timedelta(days=1) - pd.Timedelta(minutes=minutes)
    return start_ts.tz_convert("UTC"), end_ts.tz_convert("UTC")


def _validate_target(
    df: pd.DataFrame,
    *,
    target_start: pd.Timestamp,
    target_end: pd.Timestamp,
    freq: str,
    exhausted_before_target: bool,
) -> tuple[str, str, int, int, int, int, str]:
    window = df[(df["ts"] >= target_start) & (df["ts"] <= target_end)].copy()
    if window.empty:
        return "", "", 0, 0, 0, 0, "no_target_rows"

    window = window.sort_values("ts").reset_index(drop=True)
    first_ts = pd.to_datetime(window["ts"].min(), utc=True)
    last_ts = pd.to_datetime(window["ts"].max(), utc=True)
    effective_start = first_ts if exhausted_before_target and first_ts > target_start else target_start
    expected = pd.date_range(effective_start, target_end, freq=freq)
    unique_ts = pd.DatetimeIndex(window["ts"].drop_duplicates().sort_values())
    missing = expected.difference(unique_ts)
    duplicate_rows = int(window.duplicated("ts").sum())
    bad_step_rows = int((unique_ts.to_series().diff().dropna() != pd.Timedelta(freq)).sum())

    if missing.empty and duplicate_rows == 0 and bad_step_rows == 0:
        status = "passed" if effective_start == target_start else "short_history_passed"
    else:
        status = "failed"
    return (
        first_ts.isoformat(),
        last_ts.isoformat(),
        len(expected),
        len(missing),
        duplicate_rows,
        bad_step_rows,
        status,
    )


def backfill_symbol(
    inst_id: str,
    *,
    dataset: str,
    timeframe: str,
    start: str,
    end: str,
    timezone: str,
    limit: int,
    sleep_seconds: float,
    save_every_pages: int,
    retries: int,
) -> SymbolBackfillReport:
    spec = timeframe_spec(timeframe)
    target_start, target_end = _target_window(start, end, timezone, spec.minutes)
    data_dir = find_lightweight_history(dataset)
    path = data_dir / _inst_to_filename(inst_id, spec.file_suffix)
    existing = _read_frame(path, inst_id=inst_id, timeframe=spec.key)
    rows_before = len(existing)
    cursor_exclusive = target_end + pd.Timedelta(minutes=spec.minutes)
    pages = 0
    downloaded_rows = 0
    pending: list[pd.DataFrame] = []
    exhausted_before_target = False

    try:
        with requests.Session() as session:
            while cursor_exclusive > target_start:
                raw = _request_history(
                    session,
                    inst_id=inst_id,
                    okx_bar=spec.okx_bar,
                    after_ms=int(cursor_exclusive.timestamp() * 1000),
                    limit=limit,
                    retries=retries,
                )
                pages += 1
                if not raw:
                    exhausted_before_target = True
                    break

                page = _confirmed_frame(raw, inst_id=inst_id, timeframe=spec.key)
                if page.empty:
                    exhausted_before_target = True
                    break
                page["ts"] = pd.to_datetime(page["ts"], utc=True)
                oldest = pd.to_datetime(page["ts"].min(), utc=True)
                if oldest >= cursor_exclusive:
                    raise RuntimeError(f"pagination did not move backward at {cursor_exclusive.isoformat()}")

                in_range = page[(page["ts"] >= target_start) & (page["ts"] <= target_end)]
                if not in_range.empty:
                    pending.append(in_range)
                    downloaded_rows += len(in_range)

                cursor_exclusive = oldest
                if pages % save_every_pages == 0:
                    existing = _merge_save(path, existing, pending)
                    pending = []
                    first = "" if existing.empty else pd.to_datetime(existing["ts"].min(), utc=True).isoformat()
                    print(
                        f"{inst_id}: pages={pages} downloaded={downloaded_rows} "
                        f"saved_rows={len(existing)} first={first}",
                        flush=True,
                    )
                if oldest <= target_start:
                    break
                time.sleep(sleep_seconds)

        if pending:
            existing = _merge_save(path, existing, pending)

        (
            first_in_target,
            last_in_target,
            target_rows,
            missing_rows,
            duplicate_rows,
            bad_step_rows,
            status,
        ) = _validate_target(
            existing,
            target_start=target_start,
            target_end=target_end,
            freq=spec.pandas_freq,
            exhausted_before_target=exhausted_before_target,
        )
        return SymbolBackfillReport(
            inst_id=inst_id,
            path=str(path),
            rows_before=rows_before,
            rows_after=len(existing),
            target_start_utc=target_start.isoformat(),
            target_end_utc=target_end.isoformat(),
            first_in_target_utc=first_in_target,
            last_in_target_utc=last_in_target,
            pages=pages,
            downloaded_rows=downloaded_rows,
            target_rows=target_rows,
            missing_rows=missing_rows,
            duplicate_rows=duplicate_rows,
            bad_step_rows=bad_step_rows,
            status=status,
        )
    except Exception as exc:  # noqa: BLE001
        if pending:
            existing = _merge_save(path, existing, pending)
        return SymbolBackfillReport(
            inst_id=inst_id,
            path=str(path),
            rows_before=rows_before,
            rows_after=len(existing),
            target_start_utc=target_start.isoformat(),
            target_end_utc=target_end.isoformat(),
            first_in_target_utc="",
            last_in_target_utc="",
            pages=pages,
            downloaded_rows=downloaded_rows,
            target_rows=0,
            missing_rows=0,
            duplicate_rows=0,
            bad_step_rows=0,
            status="failed",
            error=str(exc),
        )


def _symbols_from_config() -> list[str]:
    cfg = load_config("base.yaml")
    return list(cfg.get("data", {}).get("symbols", []))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="okx_5m_extended")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--start", default="2023-06-15")
    parser.add_argument("--end", default="2026-06-16")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--sleep-seconds", type=float, default=0.16)
    parser.add_argument("--save-every-pages", type=int, default=40)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--retries", type=int, default=8)
    parser.add_argument("--symbols", nargs="*", default=None)
    args = parser.parse_args()

    symbols = args.symbols or _symbols_from_config()
    if not symbols:
        raise SystemExit("no symbols configured")

    reports: list[SymbolBackfillReport] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {}
        for idx, inst_id in enumerate(symbols, 1):
            print(f"[{idx}/{len(symbols)}] queued {inst_id}", flush=True)
            futures[
                pool.submit(
                    backfill_symbol,
                    inst_id,
                    dataset=args.dataset,
                    timeframe=args.timeframe,
                    start=args.start,
                    end=args.end,
                    timezone=args.timezone,
                    limit=args.limit,
                    sleep_seconds=args.sleep_seconds,
                    save_every_pages=args.save_every_pages,
                    retries=args.retries,
                )
            ] = inst_id

        for future in as_completed(futures):
            report = future.result()
            reports.append(report)
            print(json.dumps(asdict(report), ensure_ascii=False), flush=True)

    reports = sorted(reports, key=lambda item: item.inst_id)
    output = (
        project_paths().output_dir
        / f"{args.timeframe}_range_backfill_{args.start}_to_{args.end}_report.json"
    )
    output.write_text(
        json.dumps([asdict(item) for item in reports], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failed = [item for item in reports if item.status == "failed"]
    print(f"report={output}", flush=True)
    print(f"failed={len(failed)} total={len(reports)}", flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
