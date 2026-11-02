#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
N 字二板模型：每日模拟盘引擎

用法：
  python3 paper_engine.py --date 20261020 --state paper_account_live.json

每天收盘后运行一次：
  1. 检查持仓是否断板并卖出
  2. 回看最近 3 个交易日的首板
  3. 判断 N 字回调、单阳不破、分支 A/B
  4. 用 09:30-09:45 分钟数据判断是否触发 +3%
  5. 按固定 20% 仓位模拟成交
"""
import argparse
import csv
import datetime
import json
import math
import os
import statistics
import sys
import threading
import time
from collections import defaultdict

import eastmoney_api as backup_api
import miana_api as api

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "cache")
SHARD = os.path.join(CACHE, "v2")
LIMIT_DIR = os.path.join(CACHE, "limit_list")
MIN_DIR = os.path.join(CACHE, "mins15")
FEE = 0.0003
SLIP = 0.001
INITIAL = 100_000.0
MAX_POSITIONS = 5
DAILY_NEW = 3
PRIMARY_SOURCE = "miana_tushare"
LEGACY_CACHE_SOURCE = "miana_tushare_cache"

_PROVENANCE_LOCK = threading.Lock()
_PROVENANCE = {"sources": {}, "fallbacks": []}


def reset_provenance():
    with _PROVENANCE_LOCK:
        _PROVENANCE["sources"].clear()
        _PROVENANCE["fallbacks"].clear()


def get_provenance():
    with _PROVENANCE_LOCK:
        return {
            "sources": {
                kind: dict(values)
                for kind, values in _PROVENANCE["sources"].items()
            },
            "fallbacks": [dict(item) for item in _PROVENANCE["fallbacks"]],
        }


def _record_source(kind, key, source, fallback_reason=None):
    with _PROVENANCE_LOCK:
        _PROVENANCE["sources"].setdefault(kind, {})[str(key)] = source
        if fallback_reason:
            item = {
                "data": kind,
                "key": str(key),
                "primary": PRIMARY_SOURCE,
                "fallback": source,
                "reason": str(fallback_reason)[:240],
            }
            if item not in _PROVENANCE["fallbacks"]:
                _PROVENANCE["fallbacks"].append(item)


def _tag_rows(rows, source):
    return [{**row, "_source": row.get("_source") or source} for row in rows]


def _row_source(rows, default=LEGACY_CACHE_SOURCE):
    sources = {
        str(row.get("_source") or default)
        for row in rows
        if row is not None
    }
    return ",".join(sorted(sources)) if sources else default


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_ndjson(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def time_seconds(value):
    text = str(value or "")
    if len(text) == 5:
        text = "0" + text
    if len(text) != 6:
        return -1
    try:
        return int(text[:2]) * 3600 + int(text[2:4]) * 60 + int(text[4:6])
    except Exception:
        return -1


def is_main_board(code):
    return code.startswith(
        ("600", "601", "603", "605", "000", "001", "002", "003")
    )


def get_trade_dates(end_date=None):
    path = os.path.join(CACHE, "trade_dates.json")
    if os.path.exists(path):
        dates = read_json(path)
        if dates and (end_date is None or end_date <= dates[-1]):
            _record_source("trade_cal", end_date or dates[-1], "cache")
            return dates
    start_date = "20240101"
    end_date = end_date or "20261231"
    primary_error = None
    try:
        rows = api.call(
            "trade_cal",
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:
        rows = []
        primary_error = exc
    if rows:
        source = PRIMARY_SOURCE
    else:
        rows = backup_api.trade_dates(start_date, end_date)
        source = backup_api.SOURCE
        if rows:
            _record_source(
                "trade_cal",
                end_date,
                source,
                primary_error or "empty response",
            )
    dates = sorted(r["cal_date"] for r in rows if str(r.get("is_open")) == "1")
    if not dates:
        raise RuntimeError(
            f"交易日历为空；主源：{primary_error or 'empty response'}；备用源：empty response"
        )
    _record_source("trade_cal", end_date, source)
    os.makedirs(CACHE, exist_ok=True)
    write_json(path, dates)
    return dates


def ensure_daily(date, allow_spot=True):
    path = os.path.join(SHARD, f"daily_{date}.json")
    if os.path.exists(path) and os.path.getsize(path) > 10:
        rows = _tag_rows(read_json(path), LEGACY_CACHE_SOURCE)
        _record_source("daily", date, _row_source(rows))
        return rows
    primary_error = None
    try:
        rows = api.call("daily", trade_date=date, retries=4)
    except Exception as exc:
        rows = []
        primary_error = exc
    if rows:
        source = PRIMARY_SOURCE
    elif allow_spot:
        try:
            rows = backup_api.daily_snapshot(date)
            fallback_error = None
        except Exception as exc:
            rows = []
            fallback_error = exc
        source = backup_api.SOURCE
        if rows:
            _record_source(
                "daily",
                date,
                source,
                primary_error or "empty response",
            )
        else:
            raise RuntimeError(
                f"{date} 日线数据为空；主源：{primary_error or 'empty response'}；"
                f"备用源：{fallback_error or 'empty response'}"
            )
    else:
        raise RuntimeError(
            f"{date} 日线数据为空；主源：{primary_error or 'empty response'}"
        )
    keep = [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "pct_chg",
        "vol",
        "amount",
        "_source",
    ]
    rows = [
        {
            **{k: r.get(k) for k in keep},
            "_source": r.get("_source") or source,
        }
        for r in rows
    ]
    os.makedirs(SHARD, exist_ok=True)
    write_json(path, rows)
    _record_source("daily", date, _row_source(rows, source))
    return rows


def ensure_limit(date):
    month = date[:6]
    path = os.path.join(LIMIT_DIR, f"{month}.json")
    os.makedirs(LIMIT_DIR, exist_ok=True)
    rows = read_json(path) if os.path.exists(path) else []
    current = [r for r in rows if r.get("trade_date") == date]
    if current:
        current = _tag_rows(current, LEGACY_CACHE_SOURCE)
        _record_source("limit_list", date, _row_source(current))
        return current

    primary_error = None
    try:
        new = api.call(
            "limit_list_d",
            start_date=f"{month}01",
            end_date=f"{month}31",
            retries=4,
        )
    except Exception as exc:
        new = []
        primary_error = exc
    if new:
        new = _tag_rows(new, PRIMARY_SOURCE)
        dedup = {(r.get("trade_date"), r.get("ts_code")): r for r in rows + new}
        rows = list(dedup.values())

    current = [r for r in rows if r.get("trade_date") == date]
    fallback_error = None
    if not current:
        try:
            fallback = backup_api.limit_list(date)
            fallback_error = None
        except Exception as exc:
            fallback = []
            fallback_error = exc
        if not fallback:
            raise RuntimeError(
                f"{date} 涨跌停明细为空；主源：{primary_error or 'empty response'}；"
                f"备用源：{fallback_error or 'empty response'}"
            )
        fallback = _tag_rows(fallback, backup_api.SOURCE)
        dedup = {(r.get("trade_date"), r.get("ts_code")): r for r in rows + fallback}
        rows = list(dedup.values())
        current = [r for r in rows if r.get("trade_date") == date]
        _record_source(
            "limit_list",
            date,
            backup_api.SOURCE,
            primary_error or "no rows for requested date",
        )

    write_json(path, rows)
    _record_source("limit_list", date, _row_source(current))
    return current


def ensure_index(end_date):
    path = os.path.join(CACHE, "index_daily.jsonl")
    rows = read_ndjson(path) if os.path.exists(path) else []
    have = {r.get("trade_date") for r in rows if r.get("ts_code") == "000300.SH"}
    start = "20240101"
    if have:
        latest = max(have)
        start = latest
    if end_date not in have:
        primary_error = None
        try:
            new = api.call(
                "index_daily",
                ts_code="000300.SH",
                start_date=start,
                end_date=end_date,
                retries=4,
            )
        except Exception as exc:
            new = []
            primary_error = exc
        if new:
            new = _tag_rows(new, PRIMARY_SOURCE)
        dedup = {
            (r.get("ts_code"), r.get("trade_date")): r for r in rows + new
        }
        rows = list(dedup.values())
        have = {
            r.get("trade_date")
            for r in rows
            if r.get("ts_code") == "000300.SH"
        }
        if end_date not in have:
            fallback_error = None
            try:
                fallback = backup_api.index_daily(start, end_date)
            except Exception as exc:
                fallback = []
                fallback_error = exc
            if not fallback:
                raise RuntimeError(
                    f"{end_date} 沪深300指数数据为空；"
                    f"主源：{primary_error or 'empty response'}；"
                    f"备用源：{fallback_error or 'empty response'}"
                )
            fallback = _tag_rows(fallback, backup_api.SOURCE)
            dedup = {
                (r.get("ts_code"), r.get("trade_date")): r
                for r in rows + fallback
            }
            rows = list(dedup.values())
            _record_source(
                "index_daily",
                end_date,
                backup_api.SOURCE,
                primary_error or "no rows for requested date",
            )
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                r.setdefault("_source", LEGACY_CACHE_SOURCE)
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    rows = [
        {**r, "_source": r.get("_source") or LEGACY_CACHE_SOURCE}
        for r in rows
    ]
    current = [
        r for r in rows
        if r.get("ts_code") == "000300.SH" and r.get("trade_date") == end_date
    ]
    if current:
        _record_source("index_daily", end_date, _row_source(current))
    return rows


def hs300_context(end_date, dates):
    rows = [
        r
        for r in ensure_index(end_date)
        if r.get("ts_code") == "000300.SH" and r.get("trade_date") <= end_date
    ]
    rows.sort(key=lambda r: r["trade_date"])
    closes = [float(r["close"]) for r in rows]
    if len(closes) < 21:
        return {}
    ma20 = statistics.mean(closes[-20:])
    return {
        "date": rows[-1]["trade_date"],
        "close": closes[-1],
        "ma20": ma20,
        "market_up": closes[-1] >= ma20,
        "source": rows[-1].get("_source") or LEGACY_CACHE_SOURCE,
    }


def load_window_rows(codes, dates):
    """读取最近 80 个交易日的候选股票日线。"""
    needed = set(dates[-80:])
    by_code = defaultdict(list)
    missing_dates = []
    for date in sorted(needed):
        try:
            rows = ensure_daily(date, allow_spot=False)
        except Exception as exc:
            missing_dates.append(date)
            continue
        for row in rows:
            code = row.get("ts_code")
            if code in codes:
                by_code[code].append(
                    {
                        "date": date,
                        "open": float(row.get("open") or 0),
                        "high": float(row.get("high") or 0),
                        "low": float(row.get("low") or 0),
                        "close": float(row.get("close") or 0),
                        "pre_close": float(row.get("pre_close") or 0),
                        "vol": float(row.get("vol") or 0),
                        "amount": float(row.get("amount") or 0) * 1000,
                        "_source": row.get("_source") or LEGACY_CACHE_SOURCE,
                    }
                )
    if missing_dates:
        missing = set(missing_dates)
        start_date = min(missing)
        end_date = max(missing)
        _record_source(
            "daily_window",
            f"{start_date}~{end_date}",
            "candidate_window_fallback",
            f"{len(missing)} full-market daily shards unavailable",
        )
        for code in sorted(codes):
            try:
                fallback = backup_api.daily_bar(code, start_date, end_date)
                fallback_error = None
            except Exception as exc:
                fallback = []
                fallback_error = exc
            have_dates = {row["date"] for row in by_code[code]}
            added = False
            for row in fallback:
                date = str(row.get("trade_date") or "")
                if date not in missing or date in have_dates:
                    continue
                bar = {
                    "date": date,
                    "open": float(row.get("open") or 0),
                    "high": float(row.get("high") or 0),
                    "low": float(row.get("low") or 0),
                    "close": float(row.get("close") or 0),
                    "pre_close": float(row.get("pre_close") or 0),
                    "vol": float(row.get("vol") or 0),
                    "amount": float(row.get("amount") or 0) * 1000,
                    "_source": row.get("_source") or backup_api.SOURCE,
                }
                by_code[code].append(bar)
                have_dates.add(date)
                added = True
            if added:
                _record_source(
                    "daily_window",
                    code,
                    backup_api.SOURCE,
                    "missing primary daily dates",
                )
            elif fallback_error:
                _record_source(
                    "daily_window",
                    code,
                    "missing",
                    fallback_error,
                )
    for code in by_code:
        by_code[code].sort(key=lambda row: row["date"])
    return by_code


def first_board_events(dates, today_index):
    events = []
    for offset in (1, 2, 3):
        if today_index - offset < 0:
            continue
        date = dates[today_index - offset]
        for row in ensure_limit(date):
            if row.get("limit") != "U":
                continue
            code = row.get("ts_code", "")
            if not is_main_board(code):
                continue
            name = str(row.get("name") or "")
            if "ST" in name.upper() or "退" in name:
                continue
            if time_seconds(row.get("first_time")) <= 10 * 3600 + 45 * 60:
                continue
            events.append(
                {
                    "first_date": date,
                    "gap_days": offset,
                    "code": code,
                    "name": name,
                    "first_time": str(row.get("first_time") or "").zfill(6),
                    "first_close": float(row.get("close") or 0),
                    "first_open_times": int(row.get("open_times") or 0),
                    "first_turnover": float(row.get("turnover_ratio") or 0),
                    "float_mv": float(row.get("float_mv") or 0),
                    "_source": row.get("_source") or LEGACY_CACHE_SOURCE,
                }
            )
    return events


def stock_list_dates():
    path = os.path.join(CACHE, "stock_basic.jsonl")
    rows = read_ndjson(path) if os.path.exists(path) else []
    return {r.get("ts_code"): r.get("list_date") for r in rows}


def enrich_candidate(event, rows, market_up):
    if not rows:
        return None
    first_index = next(
        (i for i, r in enumerate(rows) if r["date"] == event["first_date"]),
        None,
    )
    if first_index is None or first_index < 60:
        return None
    first = rows[first_index]
    if first["close"] <= 0:
        return None
    pre = rows[:first_index]
    closes = [r["close"] for r in pre]
    vols = [r["vol"] for r in pre]
    amts = [r["amount"] for r in pre]
    if first["amount"] < 5e7:
        return None
    if len(amts) >= 20 and statistics.mean(amts[-20:]) < 8e7:
        return None
    last = closes[-1]
    prior20 = last / closes[-21] - 1 if len(closes) >= 21 else 0.0
    pre_ret20 = prior20
    pre_ret60 = last / closes[-61] - 1 if len(closes) >= 61 else 0.0
    ma5 = statistics.mean(closes[-5:])
    ma20 = statistics.mean(closes[-20:])
    ma60 = statistics.mean(closes[-60:]) if len(closes) >= 60 else ma20
    v5 = statistics.mean(vols[-5:])
    v20 = statistics.mean(vols[-20:])
    return {
        **event,
        "data_sources": {
            "first_board": event.get("_source") or LEGACY_CACHE_SOURCE,
            "daily": sorted(
                {
                    row.get("_source") or LEGACY_CACHE_SOURCE
                    for row in rows
                }
            ),
        },
        "first_open": first["open"],
        "first_low": first["low"],
        "first_vol": first["vol"],
        "prior20": prior20,
        "pre_ret20": pre_ret20,
        "pre_ret60": pre_ret60,
        "dist_ma20": last / ma20 - 1 if ma20 else 0,
        "vol_ratio_5_20": v5 / v20 if v20 else 1.0,
        "bear_ma": ma5 < ma20 < ma60,
        "market_up": market_up,
    }


def pullback_ok(event, rows, today):
    first_index = next(
        (i for i, r in enumerate(rows) if r["date"] == event["first_date"]),
        None,
    )
    if first_index is None:
        return False, None, None
    today_index = next(
        (i for i, r in enumerate(rows) if r["date"] == today),
        None,
    )
    if today_index is None or today_index - first_index < 2:
        return False, None, None
    pullback = rows[first_index + 1 : today_index]
    if not pullback:
        return False, None, None
    if any(r["close"] >= event["first_close"] for r in pullback):
        return False, pullback, None
    pullback_low = min(r["low"] for r in pullback)
    small_yang = sum(1 for r in pullback if r["close"] > r["open"])
    if pullback_low <= event["first_low"]:
        return False, pullback, None
    if small_yang > 1:
        return False, pullback, None
    return True, pullback, small_yang


def branch_ok(event):
    if event["market_up"]:
        return (
            event["prior20"] > 0.30
            and 1 <= event["first_open_times"] <= 2
        ), "A"
    return (
        event["prior20"] < -0.05
        and event["vol_ratio_5_20"] < 0.8
    ), "B"


def minute_trigger(date, events):
    if not events:
        return {}
    codes = sorted({e["code"] for e in events})
    result = {}
    os.makedirs(MIN_DIR, exist_ok=True)
    for i in range(0, len(codes), 50):
        chunk = codes[i : i + 50]
        key = f"{date}_{i//50}"
        path = os.path.join(MIN_DIR, key + ".json")
        if os.path.exists(path) and os.path.getsize(path) > 2:
            data = read_json(path)
            data = {
                code: _tag_rows(bars, "cache_unknown")
                for code, bars in data.items()
            }
        else:
            try:
                rows = api.call(
                    "stk_mins",
                    ts_code=",".join(chunk),
                    freq="1min",
                    start_date=f"{date[:4]}-{date[4:6]}-{date[6:]} 09:30:00",
                    end_date=f"{date[:4]}-{date[4:6]}-{date[6:]} 09:45:00",
                    retries=4,
                )
            except Exception as exc:
                rows = []
                primary_error = exc
            else:
                primary_error = None
            data = defaultdict(list)
            for row in rows:
                data[row["ts_code"]].append(
                    {**row, "_source": PRIMARY_SOURCE}
                )
            for code in chunk:
                if data.get(code):
                    continue
                native_error = None
                try:
                    fallback_rows = backup_api.minute_bar(code, date)
                except Exception as exc:
                    fallback_rows = []
                    fallback_error = exc
                else:
                    fallback_error = None
                if fallback_rows:
                    data[code].extend(
                        row
                        for row in fallback_rows
                        if str(row.get("trade_time") or "")[11:16]
                        <= "09:45"
                    )
                if not data.get(code):
                    try:
                        native_rows = api.native_get(
                            "/api/stock/v3/kline",
                            symbol=code,
                            type="1min",
                            beginDate=(
                                f"{date[:4]}-{date[4:6]}-{date[6:]} 09:30:00"
                            ),
                            endDate=(
                                f"{date[:4]}-{date[4:6]}-{date[6:]} 09:45:59"
                            ),
                            limit="30",
                            method="bfq",
                        )
                    except Exception as exc:
                        native_rows = []
                        native_error = exc
                    else:
                        native_error = None
                    for row in native_rows:
                        bar_date = str(row.get("date") or "")
                        if not bar_date.startswith(
                            f"{date[:4]}-{date[4:6]}-{date[6:]}"
                        ):
                            continue
                        data[code].append(
                            {
                                "ts_code": code,
                                "trade_time": bar_date,
                                "open": row.get("open"),
                                "high": row.get("high"),
                                "low": row.get("low"),
                                "close": row.get("close"),
                                "_source": "miana_native",
                            }
                        )
                if data.get(code):
                    reason = primary_error or fallback_error or native_error
                    source = _row_source(data[code], "cache_unknown")
                    _record_source(
                        "minutes",
                        f"{date}:{code}",
                        source,
                        reason if source != PRIMARY_SOURCE else None,
                    )
                else:
                    _record_source(
                        "minutes",
                        f"{date}:{code}",
                        "missing",
                        primary_error or fallback_error or native_error,
                    )
            if any(data.values()):
                write_json(path, dict(data))
        for code, bars in data.items():
            result[code] = sorted(
                bars, key=lambda r: r["trade_time"]
            )
            _record_source(
                "minutes",
                f"{date}:{code}",
                _row_source(bars, "cache_unknown"),
            )
    return result


def update(date, state_path):
    dates = get_trade_dates(date)
    if date not in dates:
        return {"ok": False, "message": f"{date} 不是交易日"}
    today_index = dates.index(date)
    state = (
        read_json(state_path)
        if os.path.exists(state_path)
        else {
            "account": "N字二板模拟账户",
            "model": "N字二板量化模型 v1.0",
            "initial_capital": INITIAL,
            "start_date": date,
            "cash": INITIAL,
            "positions": [],
            "trades": [],
            "signals": [],
            "equity_curve": [{"date": date, "equity": INITIAL}],
            "last_update": None,
        }
    )
    if state.get("last_update") == date:
        return {"ok": False, "message": f"{date} 已更新，请勿重复运行"}
    if date < state.get("start_date", date):
        return {"ok": False, "message": "尚未到模拟开始日期"}

    today_rows = ensure_daily(date)
    today_map = {r["ts_code"]: r for r in today_rows}
    today_limits = {r["ts_code"] for r in ensure_limit(date) if r.get("limit") == "U"}
    # 09:45 买入时只能看到上一交易日的指数收盘，避免未来函数
    prev_date = dates[today_index - 1] if today_index > 0 else date
    market = hs300_context(prev_date, dates)
    if not market:
        return {"ok": False, "message": "指数数据不足"}

    # 1. 处理持仓：断板则收盘卖出
    cash = float(state["cash"])
    positions = []
    day_trades = []
    for pos in state["positions"]:
        row = today_map.get(pos["code"])
        if not row:
            positions.append(pos)
            continue
        if pos["code"] in today_limits:
            positions.append(pos)
            continue
        price = float(row.get("close") or 0)
        proceeds = pos["shares"] * price * (1 - FEE - SLIP)
        cash += proceeds
        pnl = proceeds - pos["cost"]
        day_trades.append(
            {
                "date": date,
                "side": "SELL",
                "code": pos["code"],
                "name": pos["name"],
                "price": price,
                "shares": pos["shares"],
                "pnl": pnl,
                "reason": "断板收盘卖",
            }
        )
    state["positions"] = positions

    # 2. 回看最近 3 日首板，筛选 N 字候选
    events = first_board_events(dates, today_index)
    list_dates = stock_list_dates()
    today_dt = datetime.datetime.strptime(date, "%Y%m%d")
    codes = {e["code"] for e in events}
    series = load_window_rows(codes, dates[: today_index + 1])
    candidates = []
    signals = []
    for event in events:
        if event["code"] not in today_map:
            signals.append({**event, "status": "无当日数据", "reason": "停牌或数据缺失"})
            continue
        listed = list_dates.get(event["code"])
        if listed:
            try:
                listed_dt = datetime.datetime.strptime(str(listed), "%Y%m%d")
                if (today_dt - listed_dt).days < 365:
                    signals.append({**event, "status": "淘汰", "reason": "上市不足一年"})
                    continue
            except Exception:
                pass
        enriched = enrich_candidate(event, series.get(event["code"], []), market["market_up"])
        if not enriched:
            signals.append({**event, "status": "淘汰", "reason": "流动性或历史数据不足"})
            continue
        ok, pullback, small_yang = pullback_ok(
            enriched, series.get(event["code"], []), date
        )
        if not ok:
            signals.append(
                {**enriched, "status": "淘汰", "reason": "回调不满足单阳不破/小阳条件"}
            )
            continue
        ok_branch, branch = branch_ok(enriched)
        if not ok_branch:
            signals.append(
                {**enriched, "status": "淘汰", "reason": "分支 A/B 前置条件不符"}
            )
            continue
        enriched["branch"] = branch
        enriched["small_yang"] = small_yang
        enriched["threshold"] = float(today_map[event["code"]]["pre_close"]) * 1.03
        enriched["today_open"] = float(today_map[event["code"]]["open"])
        enriched["today_close"] = float(today_map[event["code"]]["close"])
        enriched["today_pre_close"] = float(today_map[event["code"]]["pre_close"])
        candidates.append(enriched)

    # 3. 09:30-09:45 触发
    minute = minute_trigger(date, candidates)
    entries = []
    held_codes = {p["code"] for p in state["positions"]}
    for cand in candidates:
        if cand["code"] in held_codes:
            signals.append({**cand, "status": "已持仓"})
            continue
        bars = minute.get(cand["code"], [])
        entry_price = None
        for bar in bars:
            if float(bar.get("high") or 0) >= cand["threshold"]:
                entry_price = cand["threshold"]
                break
        if entry_price is None:
            signals.append({**cand, "status": "未触发"})
            continue
        gap = entry_price / cand["today_pre_close"] - 1
        if gap > 0.05 or gap < 0.03:
            signals.append({**cand, "status": "高开幅度不符"})
            continue
        cand["entry_price"] = entry_price
        cand["entry_gap"] = gap
        cand["second_board"] = cand["code"] in today_limits
        entries.append(cand)
        signals.append({**cand, "status": "触发买入"})

    # 4. 开仓：固定 20%，最多 5 笔，单日最多 3 笔
    entries = entries[:DAILY_NEW]
    slots = min(MAX_POSITIONS - len(state["positions"]), len(entries))
    if slots > 0:
        equity_prior = cash + sum(
            p["shares"] * float(today_map.get(p["code"], {}).get("close", p["entry_price"]))
            for p in state["positions"]
        )
        per_trade = min(
            equity_prior * 0.20,
            cash / slots if slots else 0,
        )
        for cand in entries[:slots]:
            shares = per_trade / (cand["entry_price"] * (1 + FEE + SLIP))
            cost = shares * cand["entry_price"] * (1 + FEE + SLIP)
            if cost > cash:
                continue
            cash -= cost
            pos = {
                "code": cand["code"],
                "name": cand["name"],
                "branch": cand["branch"],
                "entry_date": date,
                "entry_price": cand["entry_price"],
                "shares": shares,
                "cost": cost,
            }
            day_trades.append(
                {
                    "date": date,
                    "side": "BUY",
                    "code": cand["code"],
                    "name": cand["name"],
                    "price": cand["entry_price"],
                    "shares": shares,
                    "pnl": 0.0,
                    "reason": f"分支{cand['branch']} 09:45触发",
                }
            )
            if cand["second_board"]:
                state["positions"].append(pos)
            else:
                proceeds = shares * cand["today_close"] * (1 - FEE - SLIP)
                cash += proceeds
                pnl = proceeds - cost
                day_trades.append(
                    {
                        "date": date,
                        "side": "SELL",
                        "code": cand["code"],
                        "name": cand["name"],
                        "price": cand["today_close"],
                        "shares": shares,
                        "pnl": pnl,
                        "reason": "未封二板收盘卖",
                    }
                )

    # 5. 更新净值
    equity = cash
    for pos in state["positions"]:
        row = today_map.get(pos["code"])
        price = float(row.get("close") or 0) if row else pos["entry_price"]
        equity += pos["shares"] * price
    state["cash"] = cash
    state["trades"].extend(day_trades)
    state["signals"].extend(signals)
    state["equity_curve"] = [
        p for p in state["equity_curve"] if p.get("date") != date
    ] + [{"date": date, "equity": equity}]
    state["equity_curve"].sort(key=lambda p: p["date"])
    state["last_update"] = date
    state["market"] = market
    write_json(state_path, state)
    return {
        "ok": True,
        "date": date,
        "equity": equity,
        "cash": cash,
        "positions": state["positions"],
        "trades": day_trades,
        "signals": signals,
        "message": f"已更新 {date}",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument(
        "--state",
        default=os.path.join(BASE, "paper_account_live.json"),
    )
    args = parser.parse_args()
    result = update(args.date, args.state)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    message = str(result.get("message", ""))
    if result.get("ok") or "不是交易日" in message or "已更新" in message:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
