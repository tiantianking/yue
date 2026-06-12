"""Rolling backtest guard for adaptive parameters."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from okx_signal_system.backtest.runner import run_backtest, summarize_trades
from okx_signal_system.data.loader import file_symbol_to_inst_id, load_symbol_file
from okx_signal_system.paths import find_lightweight_history
from okx_signal_system.strategy.trend_breakout import StrategyParams

log = logging.getLogger(__name__)

ROLLING_WINDOW_DAYS = 30
MIN_TRADES_TO_COMPARE = 5
VALIDATION_INTERVAL_DAYS = 7


class RollingBacktestValidator:
    def __init__(self, data_dir: Path | str | None = None):
        if data_dir is None:
            data_dir = find_lightweight_history("okx_1h_extended").parent / "rolling_backtest"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.last_backtest_time: str | None = None
        self.last_current_score = 0.0
        self.last_best_score = 0.0
        self._load_state()

    @property
    def state_file(self) -> Path:
        return self.data_dir / "backtest_state.json"

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.last_backtest_time = state.get("last_backtest_time")
            self.last_current_score = float(state.get("last_current_score", 0.0))
            self.last_best_score = float(state.get("last_best_score", 0.0))
        except Exception as exc:
            log.warning("Failed to load rolling backtest state: %s", exc)

    def _save_state(self) -> None:
        self.state_file.write_text(
            json.dumps(
                {
                    "last_backtest_time": self.last_backtest_time,
                    "last_current_score": self.last_current_score,
                    "last_best_score": self.last_best_score,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def should_run_backtest(self) -> bool:
        if not self.last_backtest_time:
            return True
        try:
            last = pd.Timestamp(self.last_backtest_time)
            if last.tzinfo is None:
                last = last.tz_localize("UTC")
            return (pd.Timestamp.now(tz="UTC") - last).days >= VALIDATION_INTERVAL_DAYS
        except Exception:
            return True

    def run_validation(
        self,
        symbols: list[str],
        current_params: StrategyParams,
        best_params: StrategyParams,
        cost_config=None,
    ) -> dict:
        current_trades = []
        best_trades = []
        data_root = find_lightweight_history("okx_1h_extended")

        for path in sorted(data_root.glob("*_1h.parquet")):
            inst_id = file_symbol_to_inst_id(path)
            if symbols and inst_id not in symbols:
                continue
            try:
                data = load_symbol_file(path)
                frame = data.frame
                if "ts" in frame.columns:
                    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=ROLLING_WINDOW_DAYS)
                    frame = frame[pd.to_datetime(frame["ts"], utc=True) >= cutoff]
                if len(frame) < 100:
                    continue
                current_trades.append(run_backtest(frame, inst_id=inst_id, params=current_params))
                best_trades.append(run_backtest(frame, inst_id=inst_id, params=best_params))
            except Exception as exc:
                log.debug("Rolling backtest skipped %s: %s", path.name, exc)

        current_summary = _summarize_many(current_trades)
        best_summary = _summarize_many(best_trades)
        current_score = _score(current_summary)
        best_score = _score(best_summary)

        recommendation = "keep_current"
        if current_summary["total_trades"] < MIN_TRADES_TO_COMPARE:
            recommendation = "insufficient_data"
        elif best_score > current_score * 1.3:
            recommendation = "switch_to_best"

        self.last_backtest_time = datetime.now(timezone.utc).isoformat()
        self.last_current_score = current_score
        self.last_best_score = best_score
        self._save_state()

        report = {
            "timestamp": self.last_backtest_time,
            "symbols_tested": len(symbols),
            "window_days": ROLLING_WINDOW_DAYS,
            "current_params_result": current_summary,
            "best_params_result": best_summary,
            "current_score": f"{current_score:.3f}",
            "best_score": f"{best_score:.3f}",
            "recommendation": recommendation,
            "current_params": asdict(current_params),
            "best_params": asdict(best_params),
        }
        (self.data_dir / "latest_validation.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report


def _summarize_many(frames: list[pd.DataFrame]) -> dict:
    non_empty = [frame for frame in frames if frame is not None and not frame.empty]
    if not non_empty:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "status": "no_trades",
        }
    trades = pd.concat(non_empty, ignore_index=True)
    return summarize_trades(trades)


def _as_float(value) -> float:
    if isinstance(value, str):
        value = value.rstrip("%")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _score(summary: dict) -> float:
    profit_factor = min(_as_float(summary.get("profit_factor")) / 2.0, 1.0)
    win_rate = _as_float(summary.get("win_rate"))
    if win_rate > 1:
        win_rate = win_rate / 100.0
    total_return = max(-1.0, min(_as_float(summary.get("total_return")) / 1000.0, 1.0))
    return 0.6 * profit_factor + 0.2 * win_rate + 0.2 * total_return
