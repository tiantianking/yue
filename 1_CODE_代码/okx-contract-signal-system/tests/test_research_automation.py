from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def _load_system_check():
    path = ROOT / "scripts" / "system_check.py"
    spec = importlib.util.spec_from_file_location("system_check_automation", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_future_leak_scan_uses_ast_not_candidate_self_report(tmp_path: Path) -> None:
    module = _load_system_check()
    safe = tmp_path / "safe.py"
    safe.write_text("def signal(frame):\n    return frame['close'].shift(1)\n", encoding="utf-8")
    leaking = tmp_path / "leaking.py"
    leaking.write_text(
        "def signal(frame, i):\n"
        "    a = frame['close'].shift(-1)\n"
        "    b = frame['close'].rolling(5, center=True).mean()\n"
        "    return a + b + frame.iloc[i + 1]['close']\n",
        encoding="utf-8",
    )

    parsed, safe_hits = module.scan_future_leaks(safe)
    leak_parsed, leak_hits = module.scan_future_leaks(leaking)

    assert parsed is True and safe_hits == []
    assert leak_parsed is True
    assert any("negative_shift" in hit for hit in leak_hits)
    assert any("centered_rolling_window" in hit for hit in leak_hits)
    assert any("forward_iloc_offset" in hit for hit in leak_hits)


def test_parameter_freedom_is_derived_from_parameter_space() -> None:
    module = _load_system_check()
    candidate = {
        "parameter_space": {
            "all_choices_declared_before_pnl": True,
            "declared_free_parameters": 2,
            "declared_combinations": 6,
            "parameters": [
                {"name": "lookback", "values": [24, 48, 72]},
                {"name": "threshold", "range": {"min": 1.0, "max": 1.5, "step": 0.5}},
                {"name": "fixed_exit", "value": 8, "tuned": False},
            ],
        }
    }

    audit = module.audit_parameter_space(candidate)

    assert audit.bounded is True
    assert audit.free_parameters == 2
    assert audit.combinations == 6

    candidate["parameter_space"]["declared_combinations"] = 5
    assert module.audit_parameter_space(candidate).bounded is False


def test_family_registry_automatically_rejects_duplicate(tmp_path: Path) -> None:
    module = _load_system_check()
    family = {
        "core_signal": "cross_sectional_return_rank",
        "direction": "long_winners_short_losers",
        "holding_period_bars": 96,
        "rebalance_bars": 16,
        "selection": "top_bottom_quantile",
        "universe": "okx_usdt_swap_cross_section",
        "features": ["return", "rank"],
    }
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"families": [{"family_id": "known", "family": family}]}),
        encoding="utf-8",
    )
    candidate_path = tmp_path / "candidate.json"
    candidate = {"candidate_id": "candidate-a", "family": family}
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    results = module.run_family_duplicate_gate(candidate, candidate_path, registry_path=registry)

    dedupe = next(item for item in results if item.name == "automatic_family_deduplication")
    assert dedupe.ok is False
    assert "known" in dedupe.detail


def test_contribution_metrics_detect_few_trade_concentration() -> None:
    module = _load_system_check()
    trades = pd.DataFrame(
        [
            {"inst_id": "BTC-USDT-SWAP", "exit_time": "2026-01-01T00:00:00Z", "net_r": 8.0},
            {"inst_id": "ETH-USDT-SWAP", "exit_time": "2026-02-01T00:00:00Z", "net_r": 1.0},
            {"inst_id": "SOL-USDT-SWAP", "exit_time": "2026-03-01T00:00:00Z", "net_r": 1.0},
        ]
    )

    metrics = module.contribution_metrics(trades)

    assert metrics["single_trade_share"] == 0.8
    assert metrics["top_three_trade_share"] == 1.0
    assert metrics["effective_positive_trades"] < 2.0


def _trade_rows(count: int = 80) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        winner = index % 4 != 0
        exit_price = 120.0 if winner else 90.0
        gross_pnl = 20.0 if winner else -10.0
        rows.append(
            {
                "inst_id": f"COIN{index % 20}-USDT-SWAP",
                "entry_time": f"2026-01-{1 + index % 20:02d}T00:00:00Z",
                "exit_time": f"2026-01-{1 + index % 20:02d}T08:00:00Z",
                "side": "long",
                "entry_price": 100.0,
                "exit_price": exit_price,
                "qty": 1.0,
                "gross_pnl": gross_pnl,
                "costs": 0.0,
                "net_pnl": gross_pnl,
                "risk_amount": 10.0,
                "net_r": gross_pnl / 10.0,
                "final_net_r": gross_pnl / 10.0,
                "leverage_used": 1.0,
                "market_regime": "test",
            }
        )
    return rows


def test_cost_stress_is_generated_from_trade_facts(tmp_path: Path) -> None:
    module = _load_system_check()
    pd.DataFrame(_trade_rows()).to_csv(tmp_path / "sample_trades.csv", index=False)

    results, stress = module.execute_cost_stress(tmp_path)

    assert (tmp_path / "cost_stress.csv").is_file()
    assert stress["scenario"].tolist() == ["baseline", "stress_1_5x", "stress_2x"]
    assert stress["recompute_source"].eq("trade_fact_recompute").all()
    assert next(item for item in results if item.name == "cost_stress_execution").ok is True


def test_data_readiness_requires_increment_after_mark(tmp_path: Path, monkeypatch) -> None:
    module = _load_system_check()
    symbols = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    monkeypatch.setattr(module, "configured_symbols", lambda: symbols)
    root = tmp_path / "dataset"
    root.mkdir()
    timestamps = pd.date_range("2026-01-01", periods=3 * 24 * 4, freq="15min", tz="UTC")
    for symbol in symbols:
        pd.DataFrame({"ts": timestamps, "is_closed": True}).to_parquet(
            root / module._runtime_filename(symbol, "15m"),
            index=False,
        )
    state = tmp_path / "state.json"

    initial = module.evaluate_data_readiness(
        dataset="test",
        timeframe="15m",
        state_file=state,
        data_root=root,
        min_symbols=2,
        min_history_days=2,
        min_new_days=1,
        max_gap_ratio=0.0,
        coverage_ratio=1.0,
    )
    assert initial.ready is True
    module.mark_research_data_state(initial, state)

    repeated = module.evaluate_data_readiness(
        dataset="test",
        timeframe="15m",
        state_file=state,
        data_root=root,
        min_symbols=2,
        min_history_days=2,
        min_new_days=1,
        max_gap_ratio=0.0,
        coverage_ratio=1.0,
    )
    assert repeated.initial_research is False
    assert repeated.ready is False
    assert repeated.new_data_qualified_symbols == 0


def test_data_readiness_respects_candidate_symbol_subset(tmp_path: Path, monkeypatch) -> None:
    module = _load_system_check()
    all_symbols = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "HYPE-USDT-SWAP"]
    monkeypatch.setattr(module, "configured_symbols", lambda: all_symbols)
    root = tmp_path / "dataset"
    root.mkdir()
    full = pd.date_range("2023-01-01", periods=370 * 24 * 4, freq="15min", tz="UTC")
    short = pd.date_range("2026-01-01", periods=30 * 24 * 4, freq="15min", tz="UTC")
    for symbol, timestamps in {
        "BTC-USDT-SWAP": full,
        "ETH-USDT-SWAP": full,
        "HYPE-USDT-SWAP": short,
    }.items():
        pd.DataFrame({"ts": timestamps, "is_closed": True}).to_parquet(
            root / module._runtime_filename(symbol, "15m"),
            index=False,
        )

    readiness = module.evaluate_data_readiness(
        dataset="test",
        timeframe="15m",
        state_file=tmp_path / "state.json",
        data_root=root,
        symbols=["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        min_symbols=2,
        min_history_days=365,
        min_new_days=1,
        max_gap_ratio=0.0,
        coverage_ratio=1.0,
    )

    assert readiness.ready is True
    assert readiness.symbol_count == 2
    assert {row["symbol"] for row in readiness.rows} == {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}


def test_failed_research_archive_is_idempotent(tmp_path: Path) -> None:
    module = _load_system_check()
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({"candidate_id": "failed-a"}), encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    pd.DataFrame([{"net_r": -1.0}]).to_csv(artifacts / "sample_trades.csv", index=False)
    (artifacts / "robustness_screen.json").write_text(
        json.dumps({"passed": False, "decision": "FAIL_STOP_NO_RESCUE"}),
        encoding="utf-8",
    )
    failure = module.CheckResult("research", "automatic_future_leak_scan", False, "line=1")
    archive_root = tmp_path / "archive"

    first = module.archive_failed_research(candidate, artifacts, archive_root, [failure])
    second = module.archive_failed_research(candidate, artifacts, archive_root, [failure])

    assert first == second
    assert (first / "failure_summary.json").is_file()
    assert (first / "失败说明.md").is_file()
    assert (first / "sample_trades.csv").is_file()
    assert (first / "robustness_screen.json").is_file()
    summary = json.loads((first / "failure_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "REJECT_AND_ARCHIVE_NO_RESCUE"
    assert len(summary["failure_hash"]) == 64
