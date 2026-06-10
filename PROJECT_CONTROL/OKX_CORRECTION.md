# OKX 纠正文档

报告中所有 Binance / Bybit 交易所实现依据统一替换为 OKX。

实施口径：
- 合约标的使用 OKX SWAP。
- 交易 ID 使用 OKX `instId`，例如 `BTC-USDT-SWAP`。
- 仓位默认单向模式。
- 保证金模式默认逐仓。
- 默认不开启实盘下单。
- 资金费、标记价格、维护保证金、最小下单量后续只接 OKX 数据。
