from __future__ import annotations

import pandas as pd
import pytest

from okx_signal_system.signal_quality.model import (
    BaselineQualityModel,
    fit_quality_model,
    infer_feature_columns,
    rank_signals,
    walk_forward_validate,
)


def _training_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ts": "2026-01-01T00:00:00Z", "trend_spread": 0.80, "volume_percentile": 0.90, "outcome": "TP", "final_net_r": 1.8},
            {"ts": "2026-01-01T00:15:00Z", "trend_spread": 0.75, "volume_percentile": 0.85, "outcome": "TP", "final_net_r": 1.6},
            {"ts": "2026-01-01T00:30:00Z", "trend_spread": 0.70, "volume_percentile": 0.80, "outcome": "TP", "final_net_r": 1.5},
            {"ts": "2026-01-01T00:45:00Z", "trend_spread": 0.55, "volume_percentile": 0.60, "outcome": "TIMEOUT", "final_net_r": 0.2},
            {"ts": "2026-01-01T01:00:00Z", "trend_spread": 0.35, "volume_percentile": 0.45, "outcome": "TIMEOUT", "final_net_r": -0.1},
            {"ts": "2026-01-01T01:15:00Z", "trend_spread": 0.20, "volume_percentile": 0.30, "outcome": "SL", "final_net_r": -1.1},
            {"ts": "2026-01-01T01:30:00Z", "trend_spread": 0.10, "volume_percentile": 0.20, "outcome": "SL", "final_net_r": -1.2},
            {"ts": "2026-01-01T01:45:00Z", "trend_spread": 0.05, "volume_percentile": 0.10, "outcome": "SL", "final_net_r": -1.3},
        ]
    )


def _multi_symbol_training_frame() -> pd.DataFrame:
    rows = []
    outcomes = ["TP", "TIMEOUT", "SL", "TP", "SL"]
    for idx, ts in enumerate(pd.date_range("2026-01-01T00:00:00Z", periods=5, freq="15min")):
        for symbol_offset, symbol in enumerate(["BTC-USDT-SWAP", "ETH-USDT-SWAP"]):
            rows.append(
                {
                    "ts": ts,
                    "symbol": symbol,
                    "trend_spread": 0.8 - idx * 0.1 - symbol_offset * 0.01,
                    "volume_percentile": 0.9 - idx * 0.1,
                    "outcome": outcomes[idx],
                    "final_net_r": 1.0 - idx * 0.4 - symbol_offset * 0.05,
                }
            )
    return pd.DataFrame(rows)


def test_baseline_quality_model_outputs_probabilities_and_ranking_score() -> None:
    model = fit_quality_model(_training_frame(), feature_columns=["trend_spread", "volume_percentile"])

    strong = model.predict_one({"trend_spread": 0.82, "volume_percentile": 0.88})
    weak = model.predict_one({"trend_spread": 0.06, "volume_percentile": 0.12})

    assert strong.p_tp + strong.p_sl + strong.p_timeout == pytest.approx(1.0)
    assert weak.p_tp + weak.p_sl + weak.p_timeout == pytest.approx(1.0)
    assert strong.p_tp > weak.p_tp
    assert strong.expected_net_r > weak.expected_net_r
    assert hasattr(strong, "uncertainty")
    assert hasattr(strong, "rank_score")


def test_model_degrades_to_prior_when_prediction_features_are_missing() -> None:
    model = BaselineQualityModel.fit(_training_frame(), feature_columns=["trend_spread", "volume_percentile"])

    prediction = model.predict_one({"unseen_feature": 1.0})

    assert prediction.p_tp + prediction.p_sl + prediction.p_timeout == pytest.approx(1.0)
    assert prediction.expected_net_r == pytest.approx(model.prior.expected_net_r)
    assert prediction.support == model.prior.support


def test_rank_signals_keeps_all_rows_without_hard_reject_gate() -> None:
    model = fit_quality_model(_training_frame(), feature_columns=["trend_spread"])
    candidates = pd.DataFrame(
        [
            {"inst_id": "WEAK", "trend_spread": 0.05},
            {"inst_id": "STRONG", "trend_spread": 0.90},
        ]
    )

    ranked = rank_signals(model, candidates)

    assert list(ranked["inst_id"]) == ["STRONG", "WEAK"]
    assert len(ranked) == len(candidates)
    assert set(["p_tp", "p_sl", "p_timeout", "expected_net_r", "quality_rank"]).issubset(ranked.columns)


def test_walk_forward_validation_uses_ordered_purged_splits() -> None:
    frame = _training_frame().sample(frac=1.0, random_state=7).reset_index(drop=True)

    result = walk_forward_validate(
        frame,
        feature_columns=["trend_spread", "volume_percentile"],
        train_size=4,
        test_size=2,
        purge_size=1,
    )

    assert not result.empty
    assert (result["train_end_position"] < result["valid_start_position"]).all()
    assert (result["valid_start_position"] - result["train_end_position"] - 1 == 1).all()
    assert (pd.to_datetime(result["train_end_ts"], utc=True) < pd.to_datetime(result["valid_start_ts"], utc=True)).all()
    assert set(["p_tp", "p_sl", "p_timeout", "expected_net_r", "actual_outcome", "actual_net_r"]).issubset(result.columns)


def test_walk_forward_validation_keeps_same_timestamp_symbols_in_same_split() -> None:
    result = walk_forward_validate(
        _multi_symbol_training_frame(),
        feature_columns=["trend_spread", "volume_percentile"],
        train_size=2,
        test_size=1,
        purge_size=1,
    )

    assert not result.empty
    first_fold = result[result["fold"] == 0]
    assert set(first_fold["symbol"]) == {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}
    assert pd.to_datetime(first_fold["ts"], utc=True).nunique() == 1
    assert first_fold["valid_start_position"].nunique() == 1
    assert first_fold["valid_start_position"].iloc[0] - first_fold["train_end_position"].iloc[0] - 1 == 2
    assert pd.Timestamp(first_fold["train_end_ts"].iloc[0]) < pd.Timestamp(first_fold["valid_start_ts"].iloc[0])


def test_infer_feature_columns_excludes_future_label_fields() -> None:
    columns = infer_feature_columns(
        pd.DataFrame(
            {
                "ts": ["2026-01-01T00:00:00Z"],
                "trend_spread": [0.4],
                "future_return": [0.9],
                "exit_price": [110.0],
                "holding_bars": [2],
                "mae": [-0.4],
                "mfe": [1.2],
                "accidental_numeric_feature": [99.0],
                "outcome": ["TP"],
                "final_net_r": [1.5],
            }
        )
    )

    assert columns == ["trend_spread"]


def test_quality_model_fit_locks_feature_columns_to_schema() -> None:
    frame = _training_frame().assign(
        future_return=[9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0],
        mfe=[3.0, 2.5, 2.2, 1.4, 1.0, 0.6, 0.2, 0.1],
        accidental_numeric_feature=[100.0] * 8,
    )

    inferred_model = fit_quality_model(frame)
    explicit_model = fit_quality_model(
        frame,
        feature_columns=["trend_spread", "future_return", "mfe", "accidental_numeric_feature"],
    )

    assert inferred_model.feature_columns == ("trend_spread", "volume_percentile")
    assert explicit_model.feature_columns == ("trend_spread",)
