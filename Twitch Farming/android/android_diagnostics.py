import atexit
import faulthandler
import logging
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path

_PACKAGE = "org.gamooda.twitchfarming"
_LOCK = threading.RLock()
_HANDLERS = {}
_LOG_DIRS = []
_FAULT_HANDLE = None
_INSTALLED = False
_LOGGER = logging.getLogger("twitch_farming.android")
_LOGGER.setLevel(logging.DEBUG)
_LOGGER.propagate = True


def _redact(value):
    text = str(value or "")
    patterns = (
        (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(oauth:)[a-z0-9]+", r"\1[REDACTED]"),
        (r"(?i)(device-code=)[^&\s]+", r"\1[REDACTED]"),
        (r"(?i)(access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password|cookie)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]"),
        (r"(?i)(код входа готов\s*[·:]\s*)[a-z0-9-]+", r"\1[REDACTED]"),
    )
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


class _RedactingFilter(logging.Filter):
    def filter(self, record):
        try:
            record.msg = _redact(record.getMessage())
            record.args = ()
        except Exception:
            record.msg = "Log message formatting failed"
            record.args = ()
        return True


def _candidate_directories():
    candidates = []
    try:
        from jnius import autoclass
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        service = autoclass("org.kivy.android.PythonService").mService
        context = activity if activity is not None else service
        if context is not None:
            external = context.getExternalFilesDir("logs")
            if external is not None:
                candidates.append(Path(str(external.getAbsolutePath())))
            internal = context.getFilesDir()
            if internal is not None:
                candidates.append(Path(str(internal.getAbsolutePath())) / "logs")
    except Exception:
        pass
    try:
        from android.storage import app_storage_path
        candidates.append(Path(app_storage_path()) / "logs")
    except Exception:
        pass
    custom = os.environ.get("TWITCH_FARMING_LOG_DIR", "").strip()
    if custom:
        candidates.append(Path(custom))
    if not candidates:
        candidates.append(Path.home() / ".twitch_farming_android" / "logs")
    unique = []
    seen = set()
    for candidate in candidates:
        try:
            value = str(candidate.expanduser().resolve())
        except Exception:
            value = str(candidate)
        if value not in seen:
            seen.add(value)
            unique.append(Path(value))
    return unique


def initialize_android_logging(component="app"):
    global _FAULT_HANDLE, _INSTALLED
    with _LOCK:
        candidates = _candidate_directories()
        for index, directory in enumerate(candidates):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                log_file = directory / "twitch-farming-android.log"
                if log_file.exists() and log_file.stat().st_size > 4 * 1024 * 1024:
                    backup = directory / "twitch-farming-android.log.1"
                    try:
                        backup.unlink(missing_ok=True)
                    except Exception:
                        pass
                    log_file.replace(backup)
                key = str(log_file)
                if key not in _HANDLERS:
                    handler = logging.FileHandler(log_file, mode="a", encoding="utf-8", delay=False)
                    handler.setLevel(logging.DEBUG)
                    handler.addFilter(_RedactingFilter())
                    handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)s] [%(process)d:%(threadName)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
                    logging.getLogger().addHandler(handler)
                    _HANDLERS[key] = handler
                if directory in _LOG_DIRS:
                    _LOG_DIRS.remove(directory)
                if index == 0:
                    _LOG_DIRS.insert(0, directory)
                else:
                    _LOG_DIRS.append(directory)
                if _FAULT_HANDLE is None or str(directory) == str(_LOG_DIRS[0]):
                    try:
                        faulthandler.disable()
                        if _FAULT_HANDLE is not None:
                            _FAULT_HANDLE.close()
                    except Exception:
                        pass
                    _FAULT_HANDLE = log_file.open("a", encoding="utf-8")
                    try:
                        faulthandler.enable(file=_FAULT_HANDLE, all_threads=True)
                    except Exception:
                        pass
            except Exception:
                continue
        root = logging.getLogger()
        if root.level > logging.DEBUG:
            root.setLevel(logging.DEBUG)
        if not _INSTALLED:
            original_sys_hook = sys.excepthook
            original_thread_hook = getattr(threading, "excepthook", None)
            original_unraisable_hook = getattr(sys, "unraisablehook", None)

            def sys_hook(exc_type, exc_value, exc_tb):
                log_exception("UNHANDLED_MAIN_EXCEPTION", exc_value, exc_tb)
                try:
                    original_sys_hook(exc_type, exc_value, exc_tb)
                except Exception:
                    pass

            def thread_hook(args):
                log_exception("UNHANDLED_THREAD_EXCEPTION " + str(args.thread.name if args.thread else "unknown"), args.exc_value, args.exc_traceback)
                if original_thread_hook:
                    try:
                        original_thread_hook(args)
                    except Exception:
                        pass

            def unraisable_hook(args):
                log_exception("UNRAISABLE_EXCEPTION " + str(args.err_msg or ""), args.exc_value, args.exc_traceback)
                if original_unraisable_hook:
                    try:
                        original_unraisable_hook(args)
                    except Exception:
                        pass

            sys.excepthook = sys_hook
            if original_thread_hook:
                threading.excepthook = thread_hook
            if original_unraisable_hook:
                sys.unraisablehook = unraisable_hook
            atexit.register(lambda: log_event("PROCESS_EXIT", "Python shutdown hook reached"))
            _INSTALLED = True
        os.environ["TWITCH_FARMING_LOG_PATH"] = get_log_path()
        log_event("DIAGNOSTICS_READY", "component=" + str(component) + " log_path=" + get_log_path())
        return get_log_path()


def get_log_paths():
    initialize_android_logging("get-log-paths")
    with _LOCK:
        return [str(directory / "twitch-farming-android.log") for directory in _LOG_DIRS]


def get_log_path():
    with _LOCK:
        if _LOG_DIRS:
            return str(_LOG_DIRS[0] / "twitch-farming-android.log")
    return str(Path.home() / ".twitch_farming_android" / "logs" / "twitch-farming-android.log")


def log_event(event, message=""):
    try:
        initialize_if_needed()
        _LOGGER.info("%s | %s", _redact(event), _redact(message))
        for handler in tuple(_HANDLERS.values()):
            try:
                handler.flush()
            except Exception:
                pass
    except Exception:
        pass


def initialize_if_needed():
    if not _HANDLERS:
        initialize_android_logging("lazy-init")


def log_exception(event, error=None, exc_tb=None):
    try:
        initialize_if_needed()
        if error is None:
            details = traceback.format_exc()
        elif isinstance(error, BaseException):
            details = "".join(traceback.format_exception(type(error), error, exc_tb or error.__traceback__))
        else:
            details = str(error)
        _LOGGER.error("%s\n%s", _redact(event), _redact(details))
        for handler in tuple(_HANDLERS.values()):
            try:
                handler.flush()
            except Exception:
                pass
    except Exception:
        pass


def get_log_text(max_chars=120000):
    initialize_android_logging("read-log")
    chunks = []
    for value in get_log_paths():
        try:
            path = Path(value)
            if path.is_file():
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as error:
            chunks.append("Cannot read " + value + ": " + str(error))
    text = "\n".join(chunks)
    return text[-max(1000, int(max_chars)):]
