from okx_signal_system.exchange import okx
from okx_signal_system.exchange.okx import OKXInstrument, _okx_rest_proxy_url, _proxy_dict, okx_place_order_preview


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
    okx.get_candles("BTC-USDT-SWAP", bar="1h")
    assert captured["bar"] == "1H"
