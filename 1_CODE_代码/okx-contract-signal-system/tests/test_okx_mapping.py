from okx_signal_system.exchange import okx
import pytest

from okx_signal_system.exchange.okx import OKXInstrument, OrderParams, _okx_rest_proxy_url, _proxy_dict, okx_place_order_preview, place_order


def test_okx_instrument_from_plain_symbol() -> None:
    assert OKXInstrument.from_symbol("BTCUSDT").inst_id == "BTC-USDT-SWAP"


def test_okx_instrument_from_file_symbol() -> None:
    assert OKXInstrument.from_symbol("ETH_USDT_USDT").inst_id == "ETH-USDT-SWAP"


def test_order_preview_is_isolated_and_clordid_limited() -> None:
    preview = okx_place_order_preview(
        inst_id="BTC-USDT-SWAP",
        side="buy",
        size=1.5,
        price=65000,
        client_order_id="x" * 80,
    )
    assert preview["tdMode"] == "isolated"
    assert preview["ordType"] == "limit"
    assert preview["clOrdId"] == "x" * 32


def test_place_order_does_not_silently_drop_tp_sl(monkeypatch) -> None:
    calls = []

    def fake_request(method, path, params):
        calls.append((method, path, params))
        return {"code": "0", "data": [{"ordId": "ord-1"}]}

    monkeypatch.setattr(okx, "_request", fake_request)

    with pytest.raises(ValueError, match="TP/SL"):
        place_order(
            OrderParams(
                inst_id="BTC-USDT-SWAP",
                side="buy",
                size=1.0,
                stop_loss=95.0,
                take_profit=110.0,
            )
        )

    assert calls == []


def test_place_order_is_disabled_by_default(monkeypatch) -> None:
    calls = []

    def fake_request(method, path, params):
        calls.append((method, path, params))
        return {"code": "0", "data": [{"ordId": "ord-1"}]}

    monkeypatch.delenv("OKX_LIVE_ORDER_ENABLED", raising=False)
    monkeypatch.setattr(okx, "_request", fake_request)

    with pytest.raises(RuntimeError, match="live order execution is disabled"):
        place_order(
            OrderParams(
                inst_id="BTC-USDT-SWAP",
                side="buy",
                size=1.0,
            )
        )

    assert calls == []


def test_okx_rest_proxy_env_can_disable_fallback(monkeypatch) -> None:
    monkeypatch.setenv("OKX_REST_PROXY", "off")
    assert _okx_rest_proxy_url() is None


def test_proxy_dict_sets_http_and_https() -> None:
    assert _proxy_dict("http://127.0.0.1:1088") == {
        "http": "http://127.0.0.1:1088",
        "https": "http://127.0.0.1:1088",
    }


def test_get_candles_normalizes_one_hour_bar(monkeypatch) -> None:
    captured = {}

    def fake_request(method, path, params):
        captured.update(params)
        return {"code": "0", "data": []}

    monkeypatch.setattr(okx, "_request", fake_request)
    okx.get_candles("BTC-USDT-SWAP", bar="1h", before="1000", after="2000")
    assert captured["bar"] == "1H"
    assert captured["before"] == "1000"
    assert captured["after"] == "2000"
