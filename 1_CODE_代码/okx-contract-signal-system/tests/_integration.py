from __future__ import annotations

import os
from pathlib import Path

import pytest


def require_lightweight_history(
    dataset: str,
    *required_files: str,
    min_parquet_files: int = 0,
) -> Path:
    data_root_value = os.getenv("JIAOYI_DATA_DIR")
    if not data_root_value:
        pytest.skip("JIAOYI_DATA_DIR is not configured")

    data_root = Path(data_root_value).expanduser()
    if not data_root.exists():
        pytest.skip(f"JIAOYI_DATA_DIR does not exist: {data_root}")

    if data_root.name == dataset:
        dataset_root = data_root
    elif data_root.name == "lightweight_history":
        dataset_root = data_root / dataset
    else:
        dataset_root = data_root / "lightweight_history" / dataset

    if not dataset_root.is_dir():
        pytest.skip(f"historical dataset is not available: {dataset_root}")

    for filename in required_files:
        if not (dataset_root / filename).exists():
            pytest.skip(f"historical data file is not available: {dataset_root / filename}")

    if min_parquet_files:
        parquet_count = sum(1 for _ in dataset_root.glob("*.parquet"))
        if parquet_count < min_parquet_files:
            pytest.skip(
                f"historical dataset requires at least {min_parquet_files} parquet files: {dataset_root}"
            )

    return dataset_root
