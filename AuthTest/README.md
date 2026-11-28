# Twitch Auth Test

Independent Android utility for diagnosing Twitch Device Authorization on the phone. It is not connected to the Twitch Farming application and does not import or modify its backend.

## Build

GitHub Actions workflow: Build Twitch Auth Test APK.

It can be started manually from Actions or automatically by changes under AuthTest/ and .github/workflows/build-authtest-android.yml.

The workflow builds an ARMv7 APK for Android 5.0+ devices and uploads it as the Twitch-Auth-Test-Android-armeabi-v7a artifact.

## Test sequence

1. Request a device code from id.twitch.tv/oauth2/device.
2. Poll id.twitch.tv/oauth2/token in the separate foreground Android service, including while the UI is paused.
3. Validate the token with id.twitch.tv/oauth2/validate.
4. Test writing and reading the session with Android Keystore AES-GCM. If Keystore fails, test a JSON fallback in the application's private storage.

The lower part of the screen displays the live service log. The app records timestamps, HTTP status codes, response fields, network availability, UI foreground state, and full Python tracebacks for failed steps.

## Token handling

OAuth access tokens, refresh tokens, and device codes are never written in full to the visible log or copied log. The log shows a masked value and its length. The test service uses the actual values in memory and stores the successful round-trip test payload in the app-private directory. The primary file is encrypted using Android Keystore; the fallback JSON is only written when Keystore fails.

Uninstalling the test app removes its private test data.
