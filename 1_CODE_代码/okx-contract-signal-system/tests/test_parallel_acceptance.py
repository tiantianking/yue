from __future__ import annotations

import json
from pathlib import Path

import pytest

from okx_signal_system.research.parallel_acceptance import (
    AcceptanceTrackConfig,
    ForwardSampleProfile,
    ParallelAcceptanceConfig,
    archive_failed_track,
    build_parallel_acceptance_status,
    evaluate_track_admission,
    evaluate_variant,
    format_research_shadow_message,
    _load_frozen_early_stop_protocol,
)


def _config(tmp_path: Path) -> ParallelAcceptanceConfig:
    return ParallelAcceptanceConfig(
        enabled=True,
        source_status=tmp_path / "source.json",
        source_ledger=tmp_path / "ledger.json",
        output_status=tmp_path / "out.json",
        notification_state=tmp_path / "notify.json",
        notification_enabled=True,
        day14_min_rebalances=10,
        day30_min_rebalances=20,
        day30_min_closed_per_variant=20,
        day30_base_pf_catastrophic_below=0.70,
        day30_stress_pf_catastrophic_below=0.60,
        day30_base_return_catastrophic_below=-0.08,
        day30_stress_return_catastrophic_below=-0.10,
        day45_min_rebalances=35,
        day45_min_closed_per_variant=35,
        day45_symbol_concentration_catastrophic_above=0.60,
        day45_top5_concentration_catastrophic_above=0.85,
    )


def _variant_summary(
    *,
    closed: int = 1,
    base_pf: float | None = 1.2,
    stress_pf: float | None = 1.1,
    base_return: float = 0.02,
    stress_return: float = 0.01,
    symbol_share: float = 0.20,
    top5_share: float = 0.40,
) -> dict:
    return {
        "closed_count": closed,
        "base": {
            "profit_factor": base_pf,
            "profit_factor_infinite": False,
            "total_return": base_return,
        },
        "stress": {
            "profit_factor": stress_pf,
            "profit_factor_infinite": False,
            "total_return": stress_return,
        },
        "single_symbol_positive_contribution_share": symbol_share,
        "top_5_profitable_rebalances_positive_contribution_share": top5_share,
    }


def _status(*, days: int = 1, rebalances: int = 2, summary: dict | None = None, fixed_pass=None) -> dict:
    variant_summary = summary or _variant_summary()
    fixed_status = "PASS" if fixed_pass is True else "FAIL" if fixed_pass is False else "NOT_EVALUATED_SAMPLE_INCOMPLETE"
    return {
        "acceptance_protocol_id": "P1",
        "candidate_protocol_id": "C1",
        "elapsed_closed_data_days": days,
        "fully_prospective_rebalance_count": rebalances,
        "closed_data_through_utc": "2026-06-25T04:45:00Z",
        "protocol_integrity": "PASS",
        "ledger_integrity": "PASS",
        "data_quality_integrity": "PASS",
        "daily_snapshot_chain_integrity": "PASS",
        "variants": {
            "original": dict(variant_summary),
            "hysteresis_4_in_6_out": dict(variant_summary),
        },
        "variant_fixed_gate_results": {
            "original": {"status": fixed_status, "all_pass": fixed_pass},
            "hysteresis_4_in_6_out": {"status": fixed_status, "all_pass": fixed_pass},
        },
    }


def test_early_sample_remains_research_shadow(tmp_path: Path) -> None:
    result = evaluate_variant(_status(), "original", _config(tmp_path))
    assert result["stage"] == "RESEARCH_SHADOW"
    assert result["formal_a"] is False
    assert result["automatic_ordering"] is False


def test_integrity_failure_blocks_evidence_instead_of_promoting(tmp_path: Path) -> None:
    status = _status(days=90, rebalances=90, fixed_pass=True)
    status["ledger_integrity"] = "FAIL"
    result = evaluate_variant(status, "original", _config(tmp_path))
    assert result["stage"] == "EVIDENCE_BLOCKED"
    assert result["decision"] == "FAIL_CLOSED_EVIDENCE_CHAIN"


def test_day30_only_terminates_joint_catastrophic_failure(tmp_path: Path) -> None:
    summary = _variant_summary(
        closed=30,
        base_pf=0.50,
        stress_pf=0.40,
        base_return=-0.12,
        stress_return=-0.15,
    )
    result = evaluate_variant(
        _status(days=30, rebalances=30, summary=summary),
        "original",
        _config(tmp_path),
    )
    assert result["stage"] == "FAILED_ARCHIVE"
    assert result["decision"] == "EARLY_TERMINATE_FROZEN_DAY30_RULE"


def test_day30_does_not_kill_candidate_for_pf_alone(tmp_path: Path) -> None:
    summary = _variant_summary(
        closed=30,
        base_pf=0.50,
        stress_pf=0.40,
        base_return=-0.01,
        stress_return=-0.02,
    )
    result = evaluate_variant(
        _status(days=30, rebalances=30, summary=summary),
        "original",
        _config(tmp_path),
    )
    assert result["stage"] == "RESEARCH_SHADOW"


def test_day45_joint_extreme_concentration_is_archived(tmp_path: Path) -> None:
    summary = _variant_summary(closed=45, symbol_share=0.70, top5_share=0.90)
    result = evaluate_variant(
        _status(days=45, rebalances=45, summary=summary),
        "original",
        _config(tmp_path),
    )
    assert result["stage"] == "FAILED_ARCHIVE"
    assert "day45_extreme_symbol_and_few_trade_concentration" in result["reasons"]


def test_day60_fixed_gate_pass_becomes_forward_survivor(tmp_path: Path) -> None:
    summary = _variant_summary(closed=60)
    result = evaluate_variant(
        _status(days=60, rebalances=60, summary=summary, fixed_pass=True),
        "original",
        _config(tmp_path),
    )
    assert result["stage"] == "FORWARD_SURVIVOR"
    assert result["formal_a"] is False


def test_low_turnover_profile_scales_early_calendar_checkpoints(tmp_path: Path) -> None:
    profile = ForwardSampleProfile(
        cadence_days=3,
        minimum_calendar_days=90,
        minimum_closed_observations=30,
        preferred_calendar_days=150,
        preferred_closed_observations=50,
        minimum_failure_terminal=False,
    )
    result = evaluate_variant(
        _status(days=30, rebalances=0, summary=_variant_summary(closed=0)),
        "original",
        _config(tmp_path),
        profile,
    )
    assert result["stage"] == "RESEARCH_SHADOW"
    assert result["sample_requirements"]["day14_equivalent_calendar_day"] == 42
    assert result["sample_requirements"]["day30_equivalent_calendar_day"] == 90
    assert result["sample_requirements"]["day45_equivalent_calendar_day"] == 135


def test_low_turnover_minimum_failure_is_not_archived_before_final_sample(tmp_path: Path) -> None:
    profile = ForwardSampleProfile(
        cadence_days=3,
        minimum_calendar_days=90,
        minimum_closed_observations=30,
        preferred_calendar_days=150,
        preferred_closed_observations=50,
        minimum_failure_terminal=False,
    )
    result = evaluate_variant(
        _status(days=90, rebalances=30, summary=_variant_summary(closed=30), fixed_pass=False),
        "original",
        _config(tmp_path),
        profile,
    )
    assert result["stage"] == "RESEARCH_SHADOW"
    assert result["decision"] == "CONTINUE_TO_PREFERRED_CONFIRMATION_MINIMUM_GATE_NOT_YET_PASSED"


def test_low_turnover_preferred_failure_is_archived(tmp_path: Path) -> None:
    profile = ForwardSampleProfile(
        cadence_days=3,
        minimum_calendar_days=90,
        minimum_closed_observations=30,
        preferred_calendar_days=150,
        preferred_closed_observations=50,
        minimum_failure_terminal=False,
    )
    result = evaluate_variant(
        _status(days=150, rebalances=50, summary=_variant_summary(closed=50), fixed_pass=False),
        "original",
        _config(tmp_path),
        profile,
    )
    assert result["stage"] == "FAILED_ARCHIVE"
    assert result["decision"] == "ARCHIVE_PREFERRED_FIXED_GATE_FAILURE"


def test_final_due_uses_closed_observations_not_open_rebalances(tmp_path: Path) -> None:
    result = evaluate_variant(
        _status(days=90, rebalances=90, summary=_variant_summary(closed=49), fixed_pass=True),
        "original",
        _config(tmp_path),
    )
    assert result["stage"] == "RESEARCH_SHADOW"
    assert result["checks"]["minimum_fixed_gate"] is None


def test_day90_pass_is_manual_review_ready_not_auto_a(tmp_path: Path) -> None:
    summary = _variant_summary(closed=90)
    status = _status(days=90, rebalances=90, summary=summary, fixed_pass=True)
    result = build_parallel_acceptance_status(
        status,
        source_status_sha256="a" * 64,
        source_ledger_sha256="b" * 64,
        config=_config(tmp_path),
    )
    assert result["overall_stage"] == "FORMAL_A_REVIEW_READY"
    assert all(item["formal_a"] is False for item in result["variants"].values())
    assert result["signal_policy"]["manual_review_required_for_promotion"] is True


def test_research_message_is_explicitly_not_a_tier(tmp_path: Path) -> None:
    governance = build_parallel_acceptance_status(
        _status(),
        source_status_sha256="a" * 64,
        source_ledger_sha256="b" * 64,
        config=_config(tmp_path),
    )
    observation = {
        "detected_and_entry_utc": "2026-06-25T04:00:00Z",
        "longs": ["SOL-USDT-SWAP"],
        "shorts": ["DOGE-USDT-SWAP"],
    }
    ledger = {
        "original": {"observations": [observation]},
        "hysteresis_4_in_6_out": {"observations": [observation]},
    }
    message = format_research_shadow_message(governance, ledger)
    assert message is not None
    assert "研究级/影子信号（非A级）" in message
    assert "不自动下单" in message
    assert "NOT_A_TIER" in message


def _track(tmp_path: Path, **overrides) -> AcceptanceTrackConfig:
    values = {
        "track_id": "candidate-x",
        "label": "候选X",
        "source_status": tmp_path / "source.json",
        "source_ledger": tmp_path / "ledger.json",
        "variants": ("original",),
        "variant_labels": (("original", "候选X原始版"),),
        "updater_script": None,
        "admission_report": None,
        "admission_exempt_frozen_reference": False,
    }
    values.update(overrides)
    return AcceptanceTrackConfig(**values)


def test_new_candidate_requires_passed_research_gate_report(tmp_path: Path) -> None:
    blocked = evaluate_track_admission(_track(tmp_path))
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason"] == "admission_report_required_for_new_candidate"

    report = tmp_path / "research_gate_report.json"
    report.write_text(
        json.dumps({"schema": "okx_research_gate_report_v2", "ok": True}),
        encoding="utf-8",
    )
    admitted = evaluate_track_admission(_track(tmp_path, admission_report=report))
    assert admitted["status"] == "PASS"
    assert admitted["reason"] == "frozen_research_gate_passed"


def test_failed_forward_track_is_archived_idempotently(tmp_path: Path) -> None:
    track = _track(tmp_path, admission_exempt_frozen_reference=True)
    track.source_status.write_text("{}", encoding="utf-8")
    track.source_ledger.write_text("{}", encoding="utf-8")
    governance = {
        "source": {
            "source_status_sha256": "a" * 64,
            "source_ledger_sha256": "b" * 64,
        },
        "variants": {
            "original": {
                "stage": "FAILED_ARCHIVE",
                "decision": "EARLY_TERMINATE_FROZEN_DAY30_RULE",
                "reasons": ["day30_catastrophic_cost_adjusted_failure"],
            }
        },
    }
    first = archive_failed_track(track, governance, archive_root=tmp_path / "failed")
    second = archive_failed_track(track, governance, archive_root=tmp_path / "failed")
    assert first == second
    assert first is not None
    assert (first / "failure_summary.json").is_file()
    assert (first / "禁止调参营救.txt").is_file()


def test_frozen_early_stop_protocol_rejects_tampering(tmp_path: Path) -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "parallel_acceptance_early_stop_protocol.json"
    )
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["checkpoints"]["day30"]["base_pf_catastrophic_below"] = 0.71
    tampered = tmp_path / "tampered_protocol.json"
    tampered.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum invalid"):
        _load_frozen_early_stop_protocol(tampered)
