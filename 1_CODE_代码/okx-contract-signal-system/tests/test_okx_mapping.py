from okx_signal_system.exchange.okx import OKXInstrument, okx_place_order_preview


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
