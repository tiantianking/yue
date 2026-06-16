from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from okx_signal_system.signal_quality.candidate import SignalCandidate


DEFAULT_CORRELATION_WINDOW_DAYS = 30
DEFAULT_HIGH_CORRELATION_THRESHOLD = 0.75
DEFAULT_MIN_CORRELATION_SAMPLES = 500


def assign_correlation_groups(
    candidates: list[SignalCandidate],
    price_history: Mapping[str, pd.DataFrame] | None,
    *,
    window_days: int = DEFAULT_CORRELATION_WINDOW_DAYS,
    threshold: float = DEFAULT_HIGH_CORRELATION_THRESHOLD,
    min_samples: int = DEFAULT_MIN_CORRELATION_SAMPLES,
) -> dict[str, str]:
    candidate_keys = [_candidate_key(candidate) for candidate in candidates]
    symbols = list(dict.fromkeys(candidate.inst_id for candidate in candidates))
    if not candidate_keys:
        return {}
    if not price_history:
        return {key: f"solo:{key}" for key in candidate_keys}

    cutoff = _latest_candidate_time(candidates)
    returns_by_symbol = {
        symbol: series
        for symbol in symbols
        if (
            series := _recent_returns(
                price_history.get(symbol),
                cutoff=cutoff,
                window_days=window_days,
            )
        )
        is not None
    }

    parent = {key: key for key in candidate_keys}
    key_by_candidate = {id(candidate): _candidate_key(candidate) for candidate in candidates}
    unknown_keys = {
        key
        for candidate in candidates
        for key in [key_by_candidate[id(candidate)]]
        if len(returns_by_symbol.get(candidate.inst_id, pd.Series(dtype=float))) < min_samples
    }

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        root = min(left_root, right_root)
        child = right_root if root == left_root else left_root
        parent[child] = root

    candidates_by_side: dict[str, list[SignalCandidate]] = {}
    for candidate in candidates:
        candidates_by_side.setdefault(candidate.side, []).append(candidate)

    for same_side in candidates_by_side.values():
        for idx, left_candidate in enumerate(same_side):
            left = key_by_candidate[id(left_candidate)]
            if left in unknown_keys:
                continue
            for right_candidate in same_side[idx + 1 :]:
                right = key_by_candidate[id(right_candidate)]
                if right in unknown_keys:
                    continue
                correlation = _return_correlation(
                    returns_by_symbol.get(left_candidate.inst_id),
                    returns_by_symbol.get(right_candidate.inst_id),
                    min_samples=min_samples,
                )
                if correlation is not None and correlation >= threshold:
                    union(left, right)

    groups: dict[str, list[str]] = {}
    for key in candidate_keys:
        if key in unknown_keys:
            continue
        groups.setdefault(find(key), []).append(key)

    group_ids: dict[str, str] = {}
    for key in unknown_keys:
        group_ids[key] = f"unknown:{key}"
    for members in groups.values():
        sorted_members = sorted(members)
        prefix = "corr" if len(sorted_members) > 1 else "solo"
        group_id = f"{prefix}:{sorted_members[0]}"
        for key in sorted_members:
            group_ids[key] = group_id
    return group_ids


def _candidate_key(candidate: SignalCandidate) -> str:
    return f"{candidate.side}:{candidate.inst_id}"


def _is_closed_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no"}
    return bool(value)


def _latest_candidate_time(candidates: list[SignalCandidate]) -> pd.Timestamp | None:
    times: list[pd.Timestamp] = []
    for candidate in candidates:
        ts = pd.to_datetime(candidate.candle_time, utc=True, errors="coerce")
        if pd.notna(ts):
            times.append(ts)
    if not times:
        return None
    return max(times)


def _recent_returns(
    frame: pd.DataFrame | None,
    *,
    cutoff: pd.Timestamp | None,
    window_days: int,
) -> pd.Series | None:
    if frame is None or frame.empty or "ts" not in frame.columns or "close" not in frame.columns:
        return None

    columns = ["ts", "close"]
    if "is_closed" in frame.columns:
        columns.append("is_closed")
    df = frame.loc[:, columns].copy()
    if "is_closed" in df.columns:
        df = df[df["is_closed"].map(_is_closed_value)]
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = (
        df.dropna(subset=["ts", "close"])
        .query("close > 0")
        .drop_duplicates(subset=["ts"], keep="last")
        .sort_values("ts")
    )
    if cutoff is not None:
        df = df[df["ts"] <= cutoff]
    if len(df) < 3:
        return None

    if window_days > 0:
        window_start = df["ts"].iloc[-1] - pd.Timedelta(days=window_days)
        windowed = df[df["ts"] >= window_start]
        if len(windowed) >= 3:
            df = windowed

    returns = df.set_index("ts")["close"].pct_change()
    returns = returns.replace([float("inf"), float("-inf")], pd.NA).dropna()
    if len(returns) < 2:
        return None
    return returns


def _return_correlation(left: pd.Series | None, right: pd.Series | None, *, min_samples: int) -> float | None:
    if left is None or right is None:
        return None
    aligned = pd.concat([left, right], axis=1, join="inner").dropna()
    if len(aligned) < min_samples:
        return None
    if float(aligned.iloc[:, 0].std() or 0.0) == 0.0:
        return None
    if float(aligned.iloc[:, 1].std() or 0.0) == 0.0:
        return None
    correlation = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    if pd.isna(correlation):
        return None
    return float(correlation)
