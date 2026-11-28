[app]

title = Twitch Auth Test
package.name = twitchauthtest
package.domain = org.gamooda
source.dir = .
source.include_exts = py,kv,png,jpg,atlas,json,txt,svg
source.exclude_dirs = .git,.buildozer,bin,__pycache__,screenshots,logs
version = 1.0.0
requirements = python3,kivy,requests,certifi
orientation = portrait
fullscreen = 1
android.archs = armeabi-v7a
android.api = 33
android.minapi = 21
android.ndk = 25b
android.ndk_api = 21
android.permissions = INTERNET,ACCESS_NETWORK_STATE,FOREGROUND_SERVICE,WAKE_LOCK,POST_NOTIFICATIONS
android.allow_backup = False
android.enable_androidx = True
android.accept_sdk_license = True
android.wakelock = True
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png
services = authworker:service.py:foreground:sticky
p4a.branch = master
p4a.bootstrap = sdl2

[buildozer]

log_level = 2
warn_on_root = 0
