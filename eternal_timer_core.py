#!/usr/bin/env python3
"""
eternal_timer_core.py

Shared persistence/logic for Eternal Timer. Supports any number of
simultaneous timers and stopwatches. Nothing is ever stored as a "live"
countdown -- only absolute Unix timestamps + an accumulated offset (for
pause support) per item. Anything that wants the current state (CLI,
GUI, tray) recomputes from wall-clock time on demand, so it is correct
no matter how long the machine was off (shutdown or power cut both --
see note below).

Files in ~/.local/share/eternal-timer/
  state.json      items (timers/stopwatches) + each item's event history
  settings.json   sound on/off, optional custom sound file
  heartbeat.json  written every few seconds by the GUI; used on the next
                  start to work out how long the app/PC was down and why
  sounds/         the generated default chime

Durability note: each mutation (start/pause/resume/remove) calls
save_state(), which does a write-then-rename onto the state file. That
write is small and synchronous, so by the time any button click or CLI
command returns, the new timestamp is already on disk. There is no
"save on exit" step to miss.

History: every item carries a `history` list of events:
  started / paused / resumed / restarted / finished   (ts)
  downtime   (ts .. end_ts, cause)  -- time the app or PC was not running
             while this item was counting
Pause durations are derived from paused->resumed pairs when displayed.
"""

import json
import math
import os
import re
import shutil
import struct
import subprocess
import time
import uuid
import wave
from datetime import datetime
from pathlib import Path

STATE_DIR = Path(os.path.expanduser("~/.local/share/eternal-timer"))
STATE_FILE = STATE_DIR / "state.json"
SETTINGS_FILE = STATE_DIR / "settings.json"
HEARTBEAT_FILE = STATE_DIR / "heartbeat.json"
SOUND_DIR = STATE_DIR / "sounds"

APP_NAME = "Eternal Timer"
MAX_EVENTS = 500          # per item; oldest events (after the first) are dropped


# ---------------------------------------------------------------- storage

def _write_json(path, data, indent=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=indent)
    tmp.replace(path)


def load_state():
    if not STATE_FILE.exists():
        return {"items": []}
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        if "items" not in data or not isinstance(data["items"], list):
            return {"items": []}
        return data
    except (json.JSONDecodeError, OSError):
        return {"items": []}


def save_state(state):
    _write_json(STATE_FILE, state)


# ---------------------------------------------------------------- settings + sound

DEFAULT_SETTINGS = {"sound_enabled": True, "sound_file": None, "sound_style": "alarm",
                    "theme": "aurora", "calm": False, "device_id": None, "sync": None,
                    "warn_stopwatch": True}


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            s.update({k: data[k] for k in DEFAULT_SETTINGS if k in data})
    except (OSError, json.JSONDecodeError):
        pass
    return s


def save_settings(settings):
    _write_json(SETTINGS_FILE, settings)
    try:
        os.chmod(SETTINGS_FILE, 0o600)      # may contain the sync pairing secret
    except OSError:
        pass


def sound_enabled():
    return bool(load_settings().get("sound_enabled", True))


def set_sound_enabled(on):
    s = load_settings()
    s["sound_enabled"] = bool(on)
    save_settings(s)


def set_sound_style(style):
    s = load_settings()
    s["sound_style"] = "chime" if style == "chime" else "alarm"
    save_settings(s)


def set_sound_file(path):
    """path=None goes back to the built-in chime."""
    s = load_settings()
    s["sound_file"] = str(Path(path).expanduser().resolve()) if path else None
    save_settings(s)


def _synthesize_chime(path):
    """A soft three-note bell arpeggio (C6-E6-G6), written as a 16-bit mono
    WAV. Generated once and cached, so there is no binary asset to ship."""
    rate = 44100
    total = 2.4
    n = int(rate * total)
    buf = [0.0] * n
    notes = [(1046.50, 0.00), (1318.51, 0.30), (1567.98, 0.60)]
    # (frequency ratio, amplitude, decay time constant in seconds)
    partials = [(1.0, 1.00, 0.55), (2.0, 0.35, 0.32), (2.76, 0.20, 0.22), (5.4, 0.07, 0.10)]
    for freq, start in notes:
        s0 = int(start * rate)
        length = int(1.5 * rate)
        for i in range(min(length, n - s0)):
            t = i / rate
            attack = min(1.0, t / 0.004)
            v = 0.0
            for ratio, amp, tau in partials:
                v += amp * math.exp(-t / tau) * math.sin(2 * math.pi * freq * ratio * t)
            buf[s0 + i] += v * attack
    peak = max(abs(x) for x in buf) or 1.0
    scale = 0.7 * 32767 / peak
    SOUND_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(x * scale)) for x in buf))
    tmp.replace(path)


def _write_wav(path, samples, rate=44100):
    SOUND_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1.0, min(1.0, x)) * 32767)) for x in samples))
    tmp.replace(path)


def _synthesize_alarm(path):
    """Digital-alarm-clock style: four sharp beeps, short rest, repeated four
    times (~5 s), loud. Written as a 16-bit mono WAV."""
    rate = 44100
    samples = []
    beep, gap, rest = 0.12, 0.08, 0.5
    for _ in range(4):
        for _ in range(4):
            n = int(beep * rate)
            for i in range(n):
                t = i / rate
                env = min(1.0, t / 0.004, (beep - t) / 0.006)
                v = math.sin(2 * math.pi * 1760 * t) + 0.35 * math.sin(2 * math.pi * 3520 * t) \
                    + 0.15 * math.sin(2 * math.pi * 5280 * t)
                samples.append(0.65 * env * v)
            samples += [0.0] * int(gap * rate)
        samples += [0.0] * int(rest * rate)
    _write_wav(path, samples, rate)


def default_sound_path():
    style = load_settings().get("sound_style", "alarm")
    if style == "chime":
        path = SOUND_DIR / "chime-v1.wav"
        if not path.exists():
            _synthesize_chime(path)
    else:
        path = SOUND_DIR / "alarm-v1.wav"
        if not path.exists():
            _synthesize_alarm(path)
    return str(path)


def sound_path():
    """Custom sound if one is set and still exists, else the built-in chime."""
    custom = load_settings().get("sound_file")
    if custom and Path(custom).is_file():
        return custom
    return default_sound_path()


_PLAYERS = [
    ["pw-play"], ["paplay"], ["aplay", "-q"], ["afplay"],
    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],
    ["canberra-gtk-play", "-f"],
]


_QT_PLAYER_CODE = """
import sys
from PySide6.QtCore import QCoreApplication, QTimer, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
app = QCoreApplication([])
out = QAudioOutput(); out.setVolume(0.85)
p = QMediaPlayer(); p.setAudioOutput(out)
p.mediaStatusChanged.connect(lambda st: app.quit() if st == QMediaPlayer.EndOfMedia else None)
p.errorOccurred.connect(lambda *a: app.quit())
p.setSource(QUrl.fromLocalFile(sys.argv[1])); p.play()
QTimer.singleShot(15000, app.quit)
app.exec()
"""


def _sound_log(msg):
    """Append to sound.log so a silent failure can be diagnosed."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "sound.log", "a") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except OSError:
        pass


def app_log(msg):
    """Append to app.log (GUI start/quit reasons) for diagnosing odd exits."""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / "app.log", "a") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except OSError:
        pass


def spawn_player(path, verbose=False):
    """Play `path` in a separate process so an audio problem can never take
    the app down. Tries command-line players first (pw-play, paplay, ...),
    then a tiny child process using Qt Multimedia. A player that exits with
    an error within 0.7 s is treated as failed and the next one is tried;
    one still running after that is assumed to be playing. Every attempt is
    logged to sound.log. May block ~0.7 s per failing player, so the GUI
    calls this from a thread."""
    import sys
    candidates = []
    for cmd in _PLAYERS:
        exe = shutil.which(cmd[0])
        if exe:
            candidates.append([exe, *cmd[1:], path])
    try:
        import importlib.util
        if importlib.util.find_spec("PySide6") is not None:
            candidates.append([sys.executable, "-c", _QT_PLAYER_CODE, path])
    except Exception:
        pass
    if not candidates:
        _sound_log("no audio player found (pw-play, paplay, aplay, ffplay, PySide6 multimedia)")
        if verbose:
            print("No audio player found.")
        return False
    for cmd in candidates:
        name = Path(cmd[0]).name if "-c" not in cmd else "qt-multimedia"
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, start_new_session=True)
        except OSError as e:
            _sound_log(f"{name}: could not start ({e})")
            continue
        try:
            _, err = proc.communicate(timeout=0.7)
        except subprocess.TimeoutExpired:
            _sound_log(f"{name}: playing {path}")
            if verbose:
                print(f"Playing with {name}")
            return True
        if proc.returncode == 0:
            _sound_log(f"{name}: finished quickly (ok) {path}")
            if verbose:
                print(f"Played with {name}")
            return True
        msg = (err or b"").decode(errors="replace").strip().splitlines()
        msg = msg[-1] if msg else f"exit code {proc.returncode}"
        _sound_log(f"{name}: failed - {msg}")
        if verbose:
            print(f"{name} failed: {msg}")
    return False


def play_sound():
    """Play the finish sound if sound is enabled. Never raises."""
    try:
        if not sound_enabled():
            _sound_log("finish sound skipped: sound is OFF (run: etimer sound on)")
            return False
        return spawn_player(sound_path())
    except Exception as e:
        _sound_log(f"play_sound error: {e!r}")
        return False


def notify(title, body, sound=True):
    try:
        subprocess.run(["notify-send", "-a", APP_NAME, title, body], check=False)
    except FileNotFoundError:
        print(f"[notify unavailable] {title}: {body}")
    if sound:
        play_sound()


# ---------------------------------------------------------------- parsing/format

def parse_duration(text):
    """'25m', '1h30m', '1:30:00', '90s', '2 hours 5 minutes' -> seconds."""
    text = text.strip().lower()
    if re.fullmatch(r"\d+(:\d+){1,2}", text):
        parts = [int(p) for p in text.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, s = parts
        return h * 3600 + m * 60 + s

    total = 0
    found = False
    for value, unit in re.findall(r"(\d+)\s*(h(?:ours?)?|m(?:in(?:ute)?s?)?|s(?:ec(?:ond)?s?)?)", text):
        v = int(value)
        if unit.startswith("h"):
            total += v * 3600
        elif unit.startswith("m"):
            total += v * 60
        elif unit.startswith("s"):
            total += v
        found = True
    if not found:
        raise ValueError(f"Couldn't parse duration: {text!r}")
    return total


def format_hms(seconds):
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_duration_long(seconds):
    """Human style: '1h 05m 03s', '5m 30s', '12s'."""
    seconds = max(0, int(round(seconds)))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h:02d}h {m:02d}m"
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# ---------------------------------------------------------------- item math

def elapsed_seconds(item, now=None):
    now = now if now is not None else time.time()
    acc = item.get("accumulated", 0.0)
    if item.get("running") and item.get("start_ts"):
        return acc + (now - item["start_ts"])
    return acc


def remaining_seconds(item, now=None):
    if item.get("mode") != "timer":
        return None
    return item["target_seconds"] - elapsed_seconds(item, now)


def is_finished(item, now=None):
    if item.get("mode") != "timer":
        return False
    return remaining_seconds(item, now) <= 0


# ---------------------------------------------------------------- sync stamping
#
# Every item carries rev / updated_at / updated_by. A device bumps these on
# each real change (never on merely receiving a change), and the sync layer
# resolves conflicts by comparing (rev, updated_at, updated_by).

_DEVICE_ID = None


def device_id():
    global _DEVICE_ID
    if _DEVICE_ID:
        return _DEVICE_ID
    s = load_settings()
    d = s.get("device_id")
    if not d:
        d = uuid.uuid4().hex[:8]
        s["device_id"] = d
        save_settings(s)
    _DEVICE_ID = d
    return d


def _touch(item, now=None, label=False):
    """Stamp a local change. The label and the timer state are versioned
    separately (label_at / state_at) so renaming on one device and
    pausing on another both survive a sync."""
    now = now if now is not None else time.time()
    item["rev"] = int(item.get("rev", 0)) + 1          # "needs pushing" counter
    item["updated_at"] = now
    item["updated_by"] = device_id()
    item["label_at" if label else "state_at"] = now


# ---------------------------------------------------------------- history log

_OPEN = {"started", "resumed", "restarted"}      # item starts counting
_CLOSE = {"paused", "finished"}                  # item stops counting


def ensure_history(item):
    """Items created before history existed get a best-effort starting log."""
    if isinstance(item.get("history"), list):
        return item["history"]
    created = item.get("created_at") or time.time()
    if item.get("running") and item.get("start_ts"):
        hist = [{"type": "started", "ts": item["start_ts"], "note": "History tracking began here"}]
    else:
        hist = [{"type": "started", "ts": created, "note": "Before history tracking"}]
        if item.get("mode") == "timer" and item.get("notified"):
            hist.append({"type": "finished", "ts": None})
        else:
            hist.append({"type": "paused", "ts": None})
    item["history"] = hist
    return hist


def _log(item, etype, ts=None, **extra):
    hist = ensure_history(item)
    ev = {"type": etype, "ts": time.time() if ts is None else ts}
    ev.update(extra)
    hist.append(ev)
    if len(hist) > MAX_EVENTS:
        del hist[1:len(hist) - MAX_EVENTS + 1]
    return ev


def running_intervals(item, now):
    """[(start, end), ...] during which the item was counting, rebuilt from
    its history. An interval still open is closed at `now` (for a timer,
    no later than the moment it reached zero)."""
    evs = sorted(
        (e for e in ensure_history(item)
         if e.get("ts") is not None and e.get("type") in (_OPEN | _CLOSE)),
        key=lambda e: e["ts"],
    )
    out, start = [], None
    for e in evs:
        if e["type"] in _OPEN:
            if start is None:
                start = e["ts"]
        elif start is not None:
            out.append((start, e["ts"]))
            start = None
    if start is not None:
        end = now
        if item.get("mode") == "timer" and item.get("running") and item.get("start_ts"):
            end = min(end, item["start_ts"] + item["target_seconds"] - item.get("accumulated", 0.0))
        if end > start:
            out.append((start, end))
    return out


# ---------------------------------------------------------------- mutations

def _new_item(mode, label, target_seconds=None):
    now = time.time()
    return {
        "id": uuid.uuid4().hex[:8],
        "label": label or "",
        "mode": mode,
        "start_ts": now,
        "accumulated": 0.0,
        "target_seconds": target_seconds,
        "running": True,
        "notified": False,
        "created_at": now,
        "history": [{"type": "started", "ts": now}],
        "rev": 0,
    }


def add_timer(state, seconds, label=""):
    item = _new_item("timer", label, target_seconds=float(seconds))
    _touch(item, item["created_at"])
    item["label_at"] = item["created_at"]
    state["items"].append(item)
    save_state(state)
    return item


def add_stopwatch(state, label=""):
    item = _new_item("stopwatch", label)
    _touch(item, item["created_at"])
    item["label_at"] = item["created_at"]
    state["items"].append(item)
    save_state(state)
    return item


def find(state, item_id):
    for item in state["items"]:
        if item["id"] == item_id:
            return item
    return None


def find_by_prefix(state, prefix):
    """Match by id prefix, falling back to a unique label match."""
    matches = [i for i in state["items"] if i["id"].startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    label_matches = [i for i in state["items"] if i.get("label", "").lower() == prefix.lower()]
    if len(label_matches) == 1:
        return label_matches[0]
    return None


def pause_item(state, item_id):
    item = find(state, item_id)
    if item and item.get("running"):
        now = time.time()
        item["accumulated"] = elapsed_seconds(item, now)
        item["running"] = False
        item["start_ts"] = None
        _log(item, "paused", now)
        _touch(item, now)
        save_state(state)
    return item


def resume_item(state, item_id):
    item = find(state, item_id)
    if item and not item.get("running") and not is_finished(item):
        now = time.time()
        item["start_ts"] = now
        item["running"] = True
        _log(item, "resumed", now)
        _touch(item, now)
        save_state(state)
    return item


def restart_item(state, item_id):
    """Timer only: reset it back to full duration and start running again."""
    item = find(state, item_id)
    if item and item.get("mode") == "timer":
        now = time.time()
        item["start_ts"] = now
        item["accumulated"] = 0.0
        item["notified"] = False
        item["running"] = True
        _log(item, "restarted", now)
        _touch(item, now)
        save_state(state)
    return item


def remove_item(state, item_id):
    item = find(state, item_id)
    now = time.time()
    tombs = state.setdefault("tombstones", {})
    if item is not None:     # leave a tombstone so other devices delete it too
        tombs[item_id] = {"rev": int(item.get("rev", 0)) + 1, "updated_at": now,
                          "updated_by": device_id(), "pushed": False}
    state["items"] = [i for i in state["items"] if i["id"] != item_id]
    for tid in [t for t, v in tombs.items() if now - v.get("updated_at", now) > 30 * 86400]:
        del tombs[tid]
    save_state(state)


def rename_item(state, item_id, new_label):
    item = find(state, item_id)
    if item:
        item["label"] = new_label
        _touch(item, label=True)
        save_state(state)
    return item


def check_and_notify(state, notifier=None):
    """Call periodically (CLI 'check', autostart, systemd timer, GUI refresh).
    Fires the finish notification exactly once per timer, even if the
    moment it hit zero happened while the machine was off or powered down.

    The history records the moment the timer actually reached zero, plus
    (if different) when we noticed -- so a timer that ended during a power
    cut shows both.

    `notifier`, if given, is called as notifier(title, body) instead of the
    default notify-send + sound call -- the GUI passes one that pops a tray
    message and plays the sound itself, which also works inside a Flatpak
    sandbox with no notify-send binary available."""
    send = notifier or notify
    changed = False
    for item in state["items"]:
        if item.get("mode") == "timer" and not item.get("notified"):
            if remaining_seconds(item) <= 0:
                now = time.time()
                if item.get("running") and item.get("start_ts"):
                    finish_ts = item["start_ts"] + item["target_seconds"] - item.get("accumulated", 0.0)
                else:
                    finish_ts = now
                label = item.get("label") or "Timer"
                send(f"{label} finished", "Your countdown reached zero.")
                item["notified"] = True
                item["accumulated"] = item["target_seconds"]
                item["running"] = False
                item["start_ts"] = None
                if not any(e.get("type") == "finished" and e.get("ts") is not None
                           for e in (item.get("history") or [])):
                    _log(item, "finished", finish_ts, noticed_ts=now)
                    _touch(item, now)
                changed = True
    if changed:
        save_state(state)
    return state


# ---------------------------------------------------------------- downtime tracking
#
# The GUI writes heartbeat.json every few seconds. On the next start:
#   * heartbeat flagged clean_exit + same boot      -> app was closed
#   * heartbeat NOT clean + same boot               -> app crashed / was killed
#   * boot id changed, clean exit                   -> system shut down / restarted
#   * boot id changed, NOT clean                    -> power cut (or hard reset)
# When the boot changed, the gap is split at the moment the PC booted, so you
# see "off until 14:02" and "app not opened until 14:05" separately.
# Limits: shutdown vs. power cut is inferred (no clean exit written = cut);
# a graceful quit followed by a shutdown can't tell you when the app was
# closed vs. when the PC went off.

CAUSE_LABELS = {
    "power_cut": "Power cut / unclean shutdown",
    "shutdown": "System shut down",
    "boot_app_closed": "App not open (PC was on)",
    "app_closed": "App closed",
    "app_interrupted": "App stopped unexpectedly",
    "suspend": "PC asleep",
}


def get_boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def get_boot_time():
    try:
        return time.time() - float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def write_heartbeat(clean=False, now=None):
    try:
        _write_json(HEARTBEAT_FILE, {
            "last_seen": now if now is not None else time.time(),
            "clean_exit": bool(clean),
            "boot_id": get_boot_id(),
        }, indent=None)
    except OSError:
        pass


def read_heartbeat():
    try:
        with open(HEARTBEAT_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def record_gap(state, gap_start, gap_end, cause):
    """Add a `downtime` event to every item that was counting during
    [gap_start, gap_end]. Returns [(item_id, start, end, cause), ...]."""
    if gap_end - gap_start < 1:
        return []
    logged = []
    for item in state["items"]:
        for a, b in running_intervals(item, gap_end):
            s, e = max(a, gap_start), min(b, gap_end)
            if e - s >= 1:
                _log(item, "downtime", s, end_ts=e, duration=e - s, cause=cause)
                logged.append((item["id"], s, e, cause))
    if logged:
        save_state(state)
    return logged


def record_startup_downtime(state, now=None):
    """Call once at GUI start, BEFORE check_and_notify, then write a fresh
    heartbeat. Returns the list of logged gaps."""
    now = now if now is not None else time.time()
    hb = read_heartbeat()
    if not hb or not hb.get("last_seen"):
        return []
    last = float(hb["last_seen"])
    clean = bool(hb.get("clean_exit"))
    if now - last < (3.0 if clean else 15.0):
        return []

    boot_now = get_boot_id()
    rebooted = bool(hb.get("boot_id") and boot_now and hb["boot_id"] != boot_now)
    segments = []
    if rebooted:
        off_cause = "shutdown" if clean else "power_cut"
        boot_ts = get_boot_time()
        if boot_ts is None or boot_ts <= last:
            segments.append((last, now, off_cause))
        else:
            boot_ts = min(boot_ts, now)
            segments.append((last, boot_ts, off_cause))
            segments.append((boot_ts, now, "boot_app_closed"))
    else:
        segments.append((last, now, "app_closed" if clean else "app_interrupted"))

    logged = []
    for s, e, cause in segments:
        logged += record_gap(state, s, e, cause)
    return logged


# ---------------------------------------------------------------- history views

def history_rows(item, now=None):
    """Turn an item's event log into display rows (oldest first) plus totals.
    paused->resumed pairs are merged into one row with a duration; a pause
    that hasn't ended yet shows as ongoing."""
    now = now if now is not None else time.time()
    events = list(ensure_history(item))
    events.sort(key=lambda e: (e.get("ts") is not None, e.get("ts") or 0))

    rows, consumed = [], set()
    pause_n = pause_s = off_n = off_s = 0

    for i, e in enumerate(events):
        if i in consumed:
            continue
        t, ts = e.get("type"), e.get("ts")

        if t == "paused":
            j_found = next((j for j in range(i + 1, len(events))
                            if events[j].get("type") in ("resumed", "restarted")), None)
            if j_found is None:
                end, ongoing = None, True
                dur = now - ts if ts is not None else None
            else:
                end, ongoing = events[j_found].get("ts"), False
                dur = end - ts if (ts is not None and end is not None) else None
                if events[j_found]["type"] == "resumed":
                    consumed.add(j_found)
            rows.append({"kind": "paused", "title": "Paused", "start": ts, "end": end,
                         "duration": dur, "ongoing": ongoing, "detail": ""})
            pause_n += 1
            pause_s += dur or 0

        elif t == "downtime":
            end = e.get("end_ts")
            dur = e.get("duration") if e.get("duration") is not None else (end - ts if end and ts else None)
            rows.append({"kind": "downtime", "title": CAUSE_LABELS.get(e.get("cause"), "Offline"),
                         "start": ts, "end": end, "duration": dur, "ongoing": False,
                         "detail": "Kept counting while offline"})
            off_n += 1
            off_s += dur or 0

        elif t == "finished":
            detail = ""
            noticed = e.get("noticed_ts")
            if ts is not None and noticed and noticed - ts > 10:
                detail = (f"Noticed at {fmt_ts(noticed, ref=ts)} "
                          f"({format_duration_long(noticed - ts)} later)")
            rows.append({"kind": "finished", "title": "Finished", "start": ts, "end": None,
                         "duration": None, "ongoing": False, "detail": detail})

        elif t == "started":
            rows.append({"kind": "started", "title": "Started", "start": ts, "end": None,
                         "duration": None, "ongoing": False, "detail": e.get("note", "")})
        elif t == "resumed":
            rows.append({"kind": "resumed", "title": "Resumed", "start": ts, "end": None,
                         "duration": None, "ongoing": False, "detail": ""})
        elif t == "restarted":
            rows.append({"kind": "restarted", "title": "Restarted", "start": ts, "end": None,
                         "duration": None, "ongoing": False, "detail": "Timer reset to full duration"})

    totals = {"pauses": pause_n, "paused_seconds": pause_s,
              "downtimes": off_n, "downtime_seconds": off_s}
    return rows, totals


def fmt_ts(ts, ref=None):
    """'14:02:10', or 'Oct 21, 14:02:10' when not on the same day as `ref`."""
    if ts is None:
        return "unknown"
    ref = ref if ref is not None else time.time()
    d, r = datetime.fromtimestamp(ts), datetime.fromtimestamp(ref)
    if d.date() == r.date():
        return d.strftime("%H:%M:%S")
    return d.strftime("%b %d, %H:%M:%S")


def row_when_text(row, now=None):
    s, e = row["start"], row["end"]
    if s is None and e is None and row["kind"] != "paused":
        return "time unknown"
    if row["kind"] in ("paused", "downtime"):
        a = fmt_ts(s, ref=now) if s is not None else "unknown"
        if row["ongoing"]:
            b = "now"
        else:
            b = fmt_ts(e, ref=s if s is not None else now) if e is not None else "unknown"
        return f"{a} \u2192 {b}"
    return fmt_ts(s, ref=now)


def history_text(item, now=None):
    """Plain-text history for the CLI."""
    now = now if now is not None else time.time()
    rows, totals = history_rows(item, now)
    label = item.get("label") or ("Timer" if item["mode"] == "timer" else "Stopwatch")
    lines = [f"[{item['id']}] {label} -- history", ""]
    for r in rows:
        dur = f"  ({format_duration_long(r['duration'])}{', ongoing' if r['ongoing'] else ''})" \
            if r["duration"] is not None else ""
        extra = f"  - {r['detail']}" if r["detail"] else ""
        lines.append(f"  {row_when_text(r, now):<34} {r['title']}{dur}{extra}")
    lines.append("")
    lines.append(f"  Pauses: {totals['pauses']} ({format_duration_long(totals['paused_seconds'])} total)"
                 f"   Offline gaps: {totals['downtimes']} ({format_duration_long(totals['downtime_seconds'])} total)")
    return lines


def summary_line(item, now=None):
    label = item.get("label") or ("Timer" if item["mode"] == "timer" else "Stopwatch")
    if item["mode"] == "timer":
        remaining = max(0, remaining_seconds(item, now))
        state_word = "finished" if remaining <= 0 else ("running" if item["running"] else "paused")
        return f"[{item['id']}] {label}: {format_hms(remaining)} remaining ({state_word})"
    else:
        elapsed = elapsed_seconds(item, now)
        state_word = "running" if item["running"] else "paused"
        return f"[{item['id']}] {label}: {format_hms(elapsed)} elapsed ({state_word})"
