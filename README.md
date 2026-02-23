# EbookRenamer

Rename `.epub` and `.pdf` files using metadata, with preview-first workflow, GUI editing, i18n, and packaging support.

## Features

- Metadata-based rename for EPUB/PDF.
- Preview before apply (CLI/TUI/GUI).
- GUI supports:
  - folder selection,
  - editable target names,
  - live filename length indicators,
  - check-for-update action (from `latest.json`),
  - About dialog and GitHub link.
- i18n language packs:
  - English (`en`)
  - 简体中文 (`zh_CN`)
  - 繁體中文 (`zh_TW`)
  - 日本語 (`ja`)
  - Tiếng Việt (`vi`)
- Windows/Linux/macOS packaging via `Makefile`.

## Quick Start

```bash
python3 rename_books_by_meta.py --ui cli --dir .
```

Apply rename:

```bash
python3 rename_books_by_meta.py --ui cli --dir . --apply
```

Launch GUI:

```bash
python3 rename_books_by_meta.py --gui --app-title "Ebook Renamer"
```

Check update from command line:

```bash
python3 rename_books_by_meta.py --check-update
```

## Build

```bash
make icon
make build-macos
make build-linux DOCKER_PLATFORM=linux/amd64
make build-windows DOCKER_PLATFORM=linux/amd64
make release DOCKER_PLATFORM=linux/amd64
```

`make build-windows` behavior:
- tries local Docker/Wine build first,
- on failure, falls back to GitHub Actions remote Windows runner,
- downloads real `EbookRenamer.exe` back to `dist/windows/`.

## CI

GitHub Actions workflow is in `.github/workflows/build.yml`.

Default release behavior:
- only runs on tags matching `vX.Y.Z` (for example `v0.0.1`),
- builds artifacts for:
- macOS
- Linux
- Windows
- creates/updates the GitHub Release for that tag,
- uploads `latest.json` for in-app update checks.

Manual run is still available via `workflow_dispatch`.

## Notes

- Ebook source files are ignored by `.gitignore` to avoid accidental upload.
- Locales are under `locales/` and bundled in builds.
