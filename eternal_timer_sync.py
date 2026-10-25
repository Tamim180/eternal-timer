#!/usr/bin/env python3
"""
eternal_timer_sync.py -- keep two (or more) devices' timers in sync.

Why this works when the power is out: a timer is just absolute timestamps.
Once the phone has a copy of the state it keeps counting and rings on its
own; sync only has to deliver the *changes* (start / pause / ... / delete)
while both devices happen to be online.

Transport: an ntfy-compatible pub/sub topic (default https://ntfy.sh -- no
account, plain HTTP; you can self-host ntfy and change the server URL).
Everything is end-to-end encrypted (AES-256-GCM); the relay only sees
ciphertext on an unguessable topic derived from the pairing code.

Pairing code: 160 random bits as 8 groups of 4 base32 characters.
  topic = "etimer-" + hex(HMAC-SHA256(secret, "eternal-timer/topic/v1"))[:40]
  key   = HMAC-SHA256(secret, "eternal-timer/key/v1")            (32 bytes)
  wire  = base64url(nonce12 || AES-GCM(key, nonce, json, aad="eternal-timer-v1"))

Merge: each item has (rev, updated_at, updated_by); the higher tuple wins.
Deletions leave tombstones. `notified` and `downtime` history stay local to
each device: every device rings for itself, and "PC lost power" is not
something that happened to the phone's copy of the timer.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import eternal_timer_core as core

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAVE_CRYPTO = True
except Exception:          # pragma: no cover
    AESGCM = None
    HAVE_CRYPTO = False

DEFAULT_SERVER = "https://ntfy.sh"
SYNC_STATE_FILE = core.STATE_DIR / "sync_state.json"
AAD = b"eternal-timer-v1"
MAX_WIRE_CHARS = 3800            # ntfy messages are limited to 4096 bytes
WIRE_FIELDS = ("id", "label", "mode", "start_ts", "accumulated", "target_seconds",
               "running", "created_at", "rev", "updated_at", "updated_by", "state_at", "label_at")
STATE_FIELDS = ("mode", "start_ts", "accumulated", "target_seconds", "running", "created_at", "state_at")


# ---------------------------------------------------------------- pairing + crypto

def new_secret():
    """Fresh pairing code like 'ABCD-EFGH-...' (8 groups of 4)."""
    raw = secrets.token_bytes(20)
    s = base64.b32encode(raw).decode()
    return "-".join(s[i:i + 4] for i in range(0, 32, 4))


def parse_secret(code):
    c = "".join(ch for ch in code.upper() if ch.isalnum())
    try:
        raw = base64.b32decode(c)
    except Exception:
        raise ValueError("That doesn't look like a valid pairing code")
    if len(raw) != 20:
        raise ValueError("Pairing code has the wrong length")
    return raw


def derive(secret_bytes):
    topic = "etimer-" + hmac.new(secret_bytes, b"eternal-timer/topic/v1", hashlib.sha256).hexdigest()[:40]
    key = hmac.new(secret_bytes, b"eternal-timer/key/v1", hashlib.sha256).digest()
    return topic, key


def _b64e(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def encrypt(key, data, nonce=None):
    nonce = nonce or os.urandom(12)
    return _b64e(nonce + AESGCM(key).encrypt(nonce, data, AAD))


def decrypt(key, text):
    raw = _b64d(text.strip())
    return AESGCM(key).decrypt(raw[:12], raw[12:], AAD)


# ---------------------------------------------------------------- config

def load_config():
    """-> {'secret','server','topic','key'} or None if sync isn't set up."""
    cfg = core.load_settings().get("sync")
    if not cfg or not cfg.get("enabled", True) or not cfg.get("secret"):
        return None
    secret = parse_secret(cfg["secret"])
    topic, key = derive(secret)
    return {"secret": cfg["secret"], "server": (cfg.get("server") or DEFAULT_SERVER).rstrip("/"),
            "topic": topic, "key": key}


def save_config(secret_code, server=DEFAULT_SERVER, enabled=True):
    s = core.load_settings()
    s["sync"] = {"secret": secret_code, "server": server.rstrip("/"), "enabled": enabled}
    core.save_settings(s)


def disable():
    s = core.load_settings()
    s["sync"] = None
    core.save_settings(s)


def _load_sync_state():
    try:
        with open(SYNC_STATE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_sync_state(d):
    try:
        core._write_json(SYNC_STATE_FILE, d, indent=None)
    except OSError:
        pass


# ---------------------------------------------------------------- merge logic

def _state_ver(o):
    return (float(o.get("state_at") or o.get("updated_at") or 0.0), str(o.get("updated_by") or ""))


def _label_ver(o):
    return (float(o.get("label_at") or o.get("created_at") or 0.0), str(o.get("updated_by") or ""))


def _item_time(o):
    return max(float(o.get("state_at") or 0.0), float(o.get("label_at") or 0.0),
               float(o.get("updated_at") or 0.0))


def item_to_wire(item):
    w = {k: item.get(k) for k in WIRE_FIELDS if k in item}
    w["history"] = [e for e in (item.get("history") or []) if e.get("type") != "downtime"]
    return w


def _hist_key(e):
    return (e.get("type"), round(e.get("ts") or 0, 1), round(e.get("end_ts") or 0, 1))


def merge_history(first, second):
    """Union of two event lists (deduplicated), oldest first."""
    seen, out = set(), []
    for e in list(first) + list(second):
        k = _hist_key(e)
        if k not in seen:
            seen.add(k)
            out.append(e)
    out.sort(key=lambda e: (e.get("ts") is not None, e.get("ts") or 0))
    if len(out) > core.MAX_EVENTS:
        out = out[:1] + out[-(core.MAX_EVENTS - 1):]
    return out


def apply_item(state, w):
    """Merge one remote item into `state`. The label and the timer state are
    each last-writer-wins on their own timestamp, so concurrent edits to
    *different* things both survive. Returns True if state changed."""
    tombs = state.setdefault("tombstones", {})
    t = tombs.get(w["id"])
    if t and not (_item_time(w) > float(t.get("updated_at") or 0.0)):
        return False                       # deleted after this version
    local = core.find(state, w["id"])
    if local is None:
        item = {k: w.get(k) for k in WIRE_FIELDS if k in w}
        item["history"] = list(w.get("history") or [])
        rem = core.remaining_seconds(item) if item.get("mode") == "timer" else None
        item["notified"] = bool(rem is not None and rem <= 0)      # already over: don't ring for it
        item["pushed_rev"] = int(w.get("rev", 0))
        state["items"].append(item)
        tombs.pop(w["id"], None)
        return True

    tombs.pop(w["id"], None)
    take_state = _state_ver(w) > _state_ver(local)
    take_label = _label_ver(w) > _label_ver(local)
    remote_lacks = (_state_ver(local) > _state_ver(w)) or (_label_ver(local) > _label_ver(w))
    changed = False
    if take_state:
        keep_notified = bool(local.get("notified"))
        local["history"] = merge_history(w.get("history") or [], local.get("history") or [])
        for k in STATE_FIELDS:
            if k in w:
                local[k] = w[k]
        over = local["mode"] == "timer" and core.remaining_seconds(local) <= 0
        local["notified"] = keep_notified if over else False
        changed = True
    if take_label:
        local["label"] = w.get("label", "")
        local["label_at"] = w.get("label_at")
        changed = True
    if changed or remote_lacks:
        local["updated_at"] = max(float(local.get("updated_at") or 0), float(w.get("updated_at") or 0))
        local["updated_by"] = w.get("updated_by") if changed and not remote_lacks else local.get("updated_by")
    if remote_lacks:       # we hold newer info than the sender: push the merged result back
        local["rev"] = max(int(local.get("rev", 0)), int(w.get("rev", 0))) + 1
    else:
        local["rev"] = max(int(local.get("rev", 0)), int(w.get("rev", 0)))
        local["pushed_rev"] = local["rev"]
    return changed


def apply_tomb(state, t):
    tombs = state.setdefault("tombstones", {})
    local = core.find(state, t["id"])
    if local is not None and not (float(t["updated_at"]) > _item_time(local)):
        return False                       # item was changed after the delete
    cur = tombs.get(t["id"])
    if cur and not (float(t["updated_at"]) > float(cur.get("updated_at") or 0)):
        return False
    state["items"] = [i for i in state["items"] if i["id"] != t["id"]]
    tombs[t["id"]] = {"rev": t["rev"], "updated_at": t["updated_at"],
                      "updated_by": t.get("updated_by", ""), "pushed": True}
    return True


def handle_payload(state, payload, my_dev):
    """-> (state_changed, peer_wants_snapshot)"""
    if not isinstance(payload, dict) or payload.get("dev") == my_dev:
        return False, False
    t = payload.get("t")
    if t == "item" and isinstance(payload.get("item"), dict) and "id" in payload["item"]:
        return apply_item(state, payload["item"]), False
    if t == "tomb" and isinstance(payload.get("tomb"), dict) and "id" in payload["tomb"]:
        return apply_tomb(state, payload["tomb"]), False
    if t == "hello":
        return False, True
    return False, False


def ensure_stamped(state):
    """Items from before sync existed get an initial revision. -> changed?"""
    changed = False
    for item in state["items"]:
        if "rev" not in item:
            t0 = item.get("created_at") or time.time()
            item.update({"rev": 1, "updated_at": t0, "updated_by": core.device_id(),
                         "state_at": t0, "label_at": t0})
            changed = True
    return changed


def has_dirty(state):
    return (any(int(i.get("pushed_rev", 0)) < int(i.get("rev", 0)) for i in state["items"])
            or any(not t.get("pushed") for t in (state.get("tombstones") or {}).values()))


def collect_outgoing(state, force_all=False):
    """-> list of (kind, id, rev, payload_dict). Call ensure_stamped first."""
    me = core.device_id()
    out = []
    for item in state["items"]:
        if force_all or int(item.get("pushed_rev", 0)) < int(item.get("rev", 0)):
            out.append(("item", item["id"], int(item.get("rev", 0)),
                        {"v": 1, "t": "item", "dev": me, "item": item_to_wire(item)}))
    for tid, t in (state.get("tombstones") or {}).items():
        if force_all or not t.get("pushed"):
            out.append(("tomb", tid, int(t.get("rev", 0)),
                        {"v": 1, "t": "tomb", "dev": me,
                         "tomb": {"id": tid, "rev": t["rev"], "updated_at": t["updated_at"],
                                  "updated_by": t.get("updated_by", "")}}))
    return out


def mark_pushed(state, kind, item_id, rev):
    if kind == "item":
        it = core.find(state, item_id)
        if it is not None:
            it["pushed_rev"] = max(int(it.get("pushed_rev", 0)), rev)
    else:
        t = (state.get("tombstones") or {}).get(item_id)
        if t is not None and int(t.get("rev", 0)) <= rev:
            t["pushed"] = True


def pack(payload, key):
    """Encrypt a payload; trims old history events if it would exceed the
    relay's message size limit."""
    while True:
        text = encrypt(key, json.dumps(payload, separators=(",", ":")).encode())
        hist = payload.get("item", {}).get("history") if payload.get("t") == "item" else None
        if len(text) <= MAX_WIRE_CHARS or not hist or len(hist) <= 2:
            return text
        del hist[1]


def unpack(text, key):
    try:
        return json.loads(decrypt(key, text))
    except Exception:
        return None          # wrong key / corrupted / not ours


# ---------------------------------------------------------------- ntfy transport

class NtfyClient:
    def __init__(self, server, topic, timeout=25):
        self.server, self.topic, self.timeout = server.rstrip("/"), topic, timeout

    def _url(self, suffix, **params):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return f"{self.server}/{self.topic}{suffix}{q}"

    def publish(self, text):
        req = urllib.request.Request(self._url(""), data=text.encode(), method="POST",
                                     headers={"Content-Type": "text/plain", "Cache": "yes",
                                              "User-Agent": "eternal-timer"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            r.read()

    def poll(self, since="all"):
        """Cached messages since `since` -> [{'id','time','message'}, ...]"""
        req = urllib.request.Request(self._url("/json", poll="1", since=since),
                                     headers={"User-Agent": "eternal-timer"})
        out = []
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            for line in r.read().decode().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event") == "message":
                    out.append(ev)
        return out

    def stream(self, since, stop, on_open=None):
        """Yield messages live over SSE until `stop` is set or the link drops."""
        req = urllib.request.Request(self._url("/sse", since=since),
                                     headers={"User-Agent": "eternal-timer", "Accept": "text/event-stream"})
        with urllib.request.urlopen(req, timeout=90) as r:
            if on_open:
                on_open()
            while not stop.is_set():
                raw = r.readline()
                if not raw:
                    return                    # server closed
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if ev.get("event") == "message":
                    yield ev


# ---------------------------------------------------------------- one-shot sync (CLI / systemd)

def sync_once(cfg=None, hello=False):
    """Pull everything cached on the relay, merge, push whatever is dirty.
    Returns a small stats dict. Raises on network errors."""
    cfg = cfg or load_config()
    if cfg is None:
        raise RuntimeError("Sync is not set up (etimer sync setup / join CODE)")
    if not HAVE_CRYPTO:
        raise RuntimeError("python-cryptography is required for sync")
    me = core.device_id()
    client = NtfyClient(cfg["server"], cfg["topic"])
    ss = _load_sync_state()
    state = core.load_state()
    stats = {"received": 0, "applied": 0, "sent": 0}
    want_snapshot = hello
    last_id = ss.get("last_id")
    for ev in client.poll(since=last_id or "all"):
        last_id = ev.get("id", last_id)
        payload = unpack(ev.get("message", ""), cfg["key"])
        if payload is None:
            continue
        stats["received"] += 1
        changed, snap = handle_payload(state, payload, me)
        stats["applied"] += int(changed)
        want_snapshot = want_snapshot or snap
    ensure_stamped(state)
    if hello:
        client.publish(pack({"v": 1, "t": "hello", "dev": me}, cfg["key"]))
    for kind, iid, rev, payload in collect_outgoing(state, force_all=want_snapshot and not hello):
        client.publish(pack(payload, cfg["key"]))
        mark_pushed(state, kind, iid, rev)
        stats["sent"] += 1
    core.save_state(state)
    if last_id:
        ss["last_id"] = last_id
        _save_sync_state(ss)
    return stats


# ---------------------------------------------------------------- live subscriber (GUI)

class Subscriber(threading.Thread):
    """Keeps an SSE connection open (auto-reconnect with backoff) and hands
    every decrypted payload to `on_payload(payload)` -- called on THIS
    thread, so GUI code must hop to its own thread. `on_status(str)` gets
    'connecting' / 'online' / 'offline'."""

    def __init__(self, cfg, on_payload, on_status):
        super().__init__(daemon=True)
        self.cfg, self.on_payload, self.on_status = cfg, on_payload, on_status
        self.stop_event = threading.Event()
        self.client = NtfyClient(cfg["server"], cfg["topic"])

    def stop(self):
        self.stop_event.set()

    def run(self):
        backoff = 2
        while not self.stop_event.is_set():
            try:
                self.on_status("connecting")
                ss = _load_sync_state()
                since = ss.get("last_id") or "all"
                for ev in self.client.stream(since, self.stop_event,
                                             on_open=lambda: self.on_status("online")):
                    backoff = 2
                    payload = unpack(ev.get("message", ""), self.cfg["key"])
                    if payload is not None:
                        self.on_payload(payload)
                    ss = _load_sync_state()
                    ss["last_id"] = ev.get("id", ss.get("last_id"))
                    _save_sync_state(ss)
            except Exception:
                pass
            if self.stop_event.is_set():
                break
            self.on_status("offline")
            self.stop_event.wait(backoff)
            backoff = min(60, backoff * 2)

    def publish(self, payload):
        self.client.publish(pack(payload, self.cfg["key"]))
