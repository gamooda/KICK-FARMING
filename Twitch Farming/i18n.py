import json
import re
import sys
from pathlib import Path

from backend import APP_DIR


SUPPORTED_LANGUAGES = ("ru", "en")
LANGUAGE_FILE = APP_DIR / "language.json"
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
LOCALE_DIR = RESOURCE_ROOT / "locales"
_CURRENT_LANGUAGE = "ru"
_TRANSLATIONS = {}


def get_language():
    try:
        data = json.loads(LANGUAGE_FILE.read_text(encoding="utf-8"))
        value = str(data.get("language") or "ru").strip().lower()
        return value if value in SUPPORTED_LANGUAGES else "ru"
    except (OSError, json.JSONDecodeError, AttributeError):
        return "ru"


def set_language(language, persist=True):
    global _CURRENT_LANGUAGE, _TRANSLATIONS
    value = str(language or "ru").strip().lower()
    if value not in SUPPORTED_LANGUAGES:
        value = "ru"
    try:
        data = json.loads((LOCALE_DIR / (value + ".json")).read_text(encoding="utf-8"))
        translations = data.get("translations", {})
        _TRANSLATIONS = translations if isinstance(translations, dict) else {}
    except (OSError, json.JSONDecodeError, AttributeError):
        _TRANSLATIONS = {}
        value = "ru"
    _CURRENT_LANGUAGE = value
    if persist:
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            LANGUAGE_FILE.write_text(json.dumps({"language": value}, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return _CURRENT_LANGUAGE


def get_current_language():
    return _CURRENT_LANGUAGE


def translate_text(value):
    source = str(value or "")
    translated = _TRANSLATIONS.get(source)
    if isinstance(translated, str):
        return translated
    templates = sorted(
        (key for key in _TRANSLATIONS if "{" in key and "}" in key),
        key=len,
        reverse=True,
    )
    for template in templates:
        names = re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", template)
        if not names:
            continue
        pattern = re.escape(template)
        for name in names:
            pattern = pattern.replace(re.escape("{" + name + "}"), "(?P<" + name + ">.+?)")
        match = re.fullmatch(pattern, source)
        if match:
            result = str(_TRANSLATIONS[template])
            for name in names:
                result = result.replace("{" + name + "}", translate_text(match.group(name)))
            return result
    return source


set_language(get_language(), persist=False)
