#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from unittest import mock

ENGINE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "engine")
sys.path.insert(0, ENGINE)

import paper_engine as pe


class DataFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_paths = {
            "CACHE": pe.CACHE,
            "SHARD": pe.SHARD,
            "LIMIT_DIR": pe.LIMIT_DIR,
            "MIN_DIR": pe.MIN_DIR,
        }
        pe.CACHE = self.tmp.name
        pe.SHARD = os.path.join(pe.CACHE, "v2")
        pe.LIMIT_DIR = os.path.join(pe.CACHE, "limit_list")
        pe.MIN_DIR = os.path.join(pe.CACHE, "mins15")
        pe.reset_provenance()

    def tearDown(self):
        for key, value in self.original_paths.items():
            setattr(pe, key, value)
        pe.reset_provenance()
        self.tmp.cleanup()

    def test_daily_uses_eastmoney_when_primary_is_empty(self):
        fallback = [
            {
                "ts_code": "002095.SZ",
                "trade_date": "20261102",
                "open": 14.08,
                "high": 15.31,
                "low": 13.82,
                "close": 15.31,
                "pre_close": 13.92,
                "pct_chg": 9.99,
                "vol": 142060,
                "amount": 210141.08,
                "_source": "eastmoney",
            }
        ]
        with mock.patch.object(pe.api, "call", return_value=[]), mock.patch.object(
            pe.backup_api, "daily_snapshot", return_value=fallback
        ) as backup:
            rows = pe.ensure_daily("20261102")

        self.assertEqual("eastmoney", rows[0]["_source"])
        self.assertEqual(1, backup.call_count)
        provenance = pe.get_provenance()
        self.assertEqual(
            "eastmoney", provenance["sources"]["daily"]["20261102"]
        )
        self.assertEqual("eastmoney", provenance["fallbacks"][0]["fallback"])

    def test_limit_uses_eastmoney_when_primary_has_no_rows(self):
        fallback = [
            {
                "trade_date": "20261102",
                "ts_code": "002095.SZ",
                "name": "生意宝",
                "limit": "U",
                "first_time": "101000",
                "_source": "eastmoney",
            }
        ]
        with mock.patch.object(pe.api, "call", return_value=[]), mock.patch.object(
            pe.backup_api, "limit_list", return_value=fallback
        ):
            rows = pe.ensure_limit("20261102")

        self.assertEqual("eastmoney", rows[0]["_source"])
        self.assertEqual(
            "eastmoney",
            pe.get_provenance()["sources"]["limit_list"]["20261102"],
        )

    def test_minute_uses_eastmoney_before_native_fallback(self):
        fallback = [
            {
                "ts_code": "002095.SZ",
                "trade_time": "2026-11-02 09:31",
                "open": 14.08,
                "high": 14.18,
                "low": 13.89,
                "close": 13.9,
                "_source": "eastmoney",
            }
        ]
        with mock.patch.object(pe.api, "call", return_value=[]), mock.patch.object(
            pe.backup_api, "minute_bar", return_value=fallback
        ), mock.patch.object(pe.api, "native_get") as native:
            data = pe.minute_trigger(
                "20261102",
                [{"code": "002095.SZ"}],
            )

        self.assertEqual("eastmoney", data["002095.SZ"][0]["_source"])
        native.assert_not_called()

    def test_window_uses_per_stock_eastmoney_history(self):
        dates = [f"202601{day:02d}" for day in range(1, 32)]
        dates += [f"202602{day:02d}" for day in range(1, 32)]
        fallback = [
            {
                "ts_code": "002095.SZ",
                "trade_date": date,
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "vol": 1000,
                "amount": 100000,
                "_source": "eastmoney",
            }
            for date in dates
        ]
        with mock.patch.object(
            pe.api, "call", side_effect=RuntimeError("primary unavailable")
        ), mock.patch.object(pe.backup_api, "daily_bar", return_value=fallback):
            series = pe.load_window_rows({"002095.SZ"}, dates)

        self.assertEqual(len(dates), len(series["002095.SZ"]))
        self.assertEqual(
            {"eastmoney"},
            {row["_source"] for row in series["002095.SZ"]},
        )

    def test_index_uses_eastmoney_when_primary_is_empty(self):
        fallback = [
            {
                "ts_code": "000300.SH",
                "trade_date": "20261102",
                "open": 4005.94,
                "high": 4038.82,
                "low": 3988.17,
                "close": 4016.92,
                "vol": 244401128,
                "amount": 528847292.2349,
                "_source": "eastmoney",
            }
        ]
        with mock.patch.object(pe.api, "call", return_value=[]), mock.patch.object(
            pe.backup_api, "index_daily", return_value=fallback
        ):
            rows = pe.ensure_index("20261102")

        self.assertEqual("eastmoney", rows[-1]["_source"])
        self.assertEqual(
            "eastmoney",
            pe.get_provenance()["sources"]["index_daily"]["20261102"],
        )

    def test_primary_daily_does_not_call_fallback(self):
        primary = [
            {
                "ts_code": "002095.SZ",
                "trade_date": "20261102",
                "open": 14.08,
                "high": 15.31,
                "low": 13.82,
                "close": 15.31,
                "pre_close": 13.92,
                "pct_chg": 9.99,
                "vol": 142060,
                "amount": 210141.08,
            }
        ]
        with mock.patch.object(pe.api, "call", return_value=primary), mock.patch.object(
            pe.backup_api, "daily_snapshot"
        ) as backup:
            rows = pe.ensure_daily("20261102")

        self.assertEqual("miana_tushare", rows[0]["_source"])
        backup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
