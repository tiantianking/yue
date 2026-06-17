from __future__ import annotations

import argparse

from okx_signal_system.backtest.research import run_dataset_research_artifacts, write_research_artifacts
from okx_signal_system.config import project_paths
from okx_signal_system.strategy.trend_breakout import StrategyParams


def smoke_grid() -> list[StrategyParams]:
    return [
        StrategyParams(fast_ema=96, slow_ema=576, breakout_window=288, atr_stop_mult=4.0, take_profit_mult=6.0, max_hold_bars=576),
        StrategyParams(fast_ema=120, slow_ema=720, breakout_window=384, atr_stop_mult=4.0, take_profit_mult=6.0, max_hold_bars=768),
        StrategyParams(fast_ema=120, slow_ema=960, breakout_window=480, atr_stop_mult=4.5, take_profit_mult=7.0, max_hold_bars=768),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="okx_15m_extended")
    parser.add_argument("--signal-timeframe", default="15m")
    parser.add_argument("--trend-timeframe", default="1h")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--smoke", action="store_true", help="run a non-formal small-grid smoke check")
    parser.add_argument("--full-grid", action="store_true", help="kept for compatibility; formal mode already uses the full grid")
    parser.add_argument("--per-symbol-params", action="store_true")
    parser.add_argument("--legacy-split", action="store_true", help="allow non-formal per-symbol fallback split")
    parser.add_argument("--unlock-blind", action="store_true", help="run blind-set evaluation and write an access manifest")
    parser.add_argument("--blind-release-token", default=None)
    parser.add_argument("--blind-release-token-sha256", default=None)
    parser.add_argument("--blind-registry-path", default=None)
    parser.add_argument("--research-version", default="v3.53-strict")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    params_grid = smoke_grid() if args.smoke else None
    max_symbols = args.max_symbols
    research_mode = "FORMAL"
    if args.smoke:
        max_symbols = 3 if max_symbols is None else max_symbols
        research_mode = "NON_FORMAL_SMOKE"
    artifacts = run_dataset_research_artifacts(
        dataset=args.dataset,
        params_grid=params_grid,
        max_symbols=max_symbols,
        shared_params=not args.per_symbol_params,
        signal_timeframe=args.signal_timeframe,
        trend_timeframe=args.trend_timeframe,
        legacy_split=args.legacy_split,
        unlock_blind=args.unlock_blind,
        blind_release_token=args.blind_release_token,
        blind_release_token_sha256=args.blind_release_token_sha256,
        blind_registry_path=args.blind_registry_path,
        research_version=args.research_version,
        research_mode=research_mode,
    )
    write_research_artifacts(artifacts, project_paths().output_dir)


if __name__ == "__main__":
    main()
