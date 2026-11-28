import json
import os
import socketserver
import threading
import time
import traceback
from pathlib import Path

from android_diagnostics import get_log_path, initialize_android_logging, log_event, log_exception

initialize_android_logging("service-module-import")

HOST = "127.0.0.1"
PORT = 43771
APP = None
APP_LOCK = threading.RLock()
ALLOWED_METHODS = {
    "login", "logout", "start_farm", "stop_farm", "save_settings", "save_channel_editor",
    "remove_channel", "reorder_channels", "refresh_channel_profiles",
    "refresh_followed_channels", "add_followed_channel", "refresh_drops_inventory",
    "set_drops_campaign_focus", "clear_drops_campaign_focus",
    "add_drop_channel_to_farm", "apply_bet_settings", "save_preset",
    "delete_preset", "finish_tutorial", "clear_logs",
}


def initialize_storage():
    log_event("SERVICE_STORAGE_BEGIN", "resolving app storage directory")
    try:
        from jnius import autoclass
        service = autoclass("org.kivy.android.PythonService").mService
        if service:
            base = str(service.getApplicationContext().getFilesDir().getAbsolutePath())
            root = Path(base) / "twitch_farming"
            root.mkdir(parents=True, exist_ok=True)
            os.environ["APPDATA"] = str(root)
            log_event("SERVICE_STORAGE_READY", "APPDATA=" + str(root) + " log=" + get_log_path())
            return
        log_event("SERVICE_STORAGE_CONTEXT_EMPTY", "PythonService.mService is None")
    except BaseException as error:
        log_exception("SERVICE_STORAGE_JNI_FAILED", error)
    root = Path.home() / ".twitch_farming_android"
    root.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(root)
    log_event("SERVICE_STORAGE_FALLBACK", "APPDATA=" + str(root) + " log=" + get_log_path())


def service_api():
    global APP
    with APP_LOCK:
        if APP is not None:
            return APP
        log_event("SERVICE_API_INIT_BEGIN", "creating shared TwitchFarmingApi")
        try:
            initialize_storage()
            import backend
            backend.webbrowser.open = lambda *_args, **_kwargs: False
            log_event("SERVICE_BACKEND_IMPORT_OK", "backend module imported")
            from backend import TwitchFarmingApi
            APP = TwitchFarmingApi()
            log_event("SERVICE_API_CREATED", "TwitchFarmingApi instance ready")
            threading.Thread(target=APP._startup_tasks, name="AndroidStartup", daemon=True).start()
            log_event("SERVICE_STARTUP_THREAD_STARTED", "AndroidStartup")

            def restore():
                try:
                    log_event("SERVICE_RESTORE_WAIT", "waiting for backend startup")
                    while not APP.get_startup_status().get("ready"):
                        time.sleep(0.25)
                    log_event("SERVICE_RESTORE_BEGIN", "backend startup ready")
                    APP.begin_session_restore()
                    log_event("SERVICE_RESTORE_REQUESTED", "session restore requested")
                except BaseException as error:
                    log_exception("SERVICE_RESTORE_THREAD_FAILED", error)

            threading.Thread(target=restore, name="AndroidSessionRestore", daemon=True).start()
            return APP
        except BaseException as error:
            log_exception("SERVICE_API_INIT_FAILED", error)
            raise


def build_snapshot(api, after_log_id, active_screen="channels"):
    result = {
        "ok": True,
        "state": api.get_state(after_log_id),
        "auth": api.get_auth_state(),
        "startup": api.get_startup_status(),
        "update": api.get_update_state(),
    }
    with api.lock:
        result["auth_flow"] = {
            "code": str(api.auth_code or ""),
            "url": str(api.auth_url or "https://www.twitch.tv/activate"),
            "status": str(api.auth_status or ""),
        }
    if active_screen == "followers":
        result["followers"] = api.get_followed_channels_state()
    elif active_screen == "drops":
        result["drops"] = api.get_drops_inventory_state()
    return result


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(20)
        line = self.rfile.readline(1024 * 1024)
        if not line:
            return
        try:
            payload = json.loads(line.decode("utf-8"))
            method = str(payload.get("method") or "")
            log_event("RPC_REQUEST", "method=" + method)
            args = payload.get("args") if isinstance(payload.get("args"), list) else []
            kwargs = payload.get("kwargs") if isinstance(payload.get("kwargs"), dict) else {}
            api = service_api()
            if method == "snapshot":
                result = build_snapshot(api, kwargs.get("after_log_id", 0), str(kwargs.get("screen") or "channels"))
            elif method in ALLOWED_METHODS:
                result = getattr(api, method)(*args, **kwargs)
                if result is None:
                    result = {"ok": True}
            else:
                result = {"ok": False, "message": "Недоступная операция: " + method}
            response = {"ok": True, "result": result}
        except BaseException as error:
            log_exception("RPC_REQUEST_FAILED", error)
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
        resources = service.getResources()
        icon_id = 0
        for resource_type, resource_name in (
            ("mipmap", "ic_launcher"),
            ("drawable", "ic_launcher"),
            ("mipmap", "icon"),
            ("drawable", "icon"),
        ):
            icon_id = resources.getIdentifier(resource_name, resource_type, package)
            if icon_id:
                break
        if icon_id == 0:
            try:
                icon_id = service.getPackageManager().getApplicationInfo(package, 0).icon
            except Exception:
                icon_id = 0
        if icon_id == 0:
            return
        Intent = autoclass("android.content.Intent")
        PendingIntent = autoclass("android.app.PendingIntent")
        launch = service.getPackageManager().getLaunchIntentForPackage(package)
        pending = PendingIntent.getActivity(service, 0, launch, 0x0C000000)
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
    except BaseException as error:
        log_exception("ANDROID_NOTIFICATION_FAILED", error)


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
        except BaseException as error:
            log_exception("ANDROID_WAKELOCK_LOOP_FAILED", error)
        time.sleep(8)


def main():
    initialize_android_logging("service-main")
    log_event("SERVICE_MAIN_BEGIN", "foreground service process entered")
    try:
        service = None
        try:
            from jnius import autoclass
            service = autoclass("org.kivy.android.PythonService").mService
            if service is not None:
                service.setAutoRestartService(True)
                log_event("SERVICE_AUTORESTART_ENABLED", "setAutoRestartService(True)")
            else:
                log_event("SERVICE_CONTEXT_EMPTY", "PythonService.mService is None")
        except BaseException as error:
            log_exception("SERVICE_JAVA_INIT_FAILED", error)
        service_api()
        log_event("SERVICE_API_READY", "API ready; starting wakelock thread")
        threading.Thread(target=manage_wakelock, name="AndroidNotification", daemon=True).start()
        log_event("SERVICE_WATCHDOG_STARTED", "notification and wakelock loop started")
        while True:
            try:
                log_event("RPC_SERVER_BIND", HOST + ":" + str(PORT))
                with Server((HOST, PORT), RequestHandler) as server:
                    log_event("RPC_SERVER_READY", HOST + ":" + str(PORT))
                    server.serve_forever(poll_interval=0.5)
            except BaseException as error:
                log_exception("RPC_SERVER_RESTART", error)
                time.sleep(2)
    except BaseException as error:
        log_exception("SERVICE_MAIN_CRASH", error)
        raise



if __name__ == "__main__":
    main()
