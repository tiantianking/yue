from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from okx_signal_system.config import project_paths
from okx_signal_system.signal_quality.feature_builder import build_signal_quality_feature_dict
from okx_signal_system.signal_quality.model import BaselineQualityModel


DEFAULT_MODEL_FILE = "signal_quality_model.json"


@dataclass(frozen=True)
class QualityModelShadowScore:
    enabled: bool
    artifact_path: str
    reason: str | None = None
    p_tp: float | None = None
    p_sl: float | None = None
    p_timeout: float | None = None
    expected_net_r: float | None = None
    uncertainty: float | None = None
    rank_score: float | None = None
    support: int | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "enabled": self.enabled,
            "artifact_path": self.artifact_path,
            "reason": self.reason,
        }
        for key in ["p_tp", "p_sl", "p_timeout", "expected_net_r", "uncertainty", "rank_score", "support"]:
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload


class QualityModelShadowScorer:
    """Load an optional quality model artifact and score candidates without changing decisions."""

    def __init__(self, artifact_path: str | Path | None = None):
        self.artifact_path = Path(artifact_path) if artifact_path else project_paths().output_dir / DEFAULT_MODEL_FILE
        self._model: BaselineQualityModel | None = None
        self._loaded_mtime: float | None = None
        self._load_error: str | None = None

    def score(self, signal: Any, frame: pd.DataFrame) -> QualityModelShadowScore:
        model = self._load_model()
        path_text = str(self.artifact_path)
        if model is None:
            return QualityModelShadowScore(enabled=False, artifact_path=path_text, reason=self._load_error or "model_artifact_missing")

        features = build_signal_quality_feature_dict(signal, frame)
        if not features:
            return QualityModelShadowScore(enabled=False, artifact_path=path_text, reason="features_unavailable")
        prediction = model.predict_one(features)
        return QualityModelShadowScore(
            enabled=True,
            artifact_path=path_text,
            p_tp=prediction.p_tp,
            p_sl=prediction.p_sl,
            p_timeout=prediction.p_timeout,
            expected_net_r=prediction.expected_net_r,
            uncertainty=prediction.uncertainty,
            rank_score=prediction.rank_score,
            support=prediction.support,
        )

    def status(self) -> dict[str, Any]:
        model = self._load_model()
        return {
            "enabled": model is not None,
            "artifact_path": str(self.artifact_path),
            "feature_columns": list(model.feature_columns) if model else [],
            "reason": None if model else self._load_error or "model_artifact_missing",
        }

    def _load_model(self) -> BaselineQualityModel | None:
        if not self.artifact_path.exists():
            self._model = None
            self._loaded_mtime = None
            self._load_error = "model_artifact_missing"
            return None
        mtime = self.artifact_path.stat().st_mtime
        if self._model is not None and self._loaded_mtime == mtime:
            return self._model
        try:
            self._model = BaselineQualityModel.load(self.artifact_path)
            self._loaded_mtime = mtime
            self._load_error = None
            return self._model
        except Exception as exc:
            self._model = None
            self._loaded_mtime = mtime
            self._load_error = f"model_load_failed:{exc}"
            return None


__all__ = [
    "DEFAULT_MODEL_FILE",
    "QualityModelShadowScore",
    "QualityModelShadowScorer",
]
