import json
import os
import socketserver
import threading
import time
import traceback
from pathlib import Path

HOST = "127.0.0.1"
PORT = 43771
APP = None
APP_LOCK = threading.RLock()
ALLOWED_METHODS = {
    "login", "start_farm", "stop_farm", "save_settings", "save_channel_editor",
    "remove_channel", "reorder_channels", "refresh_channel_profiles",
    "refresh_followed_channels", "add_followed_channel", "refresh_drops_inventory",
    "set_drops_campaign_focus", "clear_drops_campaign_focus",
    "add_drop_channel_to_farm", "apply_bet_settings", "save_preset",
    "delete_preset", "finish_tutorial", "clear_logs",
}


def initialize_storage():
    try:
        from jnius import autoclass
        service = autoclass("org.kivy.android.PythonService").mService
        if service:
            base = str(service.getApplicationContext().getFilesDir().getAbsolutePath())
            root = Path(base) / "twitch_farming"
            root.mkdir(parents=True, exist_ok=True)
            os.environ["APPDATA"] = str(root)
            return
    except Exception:
        pass
    root = Path.home() / ".twitch_farming_android"
    root.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(root)


def service_api():
    global APP
    with APP_LOCK:
        if APP is not None:
            return APP
        initialize_storage()
        from backend import TwitchFarmingApi
        APP = TwitchFarmingApi()
        threading.Thread(target=APP._startup_tasks, name="AndroidStartup", daemon=True).start()

        def restore():
            while not APP.get_startup_status().get("ready"):
                time.sleep(0.25)
            APP.begin_session_restore()

        threading.Thread(target=restore, name="AndroidSessionRestore", daemon=True).start()
        return APP


def build_snapshot(api, after_log_id):
    return {
        "ok": True,
        "state": api.get_state(after_log_id),
        "auth": api.get_auth_state(),
        "followers": api.get_followed_channels_state(),
        "drops": api.get_drops_inventory_state(),
        "startup": api.get_startup_status(),
        "update": api.get_update_state(),
    }


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(20)
        line = self.rfile.readline(1024 * 1024)
        if not line:
            return
        try:
            payload = json.loads(line.decode("utf-8"))
            method = str(payload.get("method") or "")
            args = payload.get("args") if isinstance(payload.get("args"), list) else []
            kwargs = payload.get("kwargs") if isinstance(payload.get("kwargs"), dict) else {}
            api = service_api()
            if method == "snapshot":
                result = build_snapshot(api, kwargs.get("after_log_id", 0))
            elif method in ALLOWED_METHODS:
                result = getattr(api, method)(*args, **kwargs)
                if result is None:
                    result = {"ok": True}
            else:
                result = {"ok": False, "message": "Недоступная операция: " + method}
            response = {"ok": True, "result": result}
        except Exception as error:
            response = {"ok": False, "error": str(error) or error.__class__.__name__, "traceback": traceback.format_exc()[-4000:]}
        try:
            self.wfile.write(json.dumps(response, ensure_ascii=False, default=str).encode("utf-8") + b"\n")
            self.wfile.flush()
        except Exception:
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def set_android_notification():
    try:
        from jnius import autoclass
        service = autoclass("org.kivy.android.PythonService").mService
        if service is None:
            return
        Context = autoclass("android.content.Context")
        BuildVersion = autoclass("android.os.Build$VERSION")
        NotificationBuilder = autoclass("android.app.Notification$Builder")
        NotificationChannel = autoclass("android.app.NotificationChannel")
        manager = service.getSystemService(Context.NOTIFICATION_SERVICE)
        channel_id = "twitch_farming_service"
        if BuildVersion.SDK_INT >= 26:
            channel = NotificationChannel(channel_id, "Twitch Farming", 2)
            channel.setDescription("Состояние фонового фарма Twitch")
            manager.createNotificationChannel(channel)
            builder = NotificationBuilder(service, channel_id)
        else:
            builder = NotificationBuilder(service)
        state = APP.get_state() if APP is not None else {}
        running = bool(state.get("session_running"))
        status = str(state.get("status_text") or "")
        title = "Twitch Farming · " + ("ФАРМ РАБОТАЕТ" if running else "Сервис активен")
        if running:
            channels = state.get("online_channels") or []
            body = "Сбор запущен · " + str(len(channels)) + " онлайн-каналов · " + status.replace("●", "").strip()
        else:
            body = "Фоновый сервис готов · фарм остановлен"
        package = service.getPackageName()
        icon_id = service.getResources().getIdentifier("ic_launcher", "mipmap", package)
        if icon_id == 0:
            icon_id = service.getResources().getIdentifier("icon", "drawable", package)
        if icon_id == 0:
            return
        Intent = autoclass("android.content.Intent")
        PendingIntent = autoclass("android.app.PendingIntent")
        launch = service.getPackageManager().getLaunchIntentForPackage(package)
        pending = PendingIntent.getActivity(service, 0, launch, 0x08000000)
        notification = (
            builder.setSmallIcon(icon_id)
            .setContentTitle(title)
            .setContentText(body)
            .setContentIntent(pending)
            .setOngoing(True)
            .setOnlyAlertOnce(True)
            .setShowWhen(False)
            .build()
        )
        service.startForeground(1, notification)
        manager.notify(1, notification)
    except Exception:
        pass


def manage_wakelock():
    lock = None
    try:
        from jnius import autoclass
        Context = autoclass("android.content.Context")
        service = autoclass("org.kivy.android.PythonService").mService
        if service is not None:
            power = service.getSystemService(Context.POWER_SERVICE)
            manager = autoclass("android.os.PowerManager")
            lock = power.newWakeLock(manager.PARTIAL_WAKE_LOCK, "TwitchFarming:Farm")
    except Exception:
        lock = None
    while True:
        try:
            api = service_api()
            running = bool(api.get_state().get("session_running"))
            if lock is not None:
                if running and not lock.isHeld():
                    lock.acquire()
                elif not running and lock.isHeld():
                    lock.release()
            set_android_notification()
        except Exception:
            pass
        time.sleep(8)


def main():
    service_api()
    threading.Thread(target=manage_wakelock, name="AndroidNotification", daemon=True).start()
    while True:
        try:
            with Server((HOST, PORT), RequestHandler) as server:
                server.serve_forever(poll_interval=0.5)
        except OSError:
            time.sleep(2)


if __name__ == "__main__":
    main()
