from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

import pandas as pd


def read_parquet_with_retry(path: Path, *, attempts: int = 4, base_delay: float = 0.15) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(base_delay * (attempt + 1))
    assert last_error is not None
    raise last_error


def replace_with_retry(tmp_path: Path, path: Path, *, attempts: int = 8, base_delay: float = 0.15) -> None:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            tmp_path.replace(path)
            return
        except PermissionError as exc:
            last_error = exc
        except OSError as exc:
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(base_delay * (attempt + 1))
    assert last_error is not None
    raise last_error


def write_parquet_atomic(frame: pd.DataFrame, path: Path, *, attempts: int = 8) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(
        f"{path.stem}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp{path.suffix}"
    )
    try:
        frame.to_parquet(tmp_path, index=False)
        replace_with_retry(tmp_path, path, attempts=attempts)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
