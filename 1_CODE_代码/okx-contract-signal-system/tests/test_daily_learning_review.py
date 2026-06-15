import json
from datetime import datetime, timezone

import pandas as pd

from okx_signal_system.data.loader import SymbolData
from okx_signal_system.strategy.trend_breakout import StrategyParams
from okx_signal_system.training import daily_learning
from okx_signal_system.training.daily_learning import (
    LearningReviewConfig,
    evaluate_candidate_gates,
    local_candidate_grid,
    run_daily_learning_review,
    should_run_daily_review,
)


def _summary(*, pf: float, trades: int, ret: float = 0.05, drawdown: float = 0.04) -> dict:
    return {
        "profit_factor": pf,
        "total_trades": trades,
        "total_return": ret,
        "max_drawdown": drawdown,
        "near_liq_trades": 0,
        "hit_27pct_stop": 0,
    }


def _gate_config() -> LearningReviewConfig:
    return LearningReviewConfig(
        min_validation_trades=5,
        min_validation_profit_factor=1.05,
        min_profit_factor_delta=0.05,
        min_profit_factor_ratio=1.05,
        max_validation_drawdown=0.20,
        max_drawdown_worsening=0.02,
        min_profitable_symbol_ratio=0.0,
        shadow_min_closed_signals=0,
    )


def test_should_run_daily_review_respects_interval(tmp_path) -> None:
    path = tmp_path / "daily_learning_review.json"
    assert should_run_daily_review(path, interval_hours=24, now=datetime(2026, 6, 13, tzinfo=timezone.utc))

    path.write_text(json.dumps({"generated_at": "2026-06-13T00:00:00+00:00"}), encoding="utf-8")

    assert not should_run_daily_review(path, interval_hours=24, now=datetime(2026, 6, 13, 12, tzinfo=timezone.utc))
    assert should_run_daily_review(path, interval_hours=24, now=datetime(2026, 6, 14, 1, tzinfo=timezone.utc))


def test_candidate_gate_blocks_anti_future_failure() -> None:
    result = evaluate_candidate_gates(
        current_train_summary=_summary(pf=1.2, trades=10),
        current_valid_summary=_summary(pf=1.2, trades=10),
        candidate_train_summary=_summary(pf=1.8, trades=10),
        candidate_valid_summary=_summary(pf=1.5, trades=10),
        current_params=StrategyParams(),
        candidate_params=StrategyParams(fast_ema=132),
        anti_future_checks={"prior_breakout_excludes_current_bar": False},
        frame_checks={"closed_bars_only": True},
        shadow_summary={"closed": 0, "avg_quality_score": 0},
        candidate_symbol_results=[{"status": "evaluated", "valid_total_return": 0.02}],
        config=_gate_config(),
    )

    assert not result["passed"]
    assert "anti_future_check_failed" in result["reasons"]


def test_candidate_gate_blocks_small_pf_improvement() -> None:
    result = evaluate_candidate_gates(
        current_train_summary=_summary(pf=1.2, trades=10),
        current_valid_summary=_summary(pf=1.2, trades=10),
        candidate_train_summary=_summary(pf=1.25, trades=10),
        candidate_valid_summary=_summary(pf=1.21, trades=10),
        current_params=StrategyParams(),
        candidate_params=StrategyParams(fast_ema=132),
        anti_future_checks={"prior_breakout_excludes_current_bar": True},
        frame_checks={"closed_bars_only": True},
        shadow_summary={"closed": 0, "avg_quality_score": 0},
        candidate_symbol_results=[{"status": "evaluated", "valid_total_return": 0.02}],
        config=_gate_config(),
    )

    assert not result["passed"]
    assert "candidate_profit_factor_improvement_too_small" in result["reasons"]


def test_candidate_gate_allows_better_stable_candidate() -> None:
    result = evaluate_candidate_gates(
        current_train_summary=_summary(pf=1.2, trades=20),
        current_valid_summary=_summary(pf=1.2, trades=20, drawdown=0.05),
        candidate_train_summary=_summary(pf=1.6, trades=25),
        candidate_valid_summary=_summary(pf=1.4, trades=25, drawdown=0.05),
        current_params=StrategyParams(),
        candidate_params=StrategyParams(fast_ema=132),
        anti_future_checks={"prior_breakout_excludes_current_bar": True},
        frame_checks={"closed_bars_only": True, "train_valid_order_ok": True},
        shadow_summary={"closed": 0, "avg_quality_score": 0},
        candidate_symbol_results=[{"status": "evaluated", "valid_total_return": 0.02}],
        config=_gate_config(),
    )

    assert result["passed"]
    assert result["reasons"] == []


def test_local_candidate_grid_is_bounded_and_keeps_current() -> None:
    current = StrategyParams()
    grid = local_candidate_grid(current, signal_timeframe="15m", max_candidates=8)
    assert len(grid) <= 8
    assert current in grid
    assert all(item.take_profit_mult >= 3.5 for item in grid)


def test_learning_review_config_reads_runtime_timeout() -> None:
    cfg = LearningReviewConfig.from_mapping({"max_runtime_seconds": 120})

    assert cfg.max_runtime_seconds == 120


def test_daily_learning_passes_vote_gate_to_backtest(tmp_path, monkeypatch) -> None:
    ts = pd.date_range("2026-01-01", periods=320, freq="15min", tz="UTC")
    frame = pd.DataFrame(
        {
            "ts": ts,
            "open": [100.0] * len(ts),
            "high": [101.0] * len(ts),
            "low": [99.0] * len(ts),
            "close": [100.5] * len(ts),
            "volume": [1000.0] * len(ts),
            "symbol": ["BTC-USDT-SWAP"] * len(ts),
            "timeframe": ["15m"] * len(ts),
            "is_closed": [True] * len(ts),
        }
    )
    monkeypatch.setattr(
        daily_learning,
        "load_all_symbols",
        lambda dataset: [SymbolData("BTC-USDT-SWAP", tmp_path / "BTC.parquet", frame)],
    )
    (tmp_path / "selected_params.json").write_text(
        json.dumps(
            {
                "fast_ema": 5,
                "slow_ema": 10,
                "breakout_window": 5,
                "atr_stop_mult": 1.5,
                "take_profit_mult": 3.5,
                "max_hold_bars": 12,
                "atr_window": 5,
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_backtest(*args, **kwargs):
        calls.append(kwargs.get("min_vote_approval_rate"))
        return pd.DataFrame()

    monkeypatch.setattr(daily_learning, "run_backtest", fake_backtest)
    run_daily_learning_review(
        symbols=["BTC-USDT-SWAP"],
        dataset="unit",
        signal_timeframe="15m",
        trend_timeframe="1h",
        output_dir=tmp_path,
        history_tail=320,
        run_candidate_search=False,
        config=LearningReviewConfig(
            min_validation_trades=0,
            min_validation_profit_factor=0.0,
            min_profit_factor_delta=0.0,
            min_profit_factor_ratio=1.0,
            min_profitable_symbol_ratio=0.0,
            shadow_min_closed_signals=0,
        ),
    )

    assert calls
    assert set(calls) == {0.40}


def test_daily_learning_review_writes_report_with_closed_frames(tmp_path, monkeypatch) -> None:
    ts = pd.date_range("2026-01-01", periods=320, freq="15min", tz="UTC")
    frame = pd.DataFrame(
        {
            "ts": ts,
            "open": [100 + idx * 0.01 for idx in range(len(ts))],
            "high": [101 + idx * 0.01 for idx in range(len(ts))],
            "low": [99 + idx * 0.01 for idx in range(len(ts))],
            "close": [100.5 + idx * 0.01 for idx in range(len(ts))],
            "volume": [1000.0] * len(ts),
            "symbol": ["BTC-USDT-SWAP"] * len(ts),
            "timeframe": ["15m"] * len(ts),
            "is_closed": [True] * len(ts),
        }
    )
    monkeypatch.setattr(
        daily_learning,
        "load_all_symbols",
        lambda dataset: [SymbolData("BTC-USDT-SWAP", tmp_path / "BTC.parquet", frame)],
    )
    (tmp_path / "selected_params.json").write_text(
        json.dumps(
            {
                "fast_ema": 5,
                "slow_ema": 10,
                "breakout_window": 5,
                "atr_stop_mult": 1.5,
                "take_profit_mult": 3.5,
                "max_hold_bars": 12,
                "atr_window": 5,
            }
        ),
        encoding="utf-8",
    )

    report = run_daily_learning_review(
        symbols=["BTC-USDT-SWAP"],
        dataset="unit",
        signal_timeframe="15m",
        trend_timeframe="1h",
        output_dir=tmp_path,
        history_tail=320,
        run_candidate_search=False,
        config=LearningReviewConfig(
            min_validation_trades=0,
            min_validation_profit_factor=0.0,
            min_profit_factor_delta=0.0,
            min_profit_factor_ratio=1.0,
            min_profitable_symbol_ratio=0.0,
            shadow_min_closed_signals=0,
        ),
    )

    assert report.symbols_checked == 1
    assert report.frame_checks["closed_bars_only"]
    assert (tmp_path / "daily_learning_review.json").exists()
    assert (tmp_path / "candidate_params.json").exists()
