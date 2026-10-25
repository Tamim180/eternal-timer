# Releasing the Android app (APK and F-Droid)

> The F-Droid details below are from memory of its documentation; check
> [f-droid.org/docs](https://f-droid.org/docs/) before submitting. The F-Droid maintainers also review every
> submission and will point out anything that needs changing.

## Where the APK goes, and what to call it

**Do not commit the APK into the repository.** Binaries bloat git history forever, and `*.apk` is already in
`.gitignore`. Attach it to a **GitHub Release** instead:

1. Create a tag for the version, e.g. `v3.0.0` (Releases → *Draft a new release* → *Choose a tag*).
2. Upload the signed APK named **`eternal-timer-<versionName>.apk`**, e.g. `eternal-timer-3.0.0.apk`.
3. Also upload a checksum, `eternal-timer-3.0.0.apk.sha256`:
   `sha256sum eternal-timer-3.0.0.apk > eternal-timer-3.0.0.apk.sha256`.
4. Paste the matching section of `CHANGELOG.md` as the release notes.

The README links to the Releases page, so users always find the latest file there.

## Signing

Android refuses to update an app unless every version is signed with the **same key**. Make the key once and
**back it up somewhere safe — if you lose it you can never ship an update to existing installs.** Never commit it.

```bash
keytool -genkeypair -v -keystore eternal-timer.jks -keyalg RSA -keysize 4096 -validity 10000 -alias eternal-timer
```

Create `android/keystore.properties` (git-ignored):
```
storeFile=/absolute/path/to/eternal-timer.jks
storePassword=...
keyAlias=eternal-timer
keyPassword=...
```
Build and check:
```bash
cd android
./gradlew assembleRelease        # run `gradle wrapper` once first if there is no ./gradlew
cp app/build/outputs/apk/release/app-release.apk ../eternal-timer-3.0.0.apk
apksigner verify --print-certs ../eternal-timer-3.0.0.apk
```
Without `keystore.properties` the release build comes out unsigned, which is what F-Droid wants.

## Decide the application id first

`applicationId` in `android/app/build.gradle.kts` is the app's permanent identity (currently
`com.eternaltimer.app`). **Changing it after release means existing installs can't update.** If you'd rather
have an id tied to something you own, e.g. `io.github.<your-username>.eternaltimer`, change *only* that one line
now; the code's package name can stay as it is.

## Each release checklist
- [ ] Bump `versionCode` (must always increase) and `versionName` in `android/app/build.gradle.kts`.
- [ ] Add `android/fastlane/metadata/android/en-US/changelogs/<versionCode>.txt` (≤ 500 characters).
- [ ] Update `CHANGELOG.md`; tag `v<versionName>`.
- [ ] Build, sign, verify, upload to the Release.

## F-Droid

F-Droid **builds the app from source itself** and signs it with its own key (unless you set up reproducible
builds). The consequence: an APK from GitHub and one from F-Droid carry different signatures, so a user can't
install one over the other — they have to pick a source. Say so in the README once both exist.

What F-Droid needs, and what is already done:
- ✔ Free license (MIT) and fully open source, no proprietary libraries, no trackers (the app has zero
  third-party dependencies).
- ✔ Store text and icon in `android/fastlane/metadata/android/en-US/` (title, short and full description,
  changelog, 512×512 icon). **Still to add:** phone screenshots in `images/phoneScreenshots/` — take them from
  a real device. If F-Droid doesn't pick up the metadata from this subfolder, the same texts can go in the
  metadata file below.
- A tagged release (`v3.0.0`).
- A metadata file added to the [fdroiddata](https://gitlab.com/fdroid/fdroiddata) repository through a merge
  request, roughly:

```yaml
Categories:
  - Time
License: MIT
SourceCode: https://github.com/<you>/eternal-timer
IssueTracker: https://github.com/<you>/eternal-timer/issues
Changelog: https://github.com/<you>/eternal-timer/blob/HEAD/CHANGELOG.md

AutoName: Eternal Timer
RepoType: git
Repo: https://github.com/<you>/eternal-timer.git

Builds:
  - versionName: 3.0.0
    versionCode: 300
    commit: v3.0.0
    subdir: android/app
    gradle:
      - yes

AutoUpdateMode: Version
UpdateCheckMode: Tags
CurrentVersion: 3.0.0
CurrentVersionCode: 300
```
(The file is named `<applicationId>.yml`. The `subdir` for a project nested in a monorepo may need adjusting
during review.)

Reviewers may ask about the sync relay: the app can talk to a public ntfy server, which is open-source software
but a service run by someone else. They sometimes label such apps with an "anti-feature" note; being upfront in
the description (as the store text already is) helps.
