# Architecture

## The core idea: store timestamps, not countdowns

A timer is saved as:

| field | meaning |
|---|---|
| `start_ts` | wall-clock time the current running stretch began (`null` while paused) |
| `accumulated` | seconds already counted before that stretch (pauses add to it) |
| `target_seconds` | length of a timer (`null` for a stopwatch) |
| `running` | whether it is counting |

Elapsed time is always `accumulated + (now − start_ts)` and remaining is `target − elapsed`. Nothing ticks in
the background, so it cannot be wrong after a shutdown: reopening the app just evaluates the formula against
the clock. Every mutation writes the file immediately (write to a temp file, then rename), so there is no
"save on exit" step.

## Files (desktop, `~/.local/share/eternal-timer/`)

| file | contents |
|---|---|
| `state.json` | `items` (timers/stopwatches with their history) and `tombstones` (deleted ids, for sync) |
| `settings.json` | sound, theme, calm mode, device id, sync pairing code (mode 0600) |
| `heartbeat.json` | written every 5 s by the GUI; last-seen time, clean-exit flag, boot id |
| `sync_state.json` | id of the last relay message processed |
| `sounds/`, `sound.log`, `app.log` | generated alarm/chime WAV files and diagnostic logs |

## Who checks for finished timers

- The GUI checks every 400 ms.
- `etimer check` (run by a systemd user timer every minute and at login) does the same while the GUI is closed,
  and also performs a sync round if sync is set up.
- The Android app schedules an exact `AlarmManager` alarm per running timer and re-arms them after every change,
  reboot and clock change.

A timer is reported finished exactly once **per device**: the `notified` flag is local and never synced.

## History and power-cut detection

Each item keeps an append-only list of events: `started`, `paused`, `resumed`, `restarted`, `finished` and
`downtime`. Pause durations are derived from paused→resumed pairs. A `finished` event stores the moment the
timer *actually* reached zero plus when it was noticed, so a timer that ended during a power cut shows both.

`downtime` events say *the app or PC was not running while this item was counting*. They are found like this:

1. The GUI writes `heartbeat.json` every 5 s, and `clean_exit: true` when it quits (including on SIGTERM/SIGINT).
2. On the next start, if the gap is longer than a few seconds, the boot id (`/proc/sys/kernel/random/boot_id`)
   and boot time (`/proc/uptime`) tell the cases apart:

| previous exit | boot id | recorded as |
|---|---|---|
| clean | same | **App closed** |
| not clean | same | **App stopped unexpectedly** (crash or kill) |
| clean | changed | **System shut down** (plus *App not open* from boot until now) |
| not clean | changed | **Power cut / unclean shutdown** (plus *App not open* from boot until now) |

3. While running, a 5 s tick that arrives more than 20 s late means the PC slept → **PC asleep**.

Only intervals when the item was actually running are logged. Limits: a power cut is *inferred* (no clean exit
plus a new boot), accurate to about 5 s, and after a normal quit followed by a shutdown it cannot say how long the
app had been closed before the PC went off.

## Sync layers

```
state.json  ◄── eternal_timer_core.py     timers, history, stamping (rev / state_at / label_at)
                eternal_timer_sync.py     pairing + crypto, merge rules, ntfy client, live subscriber
                eternal_timer_gui.py      SyncManager (background thread) + dialog + status button
android/engine/  Json, Crypto, Model (Store), SyncLogic, Relay   — the same logic in pure Kotlin
android/app/     alarms, services, views (Android only)
```

Details of the merge rules and wire format are in [SYNC.md](SYNC.md). The Python and Kotlin implementations
must behave identically; `tests/vectors.json` and `android/tests/interop.sh` enforce that.

## Why the Android engine has no Android imports

`android/engine/` uses only the JDK, so it compiles into the app **and** runs on a plain JVM. That is what lets CI
prove that a Kotlin "phone" and the Python "PC" interoperate without needing an emulator.

## Performance notes

The UI is custom-painted and animates at up to 30 fps. The costly parts (dial ticks, card backgrounds) are cached
as pixmaps and rebuilt only when they change; rings repaint at full rate only while something visibly moves; the
animation drops to 10 fps when the window isn't focused and stops while it is hidden in the tray. *Calm mode*
removes the continuous effects.
