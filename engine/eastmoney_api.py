#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""东方财富备用行情接口。

该模块使用标准库访问东方财富公开行情，不引入 AkShare 的 pandas 依赖。
返回结构按现有 miana/Tushare 接口的字段约定归一化。
"""
import datetime
import json
import ssl
import time
import urllib.parse
import urllib.request

BASE = "https://push2.eastmoney.com"
HISTORY_BASE = "https://push2his.eastmoney.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CTX = ssl.create_default_context()
SOURCE = "eastmoney"


def _get_json(url, params, retries=3):
    last = None
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                full_url,
                headers={
                    "User-Agent": UA,
                    "Referer": "https://quote.eastmoney.com/",
                },
            )
            raw = urllib.request.urlopen(req, timeout=30, context=CTX).read()
            data = json.loads(raw.decode("utf-8"))
            if data.get("rc", 0) == 0:
                return data
            last = data.get("rt") or data.get("msg") or data
        except Exception as exc:
            last = exc
        time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"eastmoney request failed: {last}")


def _exchange(code):
    return "SH" if str(code).startswith(("5", "6", "9")) else "SZ"


def _ts_code(code):
    code = str(code or "")
    if "." in code:
        return code
    return f"{code}.{_exchange(code)}"


def _secid(code):
    code = str(code or "")
    raw = code.split(".")[0]
    market = "1" if code.endswith(".SH") or raw.startswith(("5", "6", "9")) else "0"
    return f"{market}.{raw}"


def _number(value):
    try:
        if value in (None, "", "-"):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _trade_date(value):
    return str(value or "").replace("-", "")[:8]


def _snapshot_rows(date):
    fields = ",".join(
        (
            "f12",
            "f14",
            "f2",
            "f3",
            "f5",
            "f6",
            "f8",
            "f15",
            "f16",
            "f17",
            "f18",
            "f20",
            "f21",
            "f124",
        )
    )
    base_params = {
        "pz": 100,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": fields,
    }
    first = _get_json(
        BASE + "/api/qt/clist/get",
        {**base_params, "pn": 1},
    )
    data = first.get("data") or {}
    total = int(data.get("total") or 0)
    diff = list(data.get("diff") or [])
    page_size = 100
    for page in range(2, (total + page_size - 1) // page_size + 1):
        chunk = _get_json(
            BASE + "/api/qt/clist/get",
            {**base_params, "pn": page},
        )
        diff.extend((chunk.get("data") or {}).get("diff") or [])
    dedup = {
        str(row.get("f12") or ""): row
        for row in diff
        if row.get("f12")
    }
    diff = list(dedup.values())
    if not diff:
        return []
    timestamps = [
        int(row.get("f124") or 0)
        for row in diff
        if str(row.get("f124") or "").isdigit()
    ]
    if not timestamps:
        return []
    snapshot_date = datetime.datetime.fromtimestamp(
        max(timestamps), datetime.timezone.utc
    ).astimezone(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y%m%d")
    if snapshot_date != date:
        return []
    rows = []
    for row in diff:
        code = str(row.get("f12") or "")
        close = _number(row.get("f2"))
        if not code or close <= 0:
            continue
        pre_close = _number(row.get("f18"))
        amount = _number(row.get("f6")) / 1000
        rows.append(
            {
                "ts_code": _ts_code(code),
                "trade_date": date,
                "name": row.get("f14"),
                "open": _number(row.get("f17")),
                "high": _number(row.get("f15")),
                "low": _number(row.get("f16")),
                "close": close,
                "pre_close": pre_close,
                "pct_chg": _number(row.get("f3")),
                "vol": _number(row.get("f5")),
                "amount": amount,
                "turnover_ratio": _number(row.get("f8")),
                "float_mv": _number(row.get("f21")),
                "total_mv": _number(row.get("f20")),
                "_source": SOURCE,
            }
        )
    return rows


def daily_snapshot(date):
    """返回最近完整交易日的全市场日线；日期不匹配时返回空列表。"""
    return _snapshot_rows(date)


def _kline(code, start_date, end_date, klt):
    data = _get_json(
        HISTORY_BASE + "/api/qt/stock/kline/get",
        {
            "secid": _secid(code),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": str(klt),
            "fqt": "0",
            "beg": start_date,
            "end": end_date,
        },
    )
    bars = (data.get("data") or {}).get("klines") or []
    return [
        str(line).split(",")
        for line in bars
        if str(line or "").strip()
    ]


def daily_bar(code, start_date, end_date):
    """返回单只股票未复权日线，字段结构兼容 paper_engine。"""
    rows = []
    for parts in _kline(code, start_date, end_date, 101):
        if len(parts) < 11:
            continue
        trade_date = _trade_date(parts[0])
        rows.append(
            {
                "ts_code": _ts_code(code),
                "trade_date": trade_date,
                "open": _number(parts[1]),
                "close": _number(parts[2]),
                "high": _number(parts[3]),
                "low": _number(parts[4]),
                "vol": _number(parts[5]),
                "amount": _number(parts[6]) / 1000,
                "pct_chg": _number(parts[8]),
                "_source": SOURCE,
            }
        )
    return rows


def index_daily(start_date, end_date):
    rows = []
    for parts in _kline("000300.SH", start_date, end_date, 101):
        if len(parts) < 9:
            continue
        rows.append(
            {
                "ts_code": "000300.SH",
                "trade_date": _trade_date(parts[0]),
                "open": _number(parts[1]),
                "close": _number(parts[2]),
                "high": _number(parts[3]),
                "low": _number(parts[4]),
                "vol": _number(parts[5]),
                "amount": _number(parts[6]) / 1000,
                "pct_chg": _number(parts[8]),
                "_source": SOURCE,
            }
        )
    return rows


def minute_bar(code, date):
    """返回东方财富当前/最近交易日的 1 分钟线，历史日期不匹配时为空。"""
    start = f"{date}0930"
    end = f"{date}1500"
    rows = []
    for parts in _kline(code, start, end, 1):
        if len(parts) < 7:
            continue
        stamp = str(parts[0] or "")
        if _trade_date(stamp) != date:
            continue
        rows.append(
            {
                "ts_code": _ts_code(code),
                "trade_time": stamp,
                "open": _number(parts[1]),
                "close": _number(parts[2]),
                "high": _number(parts[3]),
                "low": _number(parts[4]),
                "_source": SOURCE,
            }
        )
    return rows


def _time_text(value):
    text = str(value or "").strip()
    return text.zfill(6) if text.isdigit() else ""


def _limit_rows(date, kind):
    endpoint = "getTopicZTPool" if kind == "U" else "getTopicDTPool"
    data = _get_json(
        "https://push2ex.eastmoney.com/" + endpoint,
        {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": 0,
            "pagesize": 10000,
            "sort": "fbt:asc" if kind == "U" else "fund:asc",
            "date": date,
        },
    )
    pool = (data.get("data") or {}).get("pool") or []
    rows = []
    for item in pool:
        code = _ts_code(item.get("c"))
        if kind == "U":
            first_time = _time_text(item.get("fbt"))
            last_time = _time_text(item.get("lbt"))
            open_times = int(item.get("zbc") or 0)
            limit_times = int(item.get("lbc") or 0)
            stat = item.get("zttj") or {}
            up_stat = f"{stat.get('ct', 0)}/{stat.get('days', 0)}"
        else:
            first_time = ""
            last_time = _time_text(item.get("lbt"))
            open_times = 0
            limit_times = 0
            up_stat = ""
        rows.append(
            {
                "trade_date": date,
                "ts_code": code,
                "industry": item.get("hybk"),
                "name": item.get("n"),
                "close": _number(item.get("p")) / 1000,
                "pct_chg": _number(item.get("zdp")),
                "amount": _number(item.get("amount")) / 1000,
                "limit_amount": _number(item.get("fund")),
                "float_mv": _number(item.get("ltsz")),
                "total_mv": _number(item.get("tshare")),
                "turnover_ratio": _number(item.get("hs")),
                "fd_amount": _number(item.get("fund")),
                "first_time": first_time,
                "last_time": last_time,
                "open_times": open_times,
                "up_stat": up_stat,
                "limit_times": limit_times,
                "limit": kind,
                "_source": SOURCE,
            }
        )
    return rows


def limit_list(date):
    """返回指定交易日的涨停和跌停明细。"""
    rows = _limit_rows(date, "U")
    rows.extend(_limit_rows(date, "D"))
    return rows


def trade_dates(start_date, end_date):
    """用上证指数日线日期生成交易日历。"""
    rows = []
    for parts in _kline("000001.SH", start_date, end_date, 101):
        if parts:
            rows.append({"cal_date": _trade_date(parts[0]), "is_open": 1})
    return rows
