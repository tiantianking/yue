from okx_signal_system.exchange import okx
from okx_signal_system.exchange.okx import OKXInstrument, _okx_rest_proxy_url, _proxy_dict


def test_okx_instrument_from_plain_symbol() -> None:
    assert OKXInstrument.from_symbol("BTCUSDT").inst_id == "BTC-USDT-SWAP"


def test_okx_instrument_from_file_symbol() -> None:
    assert OKXInstrument.from_symbol("ETH_USDT_USDT").inst_id == "ETH-USDT-SWAP"


def test_okx_public_adapter_exposes_no_private_execution_api() -> None:
    forbidden = [
        "OrderParams",
        "okx_place_order_preview",
        "place_order",
        "close_position",
        "get_open_orders",
        "get_account_balance",
        "get_account_positions",
    ]
    for name in forbidden:
        assert not hasattr(okx, name)


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

    def fake_request(path, params):
        captured.update(params)
        return {"code": "0", "data": []}

    monkeypatch.setattr("okx_signal_system.exchange.okx_public._request_public", fake_request)
    okx.get_candles("BTC-USDT-SWAP", bar="1h", before="1000", after="2000")
    assert captured["bar"] == "1H"
    assert captured["before"] == "1000"
    assert captured["after"] == "2000"
