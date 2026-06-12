"""Feishu notification helpers."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
FEISHU_WEBHOOK = FEISHU_WEBHOOK_URL


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def send_text(text: str, webhook_url: str | None = None, max_retries: int = 3) -> bool:
    webhook_url = webhook_url or FEISHU_WEBHOOK_URL
    if not webhook_url:
        log.info("Feishu webhook is not configured; notification skipped")
        return False

    payload = {"msg_type": "text", "content": {"text": text}}
    for attempt in range(max_retries):
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
            if response.status_code == 200:
                log.info("Feishu notification sent")
                return True
            log.warning(
                "Feishu notification failed (%s/%s): %s %s",
                attempt + 1,
                max_retries,
                response.status_code,
                response.text,
            )
        except Exception as exc:
            log.warning("Feishu notification error (%s/%s): %s", attempt + 1, max_retries, exc)
        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)
    return False


def send_signal_alert(
    inst_id: str,
    side: str,
    entry_ref: float,
    stop_loss: float,
    take_profit: float,
    qty: float,
    leverage: float,
    reason: str = "",
    *,
    signal_score: float | None = None,
    risk_reward_ratio: float | None = None,
    stop_reason: str = "",
    tp_reason: str = "",
    max_loss_pct: float | None = None,
    kline_time: str | None = None,
) -> bool:
    direction = "LONG" if side == "long" else "SHORT"
    stop_pct = abs(entry_ref - stop_loss) / entry_ref * 100 if entry_ref else 0.0
    tp_pct = abs(take_profit - entry_ref) / entry_ref * 100 if entry_ref else 0.0
    rr = risk_reward_ratio if risk_reward_ratio is not None else (tp_pct / stop_pct if stop_pct else 0.0)
    lines = [
        "OKX signal",
        f"time: {_now_utc():%Y-%m-%d %H:%M:%S} UTC",
        f"symbol: {inst_id}",
        f"side: {direction}",
        f"entry: {entry_ref:.8f}",
        f"stop: {stop_loss:.8f} ({stop_pct:.2f}%)",
        f"take_profit: {take_profit:.8f} ({tp_pct:.2f}%)",
        f"qty: {qty:.8f}",
        f"leverage: {leverage:.2f}x",
        f"risk_reward: {rr:.2f}:1",
    ]
    if signal_score is not None:
        lines.append(f"score: {signal_score:.1f}/10")
    if max_loss_pct is not None:
        lines.append(f"max_loss_pct: {max_loss_pct:.2%}")
    if kline_time:
        lines.append(f"kline_time: {kline_time}")
    if stop_reason:
        lines.append(f"stop_reason: {stop_reason}")
    if tp_reason:
        lines.append(f"take_profit_reason: {tp_reason}")
    if reason:
        lines.append(f"reason: {reason}")
    lines.append("manual confirmation required")
    return send_text("\n".join(lines))


def send_close_notification(
    inst_id: str,
    side: str,
    entry_price: float,
    exit_price: float,
    size: float,
    exit_reason: str,
    gross_pnl: float,
    net_pnl: float,
    net_pnl_pct: float,
    entry_fee: float = 0,
    exit_fee: float = 0,
    slippage_cost: float = 0,
    funding_fee: float = 0,
    total_costs: float = 0,
    signal_score: float | None = None,
) -> bool:
    lines = [
        "OKX position closed",
        f"time: {_now_utc():%Y-%m-%d %H:%M:%S} UTC",
        f"symbol: {inst_id}",
        f"side: {side}",
        f"reason: {exit_reason}",
        f"entry: {entry_price:.8f}",
        f"exit: {exit_price:.8f}",
        f"size: {size:.8f}",
        f"gross_pnl: {gross_pnl:+.8f}",
        f"entry_fee: -{entry_fee:.8f}",
        f"exit_fee: -{exit_fee:.8f}",
        f"slippage_cost: -{slippage_cost:.8f}",
        f"funding_fee: -{funding_fee:.8f}",
        f"total_costs: -{total_costs:.8f}",
        f"net_pnl: {net_pnl:+.8f} ({net_pnl_pct:+.2%})",
    ]
    if signal_score is not None:
        lines.append(f"score: {signal_score:.1f}/10")
    return send_text("\n".join(lines))


def send_status_report(
    *,
    cycle_count: int,
    equity: float,
    open_positions: int,
    status: str,
    loss_streak: int = 0,
    max_drawdown: float = 0.0,
    last_signal_count: int | None = None,
) -> bool:
    lines = [
        "OKX system status",
        f"time: {_now_utc():%Y-%m-%d %H:%M:%S} UTC",
        f"cycle: {cycle_count}",
        f"status: {status}",
        f"equity: {equity:.2f}",
        f"open_positions: {open_positions}",
        f"loss_streak: {loss_streak}",
        f"max_drawdown: {max_drawdown:.2%}",
    ]
    if last_signal_count is not None:
        lines.append(f"last_signal_count: {last_signal_count}")
    return send_text("\n".join(lines))


def feishu_send_text(text: str, **kwargs) -> bool:
    return send_text(text, **kwargs)


def feishu_send_signal_card(
    inst_id: str,
    direction: str,
    qty: float,
    leverage: float,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    reason: str,
) -> bool:
    side = "long" if direction in {"long", "buy", "open_long"} else "short"
    return send_signal_alert(inst_id, side, entry_price, stop_loss, take_profit, qty, leverage, reason)


def feishu_send_status_card(
    equity: float,
    open_positions: int,
    status: str,
    loss_streak: int = 0,
    max_drawdown: float = 0.0,
    cycle_count: int = 0,
    last_signal_count: int | None = None,
) -> bool:
    return send_status_report(
        cycle_count=cycle_count,
        equity=equity,
        open_positions=open_positions,
        status=status,
        loss_streak=loss_streak,
        max_drawdown=max_drawdown,
        last_signal_count=last_signal_count,
    )


def feishu_test() -> bool:
    return send_text("OKX signal system notification test")
