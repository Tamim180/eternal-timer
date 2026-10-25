"""Unit tests for timers, history and power-cut detection (no Qt, no network)."""
import json
import os
import sys
import tempfile
import time
import unittest

os.environ["HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import eternal_timer_core as core   # noqa: E402


class ParseTests(unittest.TestCase):
    def test_parse_duration(self):
        cases = {"25m": 1500, "1h30m": 5400, "90s": 90, "1:30:00": 5400, "5:00": 300, "2 hours 5 minutes": 7500}
        for text, secs in cases.items():
            self.assertEqual(core.parse_duration(text), secs, text)
        with self.assertRaises(ValueError):
            core.parse_duration("soon")

    def test_formatting(self):
        self.assertEqual(core.format_hms(65), "01:05")
        self.assertEqual(core.format_hms(3725), "01:02:05")
        self.assertEqual(core.format_duration_long(3725), "1h 02m 05s")


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        s = core.load_settings()
        self.assertTrue(s["warn_stopwatch"])        # the stopwatch warning is on until switched off
        self.assertTrue(s["sound_enabled"])
        self.assertEqual(s["theme"], "aurora")

    def test_settings_file_is_private(self):
        core.save_settings(core.load_settings())
        self.assertEqual(os.stat(core.SETTINGS_FILE).st_mode & 0o077, 0)     # it can hold the sync code


class TimerMathTests(unittest.TestCase):
    def setUp(self):
        core._DEVICE_ID = "T"
        self.st = {"items": []}

    def test_pause_resume_accumulates_exactly(self):
        t = core.add_timer(self.st, 600, "x")
        t["start_ts"] -= 100
        core.pause_item(self.st, t["id"])
        self.assertAlmostEqual(t["accumulated"], 100, delta=1)
        time.sleep(0.05)
        self.assertAlmostEqual(core.remaining_seconds(t), 500, delta=1)      # paused: time doesn't move
        core.resume_item(self.st, t["id"])
        self.assertTrue(t["running"])

    def test_finish_is_logged_once_with_the_real_finish_time(self):
        t = core.add_timer(self.st, 10, "x")
        t["start_ts"] -= 60                                                   # ended 50 s ago (e.g. PC was off)
        rang = []
        core.check_and_notify(self.st, notifier=lambda *a: rang.append(a))
        core.check_and_notify(self.st, notifier=lambda *a: rang.append(a))
        self.assertEqual(len(rang), 1)
        fin = [e for e in t["history"] if e["type"] == "finished"]
        self.assertEqual(len(fin), 1)
        self.assertAlmostEqual(fin[0]["noticed_ts"] - fin[0]["ts"], 50, delta=2)

    def test_history_rows_pair_pauses_and_total_them(self):
        t = core.add_timer(self.st, 600, "x")
        base = t["start_ts"]
        core._log(t, "paused", base + 10)
        core._log(t, "resumed", base + 40)
        rows, totals = core.history_rows(t)
        self.assertEqual(totals["pauses"], 1)
        self.assertAlmostEqual(totals["paused_seconds"], 30, delta=0.01)
        self.assertEqual([r["kind"] for r in rows], ["started", "paused"])


class DowntimeTests(unittest.TestCase):
    def setUp(self):
        core._DEVICE_ID = "T"
        self.st = {"items": []}
        self.t = core.add_timer(self.st, 3600, "x")
        self.t["start_ts"] -= 1000
        self.t["history"][0]["ts"] -= 1000
        self._orig = (core.get_boot_id, core.get_boot_time)

    def tearDown(self):
        core.get_boot_id, core.get_boot_time = self._orig

    def causes(self):
        return [e["cause"] for e in self.t["history"] if e["type"] == "downtime"]

    def heartbeat(self, ago, clean, boot):
        core._write_json(core.HEARTBEAT_FILE, {"last_seen": time.time() - ago, "clean_exit": clean, "boot_id": boot},
                         indent=None)

    def test_power_cut(self):
        core.get_boot_id = lambda: "new"
        core.get_boot_time = lambda: time.time() - 60
        self.heartbeat(300, False, "old")
        core.record_startup_downtime(self.st)
        self.assertEqual(sorted(self.causes()), ["boot_app_closed", "power_cut"])

    def test_clean_shutdown(self):
        core.get_boot_id = lambda: "new"
        core.get_boot_time = lambda: time.time() - 60
        self.heartbeat(300, True, "old")
        core.record_startup_downtime(self.st)
        self.assertIn("shutdown", self.causes())

    def test_app_closed_same_boot(self):
        core.get_boot_id = lambda: "same"
        self.heartbeat(120, True, "same")
        core.record_startup_downtime(self.st)
        self.assertEqual(self.causes(), ["app_closed"])

    def test_crash_same_boot(self):
        core.get_boot_id = lambda: "same"
        self.heartbeat(120, False, "same")
        core.record_startup_downtime(self.st)
        self.assertEqual(self.causes(), ["app_interrupted"])

    def test_short_gap_is_ignored(self):
        core.get_boot_id = lambda: "same"
        self.heartbeat(5, False, "same")
        self.assertEqual(core.record_startup_downtime(self.st), [])

    def test_paused_items_get_no_downtime(self):
        core.pause_item(self.st, self.t["id"])
        for e in self.t["history"]:                      # it was paused well BEFORE the gap began
            if e["type"] == "paused":
                e["ts"] = time.time() - 500
        core.get_boot_id = lambda: "same"
        self.heartbeat(120, True, "same")
        core.record_startup_downtime(self.st)
        self.assertEqual(self.causes(), [])


if __name__ == "__main__":
    unittest.main()
