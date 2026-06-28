from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

HERE = Path(__file__).resolve().parent
DISCOVERY = HERE.parent
WORKSPACE = HERE.parents[3]
PROJECT = WORKSPACE / "1_CODE_代码" / "okx-contract-signal-system"
DATASET_ROOT = (
    WORKSPACE
    / "历史数据_保留"
    / "lightweight_history"
    / "okx_dynamic_universe_4h_20230701_20260616_v1"
)
PROTOCOL_PATH = HERE / "PROTOCOL_LOCKED_BEFORE_RESULTS.json"
RESULT_PATH = HERE / "RESULT.json"
REPORT_PATH = HERE / "RESULTS_CN.md"
HASHES_PATH = HERE / "HASHES.txt"
H26_SCRIPT = (
    WORKSPACE
    / "HISTORY_PACKAGES_20260621"
    / "RESEARCH"
    / "h26_h22_v357_equal_weight_combination_v1"
    / "run_h26_combination.py"
)
DATASET_MANIFEST_PATH = DATASET_ROOT / "DATASET_MANIFEST.json"
QUALITY_REPORT_PATH = DATASET_ROOT / "DATA_QUALITY_REPORT.json"

sys.path.insert(0, str(PROJECT / "src"))
from okx_signal_system.shadow_ensemble import (  # noqa: E402
    build_shadow_feature_frame,
    load_shadow_ensemble_config,
)


BASE_COST_ONE_WAY = 0.0011
FUNDING_RESERVE_PER_8H = 0.0001
RISK_SCALE = 0.005
FIRST_SIGNAL = pd.Timestamp("2023-12-31T00:00:00Z")
CUTOFF = pd.Timestamp("2026-06-16T12:00:00Z")
EXCLUSIONS = {"USDC-USDT-SWAP", "XAUT-USDT-SWAP"}
FIXED_SYMBOLS = [
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "XRP-USDT-SWAP",
    "DOGE-USDT-SWAP", "ADA-USDT-SWAP", "LINK-USDT-SWAP", "AVAX-USDT-SWAP",
    "LTC-USDT-SWAP", "DOT-USDT-SWAP", "BCH-USDT-SWAP", "TRX-USDT-SWAP",
    "UNI-USDT-SWAP", "ETC-USDT-SWAP", "FIL-USDT-SWAP", "ATOM-USDT-SWAP",
    "NEAR-USDT-SWAP", "OP-USDT-SWAP",
]
SEGMENTS = [
    ("S1", pd.Timestamp("2023-12-31T00:00:00Z"), pd.Timestamp("2024-09-01T00:00:00Z")),
    ("S2", pd.Timestamp("2024-09-01T00:00:00Z"), pd.Timestamp("2025-07-01T00:00:00Z")),
    ("S3", pd.Timestamp("2025-07-01T00:00:00Z"), pd.Timestamp("2026-06-17T00:00:00Z")),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
        return value if math.isfinite(value) else None
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    raise TypeError(type(value).__name__)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False, default=json_default) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def validate_inputs(protocol: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if protocol.get("status") != "LOCKED_BEFORE_RESULTS":
        raise ValueError("protocol not locked")
    if protocol.get("protocol_id") != "V357_DYNAMIC_UNIVERSE_SURVIVORSHIP_AUDIT_V1":
        raise ValueError("unexpected protocol")
    universe = protocol["point_in_time_universe"]
    frozen = (
        int(universe["candidate_panel_size"]),
        int(universe["reference_panel_size"]),
        int(universe["minimum_consecutive_closed_bars"]),
    )
    if frozen != (18, 12, 180):
        raise ValueError(f"frozen universe changed: {frozen}")
    if protocol["comparison_panels"]["fixed_survivor_panel"] != FIXED_SYMBOLS:
        raise ValueError("fixed survivor panel changed")
    manifest = read_json(DATASET_MANIFEST_PATH)
    quality = read_json(QUALITY_REPORT_PATH)
    if manifest.get("status") != "COMPLETE_VALIDATED":
        raise ValueError("dynamic dataset is not validated")
    if quality.get("status") != "PASS" or quality.get("failures"):
        raise ValueError("dynamic dataset quality failure")
    return manifest, quality


def load_rules(protocol: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    members = protocol["frozen_parent_members"]
    donchian = {
        **members["DC_n24_t50_slow"],
        "member": "DC_n24_t50_slow",
        "compression": False,
    }
    compression = {
        **members["VCB_A"],
        "member": "VCB_A",
        "compression": True,
    }
    return donchian, compression


def dynamic_dataset() -> ds.Dataset:
    schema = pa.schema(
        [
            ("instrument_name", pa.string()),
            ("bar_open_ms", pa.int64()),
            ("open", pa.float64()),
            ("high", pa.float64()),
            ("low", pa.float64()),
            ("close", pa.float64()),
            ("vol", pa.float64()),
            ("vol_quote", pa.float64()),
        ]
    )
    return ds.dataset(
        DATASET_ROOT / "data",
        format="parquet",
        partitioning="hive",
        schema=schema,
    )


def build_dynamic_membership() -> tuple[dict[pd.Timestamp, dict[str, Any]], dict[str, Any], list[str]]:
    table = dynamic_dataset().to_table(columns=["instrument_name", "bar_open_ms", "vol_quote"])
    frame = table.to_pandas()
    frame["instrument_name"] = frame["instrument_name"].astype(str)
    frame["ts"] = pd.to_datetime(frame["bar_open_ms"], unit="ms", utc=True)
    frame["vol_quote"] = pd.to_numeric(frame["vol_quote"], errors="coerce")
    if frame.duplicated(["instrument_name", "ts"]).any():
        raise ValueError("duplicate dynamic symbol/timestamp")
    symbols = sorted(frame["instrument_name"].unique().tolist())
    index = pd.date_range(frame["ts"].min(), frame["ts"].max(), freq="4h", tz="UTC")
    volume = frame.pivot(index="ts", columns="instrument_name", values="vol_quote").reindex(
        index=index, columns=symbols
    )
    presence = volume.notna()
    trailing = volume.rolling(84, min_periods=84).sum()
    eligible = (
        volume.gt(0.0).rolling(84, min_periods=84).sum().eq(84)
        & presence.rolling(180, min_periods=180).sum().eq(180)
        & presence.shift(-1).fillna(False)
    )
    records: dict[pd.Timestamp, dict[str, Any]] = {}
    used: set[str] = set()
    timeline = index[(index >= FIRST_SIGNAL) & (index <= CUTOFF)]
    for timestamp in timeline:
        current = pd.DataFrame(
            {
                "symbol": symbols,
                "volume": trailing.loc[timestamp].reindex(symbols).to_numpy(dtype=float),
                "eligible": eligible.loc[timestamp].reindex(symbols).fillna(False).to_numpy(dtype=bool),
            }
        )
        current = current.loc[
            current["eligible"]
            & np.isfinite(current["volume"])
            & current["volume"].gt(0.0)
            & ~current["symbol"].isin(EXCLUSIONS)
        ].sort_values(["volume", "symbol"], ascending=[False, True], kind="mergesort")
        if len(current) < 18:
            continue
        selected = current.head(18)["symbol"].astype(str).tolist()
        references = selected[:12]
        used.update(selected)
        records[pd.Timestamp(timestamp)] = {
            "timestamp": pd.Timestamp(timestamp),
            "selected": selected,
            "references": references,
            "eligible_count": int(len(current)),
            "fixed_overlap_count": int(len(set(selected) & set(FIXED_SYMBOLS))),
        }
    ordered = list(records.values())
    diagnostics = {
        "panel_observations": len(ordered),
        "first_panel_utc": ordered[0]["timestamp"] if ordered else None,
        "last_panel_utc": ordered[-1]["timestamp"] if ordered else None,
        "eligible_count_min": min((row["eligible_count"] for row in ordered), default=0),
        "eligible_count_median": float(np.median([row["eligible_count"] for row in ordered])) if ordered else None,
        "eligible_count_max": max((row["eligible_count"] for row in ordered), default=0),
        "mean_fixed_panel_overlap_count": float(np.mean([row["fixed_overlap_count"] for row in ordered])) if ordered else None,
        "minimum_fixed_panel_overlap_count": min((row["fixed_overlap_count"] for row in ordered), default=0),
        "maximum_fixed_panel_overlap_count": max((row["fixed_overlap_count"] for row in ordered), default=0),
        "distinct_dynamic_symbols": len(used),
        "dynamic_symbols": sorted(used),
        "panel_change_bars": int(
            sum(
                ordered[position]["selected"] != ordered[position - 1]["selected"]
                for position in range(1, len(ordered))
            )
        ),
    }
    return records, diagnostics, sorted(used)


def build_fast_feature_frame(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy().sort_values("ts").reset_index(drop=True)
    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr14"] = true_range.rolling(14, min_periods=14).mean()
    df["atr_pct"] = df["atr14"] / df["close"].replace(0.0, math.nan)
    df["atr_rank"] = df["atr_pct"].rolling(180, min_periods=120).rank(method="max", pct=True)
    df["previous_atr"] = df["atr14"].shift(1)
    df["previous_atr_rank"] = df["atr_rank"].shift(1)
    df["ema10"] = df["close"].ewm(span=10, adjust=False, min_periods=10).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    df["return_7d"] = df["close"] / df["close"].shift(42) - 1.0
    mean_volume = df["volume"].rolling(20, min_periods=20).mean()
    df["volume_ratio"] = df["volume"] / mean_volume.replace(0.0, math.nan)
    for lookback in (12, 24):
        df[f"prior_high_{lookback}"] = df["high"].shift(1).rolling(lookback, min_periods=lookback).max()
        df[f"prior_low_{lookback}"] = df["low"].shift(1).rolling(lookback, min_periods=lookback).min()
    return df


def load_selected_features(symbols: list[str]) -> dict[str, pd.DataFrame]:
    table = dynamic_dataset().to_table(
        columns=["instrument_name", "bar_open_ms", "open", "high", "low", "close", "vol"],
        filter=ds.field("instrument_name").isin(symbols),
    )
    frame = table.to_pandas()
    frame["instrument_name"] = frame["instrument_name"].astype(str)
    frame["ts"] = pd.to_datetime(frame["bar_open_ms"], unit="ms", utc=True)
    for column in ("open", "high", "low", "close", "vol"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.duplicated(["instrument_name", "ts"]).any():
        raise ValueError("duplicate selected dynamic symbol/timestamp")
    features: dict[str, pd.DataFrame] = {}
    for symbol, group in frame.groupby("instrument_name", sort=True):
        bars = group.loc[:, ["ts", "open", "high", "low", "close", "vol"]].rename(
            columns={"vol": "volume"}
        )
        built = build_fast_feature_frame(bars)
        built["ts"] = pd.to_datetime(built["ts"], utc=True)
        features[str(symbol)] = built.set_index("ts", drop=False).sort_index()
    missing = sorted(set(symbols) - set(features))
    if missing:
        raise ValueError(f"selected symbols missing features: {missing}")
    return features


def evaluate_rule(
    row: pd.Series,
    rule: dict[str, Any],
    *,
    timestamp: pd.Timestamp,
    rs: float,
    long_breadth: float,
    short_breadth: float,
) -> dict[str, Any] | None:
    atr = float(row["atr14"])
    close = float(row["close"])
    volume_ratio = float(row["volume_ratio"])
    lookback = int(rule["donchian_lookback"])
    high = row.get(f"prior_high_{lookback}")
    low = row.get(f"prior_low_{lookback}")
    if not math.isfinite(atr) or atr <= 0.0 or pd.isna(high) or pd.isna(low):
        return None
    if not math.isfinite(volume_ratio) or volume_ratio < float(rule["volume_ratio_min"]):
        return None
    if rule.get("compression"):
        previous_rank = row.get("previous_atr_rank")
        previous_atr = row.get("previous_atr")
        if pd.isna(previous_rank) or float(previous_rank) > float(rule["previous_atr_percentile_max"]):
            return None
        if bool(rule.get("current_atr_must_expand")) and (
            pd.isna(previous_atr) or atr <= float(previous_atr)
        ):
            return None
    side: str | None = None
    breadth = 0.0
    if float(row["ema10"]) > float(row["ema50"]) and close > float(high) and rs >= float(rule["rs_long_min"]):
        side = "long"
        breadth = long_breadth
    elif float(row["ema10"]) < float(row["ema50"]) and close < float(low) and rs <= float(rule["rs_short_max"]):
        side = "short"
        breadth = short_breadth
    if side is None:
        return None
    minimum = rule.get("same_direction_breadth_min")
    if minimum is not None and breadth < float(minimum):
        return None
    return {
        "member": str(rule["member"]),
        "side": side,
        "signal_time": timestamp,
        "entry_time": timestamp + pd.Timedelta(hours=4),
        "atr_at_signal": atr,
        "initial_stop_atr": float(rule["initial_stop_atr"]),
        "trailing_stop_atr": float(rule["trailing_stop_atr"]),
        "max_hold_bars": int(rule["max_hold_bars"]),
        "state": "PENDING",
        "bars_held": 0,
    }


def finish_trade(
    completed: list[dict[str, Any]],
    trade: dict[str, Any],
    *,
    symbol: str,
    exit_time: pd.Timestamp,
    exit_price: float,
    outcome: str,
) -> None:
    entry = float(trade["entry_price"])
    risk = float(trade["risk_distance"])
    gross_r = (
        (exit_price - entry) / risk
        if trade["side"] == "long"
        else (entry - exit_price) / risk
    )
    hours = max(4, int((exit_time - pd.Timestamp(trade["entry_time"])) / pd.Timedelta(hours=1)) + 4)
    cost_r = entry * (
        2.0 * BASE_COST_ONE_WAY + math.ceil(hours / 8.0) * FUNDING_RESERVE_PER_8H
    ) / risk
    completed.append(
        {
            "symbol": symbol,
            "member": trade["member"],
            "side": trade["side"],
            "signal_time": trade["signal_time"],
            "entry_time": trade["entry_time"],
            "exit_time": exit_time,
            "outcome": outcome,
            "bars_held": int(trade["bars_held"]),
            "gross_r": float(gross_r),
            "base_net_r": float(gross_r - cost_r),
            "stress_net_r": float(gross_r - 2.0 * cost_r),
        }
    )


def run_dynamic(protocol: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    membership, diagnostics, used_symbols = build_dynamic_membership()
    features = load_selected_features(used_symbols)
    rules = load_rules(protocol)
    active: dict[str, dict[str, Any]] = {}
    completed: list[dict[str, Any]] = []
    terminal_exit_symbols: Counter[str] = Counter()
    timeline = pd.date_range(FIRST_SIGNAL, CUTOFF, freq="4h", tz="UTC")

    for timestamp in timeline:
        exited: list[str] = []
        for symbol, trade in list(active.items()):
            symbol_frame = features[symbol]
            if timestamp < pd.Timestamp(trade["entry_time"]):
                continue
            if timestamp not in symbol_frame.index:
                prior = symbol_frame.loc[symbol_frame.index < timestamp]
                if trade["state"] == "ACTIVE" and not prior.empty:
                    finish_trade(
                        completed,
                        trade,
                        symbol=symbol,
                        exit_time=pd.Timestamp(prior.index[-1]),
                        exit_price=float(prior.iloc[-1]["close"]),
                        outcome="TERMINAL_DATA_EXIT",
                    )
                    terminal_exit_symbols[symbol] += 1
                exited.append(symbol)
                continue
            bar = symbol_frame.loc[timestamp]
            if trade["state"] == "PENDING":
                entry_price = float(bar["open"])
                risk_distance = float(trade["atr_at_signal"]) * float(trade["initial_stop_atr"])
                if not math.isfinite(entry_price) or not math.isfinite(risk_distance) or risk_distance <= 0.0:
                    exited.append(symbol)
                    continue
                trade.update(
                    {
                        "state": "ACTIVE",
                        "entry_price": entry_price,
                        "risk_distance": risk_distance,
                        "trail": entry_price - risk_distance if trade["side"] == "long" else entry_price + risk_distance,
                    }
                )
            if trade["state"] != "ACTIVE":
                continue
            if trade["bars_held"] > 0:
                previous_time = timestamp - pd.Timedelta(hours=4)
                if previous_time in symbol_frame.index:
                    previous = symbol_frame.loc[previous_time]
                    previous_atr = float(previous["atr14"])
                    if trade["side"] == "long":
                        trade["trail"] = max(
                            float(trade["trail"]),
                            float(previous["high"]) - float(trade["trailing_stop_atr"]) * previous_atr,
                        )
                    else:
                        trade["trail"] = min(
                            float(trade["trail"]),
                            float(previous["low"]) + float(trade["trailing_stop_atr"]) * previous_atr,
                        )
            high = float(bar["high"])
            low = float(bar["low"])
            stop_hit = low <= float(trade["trail"]) if trade["side"] == "long" else high >= float(trade["trail"])
            trade["bars_held"] += 1
            if stop_hit:
                finish_trade(
                    completed,
                    trade,
                    symbol=symbol,
                    exit_time=timestamp,
                    exit_price=float(trade["trail"]),
                    outcome="TRAILING_STOP",
                )
                exited.append(symbol)
            elif trade["bars_held"] >= int(trade["max_hold_bars"]):
                finish_trade(
                    completed,
                    trade,
                    symbol=symbol,
                    exit_time=timestamp,
                    exit_price=float(bar["close"]),
                    outcome="MAX_HOLD",
                )
                exited.append(symbol)
        for symbol in exited:
            active.pop(symbol, None)

        panel = membership.get(pd.Timestamp(timestamp))
        if panel is None:
            continue
        selected = list(panel["selected"])
        references = list(panel["references"])
        reference = pd.DataFrame([features[symbol].loc[timestamp] for symbol in references], index=references)
        reference_returns = pd.to_numeric(reference["return_7d"], errors="coerce").dropna()
        if len(reference_returns) != len(references):
            continue
        long_breadth = float((reference["ema10"] > reference["ema50"]).mean())
        short_breadth = float((reference["ema10"] < reference["ema50"]).mean())
        for symbol in selected:
            if symbol in active:
                continue
            row = features[symbol].loc[timestamp]
            rs = float((reference_returns <= float(row["return_7d"])).mean())
            signal = None
            for rule in rules:
                signal = evaluate_rule(
                    row,
                    rule,
                    timestamp=timestamp,
                    rs=rs,
                    long_breadth=long_breadth,
                    short_breadth=short_breadth,
                )
                if signal is not None:
                    break
            if signal is not None:
                active[symbol] = signal

    for symbol, trade in list(active.items()):
        if trade.get("state") != "ACTIVE":
            continue
        symbol_frame = features[symbol]
        available = symbol_frame.loc[
            (symbol_frame.index >= pd.Timestamp(trade["entry_time"])) & (symbol_frame.index <= CUTOFF)
        ]
        if available.empty:
            continue
        last_time = pd.Timestamp(available.index[-1])
        finish_trade(
            completed,
            trade,
            symbol=symbol,
            exit_time=last_time,
            exit_price=float(available.iloc[-1]["close"]),
            outcome="CUTOFF_EXIT",
        )

    trades = pd.DataFrame(completed)
    if not trades.empty:
        trades = trades.sort_values(["exit_time", "symbol", "member"]).reset_index(drop=True)
    diagnostics = {
        **diagnostics,
        "terminal_data_exit_count": int(sum(terminal_exit_symbols.values())),
        "terminal_data_exit_symbols": dict(sorted(terminal_exit_symbols.items())),
    }
    return trades, diagnostics


def load_fixed_history() -> pd.DataFrame:
    spec = importlib.util.spec_from_file_location("h26_v357_replay", H26_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load H26 V357 replay")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    trades = module.run_shadow_history()
    trades = trades.loc[pd.to_datetime(trades["signal_time"], utc=True) >= FIRST_SIGNAL].copy()
    for column in ("signal_time", "entry_time", "exit_time"):
        trades[column] = pd.to_datetime(trades[column], utc=True)
    return trades.sort_values(["exit_time", "symbol", "member"]).reset_index(drop=True)


def day_key(timestamp: pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(timestamp).tz_convert("UTC")
    return (timestamp - pd.Timedelta(hours=4)).floor("D") + pd.Timedelta(hours=4)


def daily_series(trades: pd.DataFrame, field: str, index: pd.DatetimeIndex) -> pd.Series:
    values = pd.Series(0.0, index=index, dtype=float)
    for row in trades.itertuples(index=False):
        key = day_key(pd.Timestamp(row.exit_time))
        if key in values.index:
            values.loc[key] += RISK_SCALE * float(getattr(row, field))
    return values


def metrics(values: pd.Series) -> dict[str, Any]:
    array = values.astype(float).fillna(0.0).to_numpy(dtype=float)
    gains = array[array > 0.0]
    losses = array[array < 0.0]
    positive = float(gains.sum())
    negative = float(-losses.sum())
    equity = np.cumprod(1.0 + array)
    peaks = np.maximum.accumulate(np.concatenate([[1.0], equity]))[1:]
    drawdown = equity / peaks - 1.0 if len(equity) else np.array([], dtype=float)
    return {
        "periods": int(len(array)),
        "net_return_sum": float(array.sum()),
        "profit_factor": positive / negative if negative > 0.0 else None,
        "total_return": float(equity[-1] - 1.0) if len(equity) else 0.0,
        "maximum_drawdown": float(drawdown.min()) if len(drawdown) else 0.0,
        "positive_period_fraction": float(np.mean(array > 0.0)) if len(array) else 0.0,
    }


def trade_metrics(trades: pd.DataFrame, field: str) -> dict[str, Any]:
    values = pd.to_numeric(trades[field], errors="coerce").dropna().to_numpy(dtype=float)
    gains = values[values > 0.0]
    losses = values[values < 0.0]
    return {
        "trades": int(len(values)),
        "profit_factor": float(gains.sum() / -losses.sum()) if len(losses) and -losses.sum() > 0 else None,
        "win_rate": float(np.mean(values > 0.0)) if len(values) else 0.0,
        "mean_r": float(np.mean(values)) if len(values) else 0.0,
        "payoff_ratio": float(np.mean(gains) / -np.mean(losses)) if len(gains) and len(losses) else None,
    }


def segment_metrics(values: pd.Series) -> dict[str, dict[str, Any]]:
    return {
        name: metrics(values.loc[(values.index >= start) & (values.index < end)])
        for name, start, end in SEGMENTS
    }


def positive_share(trades: pd.DataFrame, group: pd.Series) -> tuple[float, str | None, dict[str, float]]:
    contribution = (RISK_SCALE * pd.to_numeric(trades["base_net_r"], errors="coerce")).groupby(group).sum()
    positive = contribution[contribution > 0.0]
    if positive.empty or float(positive.sum()) <= 0.0:
        return 0.0, None, {str(key): float(value) for key, value in contribution.items()}
    leader = str(positive.idxmax())
    share = float(positive.max() / positive.sum())
    return share, leader, {str(key): float(value) for key, value in contribution.items()}


def panel_result(trades: pd.DataFrame, index: pd.DatetimeIndex) -> dict[str, Any]:
    base = daily_series(trades, "base_net_r", index)
    stress = daily_series(trades, "stress_net_r", index)
    base_segments = segment_metrics(base)
    stress_segments = segment_metrics(stress)
    symbol_share, symbol, symbol_contributions = positive_share(trades, trades["symbol"].astype(str))
    months = pd.to_datetime(trades["exit_time"], utc=True).dt.strftime("%Y-%m")
    month_share, month, month_contributions = positive_share(trades, months)
    return {
        "trade_count": int(len(trades)),
        "member_counts": {str(key): int(value) for key, value in trades["member"].value_counts().items()},
        "side_counts": {str(key): int(value) for key, value in trades["side"].value_counts().items()},
        "outcome_counts": {str(key): int(value) for key, value in trades["outcome"].value_counts().items()},
        "base": {
            "daily_metrics": metrics(base),
            "trade_metrics": trade_metrics(trades, "base_net_r"),
            "segments": base_segments,
            "positive_segment_count": sum(float(item["net_return_sum"]) > 0.0 for item in base_segments.values()),
            "maximum_single_symbol_positive_net_contribution_share": symbol_share,
            "maximum_symbol": symbol,
            "symbol_contributions": symbol_contributions,
            "maximum_single_month_positive_net_contribution_share": month_share,
            "maximum_month": month,
            "month_contributions": month_contributions,
        },
        "stress": {
            "daily_metrics": metrics(stress),
            "trade_metrics": trade_metrics(trades, "stress_net_r"),
            "segments": stress_segments,
            "positive_segment_count": sum(float(item["net_return_sum"]) > 0.0 for item in stress_segments.values()),
        },
    }


def write_report(result: dict[str, Any]) -> None:
    fixed = result["fixed_survivor_panel"]
    dynamic = result["dynamic_point_in_time_panel"]
    diagnostics = result["universe_diagnostics"]
    checks = result["fixed_gate_checks"]

    def line(panel: dict[str, Any], level: str) -> str:
        daily = panel[level]["daily_metrics"]
        trade = panel[level]["trade_metrics"]
        return (
            f"| {'基础' if level == 'base' else '压力'} | {daily['profit_factor']:.4f} | "
            f"{trade['profit_factor']:.4f} | {trade['win_rate']:.2%} | {trade['payoff_ratio']:.4f} | "
            f"{daily['total_return']:.2%} | {daily['maximum_drawdown']:.2%} |"
        )

    failed = [name for name, passed in checks.items() if not passed]
    report = f"""# V357动态交易宇宙存活者偏差审计

状态：`{result['status']}`

决定：`{result['decision']}`

## 一、冻结边界

本轮只把V357当前成熟存活币名单替换为真实点时动态币池。DC与VCB两个成员、EMA、Donchian、相对强弱、广度、成交量、ATR、止损、最长持有期、成本和每笔0.5%风险归一化全部保持不变。

动态面板在每根信号K线只使用当时已经闭合的数据：连续180根4小时K线、过去84根`vol_quote`全部严格大于0，并按该84根成交额选择前18币；其中成交额最高12币用于原有横截面相对强弱和市场广度。

## 二、动态宇宙

- 面板时点：{diagnostics['panel_observations']}；
- 动态历史币种：{diagnostics['distinct_dynamic_symbols']}；
- 合格币种数：最少 {diagnostics['eligible_count_min']}，中位数 {diagnostics['eligible_count_median']:.1f}，最多 {diagnostics['eligible_count_max']}；
- 与固定18币平均重合：{diagnostics['mean_fixed_panel_overlap_count']:.2f}；
- 最低/最高重合：{diagnostics['minimum_fixed_panel_overlap_count']} / {diagnostics['maximum_fixed_panel_overlap_count']}；
- 动态名单变化K线：{diagnostics['panel_change_bars']}；
- 终止数据退出：{diagnostics['terminal_data_exit_count']}。

## 三、固定18币同规则重放

交易数：{fixed['trade_count']}。

| 成本 | 日收益PF | 逐笔PF | 胜率 | 盈亏比 | 总收益 | 最大回撤 |
|---|---:|---:|---:|---:|---:|---:|
{line(fixed, 'base')}
{line(fixed, 'stress')}

## 四、真实点时动态18币

交易数：{dynamic['trade_count']}。

| 成本 | 日收益PF | 逐笔PF | 胜率 | 盈亏比 | 总收益 | 最大回撤 |
|---|---:|---:|---:|---:|---:|---:|
{line(dynamic, 'base')}
{line(dynamic, 'stress')}

基础/压力正收益阶段：{dynamic['base']['positive_segment_count']} / {dynamic['stress']['positive_segment_count']}（各3段）。

最大单币正贡献占比：{dynamic['base']['maximum_single_symbol_positive_net_contribution_share']:.2%}（{dynamic['base']['maximum_symbol']}）。

最大单月正贡献占比：{dynamic['base']['maximum_single_month_positive_net_contribution_share']:.2%}（{dynamic['base']['maximum_month']}）。

## 五、固定门禁

"""
    for name, passed in checks.items():
        report += f"- {'通过' if passed else '失败'}：`{name}`\n"
    report += f"""

失败门禁：{', '.join(failed) if failed else '无'}。

## 六、正式结论

`{result['decision']}`

无论结果如何，禁止修改V357两个成员、参数、动态币数、参照币数、成交额窗口、最短历史、成本、日期或币种进行营救；本轮不会自动改变A级信号、飞书、杠杆或下单边界。
"""
    atomic_text(REPORT_PATH, report)


def run(*, write_outputs: bool = True) -> dict[str, Any]:
    protocol = read_json(PROTOCOL_PATH)
    manifest, quality = validate_inputs(protocol)
    dynamic_trades, diagnostics = run_dynamic(protocol)
    fixed_trades = load_fixed_history()
    if dynamic_trades.empty or fixed_trades.empty:
        raise RuntimeError("empty fixed or dynamic V357 replay")
    first_day = max(
        day_key(pd.Timestamp(dynamic_trades["exit_time"].min())),
        day_key(pd.Timestamp(fixed_trades["exit_time"].min())),
    )
    last_day = min(
        day_key(pd.Timestamp(dynamic_trades["exit_time"].max())),
        day_key(pd.Timestamp(fixed_trades["exit_time"].max())),
    )
    index = pd.date_range(first_day, last_day, freq="1D", tz="UTC")
    dynamic_trades = dynamic_trades.loc[
        (pd.to_datetime(dynamic_trades["exit_time"], utc=True) >= first_day - pd.Timedelta(hours=4))
        & (pd.to_datetime(dynamic_trades["exit_time"], utc=True) <= last_day + pd.Timedelta(hours=20))
    ].copy()
    fixed_trades = fixed_trades.loc[
        (pd.to_datetime(fixed_trades["exit_time"], utc=True) >= first_day - pd.Timedelta(hours=4))
        & (pd.to_datetime(fixed_trades["exit_time"], utc=True) <= last_day + pd.Timedelta(hours=20))
    ].copy()
    fixed = panel_result(fixed_trades, index)
    dynamic = panel_result(dynamic_trades, index)
    gates = protocol["fixed_gates"]
    base_daily = dynamic["base"]["daily_metrics"]
    stress_daily = dynamic["stress"]["daily_metrics"]
    checks = {
        "dynamic_base_profit_factor_min": float(base_daily["profit_factor"] or 0.0) >= float(gates["dynamic_base_profit_factor_min"]),
        "dynamic_stress_profit_factor_min": float(stress_daily["profit_factor"] or 0.0) >= float(gates["dynamic_stress_profit_factor_min"]),
        "dynamic_base_total_return_gt_zero": float(base_daily["total_return"]) > 0.0,
        "dynamic_stress_total_return_gt_zero": float(stress_daily["total_return"]) > 0.0,
        "dynamic_base_maximum_drawdown_abs_max": abs(float(base_daily["maximum_drawdown"])) <= float(gates["dynamic_base_maximum_drawdown_abs_max"]),
        "dynamic_stress_maximum_drawdown_abs_max": abs(float(stress_daily["maximum_drawdown"])) <= float(gates["dynamic_stress_maximum_drawdown_abs_max"]),
        "dynamic_positive_base_segments_min": int(dynamic["base"]["positive_segment_count"]) >= int(gates["dynamic_positive_base_segments_min"]),
        "dynamic_positive_stress_segments_min": int(dynamic["stress"]["positive_segment_count"]) >= int(gates["dynamic_positive_stress_segments_min"]),
        "maximum_single_symbol_positive_net_contribution_share": float(dynamic["base"]["maximum_single_symbol_positive_net_contribution_share"]) <= float(gates["maximum_single_symbol_positive_net_contribution_share"]),
        "maximum_single_month_positive_net_contribution_share": float(dynamic["base"]["maximum_single_month_positive_net_contribution_share"]) <= float(gates["maximum_single_month_positive_net_contribution_share"]),
        "minimum_completed_dynamic_trades": int(dynamic["trade_count"]) >= int(gates["minimum_completed_dynamic_trades"]),
        "minimum_mean_fixed_panel_overlap_count": float(diagnostics["mean_fixed_panel_overlap_count"] or 0.0) >= float(gates["minimum_mean_fixed_panel_overlap_count"]),
    }
    all_pass = bool(all(checks.values()))
    positive_support = (
        float(base_daily["total_return"]) > 0.0
        and float(stress_daily["total_return"]) > 0.0
        and float(base_daily["profit_factor"] or 0.0) >= 1.0
        and float(stress_daily["profit_factor"] or 0.0) >= 1.0
    )
    rules = protocol["decision_rules"]
    if all_pass:
        decision = rules["all_gates_pass"]
    elif positive_support:
        decision = rules["positive_but_gate_failure"]
    else:
        decision = rules["nonpositive_or_profit_factor_below_one"]
    result = {
        "schema": "v357_dynamic_universe_survivorship_audit_result_v1",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": sha256(PROTOCOL_PATH),
        "script_sha256": sha256(Path(__file__)),
        "dataset_manifest_sha256": sha256(DATASET_MANIFEST_PATH),
        "status": "COMPLETE",
        "decision": decision,
        "all_fixed_gates_pass": all_pass,
        "fixed_gate_checks": checks,
        "dataset_summary": {
            "dataset_id": manifest["dataset_id"],
            "quality_status": quality["status"],
            "rows": manifest["storage"]["rows"],
            "unique_instruments": manifest["coverage"]["unique_instruments"],
        },
        "common_metrics": {
            "first_day_utc": first_day,
            "last_day_utc": last_day,
            "daily_periods": len(index),
        },
        "universe_diagnostics": diagnostics,
        "fixed_survivor_panel": fixed,
        "dynamic_point_in_time_panel": dynamic,
        "production_effect": "NONE",
        "formal_signal_effect": "NONE",
        "automatic_promotion": False,
        "interpretation_boundary": "This is a survivorship audit of the released frozen V357 reconstruction, not a new strategy or permission to redesign V357.",
    }
    if write_outputs:
        atomic_json(RESULT_PATH, result)
        write_report(result)
        atomic_text(
            HASHES_PATH,
            "\n".join(
                [
                    f"{sha256(PROTOCOL_PATH)}  {PROTOCOL_PATH.name}",
                    f"{sha256(Path(__file__))}  {Path(__file__).name}",
                    f"{sha256(RESULT_PATH)}  {RESULT_PATH.name}",
                    f"{sha256(REPORT_PATH)}  {REPORT_PATH.name}",
                    f"{sha256(DATASET_MANIFEST_PATH)}  DATASET_MANIFEST.json",
                ]
            )
            + "\n",
        )
    return result


def compact_summary(result: dict[str, Any]) -> dict[str, Any]:
    fixed = result["fixed_survivor_panel"]
    dynamic = result["dynamic_point_in_time_panel"]
    return {
        "status": result["status"],
        "decision": result["decision"],
        "all_fixed_gates_pass": result["all_fixed_gates_pass"],
        "fixed_gate_checks": result["fixed_gate_checks"],
        "common_metrics": result["common_metrics"],
        "universe_diagnostics": result["universe_diagnostics"],
        "fixed": {
            "trade_count": fixed["trade_count"],
            "member_counts": fixed["member_counts"],
            "base_daily": fixed["base"]["daily_metrics"],
            "base_trade": fixed["base"]["trade_metrics"],
            "stress_daily": fixed["stress"]["daily_metrics"],
            "stress_trade": fixed["stress"]["trade_metrics"],
        },
        "dynamic": {
            "trade_count": dynamic["trade_count"],
            "member_counts": dynamic["member_counts"],
            "side_counts": dynamic["side_counts"],
            "outcome_counts": dynamic["outcome_counts"],
            "base_daily": dynamic["base"]["daily_metrics"],
            "base_trade": dynamic["base"]["trade_metrics"],
            "stress_daily": dynamic["stress"]["daily_metrics"],
            "stress_trade": dynamic["stress"]["trade_metrics"],
            "base_positive_segments": dynamic["base"]["positive_segment_count"],
            "stress_positive_segments": dynamic["stress"]["positive_segment_count"],
            "max_symbol_share": dynamic["base"]["maximum_single_symbol_positive_net_contribution_share"],
            "max_symbol": dynamic["base"]["maximum_symbol"],
            "max_month_share": dynamic["base"]["maximum_single_month_positive_net_contribution_share"],
            "max_month": dynamic["base"]["maximum_month"],
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--compact", action="store_true")
    arguments = parser.parse_args()
    output = run(write_outputs=not arguments.no_write)
    if arguments.compact:
        output = compact_summary(output)
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False, default=json_default))
