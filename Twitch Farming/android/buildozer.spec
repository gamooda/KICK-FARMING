[app]

title = Twitch Farming
package.name = twitchfarming
package.domain = org.gamooda
source.dir = .
source.include_exts = py,kv,png,jpg,atlas,json,txt,svg,ico
source.exclude_dirs = .git,.buildozer,bin,__pycache__,screenshots,diagnostics,logs
version = 1.0.0
requirements = python3,kivy,requests,websocket-client,python-dateutil,emoji,millify,colorama,irc,pytz,validators,pillow
orientation = portrait
fullscreen = 0
android.archs = armeabi-v7a
android.api = 33
android.minapi = 21
android.ndk = 25b
android.ndk_api = 21
android.permissions = INTERNET,FOREGROUND_SERVICE,WAKE_LOCK,POST_NOTIFICATIONS
android.add_resources = notification.png:drawable
android.allow_backup = True
android.enable_androidx = True
android.accept_sdk_license = True
android.wakelock = False
android.presplash_color = #101019
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png
services = farm:service.py:foreground:sticky
p4a.branch = master
p4a.bootstrap = sdl2

[buildozer]
log_level = 2
warn_on_root = 0
