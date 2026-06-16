"""Feishu notification helpers."""
from __future__ import annotations

import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
FEISHU_WEBHOOK = FEISHU_WEBHOOK_URL


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _format_beijing_time(value: Any) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S 北京时间")


def _now_beijing_text() -> str:
    return _format_beijing_time(_now_utc())


def _entry_type_from_reason(reason: str) -> str:
    upper = reason.upper()
    if "PULLBACK_RECLAIM" in upper:
        return "回踩确认"
    if "BREAKOUT" in upper:
        return "突破信号"
    return "策略信号"


QUALITY_MODEL_FIELDS = ("p_tp", "p_sl", "p_timeout", "expected_net_r", "uncertainty")


def _candidate_quality_model(candidate: Any) -> dict[str, Any] | None:
    quality_model = _candidate_value(candidate, "quality_model")
    if isinstance(quality_model, dict):
        return quality_model
    payload = _candidate_value(candidate, "payload")
    if isinstance(payload, dict):
        quality_model = payload.get("quality_model")
        if isinstance(quality_model, dict):
            return quality_model
    health = _candidate_value(candidate, "health_item")
    if isinstance(health, dict):
        quality_model = health.get("quality_model")
        if isinstance(quality_model, dict):
            return quality_model
    return None


def _quality_model_line(quality_model: dict[str, Any] | None) -> str | None:
    if not isinstance(quality_model, dict):
        return None
    parts: list[str] = []
    for key in QUALITY_MODEL_FIELDS:
        value = quality_model.get(key)
        if value is None:
            continue
        try:
            parts.append(f"{key}={float(value):.3f}")
        except (TypeError, ValueError):
            parts.append(f"{key}={value}")
    if not parts:
        return None
    return "质量模型旁路: " + ", ".join(parts)


def _health_reason_label(reason: str) -> str:
    reason = str(reason or "unknown")
    mapping = {
        "position_open": "已有持仓",
        "cooldown": "冷却中",
        "history_too_short": "历史K线太少",
        "stale_data": "K线太旧",
        "stale_signal_bar": "信号K线过期",
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


def send_signal_observation(
    inst_id: str,
    side: str,
    entry_ref: float,
    stop_loss: float,
    take_profit: float,
    reason: str = "",
    *,
    signal_score: float | None = None,
    risk_reward_ratio: float | None = None,
    stop_reason: str = "",
    tp_reason: str = "",
    kline_time: str | None = None,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
    tier: str | None = None,
    rank: int | None = None,
    total_candidates: int | None = None,
    lifecycle_status: str | None = None,
    invalidation_price: float | None = None,
    quality_model: dict[str, Any] | None = None,
) -> bool:
    direction = "LONG" if side == "long" else "SHORT"
    stop_pct = abs(entry_ref - stop_loss) / entry_ref * 100 if entry_ref else 0.0
    tp_pct = abs(take_profit - entry_ref) / entry_ref * 100 if entry_ref else 0.0
    rr = risk_reward_ratio if risk_reward_ratio is not None else (tp_pct / stop_pct if stop_pct else 0.0)
    title = f"OKX {tier}级信号观察" if tier else "OKX 信号观察"
    signal_time_text = _format_beijing_time(kline_time) if kline_time else _now_beijing_text()
    lines = [
        title,
        f"信号生成时间: {signal_time_text}",
        f"通知发送时间: {_now_beijing_text()}",
        f"币种: {inst_id}",
        f"方向: {'做多' if direction == 'LONG' else '做空'}",
        f"入场: {entry_ref:.8f}",
        f"止损: {stop_loss:.8f} ({stop_pct:.2f}%)",
        f"止盈: {take_profit:.8f} ({tp_pct:.2f}%)",
        f"目标盈亏比: {rr:.2f}R",
        f"信号类型: {_entry_type_from_reason(reason)}",
    ]
    if signal_score is not None:
        lines.append(f"评分: {signal_score:.1f}/10")
    if rank is not None and total_candidates is not None:
        lines.append(f"21币横向排名: {rank}/{total_candidates}")
    if lifecycle_status:
        lines.append(f"signal_status: {lifecycle_status}")
    if invalidation_price is not None:
        lines.append(f"invalidation_price: {invalidation_price:.8f}")
    quality_line = _quality_model_line(quality_model)
    if quality_line:
        lines.append(quality_line)
    if kline_time:
        lines.append(f"K线时间: {_format_beijing_time(kline_time)}")
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
    lines.append("提示: 仅用于信号研究和人工复核，不包含自动下单指令。")
    return send_text("\n".join(lines))


def send_signal_alert(
    inst_id: str,
    side: str,
    entry_ref: float,
    stop_loss: float,
    take_profit: float,
    reason: str = "",
    *,
    signal_score: float | None = None,
    risk_reward_ratio: float | None = None,
    stop_reason: str = "",
    tp_reason: str = "",
    kline_time: str | None = None,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
    tier: str | None = None,
    rank: int | None = None,
    total_candidates: int | None = None,
    lifecycle_status: str | None = None,
    invalidation_price: float | None = None,
    quality_model: dict[str, Any] | None = None,
) -> bool:
    return send_signal_observation(
        inst_id=inst_id,
        side=side,
        entry_ref=entry_ref,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reason=reason,
        signal_score=signal_score,
        risk_reward_ratio=risk_reward_ratio,
        stop_reason=stop_reason,
        tp_reason=tp_reason,
        kline_time=kline_time,
        signal_timeframe=signal_timeframe,
        trend_timeframe=trend_timeframe,
        tier=tier,
        rank=rank,
        total_candidates=total_candidates,
        lifecycle_status=lifecycle_status,
        invalidation_price=invalidation_price,
        quality_model=quality_model,
    )


def _candidate_value(candidate: Any, name: str, default: Any = None) -> Any:
    if hasattr(candidate, name):
        return getattr(candidate, name)
    if isinstance(candidate, dict):
        return candidate.get(name, default)
    return default


def _candidate_signal_value(candidate: Any, name: str, default: Any = None) -> Any:
    signal = _candidate_value(candidate, "signal")
    if signal is not None and hasattr(signal, name):
        return getattr(signal, name)
    if isinstance(signal, dict):
        return signal.get(name, default)
    return default


def _candidate_decision_value(candidate: Any, name: str, default: Any = None) -> Any:
    decision = _candidate_value(candidate, "decision")
    if decision is not None and hasattr(decision, name):
        return getattr(decision, name)
    if isinstance(decision, dict):
        return decision.get(name, default)
    return default


def _candidate_health_value(candidate: Any, name: str, default: Any = None) -> Any:
    health = _candidate_value(candidate, "health_item")
    if isinstance(health, dict):
        return health.get(name, default)
    return default


def _format_candidate_time(value: Any) -> str:
    if value is None:
        return "-"
    return _format_beijing_time(value)


def send_b_tier_summary(
    candidates: list[Any],
    *,
    total_candidates: int | None = None,
    signal_timeframe: str | None = None,
    trend_timeframe: str | None = None,
    max_items: int = 5,
) -> bool:
    if not candidates:
        return False

    candle_time = _candidate_value(candidates[0], "candle_time", None)
    if candle_time is None:
        candle_time = _candidate_signal_value(candidates[0], "ts", None)
    candle_text = _format_candidate_time(candle_time)
    lines = [
        "OKX B-tier candidate summary",
        f"time: {_now_beijing_text()}",
        f"candle_time: {candle_text}",
        f"B-tier candidates: {len(candidates)}",
        "note: these are not immediate A-tier alerts; review only.",
    ]
    if total_candidates is not None:
        lines.append(f"ranked_candidates: {total_candidates}")
    if signal_timeframe:
        lines.append(f"signal_timeframe: {signal_timeframe}")
    if trend_timeframe:
        lines.append(f"trend_timeframe: {trend_timeframe}")

    lines.append("top B-tier candidates:")
    for candidate in candidates[:max_items]:
        rank = _candidate_value(candidate, "rank", "-")
        symbol = _candidate_value(candidate, "inst_id") or _candidate_signal_value(candidate, "inst_id", "-")
        side = _candidate_value(candidate, "side") or _candidate_signal_value(candidate, "side", "-")
        score = _candidate_value(candidate, "raw_score", None)
        if score is None:
            score = _candidate_decision_value(candidate, "signal_score", None)
        rr = _candidate_decision_value(candidate, "risk_reward_ratio", None)
        if rr is None:
            rr = _candidate_signal_value(candidate, "risk_reward_ratio", None)
        reason = _candidate_health_value(candidate, "reason", None)
        lifecycle_status = _candidate_health_value(candidate, "lifecycle_status", None)
        invalidation_price = _candidate_health_value(candidate, "invalidation_price", None)
        if not reason:
            reason_codes = _candidate_signal_value(candidate, "reason_codes", ())
            reason = ",".join(reason_codes) if reason_codes else "-"
        score_text = f"{float(score):.1f}" if score is not None else "-"
        rr_text = f"{float(rr):.2f}R" if rr is not None else "-"
        lifecycle_text = f" status={lifecycle_status}" if lifecycle_status else ""
        invalidation_text = f" invalidation={float(invalidation_price):.8f}" if invalidation_price is not None else ""
        quality_line = _quality_model_line(_candidate_quality_model(candidate))
        quality_text = f" {quality_line}" if quality_line else ""
        lines.append(
            f"- #{rank} {symbol} {side} "
            f"score={score_text} rr={rr_text} reason={reason}{lifecycle_text}{invalidation_text}{quality_text}"
        )
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
        "OKX signal lifecycle update",
        f"time: {_now_beijing_text()}",
        f"symbol: {inst_id}",
        f"side: {side}",
        f"reason: {exit_reason}",
        f"entry: {entry_price:.8f}",
        f"exit: {exit_price:.8f}",
        f"sample_size: {size:.8f}",
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
    status: str,
    last_signal_count: int | None = None,
) -> bool:
    lines = [
        "OKX system status",
        f"time: {_now_beijing_text()}",
        f"cycle: {cycle_count}",
        "mode: SIGNAL_ONLY",
        f"status: {status}",
        "scope: market signal research and manual review notification only",
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
        f"时间: {_now_beijing_text()}",
        "说明: 这不是正式信号，只是告诉你这一轮哪些币种更接近观察阈值。",
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
                "影子样本: "
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
    lines.append("提示: 这是体检；系统只做信号研究与通知。")
    return send_text("\n".join(lines))


def feishu_send_text(text: str, **kwargs) -> bool:
    return send_text(text, **kwargs)


def feishu_send_signal_card(
    inst_id: str,
    direction: str,
    entry_price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    reason: str = "",
) -> bool:
    side = "long" if direction in {"long", "buy", "open_long"} else "short"
    return send_signal_alert(
        inst_id=inst_id,
        side=side,
        entry_ref=entry_price or 0.0,
        stop_loss=stop_loss or 0.0,
        take_profit=take_profit or 0.0,
        reason=reason,
    )


def feishu_send_status_card(
    equity: float | None = None,
    tracked_items: int | None = None,
    status: str = "",
    loss_streak: int = 0,
    max_drawdown: float = 0.0,
    cycle_count: int = 0,
    last_signal_count: int | None = None,
    **kwargs,
) -> bool:
    return send_status_report(
        cycle_count=cycle_count,
        status=status,
        last_signal_count=last_signal_count,
    )


def feishu_test() -> bool:
    return send_text("OKX signal system notification test")
