# Flatpak

> **Status:** a manifest exists in [`flatpak/`](../flatpak/) but has not been verified end-to-end in a clean
> build environment, and the app is **not on Flathub**. Treat these instructions as a starting point and please
> report problems.

## Build and run locally
```bash
sudo pacman -S --needed flatpak flatpak-builder          # or your distribution's equivalent
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install flathub org.kde.Platform//6.8 org.kde.Sdk//6.8   # check the current runtime branch first

cd flatpak
pip install --user flatpak-pip-generator
flatpak-pip-generator --runtime="org.kde.Sdk//6.8" \
  --prefer-wheels=PySide6,PySide6-Essentials,PySide6-Addons,shiboken6,cryptography,cffi \
  --output pyside6-sources PySide6 cryptography
flatpak-builder --user --install --force-clean build-dir com.eternaltimer.EternalTimer.yml
flatpak run com.eternaltimer.EternalTimer
```
Building PySide6 from source inside `flatpak-builder` is painful, so the manifest installs prebuilt wheels
(that is what `--prefer-wheels` is for). If the generator still tries to build something from source, pin an
older PySide6 (for example `PySide6==6.7.*`) and regenerate.

## Permissions (and why)
| permission | reason |
|---|---|
| `--socket=wayland`, `--socket=fallback-x11`, `--device=dri` | draw the window |
| `--share=ipc` | Qt shared memory |
| `--share=network` | optional phone sync talks to the relay |
| `--socket=pulseaudio` | play the finish alarm (also covers PipeWire) |
| `--talk-name=org.freedesktop.Notifications` | desktop notifications |

State lives in the app's own sandbox (`~/.var/app/com.eternaltimer.EternalTimer/data/eternal-timer/`); no
filesystem permission is needed.

## Differences inside the sandbox
- The systemd/autostart background check from the native install is not set up, so a finished timer rings only
  while the app is running (it still catches up correctly the moment you open it). A background-portal based
  solution is a good contribution.
- Notifications use Qt's tray API instead of `notify-send`.

## Before submitting to Flathub
This checklist is from memory of the Flathub requirements; **verify it against
[docs.flathub.org](https://docs.flathub.org/docs/for-app-authors/requirements) first.**

- [ ] **App ID.** `com.eternaltimer.*` implies a domain that must be owned. Without one, use a code-hosting ID
      such as `io.github.<user>.EternalTimer` (rename the manifest, desktop file, metainfo and icons to match).
- [ ] Metainfo passes `appstreamcli validate` and `flatpak-builder-lint`; add `<url>` entries
      (homepage, bugtracker), **screenshots hosted at stable URLs**, and a correct `<content_rating>`.
- [ ] Builds work **offline** from pinned sources (the generated pip sources must be committed to the app's own
      Flathub repository).
- [ ] The license (MIT) is declared in the metainfo and shipped (`LICENSE`).
- [ ] Be ready to justify `--share=network` (optional sync) and `--socket=pulseaudio` (alarm).
- [ ] A tagged release with a release entry in the metainfo.
