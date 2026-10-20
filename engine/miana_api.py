#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""miana.com.cn 数据接口封装：试用 token 自动获取 + 通用调用 + 磁盘缓存。"""
import json, os, ssl, time, urllib.request, urllib.parse, threading

BASE = "https://miana.com.cn"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
CTX = ssl.create_default_context()
_SWAP = {'0':'a','1':'b','2':'c','3':'d','4':'e','5':'f','6':'g','7':'h','8':'i','9':'j',
         'a':'0','b':'1','c':'2','d':'3','e':'4','f':'5','g':'6','h':'7','i':'8','j':'9'}

_tok_lock = threading.Lock()
_tok = {"v": None, "t": 0.0}

def get_token(force=False):
    """取试用 token（缓存 20 分钟）"""
    with _tok_lock:
        if not force and _tok["v"] and time.time() - _tok["t"] < 1200:
            return _tok["v"]
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    BASE + "/api/client/v1/testToken", data=b"{}",
                    headers={"User-Agent": UA, "Content-Type": "application/json"},
                    method="POST")
                enc = json.loads(urllib.request.urlopen(req, timeout=20, context=CTX).read().decode())["data"]
                val = "".join(_SWAP.get(c, c) for c in enc)
                _tok["v"], _tok["t"] = val, time.time()
                return val
            except Exception:
                time.sleep(1.5)
        raise RuntimeError("无法获取 testToken")

def call(api, retries=3, **params):
    """调用 /api/tushare/v1/<api>，返回 list[dict]"""
    last = None
    for attempt in range(retries):
        try:
            q = {"token": get_token(force=(attempt > 0)), "fields": "all"}
            q.update({k: v for k, v in params.items() if v is not None})
            url = BASE + "/api/tushare/v1/" + api + "?" + urllib.parse.urlencode(q)
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=60, context=CTX).read().decode()
            d = json.loads(raw)
            if d.get("code") != 0:
                last = d.get("msg")
                time.sleep(1.0)
                continue
            dat = d.get("data") or {}
            fields, items = dat.get("fields") or [], dat.get("items") or []
            return [dict(zip(fields, row)) for row in items]
        except Exception as e:
            last = e
            time.sleep(1.2)
    raise RuntimeError(f"{api} 调用失败: {last}")

def cached(path, fn):
    """磁盘 JSON 缓存包装"""
    if os.path.exists(path) and os.path.getsize(path) > 2:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    val = fn()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(val, f, ensure_ascii=False)
    os.replace(tmp, path)
    return val
