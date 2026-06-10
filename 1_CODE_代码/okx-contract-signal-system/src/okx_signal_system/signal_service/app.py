from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from okx_signal_system.config import project_paths


SIDE_LABELS = {
    "long": "做多",
    "short": "做空",
    "flat": "不交易",
}

REASON_LABELS = {
    "NO_BREAKOUT": "价格还没有突破入场线",
    "ATR_MISSING": "波动率数据不足",
    "BREAKOUT_MISSING": "突破窗口数据不足",
    "4H_FLAT": "4小时趋势不明确",
    "no_breakout": "价格还没有突破入场线",
    "flat_4h_bias": "4小时趋势不明确",
    "atr_missing": "波动率数据不足",
    "breakout_missing": "突破窗口数据不足",
}


def pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "-"


def money(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.4f}"
    except (TypeError, ValueError):
        return "-"


def signal_view_model(payload: dict[str, Any]) -> dict[str, Any]:
    signal = payload.get("signal", {})
    risk = payload.get("risk", {})
    side = signal.get("side", "flat")
    accepted = bool(risk.get("accepted"))
    reason_codes = signal.get("reason_codes") or []
    reject_reason = signal.get("reject_reason") or risk.get("reason")
    readable_reasons = [REASON_LABELS.get(code, str(code)) for code in reason_codes]
    if reject_reason:
        readable_reasons.append(REASON_LABELS.get(reject_reason, str(reject_reason)))

    if accepted:
        headline = f"有信号：{SIDE_LABELS.get(side, side)}"
        action = "只做人工确认，不会自动下单。"
        tone = "success"
    else:
        headline = "现在不交易"
        action = "等待下一根已闭合 1h K 线。"
        tone = "warning"

    return {
        "headline": headline,
        "action": action,
        "tone": tone,
        "side": SIDE_LABELS.get(side, side),
        "inst_id": signal.get("inst_id", "-"),
        "signal_time": signal.get("ts", "-"),
        "entry_ref": money(signal.get("entry_ref")),
        "stop_loss": money(signal.get("stop_loss")),
        "take_profit": money(signal.get("take_profit")),
        "max_hold_bars": signal.get("max_hold_bars") or "-",
        "qty": money(risk.get("qty")),
        "risk_amount": money(risk.get("risk_amount")),
        "leverage_cap": risk.get("leverage_cap") or 0,
        "margin_mode": "逐仓" if risk.get("margin_mode") == "isolated" else risk.get("margin_mode", "-"),
        "position_mode": "单向" if risk.get("position_mode") == "net_mode" else risk.get("position_mode", "-"),
        "reasons": sorted(set(readable_reasons)) or ["无"],
        "live_order": "关闭" if not payload.get("live_order_enabled") else "开启",
    }


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def readable_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades
    table = trades.tail(30).copy()
    side_map = {"long": "做多", "short": "做空"}
    rename = {
        "inst_id": "合约",
        "entry_time": "开仓时间",
        "exit_time": "平仓时间",
        "side": "方向",
        "entry_price": "开仓价",
        "exit_price": "平仓价",
        "qty": "数量",
        "gross_pnl": "毛盈亏",
        "costs": "费用",
        "net_pnl": "净盈亏",
        "exit_reason": "平仓原因",
        "leverage_cap": "杠杆上限",
    }
    table["side"] = table["side"].map(side_map).fillna(table["side"])
    keep = [col for col in rename if col in table.columns]
    table = table[keep].rename(columns=rename)
    for col in ["开仓价", "平仓价", "数量", "毛盈亏", "费用", "净盈亏"]:
        if col in table.columns:
            table[col] = table[col].map(money)
    return table


def render_signal(payload: dict[str, Any]) -> None:
    view = signal_view_model(payload)
    if view["tone"] == "success":
        st.success(view["headline"])
    else:
        st.warning(view["headline"])
    st.caption(view["action"])

    cols = st.columns(4)
    cols[0].metric("合约", view["inst_id"])
    cols[1].metric("方向", view["side"])
    cols[2].metric("实盘下单", view["live_order"])
    cols[3].metric("杠杆上限", f"{view['leverage_cap']}x")

    price_cols = st.columns(4)
    price_cols[0].metric("参考入场", view["entry_ref"])
    price_cols[1].metric("止损", view["stop_loss"])
    price_cols[2].metric("止盈", view["take_profit"])
    price_cols[3].metric("最长持仓", view["max_hold_bars"])

    risk_cols = st.columns(3)
    risk_cols[0].metric("建议数量", view["qty"])
    risk_cols[1].metric("本笔风险额", view["risk_amount"])
    risk_cols[2].metric("保证金 / 仓位", f"{view['margin_mode']} / {view['position_mode']}")

    st.write("原因：")
    for reason in view["reasons"]:
        st.write(f"- {reason}")


def main() -> None:
    st.set_page_config(page_title="OKX 半自动信号面板", layout="wide")
    paths = project_paths()
    st.title("OKX 半自动信号面板")
    st.caption("本地研究与人工确认；默认不会自动实盘下单。")

    summary_path = paths.output_dir / "sample_summary.json"
    trades_path = paths.output_dir / "sample_trades.csv"
    signal_path = paths.output_dir / "latest_signal.json"

    summary = load_json(summary_path) or {}
    cols = st.columns(5)
    cols[0].metric("交易次数", summary.get("total_trades", 0))
    cols[1].metric("胜率", pct(summary.get("win_rate")))
    cols[2].metric("总收益", pct(summary.get("total_return")))
    cols[3].metric("盈亏比", money(summary.get("payoff_ratio")))
    cols[4].metric("最大回撤", pct(summary.get("max_drawdown")))

    st.subheader("当前信号")
    payload = load_json(signal_path)
    if payload:
        render_signal(payload)
    else:
        st.info("还没有生成最新信号。")

    st.subheader("最近交易")
    if trades_path.exists():
        trades = pd.read_csv(trades_path)
        st.dataframe(readable_trades(trades), use_container_width=True, hide_index=True)
    else:
        st.info("还没有交易明细。")

    with st.expander("技术详情"):
        if payload:
            st.json(payload)
        if trades_path.exists():
            st.dataframe(pd.read_csv(trades_path), use_container_width=True)


if __name__ == "__main__":
    main()
