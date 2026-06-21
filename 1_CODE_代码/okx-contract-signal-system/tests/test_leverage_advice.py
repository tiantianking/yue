from okx_signal_system.risk.leverage_advice import build_leverage_advice


def calibrated_quality() -> dict[str, float]:
    return {"expected_net_r": 0.8, "uncertainty": 0.08, "p_sl": 0.18}


def test_formal_advice_respects_risk_budget() -> None:
    advice = build_leverage_advice(
        entry_ref=100.0,
        stop_loss=98.0,
        tier="A",
        signal_score=9.2,
        risk_reward_ratio=3.5,
        quality_model=calibrated_quality(),
    )
    assert advice is not None
    assert 1 <= advice.recommended_multiple <= 5
    assert advice.estimated_reference_loss_fraction <= 0.005 + 1e-12
    assert advice.advisory_only


def test_global_cap_cannot_be_overridden_above_five_x() -> None:
    advice = build_leverage_advice(
        entry_ref=100.0,
        stop_loss=99.9,
        tier="A",
        signal_score=10.0,
        risk_reward_ratio=5.0,
        quality_model=calibrated_quality(),
        global_cap=50,
    )
    assert advice is not None
    assert advice.recommended_multiple == 5
    assert advice.maximum_multiple == 5


def test_stop_risk_remains_the_reported_binding_constraint() -> None:
    advice = build_leverage_advice(
        entry_ref=100.0,
        stop_loss=96.0,
        tier="A",
        signal_score=8.5,
        risk_reward_ratio=3.5,
        quality_model=calibrated_quality(),
    )
    assert advice is not None
    assert advice.recommended_multiple == 1
    assert advice.binding_constraint == "STOP_RISK_LIMIT"


def test_wide_stop_reduces_reference_margin_instead_of_exceeding_budget() -> None:
    advice = build_leverage_advice(
        entry_ref=100.0,
        stop_loss=90.0,
        tier="A",
        signal_score=9.5,
        risk_reward_ratio=4.0,
        quality_model=calibrated_quality(),
    )
    assert advice is not None
    assert advice.recommended_multiple == 1
    assert advice.suggested_margin_fraction < 0.08
    assert advice.estimated_reference_loss_fraction <= 0.005 + 1e-12
    assert advice.binding_constraint == "MARGIN_FRACTION_LIMIT"


def test_shadow_and_unvalidated_advice_stay_at_one_x() -> None:
    shadow = build_leverage_advice(
        entry_ref=100.0,
        stop_loss=99.0,
        tier="A-",
        signal_score=10.0,
        risk_reward_ratio=5.0,
        quality_model={"expected_net_r": 2.0, "uncertainty": 0.01, "p_sl": 0.05},
    )
    unvalidated = build_leverage_advice(
        entry_ref=100.0,
        stop_loss=99.5,
        tier="A",
        signal_score=9.8,
        risk_reward_ratio=4.0,
        quality_model=None,
    )
    assert shadow is not None and shadow.recommended_multiple == 1
    assert shadow.confidence == "LOW"
    assert shadow.binding_constraint == "SHADOW_SIGNAL_NO_LEVERAGE"
    assert unvalidated is not None and unvalidated.recommended_multiple == 1
    assert unvalidated.confidence == "LOW"
    assert unvalidated.binding_constraint == "UNVALIDATED_QUALITY_MODEL"


def test_invalid_quality_values_are_not_treated_as_calibrated() -> None:
    advice = build_leverage_advice(
        entry_ref=100.0,
        stop_loss=99.0,
        tier="A",
        signal_score=9.5,
        risk_reward_ratio=4.0,
        quality_model={"expected_net_r": 1.0, "uncertainty": -0.1, "p_sl": 1.2},
    )
    assert advice is not None
    assert advice.recommended_multiple == 1
    assert advice.binding_constraint == "UNVALIDATED_QUALITY_MODEL"


def test_b_tier_has_no_advice() -> None:
    advice = build_leverage_advice(
        entry_ref=100.0,
        stop_loss=99.0,
        tier="B",
        signal_score=9.0,
        risk_reward_ratio=4.0,
        quality_model={"expected_net_r": 1.0, "uncertainty": 0.05, "p_sl": 0.1},
    )
    assert advice is None
