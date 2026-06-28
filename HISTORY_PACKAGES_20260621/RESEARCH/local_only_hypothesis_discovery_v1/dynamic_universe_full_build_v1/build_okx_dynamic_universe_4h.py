from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

DATASET_ID = "OKX_DYNAMIC_UNIVERSE_4H_20230701_20260616_V1"
DEFAULT_START = date(2023, 7, 1)
DEFAULT_END = date(2026, 6, 16)
BASE_URL = "https://static.okx.com/cdn/okex/traderecords/candlesticks/daily"
USER_AGENT = "Mozilla/5.0 (compatible; OKXDynamicUniverseBuilder/1.0)"
FOUR_HOURS_MS = 4 * 60 * 60 * 1000
ONE_MINUTE_MS = 60 * 1000
EXPECTED_MINUTES_PER_BAR = 240

SWAP_COLUMNS = [
    "instrument_name",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "vol_ccy",
    "vol_quote",
    "open_time",
    "confirm",
]
SPOT_COLUMNS = ["instrument_name", "open_time", "confirm"]


@dataclass(frozen=True)
class DownloadedArchive:
    kind: str
    day: str
    url: str
    payload: bytes
    sha256: str
    zip_bytes: int
    csv_name: str
    csv_bytes: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=DEFAULT_END.isoformat())
    parser.add_argument(
        "--target",
        default="历史数据_保留/lightweight_history/okx_dynamic_universe_4h_20230701_20260616_v1",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    range_file = Path(__file__).with_name("RUN_RANGE.json")
    if len(sys.argv) == 1 and range_file.exists():
        configured = json.loads(range_file.read_text(encoding="utf-8"))
        args.start = str(configured["start"])
        args.end = str(configured["end"])
        args.workers = int(configured.get("workers", 4))
        args.quiet = bool(configured.get("quiet", True))
        args.force = bool(configured.get("force", False))
    return args


def iter_days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def archive_url(kind: str, day: date) -> str:
    compact = day.strftime("%Y%m%d")
    dashed = day.isoformat()
    return f"{BASE_URL}/{compact}/all{kind}-candlesticks-{dashed}.zip?v=999"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def append_log(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def curl_fetch(url: str) -> bytes:
    completed = subprocess.run(
        [
            "curl",
            "-fsSL",
            "--retry",
            "6",
            "--retry-delay",
            "1",
            "--retry-all-errors",
            "--connect-timeout",
            "25",
            "--max-time",
            "240",
            "-A",
            USER_AGENT,
            url,
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"curl failed ({completed.returncode}) for {url}: {message}")
    if not completed.stdout:
        raise RuntimeError(f"empty response for {url}")
    return completed.stdout


def download_archive(kind: str, day: date) -> DownloadedArchive:
    url = archive_url(kind, day)
    payload = curl_fetch(url)
    digest = hashlib.sha256(payload).hexdigest()
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            bad_entry = archive.testzip()
            if bad_entry is not None:
                raise RuntimeError(f"corrupt zip entry {bad_entry}")
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if len(names) != 1:
                raise RuntimeError(f"unexpected zip entries: {names}")
            info = archive.getinfo(names[0])
            csv_name = names[0]
            csv_bytes = int(info.file_size)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"bad zip for {kind} {day}: {exc}") from exc
    return DownloadedArchive(
        kind=kind,
        day=day.isoformat(),
        url=url,
        payload=payload,
        sha256=digest,
        zip_bytes=len(payload),
        csv_name=csv_name,
        csv_bytes=csv_bytes,
    )


def read_archive_frame(archive: DownloadedArchive, usecols: list[str]) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(archive.payload)) as zipped:
        raw = zipped.read(archive.csv_name)
    frame = pd.read_csv(io.BytesIO(raw), usecols=usecols, low_memory=False)
    missing = set(usecols).difference(frame.columns)
    if missing:
        raise RuntimeError(f"missing columns in {archive.csv_name}: {sorted(missing)}")
    return frame


def normalize_archived_rows(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    normalized["open_time"] = pd.to_numeric(normalized["open_time"], errors="coerce")
    normalized["confirm"] = pd.to_numeric(normalized["confirm"], errors="coerce")
    normalized = normalized.loc[normalized["open_time"].notna()].copy()
    normalized["open_time"] = normalized["open_time"].astype("int64")
    return normalized


def build_spot_bar_presence(spot: pd.DataFrame) -> set[tuple[str, int]]:
    spot = normalize_archived_rows(spot)
    spot = spot.loc[spot["instrument_name"].astype(str).str.endswith("-USDT")].copy()
    spot["instrument_name"] = spot["instrument_name"].astype(str)
    spot = spot.loc[spot["open_time"].mod(FOUR_HOURS_MS).eq(0)].copy()
    spot["base"] = spot["instrument_name"].str[: -len("-USDT")]
    return set(zip(spot["base"].tolist(), spot["open_time"].tolist()))


def build_complete_4h(
    swap: pd.DataFrame,
    spot_bar_presence: set[tuple[str, int]],
    source_day: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    swap = normalize_archived_rows(swap)
    swap["instrument_name"] = swap["instrument_name"].astype(str)
    swap = swap.loc[
        swap["instrument_name"].str.endswith("-USDT-SWAP")
    ].copy()
    swap["base"] = swap["instrument_name"].str[: -len("-USDT-SWAP")]
    swap["bar_open_ms"] = swap["open_time"] - swap["open_time"].mod(FOUR_HOURS_MS)
    presence_keys = pd.MultiIndex.from_tuples(
        sorted(spot_bar_presence), names=["base", "bar_open_ms"]
    )
    row_keys = pd.MultiIndex.from_arrays(
        [swap["base"], swap["bar_open_ms"]], names=["base", "bar_open_ms"]
    )
    swap = swap.loc[row_keys.isin(presence_keys)].copy()

    for column in ("open", "high", "low", "close", "vol", "vol_ccy", "vol_quote"):
        swap[column] = pd.to_numeric(swap[column], errors="coerce")
    for column in ("vol", "vol_ccy", "vol_quote"):
        swap[column] = swap[column].fillna(0.0)

    swap = swap.sort_values(["instrument_name", "open_time"])
    grouped = swap.groupby(["instrument_name", "base", "bar_open_ms"], sort=True)
    diagnostics = grouped["open_time"].agg(
        minute_rows="size",
        unique_minutes="nunique",
        first_minute="min",
        last_minute="max",
    )
    diagnostics["is_complete"] = (
        diagnostics["minute_rows"].eq(EXPECTED_MINUTES_PER_BAR)
        & diagnostics["unique_minutes"].eq(EXPECTED_MINUTES_PER_BAR)
        & diagnostics["first_minute"].eq(diagnostics.index.get_level_values("bar_open_ms"))
        & diagnostics["last_minute"].eq(
            diagnostics.index.get_level_values("bar_open_ms")
            + (EXPECTED_MINUTES_PER_BAR - 1) * ONE_MINUTE_MS
        )
    )
    complete_index = diagnostics.index[diagnostics["is_complete"]]

    bars = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        vol=("vol", "sum"),
        vol_ccy=("vol_ccy", "sum"),
        vol_quote=("vol_quote", "sum"),
    )
    bars = bars.loc[bars.index.isin(complete_index)].reset_index()
    bars["bar_open_utc"] = pd.to_datetime(bars["bar_open_ms"], unit="ms", utc=True)
    bars["source_day"] = source_day
    bars["minute_count"] = EXPECTED_MINUTES_PER_BAR
    bars["spot_present_at_bar_open"] = True
    bars = bars[
        [
            "instrument_name",
            "base",
            "bar_open_utc",
            "bar_open_ms",
            "open",
            "high",
            "low",
            "close",
            "vol",
            "vol_ccy",
            "vol_quote",
            "minute_count",
            "spot_present_at_bar_open",
            "source_day",
        ]
    ].sort_values(["bar_open_ms", "instrument_name"])

    incomplete = diagnostics.loc[~diagnostics["is_complete"]].reset_index()
    summary = {
        "candidate_symbol_bar_groups": int(len(diagnostics)),
        "complete_symbol_bar_groups": int(diagnostics["is_complete"].sum()),
        "incomplete_symbol_bar_groups": int((~diagnostics["is_complete"]).sum()),
        "incomplete_examples": incomplete.head(30).to_dict(orient="records"),
        "complete_unique_instruments": int(bars["instrument_name"].nunique()),
        "complete_bar_timestamps": int(bars["bar_open_ms"].nunique()),
        "rows": int(len(bars)),
    }
    return bars, summary


def parquet_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_daily_parquet(path: Path, frame: pd.DataFrame) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(
        table,
        temporary,
        compression="zstd",
        compression_level=9,
        use_dictionary=["instrument_name", "base", "source_day"],
        write_statistics=True,
    )
    os.replace(temporary, path)
    return parquet_sha256(path)


def day_paths(target: Path, day: date) -> tuple[Path, Path]:
    partition = Path(f"year={day.year:04d}") / f"month={day.month:02d}"
    parquet_path = target / "data" / partition / f"{day.isoformat()}.parquet"
    manifest_path = target / "daily_manifests" / partition / f"{day.isoformat()}.json"
    return parquet_path, manifest_path


def manifest_is_complete(parquet_path: Path, manifest_path: Path) -> bool:
    if not parquet_path.exists() or not manifest_path.exists():
        return False
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if payload.get("status") != "COMPLETE":
        return False
    expected = payload.get("output", {}).get("parquet_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        return False
    return parquet_sha256(parquet_path) == expected


def process_day(target: Path, day: date, force: bool) -> dict[str, Any]:
    parquet_path, manifest_path = day_paths(target, day)
    if not force and manifest_is_complete(parquet_path, manifest_path):
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {
            "date": day.isoformat(),
            "status": "SKIPPED_ALREADY_COMPLETE",
            "rows": existing.get("output", {}).get("rows", 0),
        }

    started = datetime.now(timezone.utc)
    swap_archive = download_archive("swap", day)
    spot_archive = download_archive("spot", day)
    swap = read_archive_frame(swap_archive, SWAP_COLUMNS).drop_duplicates()
    spot = read_archive_frame(spot_archive, SPOT_COLUMNS).drop_duplicates()
    spot_presence = build_spot_bar_presence(spot)
    bars, aggregation = build_complete_4h(swap, spot_presence, day.isoformat())
    if bars.empty:
        raise RuntimeError(f"no complete eligible 4h bars for {day}")
    output_digest = write_daily_parquet(parquet_path, bars)

    eligible_by_bar = {
        pd.Timestamp(bar_open, unit="ms", tz="UTC").isoformat(): symbols
        for bar_open, symbols in bars.groupby("bar_open_ms")["instrument_name"].apply(list).items()
    }
    completed = datetime.now(timezone.utc)
    manifest = {
        "schema": "okx_dynamic_universe_daily_manifest_v1",
        "dataset_id": DATASET_ID,
        "status": "COMPLETE",
        "utc_date": day.isoformat(),
        "started_at_utc": started.isoformat(),
        "completed_at_utc": completed.isoformat(),
        "duration_seconds": round((completed - started).total_seconds(), 3),
        "causal_eligibility": (
            "At each UTC-aligned 4h bar open, require an exact archived BASE-USDT "
            "spot minute and a complete contiguous 240-minute BASE-USDT-SWAP bar. "
            "Archive closure is validated by timestamp continuity."
        ),
        "sources": {
            "swap": {
                "url": swap_archive.url,
                "sha256": swap_archive.sha256,
                "zip_bytes": swap_archive.zip_bytes,
                "csv_name": swap_archive.csv_name,
                "csv_bytes": swap_archive.csv_bytes,
            },
            "spot": {
                "url": spot_archive.url,
                "sha256": spot_archive.sha256,
                "zip_bytes": spot_archive.zip_bytes,
                "csv_name": spot_archive.csv_name,
                "csv_bytes": spot_archive.csv_bytes,
            },
        },
        "source_rows": {
            "swap": int(len(swap)),
            "spot": int(len(spot)),
            "spot_4h_presence_keys": int(len(spot_presence)),
        },
        "aggregation": aggregation,
        "eligible_instruments_by_bar_open_utc": eligible_by_bar,
        "output": {
            "relative_path": parquet_path.relative_to(target).as_posix(),
            "parquet_sha256": output_digest,
            "parquet_bytes": parquet_path.stat().st_size,
            "rows": int(len(bars)),
            "unique_instruments": int(bars["instrument_name"].nunique()),
            "first_bar_open_utc": bars["bar_open_utc"].min().isoformat(),
            "last_bar_open_utc": bars["bar_open_utc"].max().isoformat(),
        },
        "raw_archives_persisted": False,
    }
    atomic_write_json(manifest_path, manifest)
    return {
        "date": day.isoformat(),
        "status": "COMPLETE",
        "rows": int(len(bars)),
        "symbols": int(bars["instrument_name"].nunique()),
        "swap_mb": round(swap_archive.zip_bytes / 1024 / 1024, 2),
        "spot_mb": round(spot_archive.zip_bytes / 1024 / 1024, 2),
        "duration_seconds": manifest["duration_seconds"],
    }


def update_progress(target: Path, requested_start: date, requested_end: date) -> dict[str, Any]:
    manifests = sorted((target / "daily_manifests").glob("year=*/month=*/*.json"))
    completed_dates: list[str] = []
    total_rows = 0
    total_parquet_bytes = 0
    total_source_zip_bytes = 0
    unique_instruments: set[str] = set()
    for path in manifests:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("status") != "COMPLETE":
            continue
        completed_dates.append(str(payload["utc_date"]))
        total_rows += int(payload.get("output", {}).get("rows", 0))
        total_parquet_bytes += int(payload.get("output", {}).get("parquet_bytes", 0))
        total_source_zip_bytes += int(payload.get("sources", {}).get("swap", {}).get("zip_bytes", 0))
        total_source_zip_bytes += int(payload.get("sources", {}).get("spot", {}).get("zip_bytes", 0))
        for symbols in payload.get("eligible_instruments_by_bar_open_utc", {}).values():
            unique_instruments.update(symbols)
    requested_days = (requested_end - requested_start).days + 1
    progress = {
        "schema": "okx_dynamic_universe_build_progress_v1",
        "dataset_id": DATASET_ID,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "full_dataset_start": DEFAULT_START.isoformat(),
        "full_dataset_end": DEFAULT_END.isoformat(),
        "requested_range_start": requested_start.isoformat(),
        "requested_range_end": requested_end.isoformat(),
        "requested_days": requested_days,
        "all_completed_days_on_disk": len(set(completed_dates)),
        "first_completed_date": min(completed_dates) if completed_dates else None,
        "last_completed_date": max(completed_dates) if completed_dates else None,
        "total_rows": total_rows,
        "total_unique_instruments": len(unique_instruments),
        "total_parquet_bytes": total_parquet_bytes,
        "total_source_zip_bytes_streamed": total_source_zip_bytes,
        "raw_archives_persisted": False,
    }
    atomic_write_json(target / "PROGRESS.json", progress)
    return progress


def main() -> int:
    args = parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start < DEFAULT_START or end > DEFAULT_END or start > end:
        raise SystemExit(
            f"range must satisfy {DEFAULT_START.isoformat()} <= start <= end <= {DEFAULT_END.isoformat()}"
        )
    target = Path(args.target).resolve()
    target.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(target).free
    if free_bytes < 5 * 1024**3:
        raise SystemExit(f"insufficient disk space: {free_bytes / 1024**3:.2f} GB free")

    run_log = target / "logs" / "build_events.jsonl"
    failures = 0
    days = list(iter_days(start, end))
    workers = max(1, min(int(args.workers), 4))
    completed_in_run = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(process_day, target, day, bool(args.force)): day
            for day in days
        }
        for future in concurrent.futures.as_completed(future_map):
            day = future_map[future]
            event_started = datetime.now(timezone.utc).isoformat()
            try:
                result = future.result()
                event = {"event_recorded_at_utc": event_started, **result}
                append_log(run_log, event)
                if not args.quiet:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
            except Exception as exc:
                failures += 1
                event = {
                    "event_recorded_at_utc": event_started,
                    "date": day.isoformat(),
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                append_log(run_log, event)
                print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)
            completed_in_run += 1
            if completed_in_run % 10 == 0:
                update_progress(target, start, end)
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

    progress = update_progress(target, start, end)
    summary = {
        "status": "RANGE_COMPLETE" if failures == 0 else "RANGE_COMPLETE_WITH_FAILURES",
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "failures": failures,
        "progress": progress,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
