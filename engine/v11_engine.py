#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
N 字二板量化模型 v1.1 分阶段引擎

阶段：
  preopen  09:25 盘前选股，生成股票池
  buy      09:50 检查 09:30-09:45 是否涨超3%，触发买入
  review   16:30 收盘复盘，断板卖出，更新净值
"""
import argparse
import datetime
import json
import os
import sys
import time

import paper_engine as pe
import preopen_pool

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "paper_account_v11.json")
FEE = 0.0003
SLIP = 0.001
INITIAL = 100_000.0
MAX_POSITIONS = 5
DAILY_NEW = 3


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def empty_state(date):
    return {
        "account": "N字二板模拟账户 v1.1",
        "version": "1.1.0",
        "model": "N字二板量化模型 v1.1",
        "initial_capital": INITIAL,
        "start_date": date,
        "cash": INITIAL,
        "positions": [],
        "stock_pool": [],
        "stock_pool_date": None,
        "buy_signals": [],
        "trades": [],
        "reviews": [],
        "equity_curve": [{"date": date, "equity": INITIAL}],
        "last_preopen_date": None,
        "last_buy_date": None,
        "last_review_date": None,
        "market": None,
        "position_sizing": "单笔20%，最多5笔，单日最多3笔",
        "costs": {"commission": FEE, "slippage": SLIP},
    }


def get_state(path):
    if os.path.exists(path):
        return read_json(path)
    return None


def ensure_state(path, date):
    state = get_state(path)
    if state is None:
        state = empty_state(date)
        write_json(path, state)
    return state


def stage_preopen(date, path):
    dates = pe.get_trade_dates(date)
    if date not in dates:
        return {"ok": False, "message": f"{date} 不是交易日"}
    result = preopen_pool.build(date)
    if not result.get("ok"):
        return result
    state = ensure_state(path, date)
    state["stock_pool"] = result["pool"]
    state["stock_pool_date"] = date
    state["market"] = result["market"]
    state["last_preopen_date"] = date
    write_json(path, state)
    counts = {
        s: sum(1 for x in result["pool"] if x.get("status") == s)
        for s in ("待触发", "等待回调", "淘汰")
    }
    return {
        "ok": True,
        "stage": "preopen",
        "date": date,
        "counts": counts,
        "pool": result["pool"],
        "message": f"盘前池已生成：待触发 {counts['待触发']}，等待回调 {counts['等待回调']}，淘汰 {counts['淘汰']}",
    }


def stage_buy(date, path):
    dates = pe.get_trade_dates(date)
    if date not in dates:
        return {"ok": False, "message": f"{date} 不是交易日"}
    state = ensure_state(path, date)
    if date < state.get("start_date", date):
        return {"ok": False, "message": "尚未到模拟开始日期"}
    if state.get("last_buy_date") == date:
        return {"ok": False, "message": f"{date} 买入阶段已运行，请勿重复"}
    if state.get("stock_pool_date") != date:
        pre = stage_preopen(date, path)
        if not pre.get("ok"):
            return pre
        state = get_state(path)

    candidates = [
        p for p in state.get("stock_pool", []) if p.get("status") == "待触发"
    ]
    # 同一代码只保留最近一次首板
    unique = {}
    for p in candidates:
        unique[p["code"]] = p
    candidates = sorted(unique.values(), key=lambda p: p.get("first_date", ""), reverse=True)
    minute = pe.minute_trigger(date, candidates)
    available = sum(1 for c in candidates if minute.get(c["code"]))
    if candidates and available == 0:
        return {
            "ok": False,
            "message": "09:30-09:45 分钟数据尚未准备好，稍后重试",
        }

    triggered = []
    for cand in candidates:
        bars = minute.get(cand["code"], [])
        if not bars:
            cand["buy_status"] = "无分钟数据"
            continue
        bars = sorted(bars, key=lambda r: r["trade_time"])
        entry_price = None
        for bar in bars:
            if float(bar.get("high") or 0) >= float(cand["threshold"]):
                first_open = float(bars[0].get("open") or 0)
                entry_price = max(float(cand["threshold"]), first_open)
                break
        if entry_price is None:
            cand["buy_status"] = "未触发"
            continue
        prev_close = float(cand.get("prev_close") or 0)
        gap = entry_price / prev_close - 1 if prev_close else 0
        if gap < 0.03 or gap > 0.05:
            cand["buy_status"] = f"高开{gap*100:.2f}%不符"
            continue
        cand["entry_price"] = entry_price
        cand["entry_gap"] = gap
        cand["buy_status"] = "触发买入"
        triggered.append(cand)

    cash = float(state["cash"])
    positions = state["positions"]
    equity_prior = cash
    for p in positions:
        equity_prior += p["shares"] * p.get("entry_price", 0)
    slots = min(MAX_POSITIONS - len(positions), DAILY_NEW, len(triggered))
    trades = []
    for cand in triggered[: max(0, slots)]:
        amount = min(equity_prior * 0.20, cash)
        if amount <= 0:
            break
        price = float(cand["entry_price"])
        shares = amount / (price * (1 + FEE + SLIP))
        cost = shares * price * (1 + FEE + SLIP)
        cash -= cost
        pos = {
            "code": cand["code"],
            "name": cand["name"],
            "branch": cand.get("branch"),
            "entry_date": date,
            "entry_price": price,
            "shares": shares,
            "cost": cost,
            "status": "待收盘确认",
        }
        positions.append(pos)
        trade = {
            "date": date,
            "side": "BUY",
            "code": cand["code"],
            "name": cand["name"],
            "branch": cand.get("branch"),
            "price": price,
            "shares": shares,
            "pnl": 0.0,
            "reason": "09:45前涨超3%触发",
        }
        trades.append(trade)
        for p in state["stock_pool"]:
            if p.get("code") == cand["code"]:
                p["status"] = "触发买入"
                p["entry_price"] = price
                p["entry_gap"] = cand["entry_gap"]
    # 未触发的池内股票更新状态
    triggered_codes = {t["code"] for t in trades}
    for p in state["stock_pool"]:
        if p.get("status") == "待触发" and p.get("code") not in triggered_codes:
            p["status"] = p.get("buy_status", "未触发")

    state["cash"] = cash
    state["positions"] = positions
    state["trades"].extend(trades)
    state["buy_signals"].extend(triggered)
    state["last_buy_date"] = date
    write_json(path, state)
    return {
        "ok": True,
        "stage": "buy",
        "date": date,
        "triggered": len(triggered),
        "bought": len(trades),
        "trades": trades,
        "positions": positions,
        "message": f"09:50 买入阶段完成：触发 {len(triggered)}，成交 {len(trades)}",
    }


def stage_review(date, path):
    dates = pe.get_trade_dates(date)
    if date not in dates:
        return {"ok": False, "message": f"{date} 不是交易日"}
    state = ensure_state(path, date)
    if state.get("last_review_date") == date:
        return {"ok": False, "message": f"{date} 复盘已运行，请勿重复"}
    previous_equity = (
        state["equity_curve"][-1]["equity"]
        if state.get("equity_curve")
        else state["initial_capital"]
    )
    rows = pe.ensure_daily(date)
    today_map = {r["ts_code"]: r for r in rows}
    limits = {r["ts_code"] for r in pe.ensure_limit(date) if r.get("limit") == "U"}
    cash = float(state["cash"])
    positions = []
    trades = []
    for pos in state["positions"]:
        row = today_map.get(pos["code"])
        if not row:
            positions.append(pos)
            continue
        if pos["code"] in limits:
            pos["status"] = "封板/连板中"
            positions.append(pos)
            continue
        price = float(row.get("close") or 0)
        proceeds = pos["shares"] * price * (1 - FEE - SLIP)
        cash += proceeds
        pnl = proceeds - pos["cost"]
        trades.append(
            {
                "date": date,
                "side": "SELL",
                "code": pos["code"],
                "name": pos["name"],
                "branch": pos.get("branch"),
                "price": price,
                "shares": pos["shares"],
                "pnl": pnl,
                "reason": "未封板/断板收盘卖",
            }
        )
    state["cash"] = cash
    state["positions"] = positions
    state["trades"].extend(trades)
    equity = cash
    for pos in positions:
        row = today_map.get(pos["code"])
        price = float(row.get("close") or 0) if row else pos["entry_price"]
        equity += pos["shares"] * price
    state["equity_curve"] = [
        p for p in state["equity_curve"] if p.get("date") != date
    ] + [{"date": date, "equity": equity}]
    state["equity_curve"].sort(key=lambda p: p["date"])
    realized_pnl = sum(t.get("pnl", 0) for t in trades)
    day_pnl = equity - previous_equity
    review = {
        "date": date,
        "equity": equity,
        "cash": cash,
        "positions": len(positions),
        "sells": len(trades),
        "realized_pnl": realized_pnl,
        "day_pnl": day_pnl,
        "total_return": equity / state["initial_capital"] - 1,
    }
    state["reviews"].append(review)
    state["last_review_date"] = date
    write_json(path, state)
    return {
        "ok": True,
        "stage": "review",
        "date": date,
        "equity": equity,
        "day_pnl": day_pnl,
        "positions": positions,
        "trades": trades,
        "message": f"收盘复盘完成：卖出 {len(trades)}，当日盈亏 {day_pnl:.2f}，净值 {equity:.2f}",
    }


def run_stage(stage, date, path):
    if stage == "preopen":
        return stage_preopen(date, path)
    if stage == "buy":
        return stage_buy(date, path)
    if stage == "review":
        return stage_review(date, path)
    return {"ok": False, "message": f"未知阶段：{stage}"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=["preopen", "buy", "review"])
    parser.add_argument("--date", required=True)
    parser.add_argument("--state", default=STATE)
    args = parser.parse_args()
    result = run_stage(args.stage, args.date, args.state)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("ok"):
        sys.exit(0)
    message = str(result.get("message", ""))
    if "已运行" in message or "不是交易日" in message:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
