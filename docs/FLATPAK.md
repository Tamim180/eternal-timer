# Flatpak

> **Status:** builds locally and runs correctly; **not yet submitted to Flathub.** Treat the submission
> checklist below as a starting point and please report problems.

## Build and run locally
```bash
sudo pacman -S --needed flatpak flatpak-builder python-pipx     # or your distribution's equivalent
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.kde.Platform//6.11 org.kde.Sdk//6.11   # check the current runtime branch first
flatpak install --user flathub io.qt.PySide.BaseApp//6.11                 # must match the runtime branch above

pipx install flatpak-pip-generator   # installs as `flatpak_pip_generator` (underscore) -- Arch's externally
                                      # managed Python blocks a plain `pip install`, pipx sidesteps that cleanly

cd flatpak
flatpak_pip_generator --runtime="org.kde.Sdk//6.11" \
  --prefer-wheels=cryptography,cffi \
  --output cryptography-sources cryptography
flatpak-builder --user --install --force-clean build-dir com.eternaltimer.EternalTimer.yml
flatpak run com.eternaltimer.EternalTimer
```

PySide6 itself comes from the official **`io.qt.PySide.BaseApp`** instead of pip wheels -- this is what KDE's
own docs recommend, and the generator tool will tell you the same thing if you ever try pointing it at PySide6
directly. The `flatpak_pip_generator` step above is only for `cryptography` (needed for phone sync), which has
nothing to do with Qt and resolves as a normal prebuilt wheel. If it ever fails to find a wheel for some reason,
pin an older point release (for example `cryptography==42.*`) and regenerate.

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
- [x] **App ID.** `com.eternaltimer.EternalTimer` implies a domain that isn't owned, and a code-hosting ID
      (`io.github.Tamim180.EternalTimer`) would verify automatically instead -- but renaming now means touching
      the manifest, desktop file, metainfo and icons again, and the GitHub repo is `eternal-timer`
      (lowercase/hyphenated) rather than `EternalTimer` anyway, so the computed repo URL wouldn't match
      perfectly either way. Decision: submit as-is and let a reviewer manually verify ownership -- Flathub's
      own docs explicitly support this and invite asking reviewers for help picking an ID. Revisit only if a
      reviewer actually asks for a change.
- [x] Metainfo has `<developer>`, `<url type="homepage">` (required to pass validation) plus bugtracker/
      vcs-browser, `<categories>`, and six `<screenshots>` pointing at tag-pinned
      `raw.githubusercontent.com` URLs (checked with `curl` that all six return HTTP 200 before committing).
      `<content_rating type="oars-1.1" />` is the bare form, which Flathub's own example files use. **Still to
      do:** run the real `appstreamcli validate` and `flatpak-builder-lint` tools before submitting -- neither
      has actually been run yet, this was checked against the written requirements only.
- [ ] Builds work **offline** from pinned sources (the generated `cryptography-sources.json` must be committed
      to the app's own Flathub repository once one exists -- it isn't committed here since its exact contents
      depend on the runtime version/date you build against).
- [x] The license (MIT) is declared in the metainfo and shipped (`LICENSE`).
- [ ] Be ready to justify `--share=network` (optional sync) and `--socket=pulseaudio` (alarm) if a reviewer asks.
- [x] A tagged release (`v3.0.0`) with a matching release entry in the metainfo.
