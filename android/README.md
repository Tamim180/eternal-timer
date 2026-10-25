# Eternal Timer for Android

A native Android app (Kotlin, **no third-party dependencies** — just the platform SDK) that mirrors your desktop
timers. It keeps its own copy of every timer, so it keeps counting and **rings on its own** if your PC loses
power. Sync is optional, end-to-end encrypted, and shares the PC app's protocol
([docs/SYNC.md](../docs/SYNC.md)). Without sync it is a complete standalone timer app.

Requires Android 8.0 (API 26) or newer.

## Install
- **APK:** download `eternal-timer-<version>.apk` from the repository's Releases page and open it (Android asks to
  allow installs from your browser/files app).
- **Build it yourself** — below. An F-Droid listing is planned ([../docs/RELEASING.md](../docs/RELEASING.md)).

## Build
1. Install Android Studio (Koala or newer); let it fetch Android SDK 34 and JDK 17.
2. **File → Open** this `android/` folder and let Gradle sync (Gradle 8.7, Android Gradle Plugin 8.5.2).
3. Connect a phone (USB debugging, or Wireless debugging on Android 11+) or start an emulator, then **Run**.
   For an APK to copy over: **Build → Build APK(s)**.

## Using it
- **+** opens the add sheet: spin the **wheels** (drag, fling, or tap a faded number to step to it) or tap a
  preset, optionally name it, press *Start*. Stopwatches work the same way (with a dramatic warning that they run
  until stopped — it can be switched off).
- Every running timer gets its **own notification** with a live countdown (count-up for stopwatches) and a
  **Pause / Resume** button, even if the app is closed. Long-press a card to rename it; ☰ shows its history.
- **⚙ Settings:** alarm sound (your phone's alarm picker), how long it rings, vibration, test alarm, theme
  (Aurora / Ocean / Sunset / Mint), calm mode (less animation), battery settings, and the stopwatch warning.
- **☁** is sync: paste the pairing code from the PC → *Join*. Keep "Keep sync running in the background" on if you
  want PC-started timers to reach the phone within seconds (it shows one quiet notification). With it off the
  phone syncs when you open the app. **Alarms ring either way.**
- Allow notifications on first run (Android 13+); alarms and the per-timer notifications need them.

## How alarms stay reliable
- Every running timer gets an exact `AlarmManager.setAlarmClock` alarm, which survives Doze and the app being
  swiped away. Alarms are re-armed on every change, after a reboot, and after clock or time-zone changes.
- When one fires, a foreground service plays your chosen alarm sound at alarm volume, vibrates, and shows a
  full-screen dismiss screen over the lock screen. Timers that ended while the phone was off ring when it boots or
  when you open the app.
- **It always stops.** The alarm ends when you tap *Dismiss*, tap the in-app *Alarm ringing — tap to stop*
  banner, **swipe the notification away**, or when the time limit from Settings (default 90 s) is reached — and the
  limit is enforced two independent ways. If an alarm ever seems stuck, `adb logcat -s EternalTimer` shows exactly
  why it started and stopped; please include that in a bug report.
- Some manufacturers add their own battery restrictions on top of Android's. If an alarm ever fails to ring,
  exclude the app from battery optimization (Settings → Battery settings…; dontkillmyapp.com has per-brand
  steps), then test with **Settings → Test alarm** with the phone locked.

## Layout
- `engine/` — pure Kotlin, no Android imports: JSON, pairing/AES-GCM, store, merge rules, relay client. Compiled
  into the app *and* testable on a plain JVM.
- `app/` — Android layer: alarms, notifications, services, views, sheets, the wheel picker.
- `fastlane/` — store text and icon (used by F-Droid).

## Testing
```bash
KOTLINC=/path/to/kotlinc ./tests/interop.sh     # Kotlin "phone" <-> Python "PC" through a local relay
```
It checks that crypto output is byte-identical to Python, and that joining, pausing, deleting, concurrent edits,
finish events and live updates behave the same on both sides. There are no on-device UI tests.

## Status
Developed against a single physical device. The on-screen layout, the alarm sound/vibration, behavior over the lock
screen, and behavior under aggressive battery managers have **not** been verified broadly. Please report
device-specific issues with your phone model and Android version.
