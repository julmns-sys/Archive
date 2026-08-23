# Running Bob Archive on an iMac

## Choose the correct build

On the iMac, open **Apple menu > About This Mac**:

- If the processor says **Intel**, use `Bob-Archive-macOS-Intel.zip`.
- If the chip says **Apple M1, M2, M3, M4, or newer**, use
  `Bob-Archive-macOS-Apple-Silicon.zip`.

## Transfer with a USB drive

1. Copy the appropriate ZIP file to the USB drive. Keep the application in the
   ZIP while transferring it so the macOS application bundle remains intact.
2. On the iMac, copy the ZIP from the USB drive to `Downloads`.
3. Double-click the ZIP and move `Bob Archive.app` to `Applications`.
4. For the first launch, Control-click the app, choose **Open**, then confirm
   **Open**. This is needed because development builds are not notarized by
   Apple.

The working library is stored separately in
`~/Documents/BobArchiveLibrary`, so replacing the application with a newer
build does not remove books or the catalog. Use Bob Archive's backup command
before moving an existing library between computers, copy the resulting
`.bobbackup` file to the USB drive, and restore it on the iMac.

## Build directly on the iMac

Install Python 3.11 or newer, copy the source folder to the iMac, open Terminal
in that folder, and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test,build]'
pytest
pyinstaller --clean --noconfirm BobArchive.spec
```

The result is `dist/Bob Archive.app`. To make a USB-safe archive:

```bash
ditto -c -k --sequesterRsrc --keepParent \
  'dist/Bob Archive.app' 'Bob-Archive-macOS.zip'
```

## GitHub Actions CI/CD

The workflow in `.github/workflows/test-and-build.yml` performs these steps:

1. Every push and pull request runs the test suite on Python 3.12.
2. After successful tests, native Intel and Apple Silicon macOS applications
   are built and uploaded as workflow artifacts.
3. A Git tag beginning with `v` additionally creates a GitHub Release and
   attaches both ZIP files.

Push the project to a GitHub repository for the first run:

```bash
git add .
git commit -m "Set up Bob Archive and macOS CI/CD"
git branch -M main
git remote add origin git@github.com:YOUR_ACCOUNT/YOUR_REPOSITORY.git
git push -u origin main
```

After Actions succeeds, download a build from **GitHub > Actions > latest
workflow run > Artifacts**.

To publish a version in **GitHub > Releases**:

```bash
git tag v0.1.0
git push origin v0.1.0
```

No additional GitHub secret is required for unsigned builds. Fully seamless
double-click installation on other Macs requires an Apple Developer ID,
code-signing, and notarization; those credentials should be added later as
encrypted GitHub Actions secrets.
