import ctypes
import json
import os
import struct
import threading
from pathlib import Path


_ANDROID_MAGIC = b"TFANDROID1"
_ANDROID_ALIAS = "twitch_farming_session_storage_v1"
_ANDROID_LOCK = threading.RLock()


def _is_android():
    return (
        os.environ.get("TWITCH_FARMING_ANDROID") == "1"
        or "ANDROID_ARGUMENT" in os.environ
        or "ANDROID_PRIVATE" in os.environ
    )


def _crypt_windows(data, decrypt=False):
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = DataBlob()
    crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt32.CryptUnprotectData
        function.argtypes = [
            ctypes.POINTER(DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        ok = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        function = crypt32.CryptProtectData
        function.argtypes = [
            ctypes.POINTER(DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        ok = function(ctypes.byref(source), "Twitch Farming", None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def _java_bytes(data):
    from jnius import jarray

    return jarray("b")([value if value < 128 else value - 256 for value in data])


def _python_bytes(data):
    return bytes(int(value) & 255 for value in data)


def _android_keystore_key():
    from jnius import autoclass

    with _ANDROID_LOCK:
        KeyStore = autoclass("java.security.KeyStore")
        key_store = KeyStore.getInstance("AndroidKeyStore")
        key_store.load(None)
        key = key_store.getKey(_ANDROID_ALIAS, None)
        if key is not None:
            return key

        BuildVersion = autoclass("android.os.Build$VERSION")
        if int(BuildVersion.SDK_INT) < 23:
            raise OSError("Для защищённого хранения Twitch необходим Android 6.0 или новее.")

        KeyProperties = autoclass("android.security.keystore.KeyProperties")
        KeyGenerator = autoclass("javax.crypto.KeyGenerator")
        Builder = autoclass("android.security.keystore.KeyGenParameterSpec$Builder")
        purposes = int(KeyProperties.PURPOSE_ENCRYPT) | int(KeyProperties.PURPOSE_DECRYPT)
        builder = Builder(_ANDROID_ALIAS, purposes)
        builder.setBlockModes(["GCM"])
        builder.setEncryptionPaddings(["NoPadding"])
        builder.setRandomizedEncryptionRequired(True)
        generator = KeyGenerator.getInstance("AES", "AndroidKeyStore")
        generator.init(builder.build())
        return generator.generateKey()


def _crypt_android(data, decrypt=False):
    from jnius import autoclass, detach

    try:
        with _ANDROID_LOCK:
            key = _android_keystore_key()
            Cipher = autoclass("javax.crypto.Cipher")
            if decrypt:
                if not data.startswith(_ANDROID_MAGIC):
                    raise OSError("Файл сессии Twitch не имеет формата Android Keystore.")
                header_offset = len(_ANDROID_MAGIC)
                if len(data) < header_offset + 2:
                    raise OSError("Файл сессии Twitch повреждён.")
                iv_length = struct.unpack(">H", data[header_offset:header_offset + 2])[0]
                iv_start = header_offset + 2
                iv_end = iv_start + iv_length
                if iv_length < 12 or len(data) <= iv_end:
                    raise OSError("Файл сессии Twitch повреждён.")
                iv = data[iv_start:iv_end]
                encrypted = data[iv_end:]
                cipher = Cipher.getInstance("AES/GCM/NoPadding")
                GCMParameterSpec = autoclass("javax.crypto.spec.GCMParameterSpec")
                params = GCMParameterSpec(128, _java_bytes(iv))
                cipher.init(Cipher.DECRYPT_MODE, key, params)
                try:
                    return _python_bytes(cipher.doFinal(_java_bytes(encrypted)))
                except Exception as error:
                    raise OSError("Не удалось расшифровать сессию Twitch в Android Keystore.") from error

            cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key)
            iv = _python_bytes(cipher.getIV())
            encrypted = _python_bytes(cipher.doFinal(_java_bytes(data)))
            return _ANDROID_MAGIC + struct.pack(">H", len(iv)) + iv + encrypted
    finally:
        if threading.current_thread() is not threading.main_thread():
            try:
                detach()
            except Exception:
                pass


def _crypt(data, decrypt=False):
    if os.name == "nt":
        return _crypt_windows(data, decrypt=decrypt)
    if _is_android():
        return _crypt_android(data, decrypt=decrypt)
    raise OSError("Зашифрованное хранилище Twitch доступно через Windows DPAPI или Android Keystore.")


def save_secure_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
    encrypted = _crypt(payload)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(encrypted)
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_secure_json(path):
    payload = _crypt(Path(path).read_bytes(), decrypt=True)
    return json.loads(payload.decode("utf-8"))
