from okx_signal_system.notify import feishu
from okx_signal_system.notify.signal_dedupe import (
    BTierSummaryNotificationStore,
    SignalNotificationStore,
    b_tier_summary_key,
    signal_notification_key,
)


class DummySignal:
    inst_id = "BTC-USDT-SWAP"
    side = "long"
    ts = "2026-06-16T00:00:00+00:00"


def test_signal_alert_includes_target_rr_without_account_fields(monkeypatch) -> None:
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
        risk_reward_ratio=3.5,
    )

    assert ok
    assert sent
    text = sent[0]
    assert "OKX 信号观察" in text
    assert "信号生成时间:" in text
    assert "北京时间" in text
    assert "UTC" not in text
    assert "目标盈亏比: 3.50R" in text
    assert "账户止损风险" not in text
    assert "仓位" not in text
    assert "杠杆" not in text
    assert "保证金" not in text


def test_signal_alert_signature_is_signal_only() -> None:
    forbidden = {"qty", "leverage", "max_loss_pct", "margin_loss_pct"}
    for func in [feishu.send_signal_observation, feishu.send_signal_alert, feishu.feishu_send_signal_card]:
        params = __import__("inspect").signature(func).parameters
        assert forbidden.isdisjoint(params)


def test_legacy_signal_card_wrapper_does_not_emit_account_fields(monkeypatch) -> None:
    sent: list[str] = []

    def fake_send_text(text: str, *args, **kwargs) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(feishu, "send_text", fake_send_text)

    ok = feishu.feishu_send_signal_card(
        inst_id="BTC-USDT-SWAP",
        direction="long",
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=117.5,
        reason="BREAKOUT",
    )

    assert ok
    text = sent[0]
    assert "信号类型: 突破信号" in text
    assert "仓位" not in text
    assert "杠杆" not in text
    assert "保证金" not in text


def test_signal_alert_includes_tier_and_cross_symbol_rank(monkeypatch) -> None:
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
        tier="A",
        rank=1,
        total_candidates=6,
    )

    assert ok
    assert "OKX A级信号观察" in sent[0]
    assert "21币横向排名: 1/6" in sent[0]


def test_signal_alert_includes_lifecycle_status(monkeypatch) -> None:
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
        lifecycle_status="TRIGGERED",
        invalidation_price=95.0,
        kline_time="2026-06-16T00:00:00+00:00",
    )

    assert ok
    assert "signal_status: TRIGGERED" in sent[0]
    assert "invalidation_price: 95.00000000" in sent[0]
    assert "K线时间: 2026-06-16 08:00:00 北京时间" in sent[0]


def test_signal_alert_includes_quality_model(monkeypatch) -> None:
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
        quality_model={
            "p_tp": 0.42,
            "p_sl": 0.18,
            "p_timeout": 0.40,
            "expected_net_r": 1.27,
            "uncertainty": 0.11,
        },
    )

    assert ok
    assert "质量模型旁路: p_tp=0.420" in sent[0]
    assert "expected_net_r=1.270" in sent[0]


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
    assert "这不是正式信号" in text
    assert "可推送: 1 个" in text
    assert "主要卡点: volume_too_low=1" in text
    assert "目标盈亏比 3.50R" in text
    assert "BTC-USDT-SWAP" in text
    assert "下单" not in text


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
    assert "这不是正式信号" in text
    assert "已检查: 0 个币种" in text
    assert "主要卡点: 没有可评估候选" in text
    assert "优先看: 无" in text


def test_b_tier_summary_text_is_understandable(monkeypatch) -> None:
    sent: list[str] = []

    def fake_send_text(text: str, *args, **kwargs) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(feishu, "send_text", fake_send_text)

    ok = feishu.send_b_tier_summary(
        [
            {
                "inst_id": "ETH-USDT-SWAP",
                "side": "long",
                "rank": 3,
                "raw_score": 7.1,
                "decision": {"risk_reward_ratio": 3.5},
                "health_item": {"reason": "correlation_group_demoted"},
                "signal": {"ts": "2026-06-16T00:00:00+00:00"},
            }
        ],
        total_candidates=4,
        signal_timeframe="15m",
        trend_timeframe="1h",
    )

    assert ok
    text = sent[0]
    assert "OKX B-tier candidate summary" in text
    assert "time: " in text
    assert "北京时间" in text
    assert "UTC" not in text
    assert "candle_time: 2026-06-16 08:00:00 北京时间" in text
    assert "B-tier candidates: 1" in text
    assert "not immediate A-tier alerts" in text
    assert "#3 ETH-USDT-SWAP long score=7.1 rr=3.50R reason=correlation_group_demoted" in text


def test_b_tier_summary_includes_quality_model(monkeypatch) -> None:
    sent: list[str] = []

    def fake_send_text(text: str, *args, **kwargs) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(feishu, "send_text", fake_send_text)

    ok = feishu.send_b_tier_summary(
        [
            {
                "inst_id": "ETH-USDT-SWAP",
                "side": "long",
                "rank": 3,
                "raw_score": 7.1,
                "decision": {"risk_reward_ratio": 3.5},
                "health_item": {"reason": "correlation_group_demoted"},
                "signal": {"ts": "2026-06-16T00:00:00+00:00"},
                "payload": {
                    "quality_model": {
                        "p_tp": 0.4,
                        "p_sl": 0.2,
                        "p_timeout": 0.4,
                        "expected_net_r": 1.1,
                        "uncertainty": 0.2,
                    }
                },
            }
        ],
        total_candidates=4,
        signal_timeframe="15m",
        trend_timeframe="1h",
    )

    assert ok
    assert "质量模型旁路: p_tp=0.400" in sent[0]
    assert "expected_net_r=1.100" in sent[0]


def test_status_and_health_reports_use_beijing_time(monkeypatch) -> None:
    sent: list[str] = []

    def fake_send_text(text: str, *args, **kwargs) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(feishu, "send_text", fake_send_text)

    assert feishu.send_status_report(cycle_count=3, status="healthy", last_signal_count=1)
    assert feishu.send_candidate_health_report(items=[], push_allowed=True)

    assert all("北京时间" in text for text in sent)
    assert all("UTC" not in text for text in sent)


def test_b_tier_summary_is_not_sent_without_candidates(monkeypatch) -> None:
    sent: list[str] = []

    def fake_send_text(text: str, *args, **kwargs) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(feishu, "send_text", fake_send_text)

    assert not feishu.send_b_tier_summary([])
    assert sent == []


def test_waiting_next_bar_health_reason_is_readable() -> None:
    assert feishu._health_reason_label("waiting_next_bar") == "等待下一根K线"


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


def test_b_tier_summary_key_is_separate_from_a_tier_signal_key(tmp_path) -> None:
    signal_path = tmp_path / "signal_notifications.sqlite3"
    summary_path = tmp_path / "b_tier_summaries.sqlite3"
    signal_key = signal_notification_key(
        DummySignal(),
        signal_timeframe="15m",
        trend_timeframe="1h",
    )
    summary_key = b_tier_summary_key(
        DummySignal.ts,
        signal_timeframe="15m",
        trend_timeframe="1h",
    )

    assert summary_key != signal_key
    assert summary_key.startswith("b_tier_summary|")

    signal_store = SignalNotificationStore(signal_path)
    summary_store = BTierSummaryNotificationStore(summary_path)
    assert signal_store.mark(signal_key, {"symbol": "BTC-USDT-SWAP", "tier": "A"})
    assert signal_store.has(signal_key)
    assert not summary_store.has(summary_key)
    assert summary_store.mark(summary_key, {"kline_time": DummySignal.ts})
    assert summary_store.has(summary_key)


def test_signal_notification_key_uses_hash_when_params_are_supplied() -> None:
    key = signal_notification_key(
        DummySignal(),
        signal_timeframe="15m",
        trend_timeframe="1h",
        params=__import__("okx_signal_system.strategy.trend_breakout", fromlist=["StrategyParams"]).StrategyParams(),
    )

    assert "|" not in key
    assert len(key) == 64
