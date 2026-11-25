"""Settings the desktop app keeps between runs: named Gemini keys, the user's name and
where Word files are saved. Stored as JSON in the app's data folder
(%LOCALAPPDATA%\\QualResearcher\\config.json), which only this Windows user can read.

    {"name": "Rana", "export_dir": "", "english_words": "bangla",
     "keys": [{"id": "a1b2c3", "name": "redoan630", "key": "AIza…"}], "default_key": "a1b2c3"}
"""
import json
import secrets
import threading
from pathlib import Path

from django.conf import settings

_lock = threading.Lock()
TEXT_FIELDS = ("name", "export_dir", "english_words", "default_key")


def _path() -> Path:
    return Path(settings.DATA_DIR) / "config.json"


def _read() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _clean(data: dict) -> dict:
    out = {f: str(data.get(f, "") or "") for f in TEXT_FIELDS}
    keys = [k for k in data.get("keys", []) if isinstance(k, dict) and k.get("key")]
    # files written by the first version held one key under "gemini_api_key"
    if not keys and data.get("gemini_api_key"):
        keys = [{"id": "main", "name": "My key", "key": data["gemini_api_key"]}]
    out["keys"] = [{"id": str(k.get("id") or secrets.token_hex(3)), "name": str(k.get("name") or "Key"),
                    "key": str(k["key"]).strip()} for k in keys]
    ids = [k["id"] for k in out["keys"]]
    if out["default_key"] not in ids:
        out["default_key"] = ids[0] if ids else ""
    return out


def load() -> dict:
    return _clean(_read())


def _write(data: dict) -> dict:
    data = _clean(data)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return data


def save(**changes) -> dict:
    """Change the plain settings (name, export_dir, english_words, default_key)."""
    with _lock:
        data = load()
        data.update({k: str(v).strip() for k, v in changes.items() if k in TEXT_FIELDS})
        return _write(data)


# ---------------------------------------------------------------- named keys
def keys() -> list[dict]:
    return load()["keys"]


def add_key(name: str, key: str, make_default: bool = False) -> dict:
    with _lock:
        data = load()
        name = (name or "").strip()[:60] or f"Key {len(data['keys']) + 1}"
        existing = next((k for k in data["keys"] if k["key"] == key.strip()), None)
        if existing:  # same key pasted again: just rename it
            existing["name"] = name
            entry = existing
        else:
            entry = {"id": secrets.token_hex(3), "name": name, "key": key.strip()}
            data["keys"].append(entry)
        if make_default or len(data["keys"]) == 1:
            data["default_key"] = entry["id"]
        _write(data)
        return entry


def remove_key(key_id: str):
    with _lock:
        data = load()
        data["keys"] = [k for k in data["keys"] if k["id"] != key_id]
        _write(data)


def rename_key(key_id: str, name: str):
    with _lock:
        data = load()
        for k in data["keys"]:
            if k["id"] == key_id and name.strip():
                k["name"] = name.strip()[:60]
        _write(data)


def get_key(key_id: str = "") -> dict | None:
    data = load()
    by_id = {k["id"]: k for k in data["keys"]}
    return by_id.get(key_id) or by_id.get(data["default_key"])


def key_name(key_id: str = "") -> str:
    entry = get_key(key_id)
    return entry["name"] if entry else ""


def api_key(key_id: str = "") -> str:
    """The chosen key (or the default one) from the app; GEMINI_API_KEY from the environment otherwise."""
    if settings.DESKTOP:
        entry = get_key(key_id)
        if entry:
            return entry["key"]
    return settings.GEMINI_API_KEY


def export_dir() -> Path:
    custom = load()["export_dir"] if settings.DESKTOP else ""
    return Path(custom) if custom else Path(settings.DEFAULT_EXPORT_DIR)


def mask(key: str) -> str:
    return f"{key[:4]}…{key[-4:]}" if len(key) > 12 else ("set" if key else "")
