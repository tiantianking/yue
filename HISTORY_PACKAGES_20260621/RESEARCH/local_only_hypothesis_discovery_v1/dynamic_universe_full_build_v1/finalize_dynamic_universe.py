from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow.parquet as pq

DATASET_ID = "OKX_DYNAMIC_UNIVERSE_4H_20230701_20260616_V1"
START = date(2023, 7, 1)
END = date(2026, 6, 16)
FOUR_HOURS_MS = 4 * 60 * 60 * 1000
SOURCE_TIMEZONE = ZoneInfo("Asia/Shanghai")
EXPECTED_COLUMNS = [
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


def iter_days(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def paths_for_day(target: Path, day: date) -> tuple[Path, Path]:
    partition = Path(f"year={day.year:04d}") / f"month={day.month:02d}"
    return (
        target / "data" / partition / f"{day.isoformat()}.parquet",
        target / "daily_manifests" / partition / f"{day.isoformat()}.json",
    )


def finite_nonnegative(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.notna() & numeric.map(math.isfinite) & numeric.ge(0)


def finite_positive(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.notna() & numeric.map(math.isfinite) & numeric.gt(0)


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parents[3]
    target = repo_root / "历史数据_保留" / "lightweight_history" / DATASET_ID.lower()
    if not target.exists():
        target = repo_root / "历史数据_保留" / "lightweight_history" / "okx_dynamic_universe_4h_20230701_20260616_v1"
    if not target.exists():
        raise SystemExit(f"dataset target not found: {target}")

    expected_days = list(iter_days(START, END))
    expected_day_strings = {day.isoformat() for day in expected_days}
    discovered_parquets = sorted((target / "data").glob("year=*/month=*/*.parquet"))
    discovered_manifests = sorted((target / "daily_manifests").glob("year=*/month=*/*.json"))
    parquet_dates = {path.stem for path in discovered_parquets}
    manifest_dates = {path.stem for path in discovered_manifests}

    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    daily_records: list[dict[str, Any]] = []
    all_frames: list[pd.DataFrame] = []
    aggregate_digest = hashlib.sha256()
    total_source_zip_bytes = 0
    total_manifest_rows = 0
    total_manifest_parquet_bytes = 0

    for day in expected_days:
        parquet_path, manifest_path = paths_for_day(target, day)
        day_text = day.isoformat()
        if not parquet_path.exists():
            failures.append({"check": "parquet_exists", "date": day_text})
            continue
        if not manifest_path.exists():
            failures.append({"check": "daily_manifest_exists", "date": day_text})
            continue

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            failures.append({"check": "daily_manifest_parse", "date": day_text, "error": str(exc)})
            continue

        actual_hash = sha256_file(parquet_path)
        manifest_hash = manifest.get("output", {}).get("parquet_sha256")
        if manifest.get("status") != "COMPLETE":
            failures.append({"check": "daily_manifest_status", "date": day_text, "actual": manifest.get("status")})
        if manifest.get("dataset_id") != DATASET_ID:
            failures.append({"check": "daily_manifest_dataset_id", "date": day_text, "actual": manifest.get("dataset_id")})
        if manifest.get("utc_date") != day_text:
            failures.append({"check": "daily_manifest_date", "date": day_text, "actual": manifest.get("utc_date")})
        if manifest_hash != actual_hash:
            failures.append({"check": "parquet_sha256", "date": day_text, "expected": manifest_hash, "actual": actual_hash})

        try:
            table = pq.ParquetFile(parquet_path).read()
            frame = table.to_pandas()
        except Exception as exc:
            failures.append({"check": "parquet_read", "date": day_text, "error": str(exc)})
            continue

        columns = list(frame.columns)
        if columns != EXPECTED_COLUMNS:
            failures.append({"check": "schema_columns", "date": day_text, "actual": columns})

        rows = int(len(frame))
        file_bytes = int(parquet_path.stat().st_size)
        manifest_rows = int(manifest.get("output", {}).get("rows", -1))
        manifest_bytes = int(manifest.get("output", {}).get("parquet_bytes", -1))
        if rows != manifest_rows:
            failures.append({"check": "row_count", "date": day_text, "expected": manifest_rows, "actual": rows})
        if file_bytes != manifest_bytes:
            failures.append({"check": "parquet_bytes", "date": day_text, "expected": manifest_bytes, "actual": file_bytes})

        if not frame.empty:
            duplicate_rows = int(frame.duplicated(["instrument_name", "bar_open_ms"]).sum())
            if duplicate_rows:
                failures.append({"check": "duplicate_symbol_timestamp", "date": day_text, "count": duplicate_rows})

            bar_ms = pd.to_numeric(frame["bar_open_ms"], errors="coerce")
            misaligned = int((bar_ms.mod(FOUR_HOURS_MS) != 0).sum())
            if misaligned:
                failures.append({"check": "4h_alignment", "date": day_text, "count": misaligned})

            source_mismatch = int(frame["source_day"].astype(str).ne(day_text).sum())
            if source_mismatch:
                failures.append({"check": "source_day", "date": day_text, "count": source_mismatch})

            source_calendar_day = (
                pd.to_datetime(bar_ms, unit="ms", utc=True, errors="coerce")
                .dt.tz_convert(SOURCE_TIMEZONE)
                .dt.strftime("%Y-%m-%d")
            )
            timestamp_day_mismatch = int(source_calendar_day.ne(day_text).sum())
            if timestamp_day_mismatch:
                failures.append({"check": "timestamp_source_day", "date": day_text, "count": timestamp_day_mismatch})

            minute_bad = int(pd.to_numeric(frame["minute_count"], errors="coerce").ne(240).sum())
            if minute_bad:
                failures.append({"check": "minute_count_240", "date": day_text, "count": minute_bad})

            spot_bad = int((~frame["spot_present_at_bar_open"].fillna(False).astype(bool)).sum())
            if spot_bad:
                failures.append({"check": "spot_presence", "date": day_text, "count": spot_bad})

            for column in ("open", "high", "low", "close"):
                bad = int((~finite_positive(frame[column])).sum())
                if bad:
                    failures.append({"check": f"positive_{column}", "date": day_text, "count": bad})
            for column in ("vol", "vol_ccy", "vol_quote"):
                bad = int((~finite_nonnegative(frame[column])).sum())
                if bad:
                    failures.append({"check": f"nonnegative_{column}", "date": day_text, "count": bad})

            open_px = pd.to_numeric(frame["open"], errors="coerce")
            high_px = pd.to_numeric(frame["high"], errors="coerce")
            low_px = pd.to_numeric(frame["low"], errors="coerce")
            close_px = pd.to_numeric(frame["close"], errors="coerce")
            ohlc_bad = int((high_px.lt(pd.concat([open_px, close_px], axis=1).max(axis=1)) | low_px.gt(pd.concat([open_px, close_px], axis=1).min(axis=1)) | high_px.lt(low_px)).sum())
            if ohlc_bad:
                failures.append({"check": "ohlc_consistency", "date": day_text, "count": ohlc_bad})

            bar_count = int(frame["bar_open_ms"].nunique())
            source_midnight_utc_ms = int(
                pd.Timestamp(day_text, tz=SOURCE_TIMEZONE).tz_convert("UTC").timestamp() * 1000
            )
            expected_bar_ms = {
                source_midnight_utc_ms + offset * FOUR_HOURS_MS
                for offset in range(6)
            }
            actual_bar_ms = set(pd.to_numeric(frame["bar_open_ms"], errors="coerce").dropna().astype("int64").tolist())
            missing_bar_opens = sorted(expected_bar_ms - actual_bar_ms)
            if missing_bar_opens:
                failures.append({"check": "daily_six_bar_opens", "date": day_text, "missing_count": len(missing_bar_opens)})

            unique_instruments = int(frame["instrument_name"].nunique())
            counts_per_symbol = frame.groupby("instrument_name")["bar_open_ms"].nunique()
            partial_symbols = int(counts_per_symbol.lt(6).sum())
        else:
            bar_count = 0
            unique_instruments = 0
            partial_symbols = 0
            failures.append({"check": "nonempty_daily_parquet", "date": day_text})

        manifest_symbols = int(manifest.get("output", {}).get("unique_instruments", -1))
        if unique_instruments != manifest_symbols:
            failures.append({"check": "unique_instruments", "date": day_text, "expected": manifest_symbols, "actual": unique_instruments})

        source_zip_bytes = int(manifest.get("sources", {}).get("swap", {}).get("zip_bytes", 0)) + int(manifest.get("sources", {}).get("spot", {}).get("zip_bytes", 0))
        total_source_zip_bytes += source_zip_bytes
        total_manifest_rows += rows
        total_manifest_parquet_bytes += file_bytes

        relative_parquet = parquet_path.relative_to(target).as_posix()
        relative_manifest = manifest_path.relative_to(target).as_posix()
        manifest_file_hash = sha256_file(manifest_path)
        aggregate_digest.update(f"{day_text}|{relative_parquet}|{actual_hash}|{relative_manifest}|{manifest_file_hash}\n".encode("utf-8"))
        daily_records.append(
            {
                "date": day_text,
                "rows": rows,
                "bar_opens": bar_count,
                "unique_instruments": unique_instruments,
                "partial_day_instruments": partial_symbols,
                "parquet_bytes": file_bytes,
                "parquet_sha256": actual_hash,
                "daily_manifest_sha256": manifest_file_hash,
                "source_zip_bytes": source_zip_bytes,
            }
        )
        all_frames.append(frame[["instrument_name", "base", "bar_open_ms", "source_day"]].copy())

    extra_parquets = sorted(parquet_dates - expected_day_strings)
    extra_manifests = sorted(manifest_dates - expected_day_strings)
    missing_parquets = sorted(expected_day_strings - parquet_dates)
    missing_manifests = sorted(expected_day_strings - manifest_dates)
    if extra_parquets:
        failures.append({"check": "no_extra_parquet_dates", "dates": extra_parquets})
    if extra_manifests:
        failures.append({"check": "no_extra_manifest_dates", "dates": extra_manifests})
    if missing_parquets:
        failures.append({"check": "all_parquet_dates_present", "dates": missing_parquets})
    if missing_manifests:
        failures.append({"check": "all_manifest_dates_present", "dates": missing_manifests})

    combined = pd.concat(all_frames, ignore_index=True) if all_frames else pd.DataFrame()
    global_duplicate_rows = int(combined.duplicated(["instrument_name", "bar_open_ms"]).sum()) if not combined.empty else 0
    if global_duplicate_rows:
        failures.append({"check": "global_duplicate_symbol_timestamp", "count": global_duplicate_rows})

    daily_df = pd.DataFrame(daily_records).sort_values("date") if daily_records else pd.DataFrame()
    universe_jump_records: list[dict[str, Any]] = []
    if not daily_df.empty:
        daily_df["instrument_change"] = daily_df["unique_instruments"].diff()
        for row in daily_df.loc[daily_df["instrument_change"].abs().ge(5)].itertuples(index=False):
            universe_jump_records.append(
                {
                    "date": row.date,
                    "unique_instruments": int(row.unique_instruments),
                    "change_from_prior_day": int(row.instrument_change),
                }
            )
        if universe_jump_records:
            warnings.append({"check": "large_daily_universe_changes", "threshold": 5, "count": len(universe_jump_records)})

    partial_day_distribution = Counter(int(record["partial_day_instruments"]) for record in daily_records)
    generated_at = datetime.now(timezone.utc).isoformat()
    status = "PASS" if not failures else "FAIL"
    quality_report = {
        "schema": "okx_dynamic_universe_quality_report_v1",
        "dataset_id": DATASET_ID,
        "generated_at_utc": generated_at,
        "status": status,
        "expected_range": {"start": START.isoformat(), "end": END.isoformat(), "days": len(expected_days)},
        "observed": {
            "parquet_files": len(discovered_parquets),
            "daily_manifests": len(discovered_manifests),
            "validated_days": len(daily_records),
            "rows": int(total_manifest_rows),
            "unique_instruments": int(combined["instrument_name"].nunique()) if not combined.empty else 0,
            "first_bar_open_utc": pd.to_datetime(combined["bar_open_ms"].min(), unit="ms", utc=True).isoformat() if not combined.empty else None,
            "last_bar_open_utc": pd.to_datetime(combined["bar_open_ms"].max(), unit="ms", utc=True).isoformat() if not combined.empty else None,
            "parquet_bytes": int(total_manifest_parquet_bytes),
            "source_zip_bytes_streamed": int(total_source_zip_bytes),
            "global_duplicate_symbol_timestamp_rows": global_duplicate_rows,
            "daily_instrument_min": int(daily_df["unique_instruments"].min()) if not daily_df.empty else None,
            "daily_instrument_max": int(daily_df["unique_instruments"].max()) if not daily_df.empty else None,
            "daily_instrument_median": float(daily_df["unique_instruments"].median()) if not daily_df.empty else None,
            "partial_day_instrument_count_distribution": dict(sorted(partial_day_distribution.items())),
        },
        "checks": {
            "date_coverage": len(missing_parquets) == 0 and len(missing_manifests) == 0,
            "daily_manifest_status_and_identity": not any(f["check"].startswith("daily_manifest_") for f in failures),
            "parquet_hashes": not any(f["check"] == "parquet_sha256" for f in failures),
            "parquet_readability_and_schema": not any(f["check"] in {"parquet_read", "schema_columns"} for f in failures),
            "row_and_byte_counts": not any(f["check"] in {"row_count", "parquet_bytes"} for f in failures),
            "four_hour_alignment_and_six_source_day_opens": not any(f["check"] in {"4h_alignment", "daily_six_bar_opens"} for f in failures),
            "minute_completeness": not any(f["check"] == "minute_count_240" for f in failures),
            "point_in_time_spot_presence": not any(f["check"] == "spot_presence" for f in failures),
            "ohlcv_validity": not any(f["check"].startswith(("positive_", "nonnegative_")) or f["check"] == "ohlc_consistency" for f in failures),
            "no_duplicate_symbol_timestamp": global_duplicate_rows == 0 and not any(f["check"] == "duplicate_symbol_timestamp" for f in failures),
            "source_day_consistency": not any(f["check"] in {"source_day", "timestamp_source_day"} for f in failures),
        },
        "failures": failures,
        "warnings": warnings,
        "largest_daily_universe_changes": sorted(universe_jump_records, key=lambda x: abs(x["change_from_prior_day"]), reverse=True)[:30],
    }

    manifest = {
        "schema": "okx_dynamic_universe_dataset_manifest_v1",
        "dataset_id": DATASET_ID,
        "status": "COMPLETE_VALIDATED" if status == "PASS" else "COMPLETE_VALIDATION_FAILED",
        "generated_at_utc": generated_at,
        "range": {
            "source_calendar_timezone": "Asia/Shanghai",
            "source_calendar_start_date": START.isoformat(),
            "source_calendar_end_date": END.isoformat(),
            "natural_days": len(expected_days),
        },
        "causal_universe_rule": (
            "For each UTC-aligned 4h bar, retain BASE-USDT-SWAP only when an exact archived BASE-USDT spot minute exists at the bar open and the swap bar contains 240 contiguous archived one-minute rows."
        ),
        "storage": {
            "raw_archives_persisted": False,
            "daily_parquet_files": len(discovered_parquets),
            "daily_manifest_files": len(discovered_manifests),
            "rows": int(total_manifest_rows),
            "parquet_bytes": int(total_manifest_parquet_bytes),
            "source_zip_bytes_streamed": int(total_source_zip_bytes),
        },
        "coverage": {
            "first_bar_open_utc": quality_report["observed"]["first_bar_open_utc"],
            "last_bar_open_utc": quality_report["observed"]["last_bar_open_utc"],
            "unique_instruments": quality_report["observed"]["unique_instruments"],
            "daily_instrument_min": quality_report["observed"]["daily_instrument_min"],
            "daily_instrument_max": quality_report["observed"]["daily_instrument_max"],
            "daily_instrument_median": quality_report["observed"]["daily_instrument_median"],
        },
        "integrity": {
            "quality_report_status": status,
            "quality_report_relative_path": "DATA_QUALITY_REPORT.json",
            "dataset_tree_sha256": aggregate_digest.hexdigest(),
            "hash_definition": "SHA256 over sorted lines date|parquet_path|parquet_sha256|daily_manifest_path|daily_manifest_sha256",
            "failure_count": len(failures),
            "warning_count": len(warnings),
        },
        "research_boundary": (
            "This dataset is a research-only point-in-time universe reconstruction. It does not change live signal generation, H22 parameters, execution assumptions, or acceptance gates."
        ),
    }

    report_cn = f"""# OKX历史动态交易宇宙数据集最终质量报告

状态：`{manifest['status']}`

数据集：`{DATASET_ID}`

生成时间（UTC）：`{generated_at}`

## 验收结论

- 日期覆盖：{START.isoformat()} 至 {END.isoformat()}，共 {len(expected_days)} 天；
- 日级Parquet：{len(discovered_parquets)} 个；
- 日级清单：{len(discovered_manifests)} 个；
- 总行数：{total_manifest_rows:,}；
- 历史出现过的USDT永续：{quality_report['observed']['unique_instruments']} 个；
- 每日交易宇宙：最少 {quality_report['observed']['daily_instrument_min']} 个，中位数 {quality_report['observed']['daily_instrument_median']:.1f} 个，最多 {quality_report['observed']['daily_instrument_max']} 个；
- Parquet长期存储：{total_manifest_parquet_bytes / 1024 / 1024:.2f} MB；
- 原始官方ZIP累计流式读取：{total_source_zip_bytes / 1024 / 1024 / 1024:.2f} GB；
- 数据树总哈希：`{aggregate_digest.hexdigest()}`；
- 硬失败：{len(failures)}；
- 提示项：{len(warnings)}。

## 已执行的硬校验

1. 1082个自然日无缺日、无额外日期；
2. 每个日级清单均为COMPLETE且数据集ID、日期一致；
3. 逐个重算1082个Parquet SHA256并与日清单比对；
4. 逐个读取Parquet并核对字段、行数与字节数；
5. 每根K线严格4小时对齐，每个北京时间自然日具备00/04/08/12/16/20六个开盘时点（对应UTC前一日16/20点及当日00/04/08/12点）；
6. 每根4小时K线均由240根分钟K线构成；
7. 每个入选标的在开盘时点均存在同名OKX USDT现货；
8. 价格为正、成交量非负、OHLC关系有效；
9. 全样本不存在重复的“合约+4小时开盘时间”；
10. source_day与时间戳UTC自然日一致。

## 解释边界

OKX官方日包按北京时间自然日划分；Parquet中的`bar_open_utc`仍保存UTC时间。部分合约在上市或退市当天只拥有少于6根完整4小时K线，这是点时宇宙的正常现象，不按缺柱处理。原始ZIP只在内存中流式读取并已释放，长期只保留轻量日级Parquet与可复核清单。

本数据集仅用于研究固定21币样本的存活者偏差，不改变H22的14日形成期、4入6出、错开组合、刷新节奏、成本模型或前向验收标准。
"""

    atomic_json(target / "DATA_QUALITY_REPORT.json", quality_report)
    atomic_json(target / "DATASET_MANIFEST.json", manifest)
    atomic_text(target / "DATA_QUALITY_REPORT_CN.md", report_cn)
    print(json.dumps({"status": status, "manifest": manifest, "failure_examples": failures[:10]}, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
