from __future__ import annotations

import json
from pathlib import Path

import pytest

from okx_signal_system.research.shadow_ensemble_acceptance import (
    build_acceptance_payloads,
    build_variant_summary,
    fixed_gate_result,
    load_frozen_protocol,
    update_snapshot_chain,
)


def _row(
    observation_id: str,
    *,
    member: str = "DC_n24_t50_slow",
    symbol: str = "BTC-USDT-SWAP",
    side: str = "long",
    signal_time: str = "2026-06-23T08:00:00+00:00",
    state: str = "ACTIVE",
    gross_r=None,
    estimated_net_r=None,
    exit_time=None,
) -> dict:
    return {
        "observation_id": observation_id,
        "candidate_id": "v357-shadow-donchian-slow-plus-vcb-a",
        "member": member,
        "symbol": symbol,
        "side": side,
        "signal_time": signal_time,
        "entry_time": signal_time,
        "state": state,
        "gross_r": gross_r,
        "estimated_net_r": estimated_net_r,
        "exit_time": exit_time,
    }


def test_frozen_protocol_and_candidate_hashes_are_valid() -> None:
    protocol, path, digest = load_frozen_protocol()
    assert path.is_file()
    assert protocol["research_only"] is True
    assert protocol["production_effect"] == "NONE"
    assert digest


def test_frozen_protocol_rejects_tampering(tmp_path: Path) -> None:
    _protocol, source, _digest = load_frozen_protocol()
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["evidence_rules"]["stress_cost_multiplier"] = 1.5
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum invalid"):
        load_frozen_protocol(tampered)


def test_variant_summary_uses_base_and_double_cost_stress() -> None:
    rows = [
        _row(
            "win",
            symbol="BTC-USDT-SWAP",
            state="TIMEOUT_RESULT",
            gross_r=2.0,
            estimated_net_r=1.8,
            exit_time="2026-07-01T00:00:00+00:00",
        ),
        _row(
            "loss",
            symbol="ETH-USDT-SWAP",
            state="STOP_REACHED",
            gross_r=-1.0,
            estimated_net_r=-1.2,
            exit_time="2026-07-02T00:00:00+00:00",
        ),
    ]
    summary = build_variant_summary(rows, risk_fraction=0.005, stress_cost_multiplier=2.0)
    assert summary["closed_count"] == 2
    assert summary["base"]["net_r_sum"] == pytest.approx(0.6)
    assert summary["stress"]["net_r_sum"] == pytest.approx(0.2)
    assert summary["base"]["profit_factor"] == pytest.approx(1.5)
    assert summary["stress"]["profit_factor"] == pytest.approx(1.6 / 1.4)


def test_fixed_gate_is_not_evaluated_before_member_sample() -> None:
    protocol, _path, _digest = load_frozen_protocol()
    summary = build_variant_summary([], risk_fraction=0.005, stress_cost_multiplier=2.0)
    result = fixed_gate_result(summary, protocol, sample_due=False)
    assert result["all_pass"] is None
    assert result["status"] == "NOT_EVALUATED_SAMPLE_INCOMPLETE"


def test_each_member_requires_its_own_50_observations() -> None:
    protocol, _path, digest = load_frozen_protocol()
    rows = [
        _row(
            f"dc-{index}",
            signal_time=f"2026-06-{23 + min(index, 6):02d}T08:00:00+00:00",
        )
        for index in range(50)
    ]
    source_status = {
        "status": "running",
        "research_only": True,
        "isolated_from_formal_runtime": True,
        "eligible_symbols": 21,
        "skipped_symbols": [],
        "latest_closed_4h": "2026-08-25T12:00:00+00:00",
    }
    status, _ledger = build_acceptance_payloads(
        source_status,
        rows,
        protocol,
        protocol_sha256=digest,
        sqlite_sha256="a" * 64,
        source_status_sha256="b" * 64,
    )
    assert status["variant_fixed_gate_results"]["DC_n24_t50_slow"]["all_pass"] is False
    assert status["variant_fixed_gate_results"]["VCB_A"]["all_pass"] is None
    assert status["minimum_sample_gate"] is False


def test_ledger_is_deterministic_for_unchanged_evidence() -> None:
    protocol, _path, digest = load_frozen_protocol()
    source_status = {
        "status": "running",
        "research_only": True,
        "isolated_from_formal_runtime": True,
        "eligible_symbols": 21,
        "skipped_symbols": [],
        "latest_closed_4h": "2026-06-25T12:00:00+00:00",
    }
    rows = [_row("stable-observation")]
    _status_1, ledger_1 = build_acceptance_payloads(
        source_status,
        rows,
        protocol,
        protocol_sha256=digest,
        sqlite_sha256="a" * 64,
        source_status_sha256="b" * 64,
    )
    _status_2, ledger_2 = build_acceptance_payloads(
        source_status,
        rows,
        protocol,
        protocol_sha256=digest,
        sqlite_sha256="c" * 64,
        source_status_sha256="d" * 64,
    )
    assert ledger_1 == ledger_2
    assert ledger_1["evidence_digest"]


def test_snapshot_chain_is_idempotent_and_tamper_evident(tmp_path: Path) -> None:
    path = tmp_path / "chain.json"
    first = update_snapshot_chain(
        path,
        closed_data_through_utc="2026-06-25T12:00:00+00:00",
        source_status_sha256="a" * 64,
        source_sqlite_sha256="b" * 64,
        evidence_digest="9" * 64,
        ledger_sha256="c" * 64,
    )
    second = update_snapshot_chain(
        path,
        closed_data_through_utc="2026-06-25T12:00:00+00:00",
        source_status_sha256="a" * 64,
        source_sqlite_sha256="b" * 64,
        evidence_digest="9" * 64,
        ledger_sha256="c" * 64,
    )
    assert first["snapshot_count"] == 1
    assert second["snapshot_count"] == 1

    update_snapshot_chain(
        path,
        closed_data_through_utc="2026-06-25T16:00:00+00:00",
        source_status_sha256="d" * 64,
        source_sqlite_sha256="e" * 64,
        evidence_digest="8" * 64,
        ledger_sha256="f" * 64,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["snapshot_count"] == 2
    payload["snapshots"][0]["ledger_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="entry hash invalid"):
        update_snapshot_chain(
            path,
            closed_data_through_utc="2026-06-25T20:00:00+00:00",
            source_status_sha256="1" * 64,
            source_sqlite_sha256="2" * 64,
            evidence_digest="7" * 64,
            ledger_sha256="3" * 64,
        )
