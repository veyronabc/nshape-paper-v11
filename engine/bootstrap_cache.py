#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为 GitHub Actions 预热行情缓存。"""
import datetime
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

import miana_api as api
import paper_engine as pe

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
SHARD = os.path.join(CACHE, "v2")


def ensure_stock_basic():
    path = os.path.join(CACHE, "stock_basic.jsonl")
    if os.path.exists(path) and os.path.getsize(path) > 10:
        return
    os.makedirs(CACHE, exist_ok=True)
    rows = api.call("stock_basic", list_status="L", retries=4)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main():
    today = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    dates = pe.get_trade_dates(today)
    wanted = [d for d in dates if d <= today][-90:]
    print(f"bootstrap dates={len(wanted)} {wanted[0]}~{wanted[-1]}", flush=True)
    ensure_stock_basic()
    os.makedirs(SHARD, exist_ok=True)

    missing = [
        d
        for d in wanted
        if not os.path.exists(os.path.join(SHARD, f"daily_{d}.json"))
    ]
    print(f"missing daily shards={len(missing)}", flush=True)

    def one(date):
        try:
            pe.ensure_daily(date)
            return date, True
        except Exception as exc:
            return date, str(exc)[:80]

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(one, d) for d in missing]
        for i, fut in enumerate(as_completed(futs), 1):
            date, ok = fut.result()
            if i % 20 == 0 or not ok:
                print(f"  {i}/{len(missing)} {date} {ok}", flush=True)

    # 当前月涨跌停 + 沪深300
    try:
        pe.ensure_limit(today)
    except Exception as exc:
        print("limit warmup:", exc, flush=True)
    try:
        pe.ensure_index(today)
    except Exception as exc:
        print("index warmup:", exc, flush=True)
    print("bootstrap done", flush=True)


if __name__ == "__main__":
    sys.exit(main())
