from __future__ import annotations

"""Frozen research-only 4h shadow ensemble.

The module reads public closed candles, writes an isolated SQLite database and a
status JSON file, and never imports the formal lifecycle, notification, account,
or order modules.
"""

import asyncio
import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Literal

import pandas as pd
import yaml

from okx_signal_system.config import project_paths
from okx_signal_system.io_atomic import write_text_atomic

ShadowSide = Literal["long", "short"]

FROZEN_REFERENCE_SYMBOLS: tuple[str, ...] = (
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "XRP-USDT-SWAP",
    "BCH-USDT-SWAP",
    "LINK-USDT-SWAP",
    "LTC-USDT-SWAP",
    "UNI-USDT-SWAP",
    "ARB-USDT-SWAP",
    "ETC-USDT-SWAP",
    "APT-USDT-SWAP",
    "ATOM-USDT-SWAP",
)
PROTOCOL_VERSION = "v357-shadow-ensemble-research-protocol-2"
TERMINAL_STATES = {"STOP_REACHED", "TIMEOUT_RESULT", "CENSORED"}


@dataclass(frozen=True)
class ShadowEnsembleConfig:
    enabled: bool = True
    research_only: bool = True
    source_timeframe: str = "15m"
    evaluation_timeframe: str = "4h"
    minimum_cross_section_symbols: int = 12
    reference_symbols: tuple[str, ...] = FROZEN_REFERENCE_SYMBOLS
    history_limit_15m: int = 3500
    atr_window: int = 14
    atr_percentile_lookback: int = 180
    volume_ratio_lookback: int = 20
    relative_strength_lookback_4h: int = 42
    status_file: str = "shadow_ensemble_status.json"
    sqlite_file: str = "shadow_ensemble.sqlite3"
    desktop_display_enabled: bool = True
    research_notification_enabled: bool = False
    candidate_file: str = "config/research_candidates/v357_shadow_ensemble_candidate.json"
    donchian_candidate_file: str = "config/research_candidates/v357_4h_donchian_shadow_candidate.json"


@dataclass(frozen=True)
class ShadowObservation:
    observation_id: str
    candidate_id: str
    member: str
    symbol: str
    side: ShadowSide
    signal_time: str
    detected_at: str
    reference_close: float
    atr_at_signal: float
    initial_stop_atr: float
    trailing_stop_atr: float
    max_hold_bars: int
    relative_strength_percentile: float
    volume_ratio: float
    breadth: float
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reason_codes"] = list(self.reason_codes)
        payload.update(
            {
                "tier": "SHADOW_A_MINUS",
                "research_only": True,
                "isolated_from_formal_runtime": True,
                "entry_model": "NEXT_4H_BAR_OPEN",
            }
        )
        return payload


@dataclass(frozen=True)
class ShadowScanResult:
    status: str
    latest_closed_4h: str | None
    eligible_symbols: int
    new_observations: tuple[ShadowObservation, ...]
    skipped_symbols: tuple[str, ...]
    active_count: int
    pending_entry_count: int
    closed_count: int
    summary: dict[str, Any]

    @property
    def new_signals(self) -> tuple[ShadowObservation, ...]:
        """Compatibility alias used by the desktop integration."""
        return self.new_observations


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_text(value: Any) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat()


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def load_shadow_ensemble_config(path: str | Path | None = None) -> ShadowEnsembleConfig:
    paths = project_paths()
    config_path = Path(path) if path else paths.config_dir / "shadow_ensemble.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    values = raw.get("shadow_ensemble", raw)
    if not isinstance(values, dict):
        raise ValueError("shadow_ensemble config must be a mapping")

    def candidate_path(key: str, default: str) -> str:
        value = Path(str(values.get(key, default)))
        return str(value if value.is_absolute() else paths.root / value)

    return ShadowEnsembleConfig(
        enabled=_as_bool(values.get("enabled"), True),
        research_only=_as_bool(values.get("research_only"), True),
        source_timeframe=str(values.get("source_timeframe", "15m")),
        evaluation_timeframe=str(values.get("evaluation_timeframe", "4h")),
        minimum_cross_section_symbols=int(values.get("minimum_cross_section_symbols", 12)),
        reference_symbols=tuple(str(item) for item in values.get("reference_symbols", FROZEN_REFERENCE_SYMBOLS)),
        history_limit_15m=int(values.get("history_limit_15m", 3500)),
        atr_window=int(values.get("atr_window", 14)),
        atr_percentile_lookback=int(values.get("atr_percentile_lookback", 180)),
        volume_ratio_lookback=int(values.get("volume_ratio_lookback", 20)),
        relative_strength_lookback_4h=int(values.get("relative_strength_lookback_4h", 42)),
        status_file=str(values.get("status_file", "shadow_ensemble_status.json")),
        sqlite_file=str(values.get("sqlite_file", "shadow_ensemble.sqlite3")),
        desktop_display_enabled=_as_bool(values.get("desktop_display_enabled"), True),
        research_notification_enabled=_as_bool(values.get("research_notification_enabled"), False),
        candidate_file=candidate_path(
            "candidate_file", "config/research_candidates/v357_shadow_ensemble_candidate.json"
        ),
        donchian_candidate_file=candidate_path(
            "donchian_candidate_file",
            "config/research_candidates/v357_4h_donchian_shadow_candidate.json",
        ),
    )


def strict_resample_closed_15m_to_4h(frame: pd.DataFrame) -> pd.DataFrame:
    """Build close-labelled 4h bars from exactly sixteen consecutive closed 15m bars."""
    required = {"ts", "open", "high", "low", "close", "volume", "is_closed"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing required candle columns: {sorted(missing)}")
    if frame.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "is_closed"])

    df = frame.loc[:, list(required)].copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
    if df["ts"].duplicated().any():
        raise ValueError("duplicate 15m timestamps")
    df = df[df["is_closed"].map(lambda value: _as_bool(value, False))]
    if df.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "is_closed"])

    df["bucket"] = df["ts"].dt.floor("4h")
    offsets = pd.to_timedelta(range(0, 240, 15), unit="m")
    rows: list[dict[str, Any]] = []
    for bucket, group in df.groupby("bucket", sort=True):
        group = group.sort_values("ts")
        if len(group) != 16:
            continue
        expected = pd.DatetimeIndex(bucket + offsets)
        if not pd.DatetimeIndex(group["ts"]).equals(expected):
            continue
        rows.append(
            {
                "ts": bucket + pd.Timedelta(hours=4),
                "open": float(group["open"].iloc[0]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group["close"].iloc[-1]),
                "volume": float(group["volume"].sum()),
                "is_closed": True,
            }
        )
    return pd.DataFrame(rows)


def _rolling_last_percentile(values: pd.Series, lookback: int) -> pd.Series:
    minimum = max(30, int(lookback * 2 / 3))

    def rank(array: Any) -> float:
        series = pd.Series(array).dropna()
        if series.empty:
            return math.nan
        return float((series <= float(series.iloc[-1])).mean())

    return values.rolling(lookback, min_periods=minimum).apply(rank, raw=False)


def build_shadow_feature_frame(frame_4h: pd.DataFrame, config: ShadowEnsembleConfig) -> pd.DataFrame:
    df = frame_4h.copy().sort_values("ts").reset_index(drop=True)
    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr14"] = true_range.rolling(config.atr_window, min_periods=config.atr_window).mean()
    df["atr_pct"] = df["atr14"] / df["close"].replace(0.0, math.nan)
    df["atr_rank"] = _rolling_last_percentile(df["atr_pct"], config.atr_percentile_lookback)
    df["previous_atr"] = df["atr14"].shift(1)
    df["previous_atr_rank"] = df["atr_rank"].shift(1)
    df["ema10"] = df["close"].ewm(span=10, adjust=False, min_periods=10).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    df["return_7d"] = df["close"] / df["close"].shift(config.relative_strength_lookback_4h) - 1.0
    mean_volume = df["volume"].rolling(
        config.volume_ratio_lookback,
        min_periods=config.volume_ratio_lookback,
    ).mean()
    df["volume_ratio"] = df["volume"] / mean_volume.replace(0.0, math.nan)
    for lookback in (12, 24):
        df[f"prior_high_{lookback}"] = df["high"].shift(1).rolling(lookback, min_periods=lookback).max()
        df[f"prior_low_{lookback}"] = df["low"].shift(1).rolling(lookback, min_periods=lookback).min()
    return df


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


class ShadowEnsembleStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shadow_observations (
                    observation_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    member TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    signal_time TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    reference_close REAL NOT NULL,
                    atr_at_signal REAL NOT NULL,
                    initial_stop_atr REAL NOT NULL,
                    trailing_stop_atr REAL NOT NULL,
                    max_hold_bars INTEGER NOT NULL,
                    relative_strength_percentile REAL NOT NULL,
                    volume_ratio REAL NOT NULL,
                    breadth REAL NOT NULL,
                    reason_codes_json TEXT NOT NULL,
                    is_warmup INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL,
                    entry_time TEXT,
                    entry_price REAL,
                    stop_price REAL,
                    trail_price REAL,
                    exit_time TEXT,
                    exit_price REAL,
                    outcome TEXT,
                    bars_held INTEGER NOT NULL DEFAULT 0,
                    mfe_r REAL,
                    mae_r REAL,
                    gross_r REAL,
                    estimated_net_r REAL,
                    processed_through TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shadow_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_shadow_symbol_state ON shadow_observations(symbol, state)"
            )

    def insert(self, observation: ShadowObservation, *, warmup: bool) -> bool:
        now = _now_text()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO shadow_observations (
                    observation_id, candidate_id, member, symbol, side,
                    signal_time, detected_at, reference_close, atr_at_signal,
                    initial_stop_atr, trailing_stop_atr, max_hold_bars,
                    relative_strength_percentile, volume_ratio, breadth,
                    reason_codes_json, is_warmup, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_ENTRY', ?, ?)
                """,
                (
                    observation.observation_id,
                    observation.candidate_id,
                    observation.member,
                    observation.symbol,
                    observation.side,
                    observation.signal_time,
                    observation.detected_at,
                    observation.reference_close,
                    observation.atr_at_signal,
                    observation.initial_stop_atr,
                    observation.trailing_stop_atr,
                    observation.max_hold_bars,
                    observation.relative_strength_percentile,
                    observation.volume_ratio,
                    observation.breadth,
                    json.dumps(list(observation.reason_codes), ensure_ascii=False),
                    1 if warmup else 0,
                    now,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def has_open(self, symbol: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM shadow_observations WHERE symbol=? AND state IN ('PENDING_ENTRY','ACTIVE') LIMIT 1",
                (symbol,),
            ).fetchone()
        return row is not None

    def open_records(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM shadow_observations WHERE state IN ('PENDING_ENTRY','ACTIVE') ORDER BY signal_time"
            ).fetchall()
        return [dict(row) for row in rows]

    def update(self, observation_id: str, **values: Any) -> None:
        if not values:
            return
        values["updated_at"] = _now_text()
        assignments = ", ".join(f"{name}=?" for name in values)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE shadow_observations SET {assignments} WHERE observation_id=?",
                [*values.values(), observation_id],
            )

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM shadow_meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO shadow_meta(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, _now_text()),
            )

    def summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            counts = {
                str(row["state"]): int(row["count"])
                for row in conn.execute(
                    "SELECT state, COUNT(*) AS count FROM shadow_observations GROUP BY state"
                ).fetchall()
            }
            completed = conn.execute(
                """
                SELECT estimated_net_r FROM shadow_observations
                WHERE is_warmup=0 AND state IN ('STOP_REACHED','TIMEOUT_RESULT')
                  AND estimated_net_r IS NOT NULL
                """
            ).fetchall()
            latest = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM shadow_observations WHERE is_warmup=0 ORDER BY signal_time DESC LIMIT 10"
                ).fetchall()
            ]
            warmup = int(
                conn.execute("SELECT COUNT(*) FROM shadow_observations WHERE is_warmup=1").fetchone()[0]
            )
        returns = [float(row["estimated_net_r"]) for row in completed]
        gains = sum(value for value in returns if value > 0)
        losses = -sum(value for value in returns if value < 0)
        return {
            "total": sum(counts.values()),
            "pending_entry": counts.get("PENDING_ENTRY", 0),
            "active": counts.get("ACTIVE", 0),
            "closed": sum(counts.get(state, 0) for state in TERMINAL_STATES),
            "state_counts": counts,
            "estimated_net_r": sum(returns),
            "estimated_profit_factor": gains / losses if losses > 0 else None,
            "latest_observations": latest,
            "warmup_records": warmup,
            "warmup_completed_at": self.get_meta("warmup_completed_at"),
        }


class ShadowEnsembleService:
    def __init__(
        self,
        *,
        candle_loader: Callable[[str, int], Awaitable[pd.DataFrame]],
        config: ShadowEnsembleConfig | None = None,
        store: ShadowEnsembleStore | None = None,
    ):
        self.config = config or load_shadow_ensemble_config()
        self._candle_loader = candle_loader
        paths = project_paths()
        self.store = store or ShadowEnsembleStore(paths.output_dir / self.config.sqlite_file)
        self.status_path = paths.output_dir / self.config.status_file
        self.ensemble_candidate = self._read_candidate(Path(self.config.candidate_file), "okx_shadow_ensemble_candidate_v1")
        self.donchian_candidate = self._read_candidate(
            Path(self.config.donchian_candidate_file), "okx_signal_shadow_candidate_v1"
        )
        self.candidate_id = str(self.ensemble_candidate["candidate_id"])
        self.candidate_sha256 = hashlib.sha256(Path(self.config.candidate_file).read_bytes()).hexdigest()
        self._signal_symbols: tuple[str, ...] = ()
        self._validate_protocol()

    @staticmethod
    def _read_candidate(path: Path, schema: str) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != schema:
            raise ValueError(f"invalid frozen research candidate: {path}")
        return data

    def _validate_protocol(self) -> None:
        if not self.config.research_only:
            raise ValueError("shadow ensemble must remain research-only")
        if self.config.research_notification_enabled:
            raise ValueError("research notification must remain disabled")
        if self.config.reference_symbols != FROZEN_REFERENCE_SYMBOLS:
            raise ValueError("reference universe is frozen")
        if self.config.minimum_cross_section_symbols != len(FROZEN_REFERENCE_SYMBOLS):
            raise ValueError("all frozen reference symbols are required")
        expected = {
            "atr_window": 14,
            "atr_percentile_lookback": 180,
            "volume_ratio_lookback": 20,
            "relative_strength_lookback_4h": 42,
        }
        for name, value in expected.items():
            if getattr(self.config, name) != value:
                raise ValueError(f"frozen research parameter changed: {name}")
        for candidate in (self.ensemble_candidate, self.donchian_candidate):
            if candidate.get("blind_data_opened") is not False:
                raise ValueError("frozen blind-data declaration changed")
            if "RESEARCH" not in str(candidate.get("status", "")).upper():
                raise ValueError("candidate is not marked research-only")

    def _rules(self) -> tuple[dict[str, Any], dict[str, Any]]:
        don = dict(self.donchian_candidate["signal_rules"])
        don.update({"member": "DC_n24_t50_slow", "compression": False})
        vcb = dict(self.ensemble_candidate["volatility_compression"])
        vcb.update({"member": "VCB_A", "compression": True})
        return don, vcb

    async def scan(self, symbols: Iterable[str]) -> ShadowScanResult:
        signal_symbols = tuple(dict.fromkeys(str(symbol) for symbol in symbols))
        self._signal_symbols = signal_symbols
        load_symbols = tuple(dict.fromkeys([*signal_symbols, *self.config.reference_symbols]))
        if not self.config.enabled:
            return self._finish("disabled", None, {}, (), signal_symbols)

        loaded = await asyncio.gather(
            *(self._candle_loader(symbol, self.config.history_limit_15m) for symbol in load_symbols),
            return_exceptions=True,
        )
        features: dict[str, pd.DataFrame] = {}
        skipped: list[str] = []
        for symbol, raw in zip(load_symbols, loaded):
            if isinstance(raw, Exception):
                skipped.append(symbol)
                continue
            try:
                bars_4h = strict_resample_closed_15m_to_4h(raw)
                if len(bars_4h) < self.config.atr_percentile_lookback:
                    skipped.append(symbol)
                    continue
                features[symbol] = build_shadow_feature_frame(bars_4h, self.config)
            except Exception:
                skipped.append(symbol)

        missing_refs = [symbol for symbol in self.config.reference_symbols if symbol not in features]
        if missing_refs:
            return self._finish(
                "incomplete_frozen_reference_universe",
                None,
                features,
                (),
                tuple(sorted(set(skipped + missing_refs))),
            )
        reference_latest = [pd.Timestamp(features[symbol]["ts"].iloc[-1]) for symbol in self.config.reference_symbols]
        if len(set(reference_latest)) != 1:
            return self._finish(
                "frozen_reference_bar_misaligned",
                None,
                features,
                (),
                tuple(sorted(set(skipped))),
            )
        latest_ts = reference_latest[0]

        if self.store.get_meta("warmup_completed_at") is None:
            self._warm_start(features, latest_ts)
        self._advance_open_records(features)
        observations, eligible, long_breadth, short_breadth = self._evaluate_timestamp(
            features, latest_ts, warmup=False
        )
        self.store.set_meta("last_evaluated_4h", _utc_text(latest_ts))
        summary = self.store.summary()
        summary.update(
            {
                "long_breadth": long_breadth,
                "short_breadth": short_breadth,
                "protocol_version": PROTOCOL_VERSION,
            }
        )
        result = ShadowScanResult(
            status="running",
            latest_closed_4h=_utc_text(latest_ts),
            eligible_symbols=eligible,
            new_observations=tuple(observations),
            skipped_symbols=tuple(sorted(set(skipped))),
            active_count=int(summary.get("active", 0)),
            pending_entry_count=int(summary.get("pending_entry", 0)),
            closed_count=int(summary.get("closed", 0)),
            summary=summary,
        )
        self._write_status(result)
        return result

    def _finish(
        self,
        status: str,
        latest: pd.Timestamp | None,
        features: dict[str, pd.DataFrame],
        observations: Iterable[ShadowObservation],
        skipped: Iterable[str],
    ) -> ShadowScanResult:
        summary = self.store.summary()
        result = ShadowScanResult(
            status=status,
            latest_closed_4h=_utc_text(latest) if latest is not None else None,
            eligible_symbols=len(features),
            new_observations=tuple(observations),
            skipped_symbols=tuple(skipped),
            active_count=int(summary.get("active", 0)),
            pending_entry_count=int(summary.get("pending_entry", 0)),
            closed_count=int(summary.get("closed", 0)),
            summary=summary,
        )
        self._write_status(result)
        return result

    def _warm_start(self, features: dict[str, pd.DataFrame], latest_ts: pd.Timestamp) -> None:
        max_hold = max(int(rule["max_hold_bars"]) for rule in self._rules())
        common = set(pd.Timestamp(value) for value in features[self.config.reference_symbols[0]]["ts"])
        for symbol in self.config.reference_symbols[1:]:
            common.intersection_update(pd.Timestamp(value) for value in features[symbol]["ts"])
        replay = sorted(value for value in common if value < latest_ts)[-(max_hold + 1):]
        for timestamp in replay:
            truncated = {
                symbol: frame[frame["ts"] <= timestamp].reset_index(drop=True)
                for symbol, frame in features.items()
            }
            self._advance_open_records(truncated)
            self._evaluate_timestamp(truncated, timestamp, warmup=True)
        self.store.set_meta("warmup_completed_at", _now_text())
        self.store.set_meta("warmup_latest_replayed_4h", _utc_text(replay[-1]) if replay else "none")

    def _evaluate_timestamp(
        self,
        features: dict[str, pd.DataFrame],
        timestamp: pd.Timestamp,
        *,
        warmup: bool,
    ) -> tuple[list[ShadowObservation], int, float, float]:
        rows: list[dict[str, Any]] = []
        required = ["atr14", "atr_rank", "ema10", "ema50", "return_7d", "volume_ratio"]
        for symbol, frame in features.items():
            matches = frame.index[frame["ts"] == timestamp]
            if len(matches) != 1:
                continue
            row = frame.loc[matches[0]]
            if any(pd.isna(row.get(column)) for column in required):
                continue
            rows.append({"symbol": symbol, **row.to_dict()})
        snapshot = pd.DataFrame(rows)
        if snapshot.empty:
            return [], 0, 0.0, 0.0
        reference = snapshot[snapshot["symbol"].isin(self.config.reference_symbols)].copy()
        if set(reference["symbol"]) != set(self.config.reference_symbols):
            return [], len(snapshot), 0.0, 0.0
        reference_returns = pd.to_numeric(reference["return_7d"], errors="coerce").dropna()
        if len(reference_returns) != len(self.config.reference_symbols):
            return [], len(snapshot), 0.0, 0.0
        snapshot["rs_percentile"] = snapshot["return_7d"].map(
            lambda value: float((reference_returns <= float(value)).mean())
        )
        long_breadth = float((reference["ema10"] > reference["ema50"]).mean())
        short_breadth = float((reference["ema10"] < reference["ema50"]).mean())

        observations: list[ShadowObservation] = []
        signal_set = set(self._signal_symbols)
        for _, row in snapshot[snapshot["symbol"].isin(signal_set)].sort_values("symbol").iterrows():
            symbol = str(row["symbol"])
            if self.store.has_open(symbol):
                continue
            selected = None
            for rule in self._rules():
                selected = self._evaluate_rule(row, rule, timestamp, long_breadth, short_breadth)
                if selected is not None:
                    break
            if selected is not None and self.store.insert(selected, warmup=warmup):
                observations.append(selected)
        return observations, len(snapshot), long_breadth, short_breadth

    def _evaluate_rule(
        self,
        row: pd.Series,
        rule: dict[str, Any],
        timestamp: pd.Timestamp,
        long_breadth: float,
        short_breadth: float,
    ) -> ShadowObservation | None:
        atr = float(row["atr14"])
        close = float(row["close"])
        volume_ratio = float(row["volume_ratio"])
        rs = float(row["rs_percentile"])
        lookback = int(rule["donchian_lookback"])
        high = row.get(f"prior_high_{lookback}")
        low = row.get(f"prior_low_{lookback}")
        if not math.isfinite(atr) or atr <= 0 or pd.isna(high) or pd.isna(low):
            return None
        if volume_ratio < float(rule["volume_ratio_min"]):
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

        side: ShadowSide | None = None
        breadth = 0.0
        if (
            float(row["ema10"]) > float(row["ema50"])
            and close > float(high)
            and rs >= float(rule["rs_long_min"])
        ):
            side = "long"
            breadth = long_breadth
        elif (
            float(row["ema10"]) < float(row["ema50"])
            and close < float(low)
            and rs <= float(rule["rs_short_max"])
        ):
            side = "short"
            breadth = short_breadth
        if side is None:
            return None
        minimum_breadth = rule.get("same_direction_breadth_min")
        if minimum_breadth is not None and breadth < float(minimum_breadth):
            return None

        member = str(rule["member"])
        observation_id = "|".join(
            [self.candidate_id, member, str(row["symbol"]), side, _utc_text(timestamp)]
        )
        return ShadowObservation(
            observation_id=observation_id,
            candidate_id=self.candidate_id,
            member=member,
            symbol=str(row["symbol"]),
            side=side,
            signal_time=_utc_text(timestamp),
            detected_at=_utc_text(timestamp),
            reference_close=close,
            atr_at_signal=atr,
            initial_stop_atr=float(rule["initial_stop_atr"]),
            trailing_stop_atr=float(rule["trailing_stop_atr"]),
            max_hold_bars=int(rule["max_hold_bars"]),
            relative_strength_percentile=rs,
            volume_ratio=volume_ratio,
            breadth=breadth,
            reason_codes=(
                "RESEARCH_ONLY",
                "CLOSED_4H",
                "NEXT_4H_BAR_OPEN_MODEL",
                "CURRENT_BAR_EXCLUDED_FROM_CHANNEL",
                member,
            ),
        )

    def _advance_open_records(self, features: dict[str, pd.DataFrame]) -> None:
        for record in self.store.open_records():
            frame = features.get(str(record["symbol"]))
            if frame is not None and not frame.empty:
                self._advance_record(record, frame)

    def _advance_record(self, record: dict[str, Any], frame: pd.DataFrame) -> None:
        signal_time = pd.Timestamp(record["signal_time"])
        matches = frame.index[frame["ts"] == signal_time]
        if len(matches) != 1:
            return
        entry_index = int(matches[0]) + 1
        if entry_index >= len(frame):
            return
        entry_price = float(record["entry_price"] or frame.iloc[entry_index]["open"])
        risk_distance = float(record["atr_at_signal"]) * float(record["initial_stop_atr"])
        if risk_distance <= 0:
            self.store.update(str(record["observation_id"]), state="CENSORED", outcome="INVALID_RISK")
            return
        side = str(record["side"])
        initial_stop = float(
            record["stop_price"]
            or (entry_price - risk_distance if side == "long" else entry_price + risk_distance)
        )
        trail = float(record["trail_price"] or initial_stop)
        processed = pd.Timestamp(record["processed_through"]) if record["processed_through"] else None
        start = entry_index
        if processed is not None:
            later = frame.index[frame["ts"] > processed]
            if len(later) == 0:
                return
            start = int(later[0])
        bars_held = int(record["bars_held"] or 0)
        mfe_r = float(record["mfe_r"] or 0.0)
        mae_r = float(record["mae_r"] or 0.0)
        updates: dict[str, Any] = {
            "state": "ACTIVE",
            "entry_time": _utc_text(signal_time),
            "entry_price": entry_price,
            "stop_price": initial_stop,
        }
        for index in range(start, len(frame)):
            bar = frame.iloc[index]
            if index > entry_index:
                previous = frame.iloc[index - 1]
                previous_atr = float(previous["atr14"])
                if side == "long":
                    trail = max(trail, float(previous["high"]) - float(record["trailing_stop_atr"]) * previous_atr)
                else:
                    trail = min(trail, float(previous["low"]) + float(record["trailing_stop_atr"]) * previous_atr)
            high = float(bar["high"])
            low = float(bar["low"])
            if side == "long":
                mfe_r = max(mfe_r, (high - entry_price) / risk_distance)
                mae_r = min(mae_r, (low - entry_price) / risk_distance)
                stop_hit = low <= trail
            else:
                mfe_r = max(mfe_r, (entry_price - low) / risk_distance)
                mae_r = min(mae_r, (entry_price - high) / risk_distance)
                stop_hit = high >= trail
            bars_held += 1
            updates.update(
                {
                    "bars_held": bars_held,
                    "mfe_r": mfe_r,
                    "mae_r": mae_r,
                    "trail_price": trail,
                    "processed_through": _utc_text(bar["ts"]),
                }
            )
            terminal_state = None
            exit_price = None
            outcome = None
            if stop_hit:
                terminal_state = "STOP_REACHED"
                exit_price = trail
                outcome = "TRAILING_STOP"
            elif bars_held >= int(record["max_hold_bars"]):
                terminal_state = "TIMEOUT_RESULT"
                exit_price = float(bar["close"])
                outcome = "MAX_HOLD"
            if terminal_state is not None and exit_price is not None:
                gross_r = (
                    (exit_price - entry_price) / risk_distance
                    if side == "long"
                    else (entry_price - exit_price) / risk_distance
                )
                hours = bars_held * 4
                fee_slippage = 2 * (0.0006 + 0.0005)
                funding = math.ceil(hours / 8) * 0.0001
                estimated_cost_r = entry_price * (fee_slippage + funding) / risk_distance
                updates.update(
                    {
                        "state": terminal_state,
                        "outcome": outcome,
                        "exit_time": _utc_text(bar["ts"]),
                        "exit_price": exit_price,
                        "gross_r": gross_r,
                        "estimated_net_r": gross_r - estimated_cost_r,
                    }
                )
                break
        self.store.update(str(record["observation_id"]), **updates)

    def _write_status(self, result: ShadowScanResult) -> None:
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": _now_text(),
            "status": result.status,
            "candidate_id": self.candidate_id,
            "candidate_sha256": self.candidate_sha256,
            "protocol_version": PROTOCOL_VERSION,
            "research_only": True,
            "isolated_from_formal_runtime": True,
            "source_timeframe": self.config.source_timeframe,
            "evaluation_timeframe": self.config.evaluation_timeframe,
            "reference_symbols": list(self.config.reference_symbols),
            "latest_closed_4h": result.latest_closed_4h,
            "eligible_symbols": result.eligible_symbols,
            "skipped_symbols": list(result.skipped_symbols),
            "new_signal_count": len(result.new_observations),
            "new_signals": [item.as_dict() for item in result.new_observations],
            "pending_entry_count": result.pending_entry_count,
            "active_count": result.active_count,
            "closed_count": result.closed_count,
            "summary": result.summary,
        }
        write_text_atomic(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            self.status_path,
        )


__all__ = [
    "FROZEN_REFERENCE_SYMBOLS",
    "PROTOCOL_VERSION",
    "ShadowEnsembleConfig",
    "ShadowEnsembleService",
    "ShadowEnsembleStore",
    "ShadowObservation",
    "ShadowScanResult",
    "build_shadow_feature_frame",
    "load_shadow_ensemble_config",
    "strict_resample_closed_15m_to_4h",
]
