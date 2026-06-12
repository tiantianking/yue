"""验证脚本 - 测试所有模块"""
import sys
sys.path.insert(0, 'src')

from okx_signal_system.data.loader import load_symbol_file, list_parquet_files, load_all_symbols
from okx_signal_system.features.indicators import build_feature_frame
from okx_signal_system.strategy.trend_breakout import StrategyParams, generate_signals
from okx_signal_system.data.gap_handler import DataGapHandler, FeatureGapHandler
from pathlib import Path

def test_signal_generation():
    """测试信号生成"""
    print("=" * 60)
    print("Signal Generation Test")
    print("=" * 60)

    files = list_parquet_files('okx_1h_extended')
    params = StrategyParams()

    results = []
    for f in files:
        try:
            sd = load_symbol_file(f)
            features = build_feature_frame(sd.frame)
            signals = generate_signals(features, inst_id=sd.inst_id, params=params)
            accepted = [s for s in signals if s.accepted]

            results.append({
                'symbol': sd.inst_id,
                'bars': len(sd.frame),
                'signals': len(signals),
                'accepted': len(accepted),
            })
        except Exception as e:
            results.append({'symbol': f.stem, 'error': str(e)[:60]})

    results.sort(key=lambda x: x.get('symbol', ''))

    print(f"{'Symbol':<20} {'Bars':>8} {'Signals':>8} {'Accepted':>8}")
    print('-' * 55)
    for r in results:
        if 'error' in r:
            print(f"{r['symbol']:<20} ERROR: {r['error']}")
        else:
            sym = r['symbol'].replace('-USDT-SWAP', '')
            print(f"{sym:<20} {r['bars']:>8d} {r['signals']:>8d} {r['accepted']:>8d}")

    ok = [r for r in results if 'error' not in r]
    print()
    print(f"Result: {len(ok)}/{len(results)} symbols successful")

    return len(ok) == len(results)


def test_data_gaps():
    """测试数据缺口检测"""
    print()
    print("=" * 60)
    print("Data Gap Detection Test")
    print("=" * 60)

    handler = DataGapHandler()
    print(f"Data dir: {handler.data_dir}")

    symbols = ['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP']
    for sym in symbols:
        gaps = handler.detect_gaps(sym)
        print(f"\n{sym}:")
        if not gaps:
            print("  No gaps detected")
        else:
            for g in gaps:
                print(f"  {g.severity}: {g.missing_bars} bars ({g.start_time.strftime('%Y-%m-%d %H:%M')} to {g.end_time.strftime('%Y-%m-%d %H:%M')})")


def test_feature_nan():
    """测试特征NaN处理"""
    print()
    print("=" * 60)
    print("Feature NaN Detection Test")
    print("=" * 60)

    sd = load_symbol_file(Path(r"D:\JIAOYI-CX\历史数据_保留\lightweight_history\okx_1h_extended\BTC_USDT_USDT_1h.parquet"))
    print(f"Loaded: {len(sd.frame)} bars")

    features = build_feature_frame(sd.frame)
    print(f"Features: {len(features)} rows")

    nan_cols = ['close', 'volume', 'ema_fast', 'ema_slow', 'atr']
    for col in nan_cols:
        if col in features.columns:
            nan_count = features[col].isna().sum()
            pct = nan_count / len(features) * 100
            print(f"  {col:<15}: {nan_count:>5} NaN ({pct:.3f}%)")

    regions = FeatureGapHandler.detect_nan_regions(features)
    print(f"\nNaN regions: {len(regions)}")
    if regions:
        for i, (start, end) in enumerate(regions[:5]):
            print(f"  Region {i+1}: bars {start}-{end}")


def test_ml_modules():
    """测试ML模块"""
    print()
    print("=" * 60)
    print("ML Modules Test")
    print("=" * 60)

    from okx_signal_system.ml.online_learning import create_learning_engine, TradeRecord
    from okx_signal_system.ml.reinforcement_learning import create_rl_optimizer, MarketRegimeDetector
    from okx_signal_system.ml.symbol_rotation import create_rotator
    from datetime import datetime, timezone

    # Online Learning
    try:
        engine = create_learning_engine(Path('output/ml_test'))
        print("[OK] OnlineLearningEngine")

        trade = TradeRecord(
            inst_id='BTC-USDT-SWAP',
            side='long',
            entry_time=datetime.now(timezone.utc),
            exit_time=datetime.now(timezone.utc),
            entry_price=50000,
            exit_price=50500,
            qty=0.01,
            pnl=5.0,
            pnl_pct=0.01,
            exit_reason='tp',
            params={}
        )
        engine.record_trade(trade)
        print("[OK] Trade recorded successfully")
    except Exception as e:
        print(f"[X] OnlineLearningEngine: {e}")

    # RL Optimizer
    try:
        rl = create_rl_optimizer(Path('output/rl_test'))
        print("[OK] RLParameterOptimizer")

        regime = MarketRegimeDetector.detect_regime(0.02, 1.2, 0.01, 1.0)
        print(f"    Market regime: {regime}")
    except Exception as e:
        print(f"[X] RLParameterOptimizer: {e}")

    # Symbol Rotator
    try:
        rotator = create_rotator(
            ['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP'],
            Path('output/rotation_test')
        )
        print("[OK] SymbolRotator")
        print(f"    Active symbols: {rotator.get_active_symbols()}")
    except Exception as e:
        print(f"[X] SymbolRotator: {e}")


def test_trading_brain():
    """测试交易大脑"""
    print()
    print("=" * 60)
    print("Trading Brain Test")
    print("=" * 60)

    try:
        from okx_signal_system.ml.trading_brain import TradingBrain

        brain = TradingBrain(
            data_dir=Path('output/trading_brain_test'),
            config={
                'symbols': ['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP'],
                'api': {'paper_trading': True}
            }
        )
        print("[OK] TradingBrain initialized")
        print(f"    Symbols: {brain.symbol_rotator.get_active_symbols()}")
        print(f"    Params: {brain.current_params}")
    except Exception as e:
        print(f"[X] TradingBrain: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("OKX Signal System - Full Validation")
    print("=" * 60)

    test_signal_generation()
    test_data_gaps()
    test_feature_nan()
    test_ml_modules()
    test_trading_brain()

    print()
    print("=" * 60)
    print("Validation Complete")
    print("=" * 60)