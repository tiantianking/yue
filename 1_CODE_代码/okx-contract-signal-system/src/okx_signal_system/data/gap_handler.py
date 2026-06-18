"""
OKX 合约信号系统 - 数据回补与同步模块
处理系统离线时的数据空缺，自动从交易所API回填数据
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from okx_signal_system.data.loader import (
    MISSING_REQUIRED_IS_CLOSED_COLUMN,
    MISSING_REQUIRED_STRUCTURE_COLUMNS,
    OHLCV_COLUMNS,
    REQUIRED_METADATA_COLUMNS,
)
from okx_signal_system.exchange.candles import okx_candles_to_frame
from okx_signal_system.exchange.okx import get_candles  # OKXInstrument
from okx_signal_system.io_atomic import read_parquet_with_retry, write_parquet_atomic
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.timeframe import timeframe_spec

log = logging.getLogger(__name__)

# 回补配置
MAX_BACKFILL_GAPS = 3000  # 单轮最多回补K线；15m下约31天
MAX_GAP_BARS = 200  # 单次最大回补量
GAP_THRESHOLD_BARS = 3  # 超过3根周期视为数据断裂


def _configured_read_only() -> bool:
    try:
        from okx_signal_system.config import load_config

        data_cfg = load_config("base.yaml").get("data", {})
        if isinstance(data_cfg, dict):
            return bool(data_cfg.get("read_only", False))
    except Exception as exc:
        log.debug("Data read_only config unavailable: %s", exc)
    return False


def _to_okx_ms(value: datetime | pd.Timestamp) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return str(int(ts.timestamp() * 1000))


def summarize_sync_error(error: str) -> str:
    if any(token in error for token in ("NameResolutionError", "Failed to resolve", "getaddrinfo failed")):
        return "OKX REST DNS解析失败：www.okx.com；已继续使用本地历史数据和WebSocket"
    if "timed out" in error.lower() or "timeout" in error.lower():
        return "OKX REST连接超时；已继续使用本地历史数据和WebSocket"
    if "ProxyError" in error:
        return "OKX REST代理连接失败；已继续使用本地历史数据和WebSocket"
    return error.splitlines()[0][:240]


@dataclass
class DataGap:
    """数据缺口"""
    inst_id: str
    start_time: datetime
    end_time: datetime
    missing_bars: int
    severity: str  # minor, moderate, severe


class DataGapDetectionError(RuntimeError):
    """Raised when local gap detection cannot determine continuity."""


@dataclass
class SyncResult:
    """同步结果"""
    inst_id: str
    gaps_filled: int
    bars_added: int
    last_bar_time: datetime
    success: bool
    errors: list[str] = field(default_factory=list)


class DataGapHandler:
    """
    数据空缺处理器
    功能：
    1. 检测本地数据与实时数据的差距
    2. 从OKX API回填缺失的K线数据
    3. 检测并处理特征计算中的NaN
    4. 提供增量数据同步机制
    """

    def __init__(
        self,
        data_dir: Path | str | None = None,
        *,
        timeframe: str = "1h",
        dataset: str | None = None,
        read_only: bool | None = None,
        allow_existing_open_candles: bool = False,
    ):
        self.timeframe = timeframe_spec(timeframe)
        uses_default_history_root = data_dir is None
        if data_dir is None:
            dataset = dataset or f"okx_{self.timeframe.file_suffix}_extended"
            data_dir = find_lightweight_history(dataset)

        if isinstance(data_dir, str):
            data_dir = Path(data_dir)
        self.data_dir = data_dir
        self.read_only = _configured_read_only() if read_only is None and uses_default_history_root else bool(read_only)
        self.allow_existing_open_candles = bool(allow_existing_open_candles)
        self.last_merge_error: str | None = None
        self._api_unavailable_reason: str | None = None

    @staticmethod
    def _closed_bool(value: object) -> bool:
        if pd.isna(value):
            return True
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"true", "1", "yes"}

    @classmethod
    def _coerce_closed_series(cls, values: pd.Series) -> pd.Series:
        return values.map(cls._closed_bool).astype("bool")

    def detect_gaps(self, inst_id: str) -> list[DataGap]:
        """
        检测数据缺口
        返回缺口列表
        """
        fname = self._inst_to_filename(inst_id)
        path = self.data_dir / fname

        if not path.exists():
            missing_bars = int(365 * 24 * 60 / self.timeframe.minutes)
            return [DataGap(
                inst_id=inst_id,
                start_time=datetime.now(timezone.utc) - timedelta(days=365),
                end_time=datetime.now(timezone.utc),
                missing_bars=missing_bars,
                severity="severe",
            )]

        try:
            df = read_parquet_with_retry(path)
            df = self._validate_merge_frame(df, inst_id)
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            df = df.sort_values("ts")

            # 检测时间断裂点
            gaps = []
            times = df["ts"].values
            for i in range(1, len(times)):
                diff_seconds = (pd.Timestamp(times[i]) - pd.Timestamp(times[i-1])).total_seconds()
                diff_bars = diff_seconds / (self.timeframe.minutes * 60)
                if diff_bars > GAP_THRESHOLD_BARS:
                    gap = DataGap(
                        inst_id=inst_id,
                        start_time=pd.Timestamp(times[i-1]).to_pydatetime(),
                        end_time=pd.Timestamp(times[i]).to_pydatetime(),
                        missing_bars=max(1, int(round(diff_bars)) - 1),
                        severity="severe" if diff_bars > 72 else "moderate" if diff_bars > 24 else "minor",
                    )
                    gaps.append(gap)

            # 检测数据末尾与当前时间的差距
            last_time = df["ts"].max()
            now = datetime.now(timezone.utc)
            gap_hours = (now - last_time.to_pydatetime()).total_seconds() / 3600
            gap_bars = gap_hours * 60 / self.timeframe.minutes

            if gap_bars > GAP_THRESHOLD_BARS:
                gaps.append(DataGap(
                    inst_id=inst_id,
                    start_time=last_time.to_pydatetime(),
                    end_time=now,
                    missing_bars=max(1, int(gap_bars)),
                    severity="severe" if gap_bars > 72 else "moderate" if gap_bars > 24 else "minor",
                ))

            log.info(f"Detected {len(gaps)} gaps for {inst_id}")
            return gaps

        except Exception as e:
            log.error(f"Error detecting gaps for {inst_id}: {e}")
            raise DataGapDetectionError(f"GAP_DETECTION_FAILED: {inst_id}: {e}") from e

    def backfill_gap(self, gap: DataGap) -> pd.DataFrame | None:
        """
        回填单个数据缺口
        返回新加载的DataFrame
        """
        inst_id = gap.inst_id

        log.info(f"Backfilling {inst_id}: {gap.start_time} -> {gap.end_time}")

        try:
            all_bars = []
            gap_start = pd.Timestamp(gap.start_time)
            gap_end = pd.Timestamp(gap.end_time)
            if gap_start.tzinfo is None:
                gap_start = gap_start.tz_localize("UTC")
            else:
                gap_start = gap_start.tz_convert("UTC")
            if gap_end.tzinfo is None:
                gap_end = gap_end.tz_localize("UTC")
            else:
                gap_end = gap_end.tz_convert("UTC")

            cursor_end = gap_end
            remaining = min(max(gap.missing_bars, 1), MAX_BACKFILL_GAPS)

            while remaining > 0 and cursor_end > gap_start:
                raw_bars = get_candles(
                    inst_id,
                    bar=self.timeframe.key,
                    limit=min(MAX_GAP_BARS, remaining),
                    before=_to_okx_ms(gap_start),
                    after=_to_okx_ms(cursor_end),
                )

                if not raw_bars:
                    break

                raw_bars = [row for row in raw_bars if len(row) < 9 or str(row[8]) == "1"]
                df = self._parse_candles(raw_bars)
                df["ts"] = pd.to_datetime(df["ts"], utc=True)
                df = df[(df["ts"] > gap_start) & (df["ts"] < gap_end)]
                if df.empty:
                    break
                df["symbol"] = inst_id
                df["timeframe"] = self.timeframe.key
                df["is_closed"] = True
                all_bars.append(df)

                earliest = df["ts"].min()
                if earliest >= cursor_end:
                    break
                cursor_end = earliest
                remaining -= len(df)

            if not all_bars:
                return None

            # 合并所有批次
            result = pd.concat(all_bars, ignore_index=True)
            result = result.drop_duplicates(subset=["ts"]).sort_values("ts")
            result = result.reset_index(drop=True)

            log.info(f"Backfilled {len(result)} bars for {inst_id}")
            return result

        except Exception as e:
            self._api_unavailable_reason = summarize_sync_error(str(e))
            log.warning(f"Backfill unavailable for {inst_id}: {self._api_unavailable_reason}")
            log.debug("Raw backfill error for %s: %s", inst_id, e)
            return None

    def _parse_candles(self, raw_bars: list[list]) -> pd.DataFrame:
        """解析OKX K线数据"""
        return okx_candles_to_frame(raw_bars)

    def _validate_merge_frame(
        self,
        df: pd.DataFrame,
        inst_id: str,
        *,
        allow_existing_open_tail: bool = False,
        allow_open_candles: bool = False,
    ) -> pd.DataFrame:
        missing_ohlcv = [col for col in OHLCV_COLUMNS if col not in df.columns]
        if missing_ohlcv:
            raise ValueError(f"{inst_id} missing OHLCV columns: {missing_ohlcv}")
        missing_metadata = [col for col in REQUIRED_METADATA_COLUMNS if col not in df.columns]
        if missing_metadata:
            if "is_closed" in missing_metadata:
                raise ValueError(f"{inst_id} {MISSING_REQUIRED_IS_CLOSED_COLUMN}")
            raise ValueError(f"{inst_id} {MISSING_REQUIRED_STRUCTURE_COLUMNS}: {missing_metadata}")

        checked = df.copy()
        checked["ts"] = pd.to_datetime(checked["ts"], utc=True, errors="coerce")
        for col in ["open", "high", "low", "close", "volume"]:
            checked[col] = pd.to_numeric(checked[col], errors="coerce")
        if checked[["ts", "open", "high", "low", "close", "volume"]].isna().any(axis=None):
            raise ValueError(f"{inst_id} invalid OHLCV values")

        null_metadata = [col for col in REQUIRED_METADATA_COLUMNS if checked[col].isna().any()]
        if null_metadata:
            if "is_closed" in null_metadata:
                raise ValueError(f"{inst_id} {MISSING_REQUIRED_IS_CLOSED_COLUMN}")
            raise ValueError(f"{inst_id} {MISSING_REQUIRED_STRUCTURE_COLUMNS}: {null_metadata}")

        checked["is_closed"] = self._coerce_closed_series(checked["is_closed"])
        if not checked["is_closed"].all() and allow_open_candles:
            pass
        elif not checked["is_closed"].all() and allow_existing_open_tail:
            ordered = checked.sort_values("ts").reset_index(drop=True)
            open_mask = ~ordered["is_closed"]
            first_open_idx = int(open_mask[open_mask].index.min())
            if first_open_idx != len(ordered) - 1:
                raise ValueError(f"{inst_id} open candles cannot be saved by gap backfill")
        elif not checked["is_closed"].all():
            raise ValueError(f"{inst_id} open candles cannot be saved by gap backfill")

        symbol_mismatch = checked["symbol"].astype("string").str.strip() != inst_id
        if symbol_mismatch.fillna(True).any():
            raise ValueError(f"{inst_id} {MISSING_REQUIRED_STRUCTURE_COLUMNS}: symbol mismatch")
        timeframe_mismatch = checked["timeframe"].astype("string").str.strip() != self.timeframe.key
        if timeframe_mismatch.fillna(True).any():
            raise ValueError(f"{inst_id} {MISSING_REQUIRED_STRUCTURE_COLUMNS}: timeframe mismatch")
        return checked

    def merge_and_save(
        self,
        inst_id: str,
        new_data: pd.DataFrame,
        mode: str = "append",
        *,
        allow_existing_open_tail: bool | None = None,
    ) -> bool:
        """
        合并并保存数据
        mode: append(追加), replace(替换), merge(合并去重)
        """
        fname = self._inst_to_filename(inst_id)
        path = self.data_dir / fname
        self.last_merge_error = None
        preserve_open_tail = (
            self.allow_existing_open_candles
            if allow_existing_open_tail is None
            else bool(allow_existing_open_tail)
        )

        if self.read_only:
            self.last_merge_error = f"data directory is read-only: {self.data_dir}"
            log.error("Refusing to write read-only data directory for %s: %s", inst_id, self.data_dir)
            return False

        try:
            new_data = self._validate_merge_frame(new_data, inst_id)
            if mode == "replace" or not path.exists():
                df = new_data
            else:
                existing = read_parquet_with_retry(path)
                existing = self._validate_merge_frame(
                    existing,
                    inst_id,
                    allow_existing_open_tail=preserve_open_tail,
                    allow_open_candles=preserve_open_tail and mode == "merge",
                )

                if mode == "append":
                    df = pd.concat([existing, new_data], ignore_index=True)
                else:  # merge
                    df = pd.concat([existing, new_data], ignore_index=True)
                    df["_merge_precedence"] = range(len(df))
                    df["_merge_precedence"] = df["_merge_precedence"] + (df["is_closed"].astype(int) * (len(df) + 1))
                    df = df.sort_values(["ts", "_merge_precedence"]).drop(columns=["_merge_precedence"])

                df = df.drop_duplicates(subset=["ts"], keep="last")
                df = df.sort_values("ts").reset_index(drop=True)

            df = self._validate_merge_frame(
                df,
                inst_id,
                allow_existing_open_tail=preserve_open_tail,
            )

            write_parquet_atomic(df, path)
            log.info(f"Saved {len(df)} bars for {inst_id}")
            return True

        except Exception as e:
            message = str(e)
            self.last_merge_error = summarize_sync_error(message)
            if "open candles cannot be saved by gap backfill" in message:
                log.info("Skipped open-candle gap backfill rows for %s: %s", inst_id, message)
            else:
                log.error(f"Save error for {inst_id}: {e}")
            return False

    def sync_symbol(self, inst_id: str) -> SyncResult:
        """
        同步单个币种数据
        检测缺口并回填
        """
        gaps: list[DataGap] = []
        result = SyncResult(
            inst_id=inst_id,
            gaps_filled=0,
            bars_added=0,
            last_bar_time=datetime.now(timezone.utc),
            success=True,
        )
        try:
            gaps = self.detect_gaps(inst_id)
        except DataGapDetectionError as exc:
            result.success = False
            result.errors.append(str(exc))
            return result

        for gap in gaps:
            if self._api_unavailable_reason:
                result.success = False
                result.errors.append(self._api_unavailable_reason)
                break

            new_data = self.backfill_gap(gap)
            if new_data is not None and len(new_data) > 0:
                if not self.merge_and_save(inst_id, new_data, mode="merge"):
                    result.success = False
                    result.errors.append(f"data directory is read-only or not writable: {self.data_dir}")
                    break
                result.gaps_filled += 1
                result.bars_added += len(new_data)

                if result.last_bar_time < new_data["ts"].max():
                    result.last_bar_time = new_data["ts"].max()
            else:
                result.success = False
                reason = self._api_unavailable_reason or "backfill returned no data"
                result.errors.append(reason)
                break

        return result

    def sync_all_symbols(self, symbols: list[str]) -> dict[str, SyncResult]:
        """同步所有币种"""
        results = {}
        for sym in symbols:
            results[sym] = self.sync_symbol(sym)
        return results

    def _inst_to_filename(self, inst_id: str) -> str:
        """转换inst_id为文件名"""
        # 处理多种格式: ADA-USDT-SWAP, ADA-USDT, ADA
        normalized = inst_id.replace("-SWAP", "").replace("-", "_").upper()
        parts = normalized.split("_")
        if len(parts) >= 2:
            base = parts[0]
            quote = parts[1] if len(parts) > 1 else "USDT"
            return f"{base}_{quote}_{quote}_{self.timeframe.file_suffix}.parquet"
        return f"{normalized}_USDT_{self.timeframe.file_suffix}.parquet"


class FeatureGapHandler:
    """
    特征计算中的NaN处理
    当数据有缺口时，特征计算会产生NaN，需要特殊处理
    """

    @staticmethod
    def detect_nan_regions(df: pd.DataFrame) -> list[tuple[int, int]]:
        """
        检测NaN区域
        返回 [(start_idx, end_idx), ...]
        """
        if "close" not in df.columns:
            return []

        nan_mask = df["close"].isna()
        if not nan_mask.any():
            return []

        regions = []
        start = None

        for i, is_nan in enumerate(nan_mask):
            if is_nan and start is None:
                start = i
            elif not is_nan and start is not None:
                regions.append((start, i - 1))
                start = None

        if start is not None:
            regions.append((start, len(df) - 1))

        return regions

    @staticmethod
    def fill_nan_forward(df: pd.DataFrame, max_fill: int = 5) -> pd.DataFrame:
        """
        用前向填充处理NaN
        限制最大连续填充数量
        """
        df = df.copy()

        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                continue

            # 前向填充
            df[col] = df[col].ffill()

            # 如果还有NaN，用后向填充
            df[col] = df[col].bfill()

        return df

    @staticmethod
    def mark_unreliable_bars(
        df: pd.DataFrame,
        max_consecutive_nan: int = 3,
    ) -> pd.DataFrame:
        """
        标记不可靠的K线
        当连续NaN超过阈值时，后续K线标记为不可靠
        """
        df = df.copy()
        df["is_reliable"] = True

        nan_regions = FeatureGapHandler.detect_nan_regions(df)

        for start, end in nan_regions:
            # 缺口后的max_fill根K线标记为不可靠
            unreliable_end = min(end + max_consecutive_nan, len(df))
            df.loc[unreliable_end:unreliable_end, "is_reliable"] = False

        return df


def sync_on_startup(symbols: list[str], *, timeframe: str = "1h", dataset: str | None = None) -> dict[str, SyncResult]:
    """
    启动时同步数据
    应该在系统启动时调用
    """
    handler = DataGapHandler(timeframe=timeframe, dataset=dataset)
    return handler.sync_all_symbols(symbols)


# ============================================================
# 增量数据同步器
# ============================================================
class IncrementalSyncer:
    """
    增量数据同步器
    每次扫描周期结束时调用，保持数据最新
    """

    def __init__(self, data_dir: Path | str | None = None, *, timeframe: str = "1h", dataset: str | None = None):
        self.handler = DataGapHandler(data_dir, timeframe=timeframe, dataset=dataset)
        self.last_sync: dict[str, datetime] = {}

    def sync_if_needed(self, inst_id: str, interval_hours: int = 1) -> SyncResult | None:
        """
        按需同步
        只有当距离上次同步超过interval_hours时才同步
        """
        now = datetime.now(timezone.utc)
        last = self.last_sync.get(inst_id)

        if last and (now - last).total_seconds() < interval_hours * 3600:
            return None

        result = self.handler.sync_symbol(inst_id)
        self.last_sync[inst_id] = now
        return result

    def sync_batch(self, symbols: list[str], interval_hours: int = 1) -> dict[str, SyncResult]:
        """批量按需同步"""
        results = {}
        for sym in symbols:
            result = self.sync_if_needed(sym, interval_hours)
            if result:
                results[sym] = result
        return results


if __name__ == "__main__":
    # 测试数据同步
    logging.basicConfig(level=logging.INFO)

    test_symbols = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    results = sync_on_startup(test_symbols)

    for sym, result in results.items():
        status = "[OK]" if result.success else "[X]"
        print(f"{status} {sym}: {result.bars_added} bars added, {result.gaps_filled} gaps filled")
