from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def _write_json_with_hash(path: Path, payload: dict) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def test_future_leak_scan_blocks_indirect_shift_future_fill_and_forward_join(tmp_path: Path) -> None:
    module = _load_system_check()
    leaking = tmp_path / "indirect_leaking.py"
    leaking.write_text(
        "import pandas as pd\n"
        "PERIODS = -1\n"
        "def signal(left, right):\n"
        "    shifted = left['close'].shift(PERIODS)\n"
        "    filled = left['close'].bfill()\n"
        "    joined = pd.merge_asof(left, right, on='ts', direction='nearest')\n"
        "    return shifted + filled + joined['value']\n",
        encoding="utf-8",
    )

    parsed, hits = module.scan_future_leaks(leaking)

    assert parsed is True
    assert any("negative_shift" in hit for hit in hits)
    assert any("future_fill:bfill" in hit for hit in hits)
    assert any("merge_asof_future_direction:nearest" in hit for hit in hits)


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


def test_registered_alias_rejects_relabelled_historical_candidate(tmp_path: Path) -> None:
    module = _load_system_check()
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "families": [
                    {
                        "family_id": "MC02_DOWNSIDE_BETA_ASYMMETRY_PREMIUM",
                        "aliases": ["H28_42D_DOWNSIDE_MARKET_BETA_ASYMMETRY_PREMIUM_V1"],
                        "family": {
                            "core_signal": "downside_beta",
                            "direction": "long_high_bad_beta",
                            "selection": "weekly_extremes",
                            "universe": "okx_swaps",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    family = {
        "core_signal": "renamed_downside_covariance_score",
        "direction": "long_high_score_short_low_score",
        "holding_period_bars": 168,
        "selection": "weekly_cross_sectional_extremes",
        "universe": "okx_usdt_swap_cross_section",
    }
    candidate = {
        "candidate_id": "H28_42D_DOWNSIDE_MARKET_BETA_ASYMMETRY_PREMIUM_V1",
        "family": family,
    }
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    results = module.run_family_duplicate_gate(candidate, candidate_path, registry_path=registry)

    alias_gate = next(item for item in results if item.name == "registered_family_alias_deduplication")
    assert alias_gate.ok is False
    assert "MC02_DOWNSIDE_BETA_ASYMMETRY_PREMIUM" in alias_gate.detail


def test_h22_registry_keeps_fixed_21_candidate_pending_forward_validation() -> None:
    registry = json.loads(
        (ROOT / "config" / "research_family_registry.json").read_text(encoding="utf-8")
    )
    h22 = next(
        item
        for item in registry["families"]
        if item["family_id"] == "MOMENTUM_14D_STAGGERED_3X3_REFRESH_HYSTERESIS6_V1"
    )

    assert h22["status"] == "fixed_21_scope_candidate_forward_validation_pending"
    assert "operator-frozen 21 mature OKX USDT swaps" in h22["warning"]
    assert "not an independent Alpha" in h22["warning"]
    assert "portability warning" in h22["scope_limit"]
    assert "base PF 0.9247" in h22["out_of_scope_stress_result"]


def test_v357_registry_keeps_fixed_21_candidate_pending_forward_validation() -> None:
    registry = json.loads(
        (ROOT / "config" / "research_family_registry.json").read_text(encoding="utf-8")
    )
    v357 = next(
        item
        for item in registry["families"]
        if item["family_id"] == "4h_donchian_volatility_compression"
    )

    policy = json.loads(
        (ROOT / "config" / "research_universe_policy.json").read_text(encoding="utf-8")
    )
    assert v357["status"] == policy["candidate_definitions"]["V357"]["status"]
    assert v357["status"] == "fixed_21_scope_candidate_forward_validation_pending"
    assert "v357-shadow-donchian-slow-plus-vcb-a" in v357["aliases"]
    assert policy["dynamic_universe_evidence"]["classification"] == (
        "out_of_scope_generalization_stress_test"
    )
    assert policy["dynamic_universe_evidence"]["v357"]["base_profit_factor"] == 1.0552
    assert policy["dynamic_universe_evidence"]["v357"]["stress_profit_factor"] == 0.9386
    assert "broad-market generalization stress test" in v357["scope_limit"]


def test_allowed_21_pool_policy_matches_runtime_symbols_and_allows_candidate_subsets() -> None:
    policy = json.loads(
        (ROOT / "config" / "research_universe_policy.json").read_text(encoding="utf-8")
    )
    base_lines = (ROOT / "config" / "base.yaml").read_text(encoding="utf-8").splitlines()
    start = base_lines.index("  symbols:") + 1
    runtime_symbols: list[str] = []
    for line in base_lines[start:]:
        if line.startswith("    - "):
            runtime_symbols.append(line.removeprefix("    - ").strip())
            continue
        if line and not line.startswith("    "):
            break

    assert policy["schema"] == "okx_research_universe_policy_v2"
    assert policy["universe_mode"] == (
        "operator_selected_21_allowed_pool_candidate_specific_subset"
    )
    assert len(policy["symbols"]) == 21
    assert policy["symbols"] == runtime_symbols
    subset_policy = policy["new_candidate_subset_policy"]
    assert subset_policy["minimum_symbols"] == 1
    assert subset_policy["maximum_symbols"] == 21
    assert subset_policy["single_symbol_strategy_allowed"] is True
    assert subset_policy["minimum_breadth_required_for_profitability"] is False
    assert subset_policy["explicit_symbol_list_required_before_pnl"] is True
    assert subset_policy["outcome_based_symbol_selection_forbidden"] is True
    assert policy["candidate_definitions"]["H22"]["status"] == (
        "fixed_21_scope_candidate_forward_validation_pending"
    )
    assert policy["candidate_definitions"]["V357"]["status"] == (
        "fixed_21_scope_candidate_forward_validation_pending"
    )
    assert policy["candidate_definitions"]["H27"]["status"] == (
        "record_only_forward_diversification_observation"
    )
    assert policy["runtime_boundary"].endswith("SIGNAL_ONLY.")


def test_single_symbol_candidate_subset_gate_is_allowed_before_pnl(tmp_path: Path) -> None:
    module = _load_system_check()
    code_path = tmp_path / "single_asset_signal.py"
    code_path.write_text(
        "def signal(frame):\n    return frame['close'].shift(1)\n",
        encoding="utf-8",
    )
    candidate_id = "TEST_BTC_SINGLE_ASSET_MECHANISM"
    holdout_path = tmp_path / "historical_holdout_manifest.json"
    holdout_hash = _write_json_with_hash(
        holdout_path,
        {
            "schema": "okx_historical_holdout_manifest_v1",
            "candidate_id": candidate_id,
            "locked_before_pnl": True,
            "opened_count": 0,
            "months": 8,
            "start_utc": "2025-11-01T00:00:00Z",
            "end_utc": "2026-07-01T00:00:00Z",
            "data_snapshot_sha256": "a" * 64,
            "split_sha256": "b" * 64,
        },
    )
    trial_path = tmp_path / "family_trial_ledger.json"
    trial_hash = _write_json_with_hash(
        trial_path,
        {
            "schema": "okx_family_trial_ledger_v1",
            "complete": True,
            "trials": [{"candidate_id": candidate_id, "registered_before_pnl": True}],
        },
    )
    point_path = tmp_path / "point_in_time_evidence.json"
    point_hash = _write_json_with_hash(
        point_path,
        {
            "schema": "okx_point_in_time_evidence_v1",
            "complete": True,
            "fields": [
                {
                    "name": "test_btc_flow",
                    "available_at_rule": "Use only records published before the closed signal bar.",
                    "revision_policy": "frozen_snapshot",
                }
            ],
        },
    )
    dependency_path = tmp_path / "code_dependency_manifest.json"
    dependency_hash = _write_json_with_hash(
        dependency_path,
        {
            "schema": "okx_code_dependency_manifest_v1",
            "complete": True,
            "files": [str(code_path)],
        },
    )
    candidate = {
        "schema": "okx_pre_pnl_candidate_v2",
        "candidate_id": candidate_id,
        "mechanism": {
            "payer": "Predeclared forced BTC seller",
            "direction": "Long BTC after the forced flow completes",
            "observable_proxy": "Closed point-in-time BTC field",
            "persistence_reason": "Execution lag and inventory transfer",
        },
        "family": {
            "core_signal": "test_unique_btc_flow_completion",
            "direction": "long_after_completion",
            "holding_period_bars": 8,
            "rebalance_bars": 8,
            "selection": "single_asset_threshold",
            "universe": "operator_allowed_pool_predeclared_subset",
            "features": ["test_btc_flow"],
        },
        "universe_selection": {
            "selection_locked_before_pnl": True,
            "outcome_based_selection": False,
            "selection_basis": "The mechanism and raw field are intrinsically BTC-specific.",
            "legacy_outcomes_used_to_choose_subset": False,
        },
        "data": {
            "exchange": "OKX",
            "cross_exchange": False,
            "local_only": True,
            "closed_only": True,
            "symbols": ["BTC-USDT-SWAP"],
            "fields": ["test_btc_flow"],
            "start_utc": "2023-07-01T00:00:00Z",
            "end_utc": "2026-07-01T00:00:00Z",
        },
        "leakage": {
            "future_returns_opened": False,
            "pnl_opened": False,
            "entry_uses_next_tradable_price": True,
        },
        "historical_holdout": {
            "locked_before_pnl": True,
            "rules_frozen_before_holdout": True,
            "months": 8,
            "start_utc": "2025-11-01T00:00:00Z",
            "end_utc": "2026-07-01T00:00:00Z",
            "opened_count": 0,
            "opened_at_utc": None,
            "data_snapshot_sha256": "a" * 64,
            "split_sha256": "b" * 64,
            "split_manifest_file": str(holdout_path),
            "split_manifest_sha256": holdout_hash,
        },
        "trial_ledger": {
            "registered_before_pnl": True,
            "all_family_trials_recorded": True,
            "family_trial_count": 1,
            "file": str(trial_path),
            "sha256": trial_hash,
        },
        "point_in_time": {
            "all_fields_have_available_at": True,
            "revisions_frozen_or_versioned": True,
            "no_current_metadata_backfill": True,
            "signal_after_data_available": True,
            "execution_after_signal": True,
            "evidence_file": str(point_path),
            "evidence_sha256": point_hash,
        },
        "outcome_horizon": {
            "locked_before_pnl": True,
            "max_holding_bars": 8,
            "label_horizon_bars": 8,
            "purge_bars": 8,
            "embargo_bars": 1,
        },
        "code_dependency_manifest": {
            "complete": True,
            "file": str(dependency_path),
            "sha256": dependency_hash,
        },
        "parameter_space": {
            "all_choices_declared_before_pnl": True,
            "declared_free_parameters": 0,
            "declared_combinations": 1,
            "parameters": [],
        },
        "robustness_protocol": {
            "schema": "okx_robustness_screen_protocol_v1",
            "random_time_trials": 500,
            "random_time_alpha": 0.05,
            "entry_delay_bars": 1,
            "minimum_neighbor_variants": 3,
            "minimum_positive_neighbor_ratio": 2 / 3,
            "portfolio_increment_required": True,
            "locked_before_pnl": True,
            "evidence_files": [
                "falsification_trials.csv",
                "parameter_neighborhood.csv",
                "portfolio_increment.csv",
            ],
        },
        "representation_invariance_passed": True,
        "measurement_semantics_passed": True,
        "code_files": [str(code_path)],
    }
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    results = module.run_candidate_gate(candidate_path)
    by_name = {item.name: item for item in results}

    assert by_name["candidate_symbol_subset_explicit"].ok is True
    assert by_name["candidate_symbol_subset_unique"].ok is True
    assert by_name["candidate_symbol_subset_within_allowed_pool"].ok is True
    assert by_name["candidate_symbol_subset_size_allowed"].ok is True
    assert by_name["candidate_symbol_selection_locked_before_pnl"].ok is True
    assert by_name["candidate_symbol_selection_basis_present"].ok is True
    assert by_name["historical_holdout_precommitted_unopened"].ok is True
    assert by_name["historical_holdout_window_6_to_10_months"].ok is True
    assert by_name["historical_holdout_manifest_hash_verified"].ok is True
    assert by_name["historical_holdout_manifest_content_verified"].ok is True
    assert by_name["family_trial_ledger_hash_verified"].ok is True
    assert by_name["family_trial_ledger_content_verified"].ok is True
    assert by_name["point_in_time_evidence_hash_verified"].ok is True
    assert by_name["point_in_time_evidence_content_verified"].ok is True
    assert by_name["purge_covers_complete_outcome_horizon"].ok is True
    assert by_name["code_dependency_manifest_hash_verified"].ok is True
    assert by_name["code_dependency_manifest_content_verified"].ok is True


def test_candidate_gate_blocks_short_purge_and_reopened_holdout(tmp_path: Path) -> None:
    module = _load_system_check()
    code_path = tmp_path / "signal.py"
    code_path.write_text("def signal(frame):\n    return frame['close'].shift(1)\n", encoding="utf-8")
    candidate = {
        "schema": "okx_pre_pnl_candidate_v2",
        "candidate_id": "BLOCK_BAD_INTEGRITY",
        "mechanism": {"payer": "x", "direction": "long", "observable_proxy": "field"},
        "family": {
            "core_signal": "unique_test",
            "direction": "long",
            "holding_period_bars": 12,
            "rebalance_bars": 12,
            "selection": "fixed",
            "universe": "fixed",
            "features": ["field"],
        },
        "universe_selection": {
            "selection_locked_before_pnl": True,
            "outcome_based_selection": False,
            "legacy_outcomes_used_to_choose_subset": False,
            "selection_basis": "intrinsic",
        },
        "data": {
            "exchange": "OKX",
            "cross_exchange": False,
            "local_only": True,
            "closed_only": True,
            "symbols": ["BTC-USDT-SWAP"],
            "fields": ["field"],
            "start_utc": "2023-07-01T00:00:00Z",
            "end_utc": "2026-07-01T00:00:00Z",
        },
        "leakage": {"future_returns_opened": False, "pnl_opened": False, "entry_uses_next_tradable_price": True},
        "historical_holdout": {
            "locked_before_pnl": True,
            "rules_frozen_before_holdout": True,
            "months": 8,
            "start_utc": "2025-11-01T00:00:00Z",
            "end_utc": "2026-07-01T00:00:00Z",
            "opened_count": 1,
            "opened_at_utc": "2026-06-01T00:00:00Z",
        },
        "outcome_horizon": {
            "locked_before_pnl": True,
            "max_holding_bars": 12,
            "label_horizon_bars": 12,
            "purge_bars": 2,
            "embargo_bars": 1,
        },
        "parameter_space": {
            "all_choices_declared_before_pnl": True,
            "declared_free_parameters": 0,
            "declared_combinations": 1,
            "parameters": [],
        },
        "robustness_protocol": {
            "schema": "okx_robustness_screen_protocol_v1",
            "random_time_trials": 500,
            "random_time_alpha": 0.05,
            "entry_delay_bars": 1,
            "minimum_neighbor_variants": 3,
            "minimum_positive_neighbor_ratio": 2 / 3,
            "portfolio_increment_required": True,
            "locked_before_pnl": True,
            "evidence_files": ["falsification_trials.csv", "parameter_neighborhood.csv", "portfolio_increment.csv"],
        },
        "representation_invariance_passed": True,
        "measurement_semantics_passed": True,
        "code_files": [str(code_path)],
    }
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    by_name = {item.name: item for item in module.run_candidate_gate(candidate_path)}

    assert by_name["historical_holdout_precommitted_unopened"].ok is False
    assert by_name["purge_covers_complete_outcome_horizon"].ok is False


def test_small_frozen_subset_does_not_fail_cross_symbol_contribution_gate() -> None:
    module = _load_system_check()

    one_ok, one_detail = module.evaluate_symbol_contribution_gate(
        declared_symbol_count=1,
        single_symbol_share=1.0,
    )
    five_ok, _ = module.evaluate_symbol_contribution_gate(
        declared_symbol_count=5,
        single_symbol_share=0.8,
    )
    six_ok, six_detail = module.evaluate_symbol_contribution_gate(
        declared_symbol_count=6,
        single_symbol_share=0.8,
    )

    assert one_ok is True
    assert "not_applicable=frozen_subset_size_1" in one_detail
    assert five_ok is True
    assert six_ok is False
    assert "max=0.25" in six_detail


def test_failure_fingerprint_rejects_option_surface_relabel(tmp_path: Path) -> None:
    module = _load_system_check()
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "families": [],
                "failure_fingerprints": [
                    {
                        "fingerprint_id": "FP15_OPTION_SURFACE_DIRECTION",
                        "family_key": "option_surface_direction",
                        "tags": [
                            "options",
                            "implied_volatility",
                            "skew",
                            "term_structure",
                            "gamma",
                            "dealer_hedging",
                            "surface",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    family = {
        "core_signal": "transaction_implied_volatility_skew_from_options_surface",
        "direction": "short_underlying_when_put_skew_steepens",
        "holding_period_bars": 16,
        "selection": "market_level_skew_extreme",
        "universe": "btc_eth_options_to_okx_perpetual",
        "features": ["options", "implied_volatility", "skew", "surface"],
    }
    candidate = {"candidate_id": "renamed-option-skew", "family": family}
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    results = module.run_family_duplicate_gate(candidate, candidate_path, registry_path=registry)

    fingerprint_gate = next(item for item in results if item.name == "failure_fingerprint_deduplication")
    assert fingerprint_gate.ok is False
    assert "FP15_OPTION_SURFACE_DIRECTION" in fingerprint_gate.detail
    assert "implied_volatility" in fingerprint_gate.detail


def test_failure_fingerprint_allows_distinct_mechanism(tmp_path: Path) -> None:
    module = _load_system_check()
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "families": [],
                "failure_fingerprints": [
                    {
                        "fingerprint_id": "FP11_FUNDING_CARRY_CROWDING",
                        "family_key": "funding_carry",
                        "tags": ["funding", "carry", "persistence", "crowding", "settlement"],
                    },
                    {
                        "fingerprint_id": "FP16_CALENDAR_INTRADAY",
                        "family_key": "calendar_intraday",
                        "tags": ["calendar", "utc", "hour", "weekday", "seasonality", "same_hour"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    family = {
        "core_signal": "validator_exit_queue_acceleration",
        "direction": "long_queue_relief_short_queue_stress",
        "holding_period_bars": 168,
        "selection": "fixed_threshold_event",
        "universe": "staking_assets",
        "features": ["validator_queue", "staking_withdrawal"],
    }
    candidate = {"candidate_id": "distinct-mechanism", "family": family}
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    results = module.run_family_duplicate_gate(candidate, candidate_path, registry_path=registry)

    fingerprint_gate = next(item for item in results if item.name == "failure_fingerprint_deduplication")
    assert fingerprint_gate.ok is True
    assert fingerprint_gate.detail == "none"


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
