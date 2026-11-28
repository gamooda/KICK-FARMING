import json
import os
import struct
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import requests

CLIENT_ID = "ue6666qo983tsx6so1t0vnawi233wa"
SCOPES = "channel_read chat:read user_blocks_edit user_blocks_read user_follows_edit user_read user:read:follows"
DEVICE_URL = "https://id.twitch.tv/oauth2/device"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
VERIFY_URL = "https://www.twitch.tv/activate"
KEYSTORE_ALIAS = "twitch_auth_test_session_v1"
MAGIC = b"AUTH_TEST_KEYSTORE_1"
ROOT = None
LOG_PATH = None
STATE_PATH = None
REQUEST_PATH = None
APP_STATE_PATH = None
LOG_LOCK = threading.RLock()


def app_data_root():
    private = os.environ.get("ANDROID_PRIVATE")
    if private:
        return Path(private) / "auth_test"
    try:
        from jnius import autoclass
        service = autoclass("org.kivy.android.PythonService").mService
        if service is not None:
            base = Path(str(service.getApplicationContext().getFilesDir().getAbsolutePath())) / "app"
            return base / "auth_test"
    except Exception:
        pass
    return Path.home() / ".auth_test"


def initialize_paths():
    global ROOT, LOG_PATH, STATE_PATH, REQUEST_PATH, APP_STATE_PATH
    ROOT = app_data_root()
    ROOT.mkdir(parents=True, exist_ok=True)
    LOG_PATH = ROOT / "events.log"
    STATE_PATH = ROOT / "state.json"
    REQUEST_PATH = ROOT / "request.json"
    APP_STATE_PATH = ROOT / "app_state.json"


def log(message):
    stamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
    line = "[%s] %s\n" % (stamp, str(message))
    with LOG_LOCK:
        with LOG_PATH.open("a", encoding="utf-8") as output:
            output.write(line)
            output.flush()


def log_exception(title):
    log(title + "\n" + traceback.format_exc())


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def update_state(**values):
    state = read_json(STATE_PATH, {}) or {}
    state.update(values)
    state["updated_at"] = time.time()
    write_json(STATE_PATH, state)


def mask_secret(value):
    value = str(value or "")
    if not value:
        return "<empty>"
    if len(value) <= 10:
        return "<masked; length=%d>" % len(value)
    return value[:4] + "…" + value[-4:] + " (length=%d)" % len(value)


def sanitized_payload(payload):
    if not isinstance(payload, dict):
        return payload
    output = dict(payload)
    for key in ("device_code", "access_token", "refresh_token"):
        if key in output:
            output[key] = mask_secret(output[key])
    return output


def response_payload(response, label):
    try:
        payload = response.json()
        log(label + " JSON=" + json.dumps(sanitized_payload(payload), ensure_ascii=False, sort_keys=True))
        return payload
    except Exception:
        log(label + " BODY=" + repr(response.text))
        raise


def network_state():
    result = {"available": None, "validated": None, "transport": "unknown", "error": None}
    try:
        from jnius import autoclass
        service = autoclass("org.kivy.android.PythonService").mService
        if service is None:
            raise RuntimeError("PythonService.mService is None")
        Context = autoclass("android.content.Context")
        manager = service.getSystemService(Context.CONNECTIVITY_SERVICE)
        BuildVersion = autoclass("android.os.Build$VERSION")
        if int(BuildVersion.SDK_INT) >= 23:
            network = manager.getActiveNetwork()
            if network is None:
                result["available"] = False
                return result
            capabilities = manager.getNetworkCapabilities(network)
            if capabilities is None:
                result["available"] = False
                return result
            result["available"] = bool(capabilities.hasCapability(12))
            result["validated"] = bool(capabilities.hasCapability(16))
            if capabilities.hasTransport(1):
                result["transport"] = "wifi"
            elif capabilities.hasTransport(0):
                result["transport"] = "cellular"
            elif capabilities.hasTransport(3):
                result["transport"] = "ethernet"
            else:
                result["transport"] = "other"
        else:
            info = manager.getActiveNetworkInfo()
            result["available"] = bool(info is not None and info.isConnected())
            result["validated"] = result["available"]
            result["transport"] = str(info.getTypeName()) if info is not None else "none"
    except Exception as error:
        result["error"] = str(error)
        result["traceback"] = traceback.format_exc()
    return result


def app_foreground_state():
    state = read_json(APP_STATE_PATH, {}) or {}
    return bool(state.get("foreground", False))


def java_bytes(data):
    from jnius import jarray
    return jarray("b")([value if value < 128 else value - 256 for value in data])


def python_bytes(data):
    return bytes(int(value) & 255 for value in data)


def android_keystore_key():
    from jnius import autoclass
    KeyStore = autoclass("java.security.KeyStore")
    key_store = KeyStore.getInstance("AndroidKeyStore")
    key_store.load(None)
    key = key_store.getKey(KEYSTORE_ALIAS, None)
    if key is not None:
        return key
    KeyProperties = autoclass("android.security.keystore.KeyProperties")
    KeyGenerator = autoclass("javax.crypto.KeyGenerator")
    Builder = autoclass("android.security.keystore.KeyGenParameterSpec$Builder")
    purposes = int(KeyProperties.PURPOSE_ENCRYPT) | int(KeyProperties.PURPOSE_DECRYPT)
    builder = Builder(KEYSTORE_ALIAS, purposes)
    builder.setBlockModes(["GCM"])
    builder.setEncryptionPaddings(["NoPadding"])
    builder.setRandomizedEncryptionRequired(True)
    generator = KeyGenerator.getInstance("AES", "AndroidKeyStore")
    generator.init(builder.build())
    return generator.generateKey()


def keystore_roundtrip(path, payload):
    from jnius import autoclass
    key = android_keystore_key()
    Cipher = autoclass("javax.crypto.Cipher")
    cipher = Cipher.getInstance("AES/GCM/NoPadding")
    plain = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    cipher.init(Cipher.ENCRYPT_MODE, key)
    iv = python_bytes(cipher.getIV())
    encrypted = python_bytes(cipher.doFinal(java_bytes(plain)))
    encoded = MAGIC + struct.pack(">H", len(iv)) + iv + encrypted
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())
    os.replace(str(temporary), str(path))
    saved = path.read_bytes()
    if not saved.startswith(MAGIC) or len(saved) <= len(MAGIC) + 2:
        raise OSError("Keystore test file has an invalid header or is empty.")
    offset = len(MAGIC)
    iv_length = struct.unpack(">H", saved[offset:offset + 2])[0]
    iv_start = offset + 2
    iv_end = iv_start + iv_length
    if iv_length < 12 or len(saved) <= iv_end:
        raise OSError("Keystore test file has an invalid IV or encrypted body.")
    GCMParameterSpec = autoclass("javax.crypto.spec.GCMParameterSpec")
    decryptor = Cipher.getInstance("AES/GCM/NoPadding")
    decryptor.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, java_bytes(saved[iv_start:iv_end])))
    restored = python_bytes(decryptor.doFinal(java_bytes(saved[iv_end:])))
    decoded = json.loads(restored.decode("utf-8"))
    if decoded != payload:
        raise OSError("Keystore round-trip content does not match the original session.")
    return len(encoded), len(restored)


def fallback_roundtrip(path, payload):
    temporary = path.with_name(path.name + ".tmp")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    with temporary.open("wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(str(temporary), str(path))
    restored = json.loads(path.read_text(encoding="utf-8"))
    if restored != payload:
        raise OSError("Private-file fallback round-trip content does not match.")
    return len(raw)


def run_storage_test(access_token, refresh_token, username, user_id, scopes):
    log("STEP 4 BEGIN: session persistence test")
    payload = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "login": username,
        "user_id": user_id,
        "scopes": scopes,
        "saved_at": datetime.now().astimezone().isoformat(),
        "source": "AuthTest device authorization"
    }
    keystore_path = ROOT / "session.keystore"
    fallback_path = ROOT / "session_fallback.json"
    try:
        log("STEP 4.1 Android Keystore begin: alias=" + KEYSTORE_ALIAS)
        byte_count, plain_count = keystore_roundtrip(keystore_path, payload)
        log("STEP 4.2 ANDROID KEYSTORE WRITE+READ SUCCESS: file=%s encrypted_bytes=%d restored_json_bytes=%d" % (keystore_path, byte_count, plain_count))
        try:
            fallback_path.unlink(missing_ok=True)
        except OSError:
            pass
        update_state(storage="Android Keystore: write/read success")
    except Exception:
        log_exception("STEP 4 ANDROID KEYSTORE FAILED; FULL TRACEBACK:")
        try:
            log("STEP 4.3 PRIVATE USER DATA FILE FALLBACK BEGIN: " + str(fallback_path))
            byte_count = fallback_roundtrip(fallback_path, payload)
            log("STEP 4.4 PRIVATE FILE FALLBACK WRITE+READ SUCCESS: bytes=%d path=%s" % (byte_count, fallback_path))
            update_state(storage="Android Keystore failed; private-file fallback write/read success")
        except Exception:
            log_exception("STEP 4 PRIVATE FILE FALLBACK FAILED; FULL TRACEBACK:")
            update_state(storage="Keystore and private-file fallback failed")
            raise


def run_device_authorization():
    attempt_started = time.monotonic()
    log("=" * 72)
    log("AUTHORIZATION RUN BEGIN")
    update_state(status="Requesting device code", user_code="", error="", storage="")
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "Accept-Language": "en-US",
        "Cache-Control": "no-cache",
        "Client-Id": CLIENT_ID,
        "Origin": "https://android.tv.twitch.tv",
        "Referer": "https://android.tv.twitch.tv/",
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; Android TV) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "X-Device-Id": uuid.uuid4().hex
    })
    log("STEP 1 BEGIN: POST " + DEVICE_URL)
    log("STEP 1 REQUEST client_id=" + CLIENT_ID)
    log("STEP 1 REQUEST scopes=" + SCOPES)
    log("STEP 1 REQUEST headers=" + json.dumps(dict(session.headers), ensure_ascii=False, sort_keys=True))
    try:
        response = session.post(DEVICE_URL, data={"client_id": CLIENT_ID, "scopes": SCOPES}, timeout=20)
        log("STEP 1 HTTP status=%s reason=%s" % (response.status_code, response.reason))
        payload = response_payload(response, "STEP 1 RESPONSE")
        if response.status_code != 200:
            raise RuntimeError("Twitch device endpoint returned HTTP %s: %s" % (response.status_code, response.text))
        device_code = str(payload.get("device_code") or "")
        user_code = str(payload.get("user_code") or "")
        if not device_code or not user_code:
            raise RuntimeError("Twitch device response is missing device_code or user_code.")
        interval = max(1, int(payload.get("interval") or 5))
        expires_in = max(1, int(payload.get("expires_in") or 1800))
        update_state(status="Waiting for code confirmation", user_code=user_code, verification_uri=payload.get("verification_uri") or VERIFY_URL, interval=interval, expires_in=expires_in)
        log("STEP 1 VALUES user_code=%s expires_in=%s interval=%s verification_uri=%s device_code=%s" % (user_code, expires_in, interval, payload.get("verification_uri") or VERIFY_URL, mask_secret(device_code)))
    except Exception:
        log_exception("STEP 1 FAILED; FULL TRACEBACK:")
        update_state(status="Device-code request failed", error=traceback.format_exc())
        return

    log("STEP 2 BEGIN: background POST " + TOKEN_URL)
    expires_at = time.monotonic() + expires_in
    poll_attempt = 0
    access_token = ""
    refresh_token = ""
    while time.monotonic() < expires_at:
        poll_attempt += 1
        log("STEP 2 POLL attempt=%d interval=%ds service_thread=%s thread_alive=%s app_foreground=%s network=%s" % (
            poll_attempt,
            interval,
            threading.current_thread().name,
            threading.current_thread().is_alive(),
            app_foreground_state(),
            json.dumps(network_state(), ensure_ascii=False, sort_keys=True)
        ))
        time.sleep(interval)
        if time.monotonic() >= expires_at:
            break
        try:
            poll_data = {
                "client_id": CLIENT_ID,
                "device_code": device_code,
                "scopes": SCOPES,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
            }
            log("STEP 2 POLL attempt=%d REQUEST client_id=%s scopes=%s grant_type=%s device_code=%s" % (
                poll_attempt,
                CLIENT_ID,
                SCOPES,
                poll_data["grant_type"],
                mask_secret(device_code)
            ))
            response = session.post(TOKEN_URL, data=poll_data, timeout=20)
            log("STEP 2 POLL attempt=%d HTTP status=%s reason=%s" % (poll_attempt, response.status_code, response.reason))
            try:
                payload = response.json()
                log("STEP 2 POLL attempt=%d RESPONSE JSON=%s" % (
                    poll_attempt,
                    json.dumps(sanitized_payload(payload), ensure_ascii=False, sort_keys=True)
                ))
            except Exception:
                log("STEP 2 POLL attempt=%d RESPONSE BODY=%s" % (poll_attempt, repr(response.text)))
                raise
            if response.status_code == 200 and payload.get("access_token"):
                access_token = str(payload.get("access_token") or "")
                refresh_token = str(payload.get("refresh_token") or "")
                log("STEP 2 TOKEN RECEIVED access_token=%s refresh_token=%s token_type=%s expires_in=%s" % (
                    mask_secret(access_token),
                    mask_secret(refresh_token),
                    payload.get("token_type"),
                    payload.get("expires_in")
                ))
                break
            error_code = str(payload.get("message") or payload.get("error") or "")
            if error_code == "authorization_pending":
                log("STEP 2 STATE authorization_pending; next poll in %d seconds" % interval)
                continue
            if error_code == "slow_down":
                interval += 5
                log("STEP 2 STATE slow_down; interval increased to %d seconds" % interval)
                continue
            if error_code == "expired_token":
                raise RuntimeError("Twitch returned expired_token.")
            if error_code == "access_denied":
                raise RuntimeError("Twitch returned access_denied.")
            raise RuntimeError("Unexpected token response HTTP %s: %s" % (response.status_code, payload))
        except requests.RequestException:
            log_exception("STEP 2 POLL NETWORK ERROR; FULL TRACEBACK:")
        except Exception:
            log_exception("STEP 2 POLL FAILED; FULL TRACEBACK:")
            update_state(status="Token polling failed", error=traceback.format_exc())
            return
    if not access_token:
        error = "Device code expired after %d seconds without an access token." % expires_in
        log("STEP 2 EXPIRED: " + error)
        update_state(status="Device code expired", error=error)
        return
    update_state(status="Token received; validating account")

    log("STEP 3 BEGIN: GET " + VALIDATE_URL)
    try:
        validate_response = session.get(
            VALIDATE_URL,
            headers={"Authorization": "OAuth " + access_token},
            timeout=15
        )
        request_headers = dict(validate_response.request.headers)
        if "Authorization" in request_headers:
            request_headers["Authorization"] = "OAuth " + mask_secret(access_token)
        log("STEP 3 REQUEST HEADERS=" + json.dumps(request_headers, ensure_ascii=False, sort_keys=True))
        log("STEP 3 HTTP status=%s reason=%s" % (validate_response.status_code, validate_response.reason))
        log("STEP 3 RESPONSE HEADERS=" + json.dumps(dict(validate_response.headers), ensure_ascii=False, sort_keys=True))
        validate_payload = validate_response.json()
        safe_validate_payload = {
            "client_id": validate_payload.get("client_id"),
            "login": validate_payload.get("login"),
            "user_id": validate_payload.get("user_id"),
            "scopes": validate_payload.get("scopes"),
            "expires_in": validate_payload.get("expires_in")
        }
        log("STEP 3 RESPONSE JSON=" + json.dumps(safe_validate_payload, ensure_ascii=False, sort_keys=True))
        if validate_response.status_code != 200:
            raise RuntimeError("Token validation returned HTTP %s: %s" % (validate_response.status_code, validate_response.text))
        username = str(validate_payload.get("login") or "")
        user_id = str(validate_payload.get("user_id") or "")
        scopes = list(validate_payload.get("scopes") or [])
        if validate_payload.get("client_id") != CLIENT_ID:
            raise RuntimeError("Validated token client_id differs from the requested client_id.")
        if not username or not user_id:
            raise RuntimeError("Validated response is missing login or user_id.")
        log("STEP 3 VALIDATED login=%s user_id=%s scopes=%s" % (username, user_id, json.dumps(scopes, ensure_ascii=False)))
        update_state(status="Twitch token validated", login=username, user_id=user_id, scopes=scopes)
    except Exception:
        log_exception("STEP 3 VALIDATION FAILED; FULL TRACEBACK:")
        update_state(status="Token validation failed", error=traceback.format_exc())
        return

    try:
        run_storage_test(access_token, refresh_token, username, user_id, scopes)
    except Exception:
        update_state(status="Session persistence test failed", error=traceback.format_exc())
        return
    elapsed = time.monotonic() - attempt_started
    log("AUTHORIZATION RUN COMPLETE: Twitch account validated; persistence test finished in %.2f sec." % elapsed)
    update_state(status="SUCCESS: Twitch account validated and session read-back tested", login=username, user_id=user_id, scopes=scopes)


def main():
    initialize_paths()
    log("AUTH TEST SERVICE STARTED")
    log("service_process=%s python=%s user_data_dir=%s" % (os.getpid(), os.sys.version, ROOT))
    update_state(service_running=True, status="Diagnostic service ready")
    while True:
        try:
            if REQUEST_PATH.is_file():
                request = read_json(REQUEST_PATH, {}) or {}
                try:
                    REQUEST_PATH.unlink(missing_ok=True)
                except OSError:
                    pass
                if request.get("action") == "start":
                    log("SERVICE RECEIVED AUTHORIZATION REQUEST id=%s" % request.get("request_id"))
                    try:
                        run_device_authorization()
                    except Exception:
                        log_exception("UNHANDLED AUTHORIZATION FLOW ERROR; FULL TRACEBACK:")
                        update_state(status="Unhandled diagnostic error", error=traceback.format_exc())
            time.sleep(0.25)
        except Exception:
            log_exception("SERVICE LOOP ERROR; FULL TRACEBACK:")
            time.sleep(1)


if __name__ == "__main__":
    main()
