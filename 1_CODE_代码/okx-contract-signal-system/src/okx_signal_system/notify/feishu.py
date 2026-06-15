"""Feishu notification helpers."""
from __future__ import annotations

import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone

log = logging.getLogger(__name__)

FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
FEISHU_WEBHOOK = FEISHU_WEBHOOK_URL


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _entry_type_from_reason(reason: str) -> str:
    upper = reason.upper()
    if "PULLBACK_RECLAIM" in upper:
        return "回踩确认"
    if "BREAKOUT" in upper:
        return "突破信号"
    return "策略信号"


def _health_reason_label(reason: str) -> str:
    reason = str(reason or "unknown")
    mapping = {
        "position_open": "已有持仓",
        "cooldown": "冷却中",
        "history_too_short": "历史K线太少",
        "stale_data": "K线太旧",
        "feature_error": "特征计算失败",
        "invalid_features": "特征无效",
        "signal_rejected": "信号未通过",
        "quality_gate_blocked": "训练质量门未通过",
        "score_below_6": "分数不够",
        "vote_flat": "投票偏平",
        "vote_side_mismatch": "投票方向冲突",
        "vote_support_too_low": "投票支持不足",
        "missing_latest_closed_bar": "缺少最新闭合K线",
        "waiting_next_bar": "等待下一根K线",
        "ready": "可推送",
        "not_ready": "暂不可推送",
        "no_evaluable_candidates": "没有可评估候选",
    }
    if reason.startswith("risk_"):
        return f"风控拒绝({reason.removeprefix('risk_')})"
    return mapping.get(reason, reason)


def send_text(text: str, webhook_url: str | None = None, max_retries: int = 3) -> bool:
    try:
        import requests
    except ModuleNotFoundError as exc:
        log.warning("requests is required for Feishu notifications: %s", exc)
        return False

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
    margin_loss_pct: float | None = None,
    kline_time: str | None = None,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
) -> bool:
    direction = "LONG" if side == "long" else "SHORT"
    stop_pct = abs(entry_ref - stop_loss) / entry_ref * 100 if entry_ref else 0.0
    tp_pct = abs(take_profit - entry_ref) / entry_ref * 100 if entry_ref else 0.0
    rr = risk_reward_ratio if risk_reward_ratio is not None else (tp_pct / stop_pct if stop_pct else 0.0)
    lines = [
        "OKX 正式交易信号",
        f"时间: {_now_utc():%Y-%m-%d %H:%M:%S} UTC",
        f"币种: {inst_id}",
        f"方向: {'做多' if direction == 'LONG' else '做空'}",
        f"入场: {entry_ref:.8f}",
        f"止损: {stop_loss:.8f} ({stop_pct:.2f}%)",
        f"止盈: {take_profit:.8f} ({tp_pct:.2f}%)",
        f"仓位: {qty:.8f}",
        f"杠杆: {leverage:.2f}x",
        f"目标盈亏比: {rr:.2f}R",
        f"信号类型: {_entry_type_from_reason(reason)}",
    ]
    if signal_score is not None:
        lines.append(f"评分: {signal_score:.1f}/10")
    if max_loss_pct is not None:
        lines.append(f"账户止损风险: {max_loss_pct:.2%}")
    if margin_loss_pct is not None:
        lines.append(f"保证金止损风险: {margin_loss_pct:.2%} (上限 27.00%)")
    if kline_time:
        lines.append(f"K线时间: {kline_time}")
    if signal_timeframe:
        lines.append(f"信号周期: {signal_timeframe}")
    if trend_timeframe:
        lines.append(f"趋势周期: {trend_timeframe}")
    if stop_reason:
        lines.append(f"止损原因: {stop_reason}")
    if tp_reason:
        lines.append(f"止盈原因: {tp_reason}")
    if reason:
        lines.append(f"触发原因: {reason}")
    lines.append("提示: 这是正式信号，先确认再执行。")
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


def send_candidate_health_report(
    *,
    items: list[dict],
    push_allowed: bool,
    selected_params: dict | None = None,
    max_items: int = 8,
) -> bool:
    total = len(items)
    ready = [item for item in items if item.get("would_push")]
    reasons = Counter(str(item.get("reason") or "unknown") for item in items if not item.get("would_push"))
    params = selected_params or {}
    lines = [
        "OKX 候选体检",
        f"时间: {_now_utc():%Y-%m-%d %H:%M:%S} UTC",
        "说明: 这不是正式信号，只是告诉你这一轮哪些币种更接近能下单。",
        f"结论: {'允许推送' if push_allowed else '暂不推送'}",
        f"已检查: {total} 个币种",
        f"可推送: {len(ready)} 个",
    ]
    if params:
        lines.append(
            "参数: "
            f"ATR止损 {float(params.get('atr_stop_mult', 0)):.2f}x, "
            f"目标盈亏比 {float(params.get('take_profit_mult', 0)):.2f}R"
        )
        if params.get("signal_timeframe") or params.get("trend_timeframe"):
            lines.append(
                "周期: "
                f"信号={params.get('signal_timeframe', '-')}, "
                f"趋势={params.get('trend_timeframe', '-')}"
            )
        if params.get("shadow_closed") is not None:
            lines.append(
                "影子交易: "
                f"未平仓 {int(params.get('shadow_open', 0))}, "
                f"已平仓 {int(params.get('shadow_closed', 0))}, "
                f"止盈 {int(params.get('shadow_take_profit', 0))}, "
                f"止损 {int(params.get('shadow_stop_loss', 0))}, "
                f"平均质量 {float(params.get('shadow_avg_quality_score', 0.0)):.1f}"
            )
    if reasons:
        top_reasons = ", ".join(
            f"{_health_reason_label(reason)}={count}" for reason, count in reasons.most_common(6)
        )
        lines.append(f"主要卡点: {top_reasons}")
    elif total == 0:
        lines.append("主要卡点: 没有可评估候选")

    ranked = sorted(
        items,
        key=lambda item: (
            0 if item.get("would_push") else 1,
            float(item.get("breakout_gap_pct") if item.get("breakout_gap_pct") is not None else 99.0),
            -float(item.get("final_score") or item.get("raw_score") or 0.0),
        ),
    )
    if ranked:
        lines.append("优先看:")
    else:
        lines.append("优先看: 无")
    for item in ranked[:max_items]:
        gap = item.get("breakout_gap_pct")
        gap_text = f"，离突破 {float(gap):.2%}" if gap is not None else ""
        score = item.get("final_score") if item.get("final_score") is not None else item.get("raw_score")
        score_text = f"，分数 {float(score):.1f}" if score is not None else ""
        shadow_adj = item.get("shadow_adjustment")
        shadow_text = f"，影子加权 {float(shadow_adj):+.1f}" if shadow_adj not in (None, 0, 0.0) else ""
        side = item.get("side") or item.get("bias") or "flat"
        lines.append(
            f"- {item.get('symbol')}: "
            f"{'可推送' if item.get('would_push') else '未通过'}，"
            f"{'做多' if side == 'long' else ('做空' if side == 'short' else side)}，"
            f"{_health_reason_label(item.get('reason'))}{score_text}{shadow_text}{gap_text}"
        )
    lines.append("提示: 这是体检，不是下单提醒。")
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
