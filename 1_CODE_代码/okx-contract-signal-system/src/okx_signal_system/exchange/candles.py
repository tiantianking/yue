from __future__ import annotations

import pandas as pd


def okx_candles_to_frame(raw_bars: list[list]) -> pd.DataFrame:
    """Normalize OKX REST candle rows with 9 or 10 fields into OHLCV."""
    columns = ["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"]
    rows = []
    for bar in raw_bars or []:
        if len(bar) < 6:
            continue
        row = list(bar[: len(columns)])
        row.extend([None] * (len(columns) - len(row)))
        rows.append(row)
    output_columns = ["ts", "open", "high", "low", "close", "volume", "quote_volume"]
    if not rows:
        return pd.DataFrame(columns=output_columns)

    df = pd.DataFrame(rows, columns=columns)
    ts_num = pd.to_numeric(df["ts"], errors="coerce")
    if ts_num.notna().all():
        df["ts"] = pd.to_datetime(ts_num, utc=True, unit="ms")
    else:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for col in ["open", "high", "low", "close", "vol", "volCcyQuote"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    out = df.rename(columns={"vol": "volume", "volCcyQuote": "quote_volume"})[output_columns]
    return out.dropna(subset=["ts", "open", "high", "low", "close"]).sort_values("ts").reset_index(drop=True)
