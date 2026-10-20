#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
N 字二板量化模型 v1.2 候选版

相对 v1.1 新增两个过滤：
  1. 排除首板换手率 10%-20%（中间死亡区）
  2. 排除流通市值 100-200 亿（中间市值区）

用法：
  python3 v12_engine.py --stage preopen --date 20261020 --state paper_account_v12.json
"""
import argparse
import json
import os
import sys

import v11_engine

# v1.2：单笔 50%，最多同时 2 笔
v11_engine.POSITION_WEIGHT = 0.50
v11_engine.MAX_POSITIONS = 2
v11_engine.DAILY_NEW = 2

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "paper_account_v12.json")


def apply_v12_filters(state):
    for item in state.get("stock_pool", []):
        if item.get("status") != "待触发":
            continue
        turnover = item.get("first_turnover")
        float_mv = item.get("float_mv")
        if turnover is not None and 10 <= float(turnover) < 20:
            item["status"] = "淘汰"
            item["reason"] = "v1.2过滤：首板换手率 10%-20% 中间区"
            continue
        if float_mv is not None and 100e8 <= float(float_mv) < 200e8:
            item["status"] = "淘汰"
            item["reason"] = "v1.2过滤：流通市值 100-200 亿中间区"
    return state


def stage_preopen(date, path):
    result = v11_engine.stage_preopen(date, path)
    if not result.get("ok"):
        return result
    state = v11_engine.read_json(path)
    state["version"] = "1.2.0"
    state["model"] = "N字二板量化模型 v1.2"
    state["v12_filters"] = [
        "排除首板换手率 10%-20%",
        "排除流通市值 100-200 亿",
    ]
    apply_v12_filters(state)
    v11_engine.write_json(path, state)
    pool = state.get("stock_pool", [])
    counts = {
        status: sum(1 for x in pool if x.get("status") == status)
        for status in ("待触发", "等待回调", "淘汰")
    }
    result.update(
        {
            "version": "1.2.0",
            "counts": counts,
            "pool": pool,
            "message": (
                f"v1.2 盘前池：待触发 {counts['待触发']}，"
                f"等待回调 {counts['等待回调']}，淘汰 {counts['淘汰']}"
            ),
        }
    )
    return result


def stage_buy(date, path):
    result = v11_engine.stage_buy(date, path)
    if result.get("ok"):
        state = v11_engine.read_json(path)
        state["version"] = "1.2.0"
        state["model"] = "N字二板量化模型 v1.2"
        state["v12_filters"] = [
            "排除首板换手率 10%-20%",
            "排除流通市值 100-200 亿",
        ]
        v11_engine.write_json(path, state)
    return result


def stage_review(date, path):
    result = v11_engine.stage_review(date, path)
    if result.get("ok"):
        state = v11_engine.read_json(path)
        state["version"] = "1.2.0"
        state["model"] = "N字二板量化模型 v1.2"
        state["v12_filters"] = [
            "排除首板换手率 10%-20%",
            "排除流通市值 100-200 亿",
        ]
        v11_engine.write_json(path, state)
    return result


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
