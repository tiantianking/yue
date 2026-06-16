import pandas as pd

from okx_signal_system.signal_service.app import readable_trades, signal_view_model


def test_signal_view_model_turns_json_into_human_result() -> None:
    payload = {
        "signal": {
            "ts": "2026-06-10 05:00:00+00:00",
            "inst_id": "BTC-USDT-SWAP",
            "side": "flat",
            "entry_ref": None,
            "stop_loss": None,
            "take_profit": None,
            "max_hold_bars": None,
            "reason_codes": ["NO_BREAKOUT"],
            "reject_reason": "no_breakout",
        },
        "risk": {"accepted": False, "reason": "no_breakout", "leverage_cap": 0, "qty": None, "risk_amount": None, "margin_mode": "isolated", "position_mode": "net_mode"},
        "live_order_enabled": False,
    }
    view = signal_view_model(payload)
    assert view["headline"] == "暂无正式信号"
    assert "价格还没有突破入场线" in view["reasons"]
    assert view["signal_mode"] == "SIGNAL_ONLY"


def test_readable_trades_renames_columns():
    trades = pd.DataFrame(
        [
            {
                "inst_id": "BTC-USDT-SWAP",
                "entry_time": "2026-01-01",
                "exit_time": "2026-01-02",
                "side": "long",
                "entry_price": 100,
                "exit_price": 110,
                "qty": 1,
                "gross_pnl": 10,
                "costs": 1,
                "net_pnl": 9,
                "exit_reason": "take_profit",
                "leverage_cap": 2,
            }
        ]
    )
    table = readable_trades(trades)
    assert "合约" in table.columns
    assert "净盈亏" in table.columns
    assert "数量" not in table.columns
    assert "杠杆上限" not in table.columns
    assert "样本数量" not in table.columns
    assert "参考价" in table.columns
    assert "结果价" in table.columns
