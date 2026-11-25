"""QualResearcher for Windows: starts the app on this computer and shows it in its own window.

Built into QualResearcher.exe by PyInstaller (see packaging/). For developers:
    python desktop_app.py             open the app window
    python desktop_app.py --selftest  start everything without a window, check it works, exit
"""
import argparse
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "QualResearcher"


def root() -> Path:
    """Folder holding the app's code, templates, static files, fonts and ffmpeg."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def prepare_environment():
    os.environ.setdefault("APP_MODE", "desktop")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ.setdefault("PYTHONUTF8", "1")
    base = root()
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    bundled_ffmpeg = base / "bin"
    if bundled_ffmpeg.is_dir():
        os.environ["PATH"] = str(bundled_ffmpeg) + os.pathsep + os.environ.get("PATH", "")


def setup_logging(data_dir: Path):
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_file = logs / "app.log"
    # A windowed .exe has no console: send anything printed to the log file instead.
    if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
        stream = open(log_file, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 — lives as long as the app
        sys.stdout = sys.stderr = stream
    handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    return log_file


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int):
    from waitress import serve

    from config.wsgi import application

    t = threading.Thread(
        target=serve, name="web-server", daemon=True,
        kwargs=dict(app=application, host="127.0.0.1", port=port, threads=8,
                    max_request_body_size=8 * 1024 ** 3, channel_timeout=600, ident=APP_NAME),
    )
    t.start()
    return t


def wait_until_ready(url: str, timeout: float = 40) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310 — our own local server
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.25)
    return False


def message_box(text: str, title: str = APP_NAME, yes_no: bool = False) -> bool:
    """A plain Windows message box (works from any thread, needs no window)."""
    if os.name != "nt":
        print(f"{title}: {text}")
        return True
    import ctypes

    flags = 0x00040000 | (0x04 | 0x30 if yes_no else 0x40)  # topmost | (yes/no + warning, or info)
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags) in (1, 6)


_mutex = None


def already_running() -> bool:
    global _mutex
    if os.name != "nt":
        return False
    import ctypes

    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\QualResearcherSingleInstance")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def install_fonts(data_dir: Path):
    """Put Hind Siliguri in the user's fonts once, so Word shows the Bangla transcript properly."""
    if os.name != "nt":
        return
    flag = data_dir / "fonts-installed.flag"
    if flag.exists():
        return
    try:
        import ctypes
        import shutil
        import winreg

        dest = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts"
        dest.mkdir(parents=True, exist_ok=True)
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts")
        for ttf in (root() / "fonts").glob("*.ttf"):
            target = dest / ttf.name
            if not target.exists():
                shutil.copyfile(ttf, target)
            style = ttf.stem.replace("HindSiliguri-", "")
            winreg.SetValueEx(key, f"Hind Siliguri {style} (TrueType)", 0, winreg.REG_SZ, str(target))
            ctypes.windll.gdi32.AddFontResourceW(str(target))
        winreg.CloseKey(key)
        flag.write_text("ok", encoding="utf-8")
    except Exception:
        logging.getLogger(__name__).exception("Could not install the Hind Siliguri font")


def boot():
    """Load Django and bring the database up to date. Returns Django settings."""
    prepare_environment()
    import django

    django.setup()
    from django.conf import settings
    from django.core.management import call_command

    setup_logging(Path(settings.DATA_DIR))
    call_command("migrate", interactive=False, verbosity=0)
    return settings


def selftest() -> int:
    """Used by the release build: everything except the window must work."""
    import json
    import subprocess
    import tempfile

    settings = boot()
    port = free_port()
    start_server(port)
    base = f"http://127.0.0.1:{port}"
    assert wait_until_ready(base + "/desktop/ping/"), "server did not start"
    with urllib.request.urlopen(base + "/desktop/ping/") as r:  # noqa: S310
        assert json.loads(r.read())["version"] == settings.APP_VERSION
    with urllib.request.urlopen(base + "/desktop/setup/") as r:  # noqa: S310
        page = r.read().decode("utf-8")
        assert "Gemini API key" in page and "site.css" in page
    with urllib.request.urlopen(base + "/static/css/site.css") as r:  # noqa: S310
        assert b"body.app" in r.read()

    from transcription import audio, report
    from transcription.models import Job

    with tempfile.TemporaryDirectory() as tmp:
        tone = Path(tmp) / "tone.mp3"
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:duration=3", str(tone)], check=True, **flags)
        assert 2.5 < audio.probe_duration(tone) < 3.5, "ffprobe gave a wrong duration"
        chunks = audio.make_chunks(tone, Path(tmp) / "chunks", chunk_seconds=60)
        assert len(chunks) == 1 and chunks[0].path.exists(), "ffmpeg could not convert audio"
    job = Job(title="Self test", interview_type="IDI", duration_seconds=3, status="done",
              segments=[{"start": 0.0, "speaker": "Respondent", "bn": "পরীক্ষা"}], original_filename="t.mp3")
    assert len(report.build_docx(job).getvalue()) > 5000
    print(f"{APP_NAME} {settings.APP_VERSION} self-test OK")
    return 0


def run_window():
    if already_running():
        message_box(f"{APP_NAME} is already open. Look for it in the taskbar.")
        return 0
    settings = boot()
    log = logging.getLogger(APP_NAME)
    install_fonts(Path(settings.DATA_DIR))
    port = free_port()
    start_server(port)
    url = f"http://127.0.0.1:{port}/"
    if not wait_until_ready(url + "desktop/ping/"):
        message_box(f"{APP_NAME} could not start. Details are in {Path(settings.DATA_DIR) / 'logs' / 'app.log'}")
        return 1

    import webview

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(APP_NAME, url + "app/", width=1320, height=840, min_size=(1040, 640),
                                   text_select=True, background_color="#FBFCFA")

    def on_closing():
        from transcription import runner

        if runner.RUNNING or runner.QUEUED:
            return message_box(
                "A transcription is still running. If you close the app it stops, and you can continue it "
                "later from where it stopped.\n\nClose anyway?", "Transcription running", yes_no=True)
        return True

    window.events.closing += on_closing
    log.info("Window opening at %s", url)
    webview.start(private_mode=False, storage_path=str(Path(settings.DATA_DIR) / "webview"))
    os._exit(0)  # stop the server and any worker threads with the window


def main():
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--selftest", action="store_true", help="check the app works without opening a window")
    args = parser.parse_args()
    try:
        sys.exit(selftest() if args.selftest else run_window())
    except SystemExit:
        raise
    except Exception as exc:
        logging.getLogger(APP_NAME).exception("Fatal error")
        if args.selftest:
            raise
        message_box(f"{APP_NAME} stopped because of an error:\n\n{exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
