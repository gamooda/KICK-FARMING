# Twitch Farming Android

Android client for the existing Twitch Farming engine.

## Target device

- ABI: armeabi-v7a
- Minimum Android API: 21 (Android 5.0+); tested target device: Android 10 (API 29)
- Target Android API: 33
- UI: Kivy with lazy rendering for active tabs, custom PNG icon set and animated Twitch logo
- Engine: the existing backend.py and engine/ copied from the desktop project during the build
- Authentication: first-run Twitch device authorization, confirmation code and account sign-out
- Background operation: a python-for-android foreground service with an ongoing notification using the generated app logo

## Build

Run on Linux (or use the GitHub Actions workflow). Buildozer requires a project path without spaces, so prepare the shared engine in the repository and then copy the Android project to a simple temporary path:

    cd "Twitch Farming/android"
    bash prepare.sh
    python generate_assets.py
    python -m pip install --upgrade buildozer cython==0.29.36
    mkdir -p /tmp/twitch-farming-android
    cp -a . /tmp/twitch-farming-android/
    cd /tmp/twitch-farming-android
    buildozer android debug

The APK is written to /tmp/twitch-farming-android/bin/. The asset generator creates the app icon, animated-logo layers and UI icons before the build. The build intentionally targets only armeabi-v7a because the official Redmi 7A Android 10 firmware is 32-bit. The chipset's 64-bit CPU capability does not make a 64-bit APK compatible with a 32-bit Android userspace.

## Background behavior

The mobile interface redraws only the active tab and only when its data changes. Followers and Drops state are requested only while their tabs are open, reducing rendering and data-transfer work on low-end ARMv7 devices.

The first run displays the Twitch sign-in dialog. The app shows and copies the Twitch device code and opens the official activation page. The header and Settings provide access to sign-in and sign-out.

The farm runs in a separate Android foreground service. The ongoing notification reports whether the service is ready or farming is active. The service holds a partial wake lock only while the farm is running. Android notification and battery controls remain available to the user.

MIUI may still restrict background processes. On Redmi 7A, enable Autostart for Twitch Farming and set Battery saver to No restrictions. Do not force-stop the app while farming.

## Important limits

- The Windows auto-updater is intentionally not exposed in Android; it handles Windows EXE/ZIP replacement. Android builds must be installed as APK updates.
- The mobile UI is a responsive native-style port, not a byte-for-byte rendering of the desktop window. The Twitch farming backend and miner engine are shared with the desktop app.

## Release downloads

The `Twitch-Farming-Android-armeabi-v7a.apk` and phone UI screenshots are attached to the same GitHub Release as the matching Windows `Twitch.Farming.zip`. The Android workflow waits for the successful Windows build for the exact same source commit before uploading the Android files.
