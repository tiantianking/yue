from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from okx_signal_system.config import project_paths
from okx_signal_system.data.gap_handler import DataGapHandler, summarize_sync_error
from okx_signal_system.exchange.candles import okx_candles_to_frame
from okx_signal_system.exchange.okx import get_candles
from okx_signal_system.paths import find_runtime_cache_root
from okx_signal_system.timeframe import timeframe_spec

log = logging.getLogger(__name__)


@dataclass
class ClosedBackfillSymbolStatus:
    inst_id: str
    status: str
    rows_before: int
    rows_after: int
    added_rows: int
    first_ts: str
    last_ts: str
    expected_latest_closed: str
    missing_closed_bars: int
    error: str = ""
    internal_gap_count: int = 0
    max_gap_bars: int = 0
    continuous_tail_bars: int = 0
    minimum_continuous_tail: int = 0
    required_history_bars: int = 0


@dataclass
class ClosedBackfillCycleStatus:
    generated_at: str
    timeframe: str
    dataset: str
    expected_latest_closed: str
    next_run_at: str
    all_complete: bool
    symbols_checked: int
    symbols: list[ClosedBackfillSymbolStatus] = field(default_factory=list)


def latest_closed_candle_start(
    timeframe: str,
    *,
    now: datetime | pd.Timestamp | None = None,
    settle_seconds: int = 60,
) -> datetime:
    spec = timeframe_spec(timeframe)
    ts = pd.Timestamp(now or datetime.now(timezone.utc))
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    ts = ts - pd.Timedelta(seconds=max(0, settle_seconds))
    interval = spec.minutes * 60
    closed_end = int(ts.timestamp()) // interval * interval
    return datetime.fromtimestamp(closed_end - interval, tz=timezone.utc)


def seconds_until_next_closed_run(
    timeframe: str,
    *,
    now: datetime | pd.Timestamp | None = None,
    settle_seconds: int = 60,
) -> float:
    spec = timeframe_spec(timeframe)
    ts = pd.Timestamp(now or datetime.now(timezone.utc))
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    interval = spec.minutes * 60
    now_s = ts.timestamp()
    next_close = (int(now_s) // interval + 1) * interval
    return max(1.0, next_close + max(0, settle_seconds) - now_s)


def _read_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_parquet(path)
    if frame.empty:
        return frame
    frame = frame.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    return frame.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)


def _is_closed_value(value: object) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _closed_existing(frame: pd.DataFrame, *, expected_latest_closed: datetime) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    df = frame.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    if "is_closed" in df.columns:
        df = df[df["is_closed"].map(_is_closed_value)]
    df = df[df["ts"] <= pd.Timestamp(expected_latest_closed)]
    return df.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)


def _confirmed_frame(
    raw_bars: list[list],
    *,
    inst_id: str,
    timeframe: str,
    expected_latest_closed: datetime,
) -> pd.DataFrame:
    confirmed = [row for row in raw_bars if len(row) < 9 or str(row[8]) == "1"]
    frame = okx_candles_to_frame(confirmed)
    if frame.empty:
        return frame
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame[frame["ts"] <= pd.Timestamp(expected_latest_closed)]
    frame["symbol"] = inst_id
    frame["timeframe"] = timeframe
    frame["is_closed"] = True
    return frame.sort_values("ts").drop_duplicates("ts", keep="last").reset_index(drop=True)


def _missing_closed_bars(last_ts: str, expected_latest_closed: datetime, timeframe: str) -> int:
    if not last_ts:
        return 0
    latest = pd.Timestamp(last_ts)
    if latest.tzinfo is None:
        latest = latest.tz_localize("UTC")
    else:
        latest = latest.tz_convert("UTC")
    diff_seconds = (pd.Timestamp(expected_latest_closed) - latest).total_seconds()
    return max(0, int(round(diff_seconds / (timeframe_spec(timeframe).minutes * 60))))


def _gap_summary(frame: pd.DataFrame, *, timeframe: str) -> tuple[int, int]:
    if frame.empty or len(frame) < 2:
        return 0, 0
    interval_seconds = timeframe_spec(timeframe).minutes * 60
    ts = pd.to_datetime(frame["ts"], utc=True).drop_duplicates().sort_values()
    gap_sizes: list[int] = []
    for diff in ts.diff().dropna():
        diff_seconds = diff.total_seconds()
        if diff_seconds <= interval_seconds:
            continue
        gap_sizes.append(max(1, int(round(diff_seconds / interval_seconds)) - 1))
    return len(gap_sizes), max(gap_sizes, default=0)


def _continuous_tail_bars(
    frame: pd.DataFrame,
    *,
    expected_latest_closed: datetime,
    timeframe: str,
) -> int:
    if frame.empty:
        return 0
    interval = pd.Timedelta(minutes=timeframe_spec(timeframe).minutes)
    cursor = pd.Timestamp(expected_latest_closed)
    if cursor.tzinfo is None:
        cursor = cursor.tz_localize("UTC")
    else:
        cursor = cursor.tz_convert("UTC")
    values = set(pd.to_datetime(frame["ts"], utc=True).dropna())
    count = 0
    while cursor in values:
        count += 1
        cursor -= interval
    return count


def _status_from_closed_frame(
    frame: pd.DataFrame,
    *,
    expected_latest_closed: datetime,
    timeframe: str,
    required_history_bars: int,
    minimum_continuous_tail: int,
) -> dict[str, int | str]:
    first_ts = "" if frame.empty else pd.to_datetime(frame["ts"].min(), utc=True).isoformat()
    last_ts = "" if frame.empty else pd.to_datetime(frame["ts"].max(), utc=True).isoformat()
    missing = _missing_closed_bars(last_ts, expected_latest_closed, timeframe)
    internal_gap_count, max_gap_bars = _gap_summary(frame, timeframe=timeframe)
    continuous_tail_bars = _continuous_tail_bars(
        frame,
        expected_latest_closed=expected_latest_closed,
        timeframe=timeframe,
    )
    tail_floor = max(0, int(required_history_bars), int(minimum_continuous_tail))

    if frame.empty:
        status = "lagging"
    elif internal_gap_count > 0:
        status = "gapped"
    elif missing > 0:
        status = "lagging"
    elif continuous_tail_bars < tail_floor:
        status = "insufficient_history"
    else:
        status = "passed"

    return {
        "first_ts": first_ts,
        "last_ts": last_ts,
        "missing_closed_bars": missing,
        "internal_gap_count": internal_gap_count,
        "max_gap_bars": max_gap_bars,
        "continuous_tail_bars": continuous_tail_bars,
        "minimum_continuous_tail": int(minimum_continuous_tail),
        "required_history_bars": int(required_history_bars),
        "status": status,
    }


def sync_latest_closed_symbol(
    inst_id: str,
    *,
    timeframe: str = "15m",
    dataset: str | None = None,
    data_dir: Path | None = None,
    expected_latest_closed: datetime | None = None,
    limit: int = 100,
    required_history_bars: int = 0,
    minimum_continuous_tail: int = 0,
) -> ClosedBackfillSymbolStatus:
    spec = timeframe_spec(timeframe)
    dataset_name = dataset or f"okx_{spec.file_suffix}_extended"
    expected = expected_latest_closed or latest_closed_candle_start(spec.key)
    resolved_data_dir = data_dir or find_runtime_cache_root(dataset_name)
    handler = DataGapHandler(resolved_data_dir, timeframe=spec.key, dataset=dataset_name)
    path = handler.data_dir / handler._inst_to_filename(inst_id)
    existing = _read_existing(path)
    rows_before = len(existing)

    try:
        raw_bars = get_candles(inst_id, bar=spec.key, limit=limit)
        latest = _confirmed_frame(
            raw_bars,
            inst_id=inst_id,
            timeframe=spec.key,
            expected_latest_closed=expected,
        )
        if not latest.empty:
            if not handler.merge_and_save(inst_id, latest, mode="merge"):
                raise PermissionError(f"refusing to write closed backfill data dir: {handler.data_dir}")
            existing = _read_existing(path)

        rows_after = len(existing)
        closed_existing = _closed_existing(existing, expected_latest_closed=expected)
        status = _status_from_closed_frame(
            closed_existing,
            expected_latest_closed=expected,
            timeframe=spec.key,
            required_history_bars=required_history_bars,
            minimum_continuous_tail=minimum_continuous_tail,
        )
        return ClosedBackfillSymbolStatus(
            inst_id=inst_id,
            status=str(status["status"]),
            rows_before=rows_before,
            rows_after=rows_after,
            added_rows=max(0, rows_after - rows_before),
            first_ts=str(status["first_ts"]),
            last_ts=str(status["last_ts"]),
            expected_latest_closed=expected.isoformat(),
            missing_closed_bars=int(status["missing_closed_bars"]),
            internal_gap_count=int(status["internal_gap_count"]),
            max_gap_bars=int(status["max_gap_bars"]),
            continuous_tail_bars=int(status["continuous_tail_bars"]),
            minimum_continuous_tail=int(status["minimum_continuous_tail"]),
            required_history_bars=int(status["required_history_bars"]),
        )
    except Exception as exc:
        closed_existing = _closed_existing(existing, expected_latest_closed=expected)
        status = _status_from_closed_frame(
            closed_existing,
            expected_latest_closed=expected,
            timeframe=spec.key,
            required_history_bars=required_history_bars,
            minimum_continuous_tail=minimum_continuous_tail,
        )
        return ClosedBackfillSymbolStatus(
            inst_id=inst_id,
            status="failed",
            rows_before=rows_before,
            rows_after=len(existing),
            added_rows=0,
            first_ts=str(status["first_ts"]),
            last_ts=str(status["last_ts"]),
            expected_latest_closed=expected.isoformat(),
            missing_closed_bars=int(status["missing_closed_bars"]),
            error=summarize_sync_error(str(exc)),
            internal_gap_count=int(status["internal_gap_count"]),
            max_gap_bars=int(status["max_gap_bars"]),
            continuous_tail_bars=int(status["continuous_tail_bars"]),
            minimum_continuous_tail=int(status["minimum_continuous_tail"]),
            required_history_bars=int(status["required_history_bars"]),
        )


class ClosedCandleBackfillService:
    def __init__(
        self,
        symbols: list[str],
        *,
        timeframe: str = "15m",
        dataset: str | None = None,
        settle_seconds: int = 60,
        output_path: Path | None = None,
        data_dir: Path | None = None,
        fetch_limit: int = 100,
        required_history_bars: int | None = None,
        minimum_continuous_tail_bars: int = 0,
    ) -> None:
        self.symbols = symbols
        self.timeframe = timeframe_spec(timeframe).key
        self.dataset = dataset or f"okx_{timeframe_spec(timeframe).file_suffix}_extended"
        self.settle_seconds = settle_seconds
        self.output_path = output_path or project_paths().output_dir / "closed_kline_backfill_status.json"
        self.data_dir = Path(data_dir) if data_dir is not None else find_runtime_cache_root(self.dataset)
        self.fetch_limit = fetch_limit
        self.minimum_continuous_tail = max(0, int(minimum_continuous_tail_bars))
        self.required_history_bars = max(
            0,
            int(required_history_bars if required_history_bars is not None else self.minimum_continuous_tail),
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def next_run_at(self, *, now: datetime | None = None) -> datetime:
        base = pd.Timestamp(now or datetime.now(timezone.utc))
        delay = seconds_until_next_closed_run(
            self.timeframe,
            now=base,
            settle_seconds=self.settle_seconds,
        )
        return (base + pd.Timedelta(seconds=delay)).floor("us").to_pydatetime()

    def run_once(self) -> ClosedBackfillCycleStatus:
        expected = latest_closed_candle_start(
            self.timeframe,
            settle_seconds=self.settle_seconds,
        )
        rows = [
            sync_latest_closed_symbol(
                inst_id,
                timeframe=self.timeframe,
                dataset=self.dataset,
                data_dir=self.data_dir,
                expected_latest_closed=expected,
                limit=self.fetch_limit,
                required_history_bars=self.required_history_bars,
                minimum_continuous_tail=self.minimum_continuous_tail,
            )
            for inst_id in self.symbols
        ]
        payload = ClosedBackfillCycleStatus(
            generated_at=datetime.now(timezone.utc).isoformat(),
            timeframe=self.timeframe,
            dataset=self.dataset,
            expected_latest_closed=expected.isoformat(),
            next_run_at=self.next_run_at().isoformat(),
            all_complete=all(row.status == "passed" for row in rows),
            symbols_checked=len(rows),
            symbols=rows,
        )
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(asdict(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info(
            "closed candle backfill complete: timeframe=%s complete=%s symbols=%s",
            self.timeframe,
            payload.all_complete,
            len(rows),
        )
        return payload

    async def run_forever(self) -> None:
        await asyncio.to_thread(self.run_once)
        while True:
            delay = seconds_until_next_closed_run(
                self.timeframe,
                settle_seconds=self.settle_seconds,
            )
            await asyncio.sleep(delay)
            await asyncio.to_thread(self.run_once)
