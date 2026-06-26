from __future__ import annotations

from pathlib import Path

import pandas as pd

from okx_signal_system.research.robustness_screen import (
    evaluate_parameter_neighborhood,
    evaluate_robustness_screen,
    frozen_protocol_ok,
)


PROTOCOL = {
    "schema": "okx_robustness_screen_protocol_v1",
    "random_time_trials": 500,
    "random_time_alpha": 0.05,
    "entry_delay_bars": 1,
    "minimum_neighbor_variants": 3,
    "minimum_positive_neighbor_ratio": 2.0 / 3.0,
    "portfolio_increment_required": True,
    "locked_before_pnl": True,
}


def _write_passing_evidence(root: Path) -> None:
    falsification_rows = [
        {
            "test": "observed",
            "trial_id": "observed",
            "net_r": 10.0,
            "profit_factor": 1.40,
            "total_trades": 100,
        },
        {
            "test": "direction_reversed",
            "trial_id": "reversed",
            "net_r": 1.0,
            "profit_factor": 1.10,
            "total_trades": 100,
        },
        {
            "test": "entry_delay_1bar",
            "trial_id": "delay-1",
            "net_r": 5.0,
            "profit_factor": 1.10,
            "total_trades": 100,
        },
    ]
    falsification_rows.extend(
        {
            "test": "random_time",
            "trial_id": f"random-{index}",
            "net_r": float(index % 5),
            "profit_factor": 0.90,
            "total_trades": 100,
        }
        for index in range(500)
    )
    pd.DataFrame(falsification_rows).to_csv(root / "falsification_trials.csv", index=False)

    pd.DataFrame(
        [
            {
                "config_id": "primary",
                "is_primary": True,
                "distance": 0.0,
                "net_r": 10.0,
                "profit_factor": 1.40,
                "total_trades": 100,
            },
            {
                "config_id": "neighbor-a",
                "is_primary": False,
                "distance": 1.0,
                "net_r": 7.0,
                "profit_factor": 1.10,
                "total_trades": 95,
            },
            {
                "config_id": "neighbor-b",
                "is_primary": False,
                "distance": 1.0,
                "net_r": 6.0,
                "profit_factor": 1.05,
                "total_trades": 102,
            },
            {
                "config_id": "neighbor-c",
                "is_primary": False,
                "distance": 1.0,
                "net_r": 4.0,
                "profit_factor": 1.02,
                "total_trades": 98,
            },
        ]
    ).to_csv(root / "parameter_neighborhood.csv", index=False)

    pd.DataFrame(
        [
            {
                "scenario": "baseline",
                "profit_factor": 1.10,
                "max_drawdown": 0.15,
                "max_loss_streak": 8,
                "effective_signal_count": 100,
                "regime_coverage_count": 2,
            },
            {
                "scenario": "combined",
                "profit_factor": 1.14,
                "max_drawdown": 0.14,
                "max_loss_streak": 7,
                "effective_signal_count": 120,
                "regime_coverage_count": 3,
            },
        ]
    ).to_csv(root / "portfolio_increment.csv", index=False)


def test_frozen_protocol_rejects_weakened_trial_count() -> None:
    ok, detail = frozen_protocol_ok({**PROTOCOL, "random_time_trials": 100})

    assert ok is False
    assert "random_time_trials" in detail


def test_complete_robustness_evidence_passes(tmp_path: Path) -> None:
    _write_passing_evidence(tmp_path)

    result = evaluate_robustness_screen(tmp_path, protocol=PROTOCOL)

    assert result["passed"] is True
    assert result["decision"] == "PASS_TO_LOCKED_VALIDATION"
    assert all(item["ok"] for item in result["checks"])


def test_missing_evidence_fails_closed(tmp_path: Path) -> None:
    result = evaluate_robustness_screen(tmp_path, protocol=PROTOCOL)

    assert result["passed"] is False
    assert result["decision"] == "FAIL_STOP_NO_RESCUE"
    evidence = next(item for item in result["checks"] if item["name"] == "robustness_evidence_complete")
    assert evidence["ok"] is False
    assert "missing evidence file" in evidence["detail"]


def test_isolated_parameter_spike_is_rejected() -> None:
    frame = pd.DataFrame(
        [
            {
                "config_id": "primary",
                "is_primary": True,
                "distance": 0.0,
                "net_r": 20.0,
                "profit_factor": 3.0,
                "total_trades": 100,
            },
            {
                "config_id": "neighbor-a",
                "is_primary": False,
                "distance": 1.0,
                "net_r": 1.0,
                "profit_factor": 1.01,
                "total_trades": 100,
            },
            {
                "config_id": "neighbor-b",
                "is_primary": False,
                "distance": 1.0,
                "net_r": 1.0,
                "profit_factor": 1.01,
                "total_trades": 100,
            },
            {
                "config_id": "neighbor-c",
                "is_primary": False,
                "distance": 1.0,
                "net_r": 1.0,
                "profit_factor": 1.01,
                "total_trades": 100,
            },
        ]
    )

    result = evaluate_parameter_neighborhood(frame)

    spike = next(item for item in result["checks"] if item["name"] == "parameter_is_not_isolated_spike")
    assert spike["ok"] is False
