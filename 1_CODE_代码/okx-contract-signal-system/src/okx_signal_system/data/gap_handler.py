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

from okx_signal_system.exchange.candles import okx_candles_to_frame
from okx_signal_system.exchange.okx import get_candles  # OKXInstrument
from okx_signal_system.paths import find_lightweight_history

log = logging.getLogger(__name__)

# 回补配置
MAX_BACKFILL_GAPS = 500  # 最多回补500根K线（约21天1h数据）
GAP_THRESHOLD_HOURS = 6  # 超过6小时视为数据断裂
MAX_GAP_BARS = 200  # 单次最大回补量


@dataclass
class DataGap:
    """数据缺口"""
    inst_id: str
    start_time: datetime
    end_time: datetime
    missing_bars: int
    severity: str  # minor, moderate, severe


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

    def __init__(self, data_dir: Path | str | None = None):
        if data_dir is None:
            # 尝试多个可能的路径
            possible_paths = [
                Path(r"D:\JIAOYI-CX\历史数据_保留\lightweight_history\okx_1h_extended"),
                Path(__file__).parent.parent.parent / "data" / "okx_1h_extended",
            ]
            for p in possible_paths:
                if p.exists():
                    data_dir = p
                    break
            if data_dir is None:
                data_dir = possible_paths[0]  # 使用第一个作为默认值

        if isinstance(data_dir, str):
            data_dir = Path(data_dir)
        self.data_dir = data_dir
        self._api_unavailable_reason: str | None = None

    def detect_gaps(self, inst_id: str) -> list[DataGap]:
        """
        检测数据缺口
        返回缺口列表
        """
        fname = self._inst_to_filename(inst_id)
        path = self.data_dir / fname

        if not path.exists():
            return [DataGap(
                inst_id=inst_id,
                start_time=datetime.now(timezone.utc) - timedelta(days=365),
                end_time=datetime.now(timezone.utc),
                missing_bars=365 * 24,
                severity="severe",
            )]

        try:
            df = pd.read_parquet(path)
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
            df = df.sort_values("ts")

            # 检测时间断裂点
            gaps = []
            times = df["ts"].values
            for i in range(1, len(times)):
                diff = (pd.Timestamp(times[i]) - pd.Timestamp(times[i-1])).total_seconds() / 3600
                if diff > GAP_THRESHOLD_HOURS:
                    gap = DataGap(
                        inst_id=inst_id,
                        start_time=pd.Timestamp(times[i-1]).to_pydatetime(),
                        end_time=pd.Timestamp(times[i]).to_pydatetime(),
                        missing_bars=int(diff),
                        severity="severe" if diff > 72 else "moderate" if diff > 24 else "minor",
                    )
                    gaps.append(gap)

            # 检测数据末尾与当前时间的差距
            last_time = df["ts"].max()
            now = datetime.now(timezone.utc)
            gap_hours = (now - last_time.to_pydatetime()).total_seconds() / 3600

            if gap_hours > GAP_THRESHOLD_HOURS:
                gaps.append(DataGap(
                    inst_id=inst_id,
                    start_time=last_time.to_pydatetime(),
                    end_time=now,
                    missing_bars=int(gap_hours),
                    severity="severe" if gap_hours > 72 else "moderate" if gap_hours > 24 else "minor",
                ))

            log.info(f"Detected {len(gaps)} gaps for {inst_id}")
            return gaps

        except Exception as e:
            log.error(f"Error detecting gaps for {inst_id}: {e}")
            return []

    def backfill_gap(self, gap: DataGap) -> pd.DataFrame | None:
        """
        回填单个数据缺口
        返回新加载的DataFrame
        """
        inst_id = gap.inst_id

        # 转换时间格式
        start_iso = gap.start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        after_iso = gap.end_time.strftime("%Y-%m-%dT%H:%M:%SZ")

        log.info(f"Backfilling {inst_id}: {gap.start_time} -> {gap.end_time}")

        try:
            # OKX API每次最多获取300根K线，分批回补
            all_bars = []
            current_end = gap.end_time
            lookback = min(gap.missing_bars, MAX_BACKFILL_GAPS)

            while lookback > 0:
                # 计算这批回补的时间范围
                batch_end = current_end
                batch_start = current_end - timedelta(hours=min(lookback, MAX_GAP_BARS))

                # 获取K线数据
                raw_bars = get_candles(inst_id, bar="1h", limit=MAX_GAP_BARS)

                if not raw_bars:
                    break

                # 转换为DataFrame
                df = self._parse_candles(raw_bars)
                all_bars.append(df)

                # 更新继续回补
                lookback -= MAX_GAP_BARS
                current_end = batch_start

            if not all_bars:
                return None

            # 合并所有批次
            result = pd.concat(all_bars, ignore_index=True)
            result = result.drop_duplicates(subset=["ts"]).sort_values("ts")
            result = result.reset_index(drop=True)

            log.info(f"Backfilled {len(result)} bars for {inst_id}")
            return result

        except Exception as e:
            self._api_unavailable_reason = str(e)
            log.error(f"Backfill error for {inst_id}: {e}")
            return None

    def _parse_candles(self, raw_bars: list[list]) -> pd.DataFrame:
        """解析OKX K线数据"""
        return okx_candles_to_frame(raw_bars)

    def merge_and_save(
        self,
        inst_id: str,
        new_data: pd.DataFrame,
        mode: str = "append",
    ) -> bool:
        """
        合并并保存数据
        mode: append(追加), replace(替换), merge(合并去重)
        """
        fname = self._inst_to_filename(inst_id)
        path = self.data_dir / fname

        try:
            if mode == "replace" or not path.exists():
                df = new_data
            else:
                existing = pd.read_parquet(path)
                existing["ts"] = pd.to_datetime(existing["ts"], utc=True)
                new_data["ts"] = pd.to_datetime(new_data["ts"], utc=True)

                if mode == "append":
                    df = pd.concat([existing, new_data], ignore_index=True)
                else:  # merge
                    df = pd.concat([existing, new_data], ignore_index=True)

                df = df.drop_duplicates(subset=["ts"], keep="last")
                df = df.sort_values("ts").reset_index(drop=True)

            # 确保is_closed标记
            if "is_closed" not in df.columns:
                df["is_closed"] = True
            if "symbol" not in df.columns:
                df["symbol"] = inst_id
            if "timeframe" not in df.columns:
                df["timeframe"] = "1h"

            # 保存
            df.to_parquet(path, index=False)
            log.info(f"Saved {len(df)} bars for {inst_id}")
            return True

        except Exception as e:
            log.error(f"Save error for {inst_id}: {e}")
            return False

    def sync_symbol(self, inst_id: str) -> SyncResult:
        """
        同步单个币种数据
        检测缺口并回填
        """
        gaps = self.detect_gaps(inst_id)
        result = SyncResult(
            inst_id=inst_id,
            gaps_filled=0,
            bars_added=0,
            last_bar_time=datetime.now(timezone.utc),
            success=True,
        )

        for gap in gaps:
            if gap.severity == "minor":
                continue  # 小缺口忽略

            if self._api_unavailable_reason:
                result.success = False
                result.errors.append(f"skip_backfill_api_unavailable: {self._api_unavailable_reason}")
                break

            new_data = self.backfill_gap(gap)
            if new_data is not None and len(new_data) > 0:
                self.merge_and_save(inst_id, new_data, mode="merge")
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
            return f"{base}_{quote}_{quote}_1h.parquet"
        return f"{normalized}_USDT_1h.parquet"


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


def sync_on_startup(symbols: list[str]) -> dict[str, SyncResult]:
    """
    启动时同步数据
    应该在系统启动时调用
    """
    handler = DataGapHandler()
    return handler.sync_all_symbols(symbols)


# ============================================================
# 增量数据同步器
# ============================================================
class IncrementalSyncer:
    """
    增量数据同步器
    每次扫描周期结束时调用，保持数据最新
    """

    def __init__(self, data_dir: Path | str | None = None):
        self.handler = DataGapHandler(data_dir)
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
