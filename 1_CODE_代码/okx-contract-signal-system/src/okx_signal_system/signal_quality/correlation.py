from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from okx_signal_system.signal_quality.candidate import SignalCandidate


DEFAULT_CORRELATION_WINDOW_DAYS = 30
DEFAULT_HIGH_CORRELATION_THRESHOLD = 0.75


def assign_correlation_groups(
    candidates: list[SignalCandidate],
    price_history: Mapping[str, pd.DataFrame] | None,
    *,
    window_days: int = DEFAULT_CORRELATION_WINDOW_DAYS,
    threshold: float = DEFAULT_HIGH_CORRELATION_THRESHOLD,
) -> dict[str, str]:
    symbols = list(dict.fromkeys(candidate.inst_id for candidate in candidates))
    if not symbols:
        return {}
    if not price_history:
        return {symbol: f"solo:{symbol}" for symbol in symbols}

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

    parent = {symbol: symbol for symbol in symbols}

    def find(symbol: str) -> str:
        while parent[symbol] != symbol:
            parent[symbol] = parent[parent[symbol]]
            symbol = parent[symbol]
        return symbol

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        root = min(left_root, right_root)
        child = right_root if root == left_root else left_root
        parent[child] = root

    for idx, left in enumerate(symbols):
        for right in symbols[idx + 1 :]:
            correlation = _return_correlation(
                returns_by_symbol.get(left),
                returns_by_symbol.get(right),
            )
            if correlation is not None and correlation >= threshold:
                union(left, right)

    groups: dict[str, list[str]] = {}
    for symbol in symbols:
        groups.setdefault(find(symbol), []).append(symbol)

    group_ids: dict[str, str] = {}
    for members in groups.values():
        sorted_members = sorted(members)
        prefix = "corr" if len(sorted_members) > 1 else "solo"
        group_id = f"{prefix}:{sorted_members[0]}"
        for symbol in sorted_members:
            group_ids[symbol] = group_id
    return group_ids


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
        df = df[df["is_closed"].astype(bool)]
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


def _return_correlation(left: pd.Series | None, right: pd.Series | None) -> float | None:
    if left is None or right is None:
        return None
    aligned = pd.concat([left, right], axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return None
    if float(aligned.iloc[:, 0].std() or 0.0) == 0.0:
        return None
    if float(aligned.iloc[:, 1].std() or 0.0) == 0.0:
        return None
    correlation = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    if pd.isna(correlation):
        return None
    return float(correlation)
