# Developing QualResearcher

The app is a Django project. The same code runs three ways, chosen by `APP_MODE`:

| Mode | Used for | How it starts |
| --- | --- | --- |
| `desktop` | the installed Windows app | `desktop_app.py` (PyInstaller turns it into `QualResearcher.exe`) |
| `local` | running from source in a browser | `start.bat`, see [RUN_FROM_SOURCE.md](RUN_FROM_SOURCE.md) |
| `saas` | a public website with accounts and credits | `deploy/` (Docker + Caddy) |

## Run the desktop app from source

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-desktop.txt
python desktop_app.py
```

ffmpeg must be on PATH when running from source (`winget install Gyan.FFmpeg`). The installer ships its own copy.

`python desktop_app.py --selftest` starts everything except the window, checks the pages, ffmpeg and the Word export, and exits with 0 when all is well. The release build runs it on the finished `.exe`.

## Tests

```
pip install -r requirements.txt waitress
python -m pytest
```

## Where things are

- `desktop/`: the parts only the app has: no login, named Gemini keys (`store.py`), Settings, Save as Word, update check.
- `templates/layouts/app.html`: the app window (sidebar, list, content, status bar). `layouts/web.html` is the website.
- `transcription/`: audio splitting (`audio.py`), Gemini calls (`gemini.py`), the pipeline, Word export (`report.py`).
- `packaging/`: PyInstaller spec, Inno Setup script, icon.
- `.github/workflows/release.yml`: tests, builds the `.exe`, self-tests it, builds the installer and publishes the release.

## Publishing a new version

1. Raise the number in `config/version.py` (for example `1.0.1`).
2. Get the code into the `main` branch, either with git, or by uploading an archive of the project as `upload/code.tar.gz` through the GitHub website; `update-from-archive.yml` unpacks and commits it.
3. The installer build starts by itself when the version has no release yet. Otherwise run it from **Actions → Build Windows installer → Run workflow**.

Installed apps show "Version … is available" in their status bar once the release is published.
