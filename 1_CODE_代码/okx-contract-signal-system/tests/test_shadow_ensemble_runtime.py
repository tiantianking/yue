from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pandas as pd

from okx_signal_system.config import load_config, project_paths
from okx_signal_system.shadow_ensemble import (
    FROZEN_REFERENCE_SYMBOLS,
    ShadowEnsembleConfig,
    ShadowEnsembleService,
    ShadowEnsembleStore,
    build_shadow_feature_frame,
    strict_resample_closed_15m_to_4h,
)


def _closed_15m_frame(*, periods_4h: int = 200, final_jump: float = 0.0) -> pd.DataFrame:
    timestamps = pd.date_range("2025-01-01T00:00:00Z", periods=periods_4h * 16, freq="15min")
    rows: list[dict[str, object]] = []
    for group in range(periods_4h):
        base = 100.0 + group * 0.03
        target = base + (final_jump if group == periods_4h - 1 else 0.0)
        for offset in range(16):
            fraction = (offset + 1) / 16
            close = base + (target - base) * fraction
            open_price = base + (target - base) * (offset / 16)
            rows.append(
                {
                    "ts": timestamps[group * 16 + offset],
                    "open": open_price,
                    "high": max(open_price, close) + 0.05,
                    "low": min(open_price, close) - 0.05,
                    "close": close,
                    "volume": 1000.0 + group,
                    "is_closed": True,
                }
            )
    return pd.DataFrame(rows)


def _config(tmp_path: Path) -> ShadowEnsembleConfig:
    root = project_paths().root
    return ShadowEnsembleConfig(
        status_file=str(tmp_path / "shadow_status.json"),
        sqlite_file=str(tmp_path / "unused.sqlite3"),
        candidate_file=str(root / "config/research_candidates/v357_shadow_ensemble_candidate.json"),
        donchian_candidate_file=str(root / "config/research_candidates/v357_4h_donchian_shadow_candidate.json"),
    )


def test_strict_resample_requires_sixteen_closed_consecutive_bars() -> None:
    frame = _closed_15m_frame(periods_4h=2)
    result = strict_resample_closed_15m_to_4h(frame)
    assert len(result) == 2
    assert result["is_closed"].all()
    assert result.iloc[0]["ts"] == pd.Timestamp("2025-01-01T04:00:00Z")

    missing = frame.drop(index=[4]).reset_index(drop=True)
    missing_result = strict_resample_closed_15m_to_4h(missing)
    assert len(missing_result) == 1

    open_tail = frame.copy()
    open_tail.loc[open_tail.index[-1], "is_closed"] = False
    open_result = strict_resample_closed_15m_to_4h(open_tail)
    assert len(open_result) == 1


def test_breakout_channel_excludes_current_closed_bar() -> None:
    config = ShadowEnsembleConfig()
    bars = strict_resample_closed_15m_to_4h(_closed_15m_frame(periods_4h=80, final_jump=8.0))
    features = build_shadow_feature_frame(bars, config)
    latest = features.iloc[-1]
    assert latest["close"] > latest["prior_high_24"]
    assert latest["prior_high_24"] == features.iloc[-25:-1]["high"].max()


def test_protocol_rejects_changed_reference_universe(tmp_path: Path) -> None:
    async def loader(symbol: str, limit: int) -> pd.DataFrame:
        return _closed_15m_frame().tail(limit)

    config = replace(_config(tmp_path), reference_symbols=FROZEN_REFERENCE_SYMBOLS[:-1])
    store = ShadowEnsembleStore(tmp_path / "shadow.sqlite3")
    try:
        ShadowEnsembleService(candle_loader=loader, config=config, store=store)
    except ValueError as exc:
        assert "reference universe" in str(exc)
    else:
        raise AssertionError("changed reference universe must be rejected")


def test_service_writes_isolated_status_and_database(tmp_path: Path) -> None:
    frames = {
        symbol: _closed_15m_frame(final_jump=8.0 if symbol == "BTC-USDT-SWAP" else 0.0)
        for symbol in FROZEN_REFERENCE_SYMBOLS
    }

    async def loader(symbol: str, limit: int) -> pd.DataFrame:
        return frames[symbol].tail(limit).reset_index(drop=True)

    config = _config(tmp_path)
    store = ShadowEnsembleStore(tmp_path / "shadow.sqlite3")
    service = ShadowEnsembleService(candle_loader=loader, config=config, store=store)
    result = asyncio.run(service.scan(FROZEN_REFERENCE_SYMBOLS))

    assert result.status == "running"
    assert result.eligible_symbols == len(FROZEN_REFERENCE_SYMBOLS)
    assert all(item.symbol in FROZEN_REFERENCE_SYMBOLS for item in result.new_observations)
    assert (tmp_path / "shadow_status.json").exists()
    assert (tmp_path / "shadow.sqlite3").exists()
    assert not (tmp_path / "signal_lifecycle.sqlite3").exists()


def test_store_closes_windows_sqlite_handles(tmp_path: Path) -> None:
    store = ShadowEnsembleStore(tmp_path / "shadow.sqlite3")
    store.summary()
    store.path.unlink()
    assert not store.path.exists()


def test_local_runtime_cache_smoke(tmp_path: Path) -> None:
    root = project_paths().root
    cache_dir = root / "outputs" / "runtime_cache" / "lightweight_history" / "okx_15m_extended"
    if not cache_dir.is_dir():
        import pytest

        pytest.skip("local 15m runtime cache is unavailable")
    symbols = tuple(str(item) for item in load_config("base.yaml").get("data", {}).get("symbols", []))

    def filename(symbol: str) -> str:
        normalized = symbol.replace("-", "_").replace("_SWAP", "").upper()
        if normalized.count("USDT") == 1:
            normalized = f"{normalized}_USDT"
        return f"{normalized}_15m.parquet"

    async def loader(symbol: str, limit: int) -> pd.DataFrame:
        path = cache_dir / filename(symbol)
        if not path.is_file():
            raise FileNotFoundError(path)
        return pd.read_parquet(path).tail(limit).reset_index(drop=True)

    config = replace(
        _config(tmp_path),
        status_file=str(tmp_path / "local_status.json"),
        sqlite_file=str(tmp_path / "local.sqlite3"),
    )
    service = ShadowEnsembleService(
        candle_loader=loader,
        config=config,
        store=ShadowEnsembleStore(tmp_path / "local.sqlite3"),
    )
    result = asyncio.run(service.scan(symbols))
    assert result.status == "running"
    assert result.eligible_symbols >= len(FROZEN_REFERENCE_SYMBOLS)
    assert result.latest_closed_4h is not None


def test_missing_or_misaligned_frozen_reference_pauses_only_shadow(tmp_path: Path) -> None:
    complete = _closed_15m_frame()
    frames = {symbol: complete.copy() for symbol in FROZEN_REFERENCE_SYMBOLS}
    missing_symbol = FROZEN_REFERENCE_SYMBOLS[-1]

    async def missing_loader(symbol: str, limit: int) -> pd.DataFrame:
        if symbol == missing_symbol:
            raise FileNotFoundError(symbol)
        return frames[symbol].tail(limit).reset_index(drop=True)

    service = ShadowEnsembleService(
        candle_loader=missing_loader,
        config=_config(tmp_path),
        store=ShadowEnsembleStore(tmp_path / "missing.sqlite3"),
    )
    missing_result = asyncio.run(service.scan(FROZEN_REFERENCE_SYMBOLS))
    assert missing_result.status == "incomplete_frozen_reference_universe"

    async def misaligned_loader(symbol: str, limit: int) -> pd.DataFrame:
        frame = frames[symbol]
        if symbol == missing_symbol:
            frame = frame.iloc[:-16]
        return frame.tail(limit).reset_index(drop=True)

    service = ShadowEnsembleService(
        candle_loader=misaligned_loader,
        config=_config(tmp_path),
        store=ShadowEnsembleStore(tmp_path / "misaligned.sqlite3"),
    )
    misaligned_result = asyncio.run(service.scan(FROZEN_REFERENCE_SYMBOLS))
    assert misaligned_result.status == "frozen_reference_bar_misaligned"
