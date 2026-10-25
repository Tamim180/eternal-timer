"""Unit tests for the sync layer: crypto, merge rules, tombstones, dirty tracking.
No network, no Qt. Run:  python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import time
import unittest

_HOME = tempfile.mkdtemp()                      # never touch the real ~/.local/share
os.environ["HOME"] = _HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import eternal_timer_core as core            # noqa: E402
import eternal_timer_sync as sync            # noqa: E402

HERE = os.path.dirname(__file__)


def as_device(name):
    """Pretend to be a particular device for the next core.* mutation."""
    core._DEVICE_ID = name


def fresh():
    return {"items": []}


def deliver(src, src_dev, dst, dst_dev):
    """Push everything dirty on `src` to `dst`, through a JSON round-trip like the real wire."""
    as_device(src_dev)
    sync.ensure_stamped(src)
    changed = False
    for kind, iid, rev, payload in sync.collect_outgoing(src):
        payload = json.loads(json.dumps(payload))
        c, _ = sync.handle_payload(dst, payload, dst_dev)
        changed = changed or c
        sync.mark_pushed(src, kind, iid, rev)
    return changed


def converge(a, b, rounds=3):
    for _ in range(rounds):
        deliver(a, "A", b, "B")
        deliver(b, "B", a, "A")


def view(state):
    return {i["id"]: (i.get("label"), bool(i.get("running")), round(i.get("accumulated", 0), 3))
            for i in state["items"]}


@unittest.skipUnless(sync.HAVE_CRYPTO, "python-cryptography not installed")
class CryptoTests(unittest.TestCase):
    def test_vectors_match_published_values(self):
        with open(os.path.join(HERE, "vectors.json")) as f:
            v = json.load(f)
        secret = sync.parse_secret(v["code"])
        topic, key = sync.derive(secret)
        self.assertEqual(topic, v["topic"])
        self.assertEqual(key.hex(), v["key"])
        self.assertEqual(sync.encrypt(key, v["plain"].encode(), nonce=bytes.fromhex(v["nonce"])), v["cipher"])
        self.assertEqual(sync.decrypt(key, v["cipher"]).decode(), v["plain"])

    def test_roundtrip_and_wrong_key(self):
        code = sync.new_secret()
        _, key = sync.derive(sync.parse_secret(code))
        text = sync.pack({"t": "hello", "dev": "x"}, key)
        self.assertEqual(sync.unpack(text, key)["t"], "hello")
        self.assertIsNone(sync.unpack(text, bytes(32)))
        self.assertIsNone(sync.unpack("not-base64-at-all!!", key))

    def test_codes(self):
        code = sync.new_secret()
        self.assertEqual(len(code.replace("-", "")), 32)
        self.assertEqual(sync.parse_secret(code.lower().replace("-", " ")), sync.parse_secret(code))
        for bad in ("ABCD-EFGH", "", "0000-0000-0000-0000-0000-0000-0000-0000"):
            with self.assertRaises(ValueError):
                sync.parse_secret(bad)

    def test_oversized_history_is_trimmed_to_fit_relay_limit(self):
        _, key = sync.derive(sync.parse_secret(sync.new_secret()))
        as_device("A")
        st = fresh()
        item = core.add_timer(st, 600, "big")
        for i in range(400):
            core._log(item, "paused", 1000.0 + i)
            core._log(item, "resumed", 1000.5 + i)
        payload = {"v": 1, "t": "item", "dev": "A", "item": sync.item_to_wire(item)}
        text = sync.pack(payload, key)
        self.assertLessEqual(len(text), sync.MAX_WIRE_CHARS)
        self.assertEqual(sync.unpack(text, key)["item"]["id"], item["id"])


class MergeTests(unittest.TestCase):
    def test_new_device_receives_items(self):
        a, b = fresh(), fresh()
        as_device("A")
        t = core.add_timer(a, 1800, "deep work")
        deliver(a, "A", b, "B")
        self.assertEqual(core.find(b, t["id"])["label"], "deep work")
        self.assertFalse(core.find(b, t["id"])["notified"])      # still running -> will ring on B too

    def test_item_that_already_finished_does_not_ring_on_arrival(self):
        a, b = fresh(), fresh()
        as_device("A")
        t = core.add_timer(a, 5, "old")
        t["start_ts"] -= 100                                       # ended long ago
        core.check_and_notify(a, notifier=lambda *_: None)
        deliver(a, "A", b, "B")
        self.assertTrue(core.find(b, t["id"])["notified"])
        rang = []
        core.check_and_notify(b, notifier=lambda *x: rang.append(x))
        self.assertEqual(rang, [])

    def test_pause_propagates(self):
        a, b = fresh(), fresh()
        as_device("A")
        t = core.add_timer(a, 600, "x")
        converge(a, b)
        as_device("B")
        core.pause_item(b, t["id"])
        converge(a, b)
        self.assertFalse(core.find(a, t["id"])["running"])

    def test_concurrent_rename_and_pause_both_survive_in_either_order(self):
        for order in (("A", "B"), ("B", "A")):
            a, b = fresh(), fresh()
            as_device("A")
            t = core.add_timer(a, 600, "x")
            converge(a, b)
            as_device("A"); core.rename_item(a, t["id"], "renamed on A")
            time.sleep(0.01)
            as_device("B"); core.pause_item(b, t["id"])
            first, second = order
            states = {"A": a, "B": b}
            deliver(states[first], first, states[second], second)
            deliver(states[second], second, states[first], first)
            converge(a, b)
            self.assertEqual(view(a), view(b), f"devices diverged ({order})")
            it = core.find(a, t["id"])
            self.assertEqual(it["label"], "renamed on A")
            self.assertFalse(it["running"])

    def test_delete_propagates_and_newer_edit_wins_over_older_delete(self):
        a, b = fresh(), fresh()
        as_device("A")
        t = core.add_timer(a, 600, "x")
        converge(a, b)
        as_device("A"); core.remove_item(a, t["id"])
        converge(a, b)
        self.assertIsNone(core.find(b, t["id"]))
        # B edits the (already deleted elsewhere) item AFTER the delete -> it comes back
        c, d = fresh(), fresh()
        as_device("A"); u = core.add_timer(c, 600, "y")
        converge(c, d)
        as_device("A"); core.remove_item(c, u["id"])
        time.sleep(0.01)
        as_device("B"); core.rename_item(d, u["id"], "still here")
        converge(c, d)
        self.assertEqual(view(c), view(d))
        self.assertEqual(core.find(c, u["id"])["label"], "still here")

    def test_downtime_events_stay_on_their_own_device(self):
        a, b = fresh(), fresh()
        as_device("A")
        t = core.add_timer(a, 600, "x")
        core._log(t, "downtime", t["start_ts"] + 5, end_ts=t["start_ts"] + 50, duration=45, cause="power_cut")
        core._touch(t)
        deliver(a, "A", b, "B")
        types = [e["type"] for e in core.find(b, t["id"])["history"]]
        self.assertNotIn("downtime", types)
        self.assertTrue(any(e["type"] == "downtime" for e in core.find(a, t["id"])["history"]))

    def test_dirty_tracking_and_idempotence(self):
        a, b = fresh(), fresh()
        as_device("A")
        core.add_timer(a, 600, "x")
        self.assertTrue(sync.has_dirty(a))
        deliver(a, "A", b, "B")
        self.assertFalse(sync.has_dirty(a))
        self.assertFalse(deliver(a, "A", b, "B"))                 # nothing new, nothing changes
        self.assertEqual(sync.collect_outgoing(a), [])

    def test_hello_requests_a_snapshot_and_own_messages_are_ignored(self):
        a = fresh()
        as_device("A")
        self.assertEqual(sync.handle_payload(a, {"t": "hello", "dev": "B"}, "A"), (False, True))
        self.assertEqual(sync.handle_payload(a, {"t": "hello", "dev": "A"}, "A"), (False, False))
        self.assertEqual(sync.handle_payload(a, {"t": "garbage", "dev": "B"}, "A"), (False, False))
        self.assertEqual(sync.handle_payload(a, "not a dict", "A"), (False, False))


if __name__ == "__main__":
    unittest.main()
