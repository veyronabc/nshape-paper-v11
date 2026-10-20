#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Actions 三阶段执行器。"""
import argparse
import datetime
import json
import os
import shutil
import sys
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "engine"))

import v11_engine

STATE = os.path.join(BASE, "paper_account_v11.json")
PUBLIC_STATE = os.path.join(BASE, "public", "state.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=["preopen", "buy", "review"])
    parser.add_argument("--state", default=STATE)
    args = parser.parse_args()

    today = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    dates = v11_engine.pe.get_trade_dates(today)
    trade_date = max(d for d in dates if d <= today)
    print(f"stage={args.stage} today={today} trade_date={trade_date}", flush=True)

    result = v11_engine.run_stage(args.stage, trade_date, args.state)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    os.makedirs(os.path.dirname(PUBLIC_STATE), exist_ok=True)
    if os.path.exists(args.state):
        shutil.copyfile(args.state, PUBLIC_STATE)

    run_info = {
        "stage": args.stage,
        "trade_date": trade_date,
        "run_at": datetime.datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "ok": bool(result.get("ok")),
        "message": result.get("message", ""),
    }
    with open(os.path.join(BASE, "public", "last_run.json"), "w", encoding="utf-8") as f:
        json.dump(run_info, f, ensure_ascii=False, indent=2)

    message = str(result.get("message", ""))
    if result.get("ok") or "已运行" in message or "不是交易日" in message:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
