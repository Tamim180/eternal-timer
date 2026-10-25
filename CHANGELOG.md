# Changelog

## 3.0.0 — 2026-10-25
- **Android companion app** with exact alarms, a full-screen alarm, history and the same ring design.
- **Optional end-to-end encrypted sync** between devices through an ntfy relay (pairing code, per-field merge,
  deletions, live updates, `etimer sync`).
- Interface rebuilt: liquid progress rings, odometer digits, HH:MM:SS picker, four themes, mood-reactive
  background, calm mode, inline rename, Wayland-compatible window dragging.
- **Finish alarm sound** (alarm or chime, custom file support, mute button) that also rings from the background check.
- **Per-timer history** with pause durations and gaps caused by power cuts, shutdowns, closed apps, crashes and sleep.
- **Android:** a live notification per timer with a **Pause / Resume** button; a scroll-wheel time picker; a
  Settings screen (alarm sound, ring duration, vibration, theme, calm mode, battery settings); an "Alarm
  ringing — tap to stop" banner.
- **Fixed:** an Android alarm could keep ringing after its notification was swiped away. It now stops when the
  notification is dismissed, from the in-app banner, or at the time limit (enforced two independent ways).
- **Fixed:** after a failed push (for example a rate-limit rejection) sync no longer stalls until reconnect; it
  retries with growing delays. Reconnecting devices answer each other's snapshot requests at most every 30 s.
- A dramatic (and skippable) warning before starting a stopwatch, which has no finish line.
- Unit tests, a Python⇄Kotlin interoperability test and CI.

## 2.0.0 — 2026-10-22
- Qt redesign: animated progress rings, sliding segmented control, glass-panel styling, tray monitor.
- Flatpak packaging.

## 1.0.0
- Persistent timers and stopwatches (absolute timestamps), `etimer` CLI, systemd background check.
