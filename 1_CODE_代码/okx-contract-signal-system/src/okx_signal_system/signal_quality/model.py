from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

OUTCOMES: tuple[str, str, str] = ("TP", "SL", "TIMEOUT")

RESERVED_COLUMNS = {
    "ts",
    "time",
    "timestamp",
    "inst_id",
    "symbol",
    "side",
    "outcome",
    "final_net_r",
    "mae",
    "mfe",
    "holding_bars",
    "exit_time",
    "exit_price",
    "rank",
    "rank_score",
    "quality_rank",
    "p_tp",
    "p_sl",
    "p_timeout",
    "expected_net_r",
    "uncertainty",
    "_source_position",
}


@dataclass(frozen=True)
class QualityPrediction:
    p_tp: float
    p_sl: float
    p_timeout: float
    expected_net_r: float
    uncertainty: float
    rank_score: float
    support: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "p_tp": self.p_tp,
            "p_sl": self.p_sl,
            "p_timeout": self.p_timeout,
            "expected_net_r": self.expected_net_r,
            "uncertainty": self.uncertainty,
            "rank_score": self.rank_score,
            "support": self.support,
        }


@dataclass(frozen=True)
class OutcomeStats:
    probabilities: tuple[float, float, float]
    expected_net_r: float
    support: int


@dataclass(frozen=True)
class FeatureProfile:
    feature: str
    first_cut: float | None
    second_cut: float | None
    buckets: Mapping[str, OutcomeStats]

    def bucket_for(self, value: float) -> str:
        if self.first_cut is None:
            return "all"
        if self.second_cut is None:
            return "low" if value <= self.first_cut else "high"
        if value <= self.first_cut:
            return "low"
        if value <= self.second_cut:
            return "mid"
        return "high"


class BaselineQualityModel:
    """Explainable baseline model for ranking signal candidates.

    The model estimates outcome probabilities and expected R from historical
    labels. It ranks candidates only; it does not apply a hard reject gate.
    """

    def __init__(
        self,
        *,
        feature_columns: Sequence[str],
        prior: OutcomeStats,
        profiles: Sequence[FeatureProfile],
        min_bucket_support: int = 2,
    ) -> None:
        self.feature_columns = tuple(feature_columns)
        self.prior = prior
        self.profiles = tuple(profiles)
        self.min_bucket_support = max(1, int(min_bucket_support))

    @classmethod
    def fit(
        cls,
        labeled_features: pd.DataFrame,
        *,
        feature_columns: Sequence[str] | None = None,
        outcome_column: str = "outcome",
        target_column: str = "final_net_r",
        min_bucket_support: int = 2,
    ) -> "BaselineQualityModel":
        data = _prepare_labeled_frame(labeled_features, outcome_column, target_column)
        columns = list(feature_columns) if feature_columns is not None else infer_feature_columns(data)
        prior = _stats_for(data, outcome_column, target_column)
        profiles = [
            profile
            for feature in columns
            if (profile := _build_feature_profile(data, feature, outcome_column, target_column)) is not None
        ]
        return cls(
            feature_columns=columns,
            prior=prior,
            profiles=profiles,
            min_bucket_support=min_bucket_support,
        )

    def predict_one(self, features: Mapping[str, Any] | pd.Series) -> QualityPrediction:
        weighted: list[tuple[OutcomeStats, float]] = [(self.prior, float(self.min_bucket_support + 1))]
        support = self.prior.support

        for profile in self.profiles:
            value = _float_or_none(features.get(profile.feature))
            if value is None:
                continue
            stats = profile.buckets.get(profile.bucket_for(value))
            if stats is None:
                continue
            weight = float(min(20, max(1, stats.support)))
            weighted.append((stats, weight))
            support += stats.support

        probabilities, expected_net_r = _combine_stats(weighted)
        p_tp, p_sl, p_timeout = probabilities
        uncertainty = min(1.0, 1.0 / math.sqrt(max(1.0, sum(weight for _, weight in weighted))))
        rank_score = float(expected_net_r + p_tp - p_sl - p_timeout * 0.1)
        return QualityPrediction(
            p_tp=float(p_tp),
            p_sl=float(p_sl),
            p_timeout=float(p_timeout),
            expected_net_r=float(expected_net_r),
            uncertainty=float(uncertainty),
            rank_score=rank_score,
            support=int(support),
        )

    def predict_frame(self, features: pd.DataFrame) -> pd.DataFrame:
        columns = ["p_tp", "p_sl", "p_timeout", "expected_net_r", "uncertainty", "rank_score", "support"]
        if features.empty:
            return pd.DataFrame(columns=columns, index=features.index)
        rows = [self.predict_one(row).as_dict() for _, row in features.iterrows()]
        return pd.DataFrame(rows, index=features.index, columns=columns)

    def rank_frame(self, features: pd.DataFrame) -> pd.DataFrame:
        predictions = self.predict_frame(features)
        input_features = features.drop(columns=[column for column in predictions.columns if column in features.columns])
        ranked = pd.concat([input_features.reset_index(drop=True), predictions.reset_index(drop=True)], axis=1)
        ranked = ranked.sort_values(["rank_score", "expected_net_r"], ascending=False, kind="mergesort").reset_index(drop=True)
        ranked["quality_rank"] = np.arange(1, len(ranked) + 1)
        return ranked


def fit_quality_model(
    labeled_features: pd.DataFrame,
    *,
    feature_columns: Sequence[str] | None = None,
    outcome_column: str = "outcome",
    target_column: str = "final_net_r",
) -> BaselineQualityModel:
    return BaselineQualityModel.fit(
        labeled_features,
        feature_columns=feature_columns,
        outcome_column=outcome_column,
        target_column=target_column,
    )


def rank_signals(model: BaselineQualityModel, features: pd.DataFrame) -> pd.DataFrame:
    return model.rank_frame(features)


def walk_forward_validate(
    labeled_features: pd.DataFrame,
    *,
    feature_columns: Sequence[str] | None = None,
    time_column: str = "ts",
    outcome_column: str = "outcome",
    target_column: str = "final_net_r",
    train_size: int = 100,
    test_size: int = 20,
    purge_size: int = 0,
    expanding: bool = True,
) -> pd.DataFrame:
    if train_size <= 0:
        raise ValueError("train_size must be positive")
    if test_size <= 0:
        raise ValueError("test_size must be positive")
    if purge_size < 0:
        raise ValueError("purge_size cannot be negative")

    data = _prepare_labeled_frame(labeled_features, outcome_column, target_column)
    data = _sort_for_time_series(data, time_column)
    if len(data) <= train_size + purge_size:
        return _empty_walk_forward_frame()

    rows: list[dict[str, Any]] = []
    fold = 0
    train_end = train_size
    while train_end + purge_size < len(data):
        valid_start = train_end + purge_size
        valid_end = min(valid_start + test_size, len(data))
        train_start = 0 if expanding else max(0, train_end - train_size)
        train = data.iloc[train_start:train_end].reset_index(drop=True)
        valid = data.iloc[valid_start:valid_end].reset_index(drop=True)
        if train.empty or valid.empty:
            break

        model = BaselineQualityModel.fit(
            train,
            feature_columns=feature_columns,
            outcome_column=outcome_column,
            target_column=target_column,
        )
        predictions = model.predict_frame(valid).reset_index(drop=True)
        fold_rows = pd.concat([valid.reset_index(drop=True), predictions], axis=1)
        for row_idx, row in fold_rows.iterrows():
            out = row.to_dict()
            out.update(
                {
                    "fold": fold,
                    "train_start_position": train_start,
                    "train_end_position": train_end - 1,
                    "valid_start_position": valid_start,
                    "valid_position": valid_start + int(row_idx),
                    "purge_size": purge_size,
                    "train_start_ts": _position_time(data, train_start, time_column),
                    "train_end_ts": _position_time(data, train_end - 1, time_column),
                    "valid_start_ts": _position_time(data, valid_start, time_column),
                    "actual_outcome": row[outcome_column],
                    "actual_net_r": float(row[target_column]),
                }
            )
            rows.append(out)

        fold += 1
        train_end = valid_end

    if not rows:
        return _empty_walk_forward_frame()
    return pd.DataFrame(rows)


def infer_feature_columns(
    frame: pd.DataFrame,
    *,
    reserved_columns: set[str] | None = None,
) -> list[str]:
    reserved = set(RESERVED_COLUMNS)
    if reserved_columns:
        reserved.update(reserved_columns)
    columns: list[str] = []
    for column in frame.columns:
        if str(column) in reserved:
            continue
        values = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
        if values.notna().any():
            columns.append(str(column))
    return columns


def _prepare_labeled_frame(frame: pd.DataFrame, outcome_column: str, target_column: str) -> pd.DataFrame:
    if outcome_column not in frame.columns:
        raise ValueError(f"missing required column: {outcome_column}")
    if target_column not in frame.columns:
        raise ValueError(f"missing required column: {target_column}")
    data = frame.copy()
    data[outcome_column] = data[outcome_column].map(_normalize_outcome)
    data[target_column] = pd.to_numeric(data[target_column], errors="coerce")
    data = data[data[outcome_column].isin(OUTCOMES)]
    data = data.replace([np.inf, -np.inf], np.nan).dropna(subset=[target_column]).reset_index(drop=True)
    if data.empty:
        raise ValueError("labeled_features has no usable labeled rows")
    return data


def _build_feature_profile(
    data: pd.DataFrame,
    feature: str,
    outcome_column: str,
    target_column: str,
) -> FeatureProfile | None:
    if feature not in data.columns:
        return None
    values = pd.to_numeric(data[feature], errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = data.loc[values.notna()].copy()
    if valid.empty:
        return None
    valid["_feature_value"] = values.loc[valid.index].astype(float)
    first_cut, second_cut = _feature_cuts(valid["_feature_value"])
    valid["_feature_bucket"] = valid["_feature_value"].map(
        lambda value: _bucket_name(float(value), first_cut, second_cut)
    )
    buckets = {
        bucket: _stats_for(bucket_frame, outcome_column, target_column)
        for bucket, bucket_frame in valid.groupby("_feature_bucket", sort=False)
    }
    return FeatureProfile(feature=feature, first_cut=first_cut, second_cut=second_cut, buckets=buckets)


def _feature_cuts(values: pd.Series) -> tuple[float | None, float | None]:
    unique = values.dropna().unique()
    if len(unique) <= 1:
        return None, None
    first = float(values.quantile(1 / 3))
    second = float(values.quantile(2 / 3))
    if math.isfinite(first) and math.isfinite(second) and first < second:
        return first, second
    median = float(values.median())
    return (median, None) if math.isfinite(median) else (None, None)


def _bucket_name(value: float, first_cut: float | None, second_cut: float | None) -> str:
    if first_cut is None:
        return "all"
    if second_cut is None:
        return "low" if value <= first_cut else "high"
    if value <= first_cut:
        return "low"
    if value <= second_cut:
        return "mid"
    return "high"


def _stats_for(data: pd.DataFrame, outcome_column: str, target_column: str) -> OutcomeStats:
    support = int(len(data))
    counts = data[outcome_column].value_counts()
    probabilities = tuple(float((counts.get(outcome, 0) + 1) / (support + len(OUTCOMES))) for outcome in OUTCOMES)
    expected_net_r = float(pd.to_numeric(data[target_column], errors="coerce").mean())
    if not math.isfinite(expected_net_r):
        expected_net_r = 0.0
    return OutcomeStats(probabilities=probabilities, expected_net_r=expected_net_r, support=support)


def _combine_stats(weighted: Sequence[tuple[OutcomeStats, float]]) -> tuple[tuple[float, float, float], float]:
    total_weight = float(sum(weight for _, weight in weighted))
    if total_weight <= 0:
        return (1 / 3, 1 / 3, 1 / 3), 0.0
    probabilities = np.zeros(len(OUTCOMES), dtype=float)
    expected_net_r = 0.0
    for stats, weight in weighted:
        probabilities += np.asarray(stats.probabilities, dtype=float) * weight
        expected_net_r += stats.expected_net_r * weight
    probabilities = _normalize_probabilities(probabilities / total_weight)
    return tuple(float(value) for value in probabilities), float(expected_net_r / total_weight)


def _normalize_probabilities(values: np.ndarray) -> np.ndarray:
    clean = np.nan_to_num(values.astype(float), nan=0.0, posinf=0.0, neginf=0.0)
    clean = np.maximum(clean, 0.0)
    total = float(clean.sum())
    if total <= 0:
        return np.asarray([1 / 3, 1 / 3, 1 / 3], dtype=float)
    return clean / total


def _normalize_outcome(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    text = str(raw).strip().upper()
    return text if text in OUTCOMES else None


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sort_for_time_series(data: pd.DataFrame, time_column: str) -> pd.DataFrame:
    sorted_data = data.copy()
    sorted_data["_source_position"] = np.arange(len(sorted_data))
    if time_column in sorted_data.columns:
        sorted_data[time_column] = pd.to_datetime(sorted_data[time_column], utc=True, errors="coerce")
        sorted_data = sorted_data.dropna(subset=[time_column]).sort_values([time_column, "_source_position"], kind="mergesort")
    else:
        sorted_data = sorted_data.sort_values("_source_position", kind="mergesort")
    return sorted_data.reset_index(drop=True)


def _position_time(data: pd.DataFrame, position: int, time_column: str) -> Any:
    if time_column in data.columns:
        return data.iloc[position][time_column]
    return int(position)


def _empty_walk_forward_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "fold",
            "p_tp",
            "p_sl",
            "p_timeout",
            "expected_net_r",
            "uncertainty",
            "rank_score",
            "actual_outcome",
            "actual_net_r",
            "train_start_position",
            "train_end_position",
            "valid_start_position",
            "valid_position",
            "purge_size",
            "train_start_ts",
            "train_end_ts",
            "valid_start_ts",
        ]
    )


__all__ = [
    "BaselineQualityModel",
    "QualityPrediction",
    "fit_quality_model",
    "infer_feature_columns",
    "rank_signals",
    "walk_forward_validate",
]
