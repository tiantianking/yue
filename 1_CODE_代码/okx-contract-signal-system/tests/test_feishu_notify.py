from okx_signal_system.notify import feishu


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
