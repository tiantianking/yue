from okx_signal_system.notify import feishu
from okx_signal_system.notify.signal_dedupe import (
    SignalNotificationStore,
    signal_notification_key,
)


class DummySignal:
    inst_id = "BTC-USDT-SWAP"
    side = "long"
    ts = "2026-06-16T00:00:00+00:00"


def test_signal_alert_includes_target_rr_and_risk_fields(monkeypatch) -> None:
    sent: list[str] = []

    def fake_send_text(text: str, *args, **kwargs) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(feishu, "send_text", fake_send_text)

    ok = feishu.send_signal_alert(
        inst_id="BTC-USDT-SWAP",
        side="long",
        entry_ref=100.0,
        stop_loss=95.0,
        take_profit=117.5,
        qty=1.0,
        leverage=3.0,
        risk_reward_ratio=3.5,
        max_loss_pct=0.01,
        margin_loss_pct=0.27,
    )

    assert ok
    assert sent
    text = sent[0]
    assert "target_rr: 3.50R" in text
    assert "account_risk_at_stop: 1.00%" in text
    assert "margin_loss_at_stop: 27.00% (cap 27.00%)" in text


def test_candidate_health_report_is_not_a_trade_signal(monkeypatch) -> None:
    sent: list[str] = []

    def fake_send_text(text: str, *args, **kwargs) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(feishu, "send_text", fake_send_text)

    ok = feishu.send_candidate_health_report(
        items=[
            {
                "symbol": "BTC-USDT-SWAP",
                "reason": "volume_too_low",
                "bias": "short",
                "breakout_gap_pct": 0.043,
                "raw_score": None,
                "would_push": False,
            },
            {
                "symbol": "ETH-USDT-SWAP",
                "reason": "ready",
                "side": "long",
                "final_score": 7.2,
                "breakout_gap_pct": 0.0,
                "would_push": True,
            },
        ],
        push_allowed=True,
        selected_params={"atr_stop_mult": 2.5, "take_profit_mult": 3.5},
    )

    assert ok
    text = sent[0]
    assert "not_trade_signal: true" in text
    assert "ready_candidates: 1" in text
    assert "blocked_reasons: volume_too_low=1" in text
    assert "target_rr=3.50R" in text
    assert "BTC-USDT-SWAP" in text


def test_candidate_health_report_sends_even_without_candidates(monkeypatch) -> None:
    sent: list[str] = []

    def fake_send_text(text: str, *args, **kwargs) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(feishu, "send_text", fake_send_text)

    ok = feishu.send_candidate_health_report(
        items=[],
        push_allowed=True,
        selected_params={"atr_stop_mult": 2.5, "take_profit_mult": 3.5},
    )

    assert ok
    text = sent[0]
    assert "not_trade_signal: true" in text
    assert "symbols_checked: 0" in text
    assert "blocked_reasons: no_evaluable_candidates" in text
    assert "watchlist: none" in text


def test_signal_notification_store_persists_dedupe_keys(tmp_path) -> None:
    path = tmp_path / "signal_notifications.json"
    key = signal_notification_key(
        DummySignal(),
        signal_timeframe="15m",
        trend_timeframe="1h",
    )

    store = SignalNotificationStore(path)
    assert not store.has(key)
    assert store.mark(key, {"symbol": "BTC-USDT-SWAP"})
    assert store.has(key)
    assert not store.mark(key, {"symbol": "BTC-USDT-SWAP"})

    reloaded = SignalNotificationStore(path)
    assert reloaded.has(key)
    assert "|15m|1h" in key
