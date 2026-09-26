# Android export with Godot 4.7.2-stable (verified 2026-09-26)

Primary source: https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_android.html
(the "stable" docs were 4.7 when read). Gradle-build details:
https://docs.godotengine.org/en/stable/tutorials/export/android_gradle_build.html

## Requirements (official)

- **OpenJDK 17** ("higher versions are also supported, but we recommend JDK 17").
- Android SDK installed via Android Studio *or* `sdkmanager`:
  ```
  sdkmanager --sdk_root=<android_sdk_path> "platform-tools" "build-tools;35.0.1" \
    "platforms;android-35" "cmdline-tools;latest" "cmake;3.10.2.4988404" "ndk;28.1.13356709"
  ```
  (Platform-Tools ≥ 35.0.0, Build-Tools 35.0.1, Platform 35, NDK r28b 28.1.13356709, CMake 3.10.2.4988404.)
- Do **not** use a distro-packaged Android SDK on Linux (outdated).
- Editor settings: `export/android/java_sdk_path` (JDK dir) and `export/android/android_sdk_path`
  (dir containing `platform-tools/adb`). Headless: write them into
  `~/.config/godot/editor_settings-4.7.tres` (see `godotai/android.py`).
- Export templates for the exact version must be installed (`Godot_v4.7.2-stable_export_templates.tpz`).

## Two export paths

1. **Prebuilt template (default, `gradle_build/use_gradle_build=false`)** — Godot patches the
   prebuilt `android_debug.apk`/`android_release.apk` from the templates, aligns and signs it with
   `apksigner` from build-tools. Needs only JDK + `platform-tools` + `build-tools`. Fast (≈10 s)
   and what the CI/Docker pipeline uses. Verified in a sandbox: 27.6 MiB debug APK, arm64-v8a.
2. **Gradle build (`use_gradle_build=true`)** — needed for AAB (Play Store), Android plugins
   (.aar), custom min/target SDK, or Java interfaces implemented from GDScript. Requires the
   full SDK package list above and `--install-android-build-template` once per project.

## Signing

- Debug: standard debug keystore, alias `androiddebugkey`, password `android`:
  `keytool -keyalg RSA -genkeypair -alias androiddebugkey -keypass android -keystore debug.keystore -storepass android -dname "CN=Android Debug,O=Android,C=US" -validity 9999`
- Release (Play Store requires a non-debug key and, since Aug 2021, an **AAB**):
  `keytool -v -genkey -keystore mygame.keystore -alias mygame -keyalg RSA -validity 10000`
  Keystore password and key password currently have to be the same; use only letters/digits.
- **Never put credentials in `export_presets.cfg`.** Godot reads these env vars at export time
  and they override the preset (official table):
  `GODOT_ANDROID_KEYSTORE_DEBUG_PATH|USER|PASSWORD`, `GODOT_ANDROID_KEYSTORE_RELEASE_PATH|USER|PASSWORD`,
  `GODOT_SCRIPT_ENCRYPTION_KEY`.
- In GitHub Actions store the release keystore as a base64 secret and decode it to a file at
  build time (see `.github/workflows/build-android.yml`).

## Preset (export_presets.cfg) essentials

- `platform="Android"`, unique `name="Android"` (the CLI references the preset **by name**).
- `package/unique_name="com.studio.game"` (reverse-DNS, lowercase, no dashes),
  `package/name` = display name, `version/code` (int, bump every store upload), `version/name`.
- `architectures/arm64-v8a=true` (required by Play); `armeabi-v7a` optional; x86 off for release.
- Launcher icons: main 192×192, adaptive fg/bg 432×432, optional monochrome 432×432; empty
  values fall back to the project icon.
- `exclude_filter="tests/*"` keeps headless test scripts out of the APK.
- `gradle_build/export_format=0` → APK, `1` → AAB (Gradle only).

## Command line

```
godot --headless --path <project> --import                       # once, warms caches
godot --headless --path <project> --export-debug   Android build/android/game.apk
godot --headless --path <project> --export-release Android build/android/game.apk
```
`--headless` is required on machines without a display. The preset name must match
`export_presets.cfg`. Exit code 0 + non-empty file = success; also scan output for `ERROR`.

## Typical failure messages → fixes

- `Release Username and/or Password is invalid` → set `GODOT_ANDROID_KEYSTORE_RELEASE_*` env vars
  (or use `--export-debug`).
- `Missing 'platform-tools' directory!` / `Missing 'build-tools' directory!` → wrong
  `android_sdk_path` or packages not installed.
- `Invalid Java SDK path` → `java_sdk_path` must be the JDK root (contains `bin/java`).
- Texture compression error → set `rendering/textures/vram_compression/import_etc2_astc=true`.
- `Could not install to device` → same package installed with a different key; uninstall first.
- `cannot connect to daemon at tcp:5037` after export → harmless (adb shutdown when no adb server).
