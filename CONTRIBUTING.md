# Contributing

Thanks for helping! A few things that keep the project healthy.

## Running the tests
```bash
pip install cryptography          # PySide6 is not needed for the tests
python -m unittest discover -s tests -v
```
The Python tests cover the timer math, history, power-cut classification, crypto vectors and the sync merge
rules. They never touch your real data (they use a temporary `$HOME`) and need no network.

## The Python and Kotlin sides must stay in lock-step
The merge rules and wire format exist twice: `eternal_timer_sync.py` and `android/engine/SyncLogic.kt` /
`Crypto.kt`. If you change one, change the other, update [docs/SYNC.md](docs/SYNC.md) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and run the interop test, which pairs a Kotlin "phone" with the
Python "PC" through a local ntfy-compatible relay:
```bash
KOTLINC=/path/to/kotlinc ./android/tests/interop.sh
```
`tests/vectors.json` holds fixed crypto vectors that both implementations must reproduce byte for byte.

## Style
- Python: standard library first, no new dependencies without discussion (sync needs only `cryptography`).
- Android: no third-party dependencies on purpose (platform SDK only).
- Keep UI work cheap to paint: the app animates at up to 30 fps, so cache what doesn't change.

## Good first areas
Flathub packaging, other desktop platforms, translations, accessibility, more relay options.
