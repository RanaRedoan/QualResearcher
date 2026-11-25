# PyInstaller build of the QualResearcher desktop app.
#   pyinstaller --noconfirm packaging/QualResearcher.spec
# Output: dist/QualResearcher/QualResearcher.exe (+ its _internal folder).
#
# The project's own packages are copied as plain source files, so Django finds templates,
# static files and migrations on disk exactly as when run from source. Their imports are
# read from the source here and handed to PyInstaller as hidden imports.
import ast
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
APP_PACKAGES = ["accounts", "billing", "config", "desktop", "pages", "transcription"]
ASSET_DIRS = ["templates", "static", "fonts"]
SKIP = {"__pycache__", "tests"}


def source_files(folder):
    for path in (ROOT / folder).rglob("*"):
        if path.is_file() and not SKIP & set(path.relative_to(ROOT).parts) and path.suffix != ".pyc":
            yield path


def imported_modules():
    """Every module the app's own code imports (except the app's own packages)."""
    names = set()
    for pkg in APP_PACKAGES:
        for path in (ROOT / pkg).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names.add(node.module)
                    names.update(f"{node.module}.{a.name}" for a in node.names)
    own = tuple(APP_PACKAGES)
    return sorted(n for n in names if not n.split(".")[0] in own and n.split(".")[0] != "celery")


datas = [(str(p), str(p.parent.relative_to(ROOT))) for d in APP_PACKAGES + ASSET_DIRS for p in source_files(d)]
datas += collect_data_files("django")
datas += collect_data_files("docx")
datas += collect_data_files("certifi")
datas += copy_metadata("google-genai")
binaries = []
ffmpeg_dir = ROOT / "build" / "ffmpeg"
for exe in ("ffmpeg.exe", "ffprobe.exe", "ffmpeg", "ffprobe"):
    if (ffmpeg_dir / exe).exists():
        binaries.append((str(ffmpeg_dir / exe), "bin"))

hiddenimports = []
for package in ("django", "whitenoise", "google.genai", "docx", "waitress", "dj_database_url", "dotenv"):
    hiddenimports += collect_submodules(package)
for name in imported_modules():
    try:
        __import__(name)
        hiddenimports.append(name)
    except Exception:  # "from x import some_function" — keep the module part only
        pass
if sys.platform == "win32":
    hiddenimports += collect_submodules("webview")

a = Analysis(
    [str(ROOT / "desktop_app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL", "pytest", "celery", "kombu", "billiard",
              "IPython", "jupyter", "django.contrib.gis.tests"] + [p for p in APP_PACKAGES],
    noarchive=False,
    # python-docx reads its XML templates via paths like docx/parts/../templates, so it must be on disk
    module_collection_mode={"docx": "py"},
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="QualResearcher",
    icon=str(ROOT / "packaging" / "icon.ico"),
    console=False,
    version=str(ROOT / "build" / "version_info.txt") if (ROOT / "build" / "version_info.txt").exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="QualResearcher")
