from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OKXInstrument:
    base: str
    quote: str = "USDT"
    contract_type: str = "SWAP"

    @property
    def inst_id(self) -> str:
        return f"{self.base}-{self.quote}-{self.contract_type}"

    @classmethod
    def from_symbol(cls, symbol: str) -> "OKXInstrument":
        normalized = symbol.replace("_", "-").upper()
        parts = normalized.split("-")
        if len(parts) == 3 and parts[1] == "USDT" and parts[2] == "USDT":
            return cls(base=parts[0], quote="USDT")
        if len(parts) == 3:
            base, quote, contract_type = parts
            return cls(base=base, quote=quote, contract_type=contract_type)
        if len(parts) == 2:
            base, quote = parts
            return cls(base=base, quote=quote)
        if normalized.endswith("USDT"):
            return cls(base=normalized[:-4], quote="USDT")
        raise ValueError(f"cannot convert symbol to OKX instrument: {symbol}")


def okx_place_order_preview(
    *,
    inst_id: str,
    side: str,
    size: float,
    price: float | None,
    client_order_id: str,
    margin_mode: str = "isolated",
) -> dict[str, str]:
    if margin_mode != "isolated":
        raise ValueError("only isolated margin is allowed")
    if not inst_id.endswith("-SWAP"):
        raise ValueError("only OKX SWAP instruments are allowed")
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if size <= 0:
        raise ValueError("size must be positive")
    body = {
        "instId": inst_id,
        "tdMode": "isolated",
        "side": side,
        "ordType": "market" if price is None else "limit",
        "sz": str(size),
        "clOrdId": client_order_id[:32],
    }
    if price is not None:
        body["px"] = str(price)
    return body
