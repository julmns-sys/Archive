# Bob Archive

Bob Archive is a local, macOS-first desktop application for cataloguing physical
books and reading/editing their scanned PDFs. Categories and flexible searchable
tags make books easy to find. Multiple PDF scans can be combined into one book
while every source PDF is preserved. The application deliberately keeps
metadata and controls small and readable.

## Run from source

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m app.main
```

By default, archive data is stored in `~/Documents/BobArchiveLibrary`. For safe
testing, set `BOB_ARCHIVE_HOME` to another folder before launching.

## Data layout

Each imported book keeps an untouched original PDF, an editable current PDF,
cached page thumbnails, and `metadata.json`. SQLite stores the searchable
catalog but contains no PDF data. Portable `.bobbackup` files export both the
catalog and readable JSON metadata and can be selected in the app to restore the
complete library. Compact backups preserve every source and current PDF while
rebuilding derived original PDFs during restore, avoiding redundant storage.

See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) for the main workflows.
See [docs/MACOS_AND_CICD.md](docs/MACOS_AND_CICD.md) for iMac installation,
USB transfer, and the GitHub Actions CI/CD workflow.

## Build a macOS application

On a Mac, create the environment above and run:

```bash
python -m pip install -e '.[build]'
pyinstaller BobArchive.spec
```

The normal double-clickable application is created at `dist/Bob Archive.app`.
The GitHub Actions workflow runs the tests and creates separate Intel and Apple
Silicon apps as downloadable ZIP artifacts. Version tags such as `v0.1.0`
publish both builds in a GitHub Release.
