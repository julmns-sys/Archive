from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable


GITHUB_REPOSITORY = "julmns-sys/Archive"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    title: str
    notes: str
    page_url: str
    asset_name: str
    asset_url: str
    asset_size: int
    checksum_url: str | None
    api_digest: str | None
    is_newer: bool


@dataclass(frozen=True)
class PreparedUpdate:
    version: str
    archive: Path
    installer: Path | None


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Unsupported version number: {value}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


class UpdateService:
    def __init__(self, updates_dir: Path, current_version: str):
        self.updates_dir = updates_dir
        self.current_version = current_version

    @staticmethod
    def supported() -> bool:
        return sys.platform == "darwin" and platform.machine().lower() in {
            "arm64", "aarch64", "x86_64", "amd64"
        }

    @staticmethod
    def asset_name() -> str:
        machine = platform.machine().lower()
        if machine in {"arm64", "aarch64"}:
            return "Bob-Archive-macOS-Apple-Silicon.zip"
        if machine in {"x86_64", "amd64"}:
            return "Bob-Archive-macOS-Intel.zip"
        raise RuntimeError(f"Automatic updates are not available for {platform.system()} {machine}.")

    def check(self, progress: Callable[[int, str], None]) -> ReleaseInfo:
        if not self.supported():
            raise RuntimeError("Automatic updates are currently available only in the macOS application.")
        progress(20, "Contacting GitHub")
        request = urllib.request.Request(
            f"{GITHUB_API}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Bob-Archive-Updater"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
        release_version = str(payload.get("tag_name", ""))
        _version_tuple(release_version)
        wanted = self.asset_name()
        assets = payload.get("assets") or []
        asset = next((item for item in assets if item.get("name") == wanted), None)
        if not asset:
            raise RuntimeError(f"The latest GitHub release does not contain {wanted}.")
        checksum = next((item for item in assets if item.get("name") == f"{wanted}.sha256"), None)
        progress(100, "Update check complete")
        return ReleaseInfo(
            version=release_version.removeprefix("v"),
            title=str(payload.get("name") or release_version),
            notes=str(payload.get("body") or ""),
            page_url=str(payload.get("html_url") or ""),
            asset_name=wanted,
            asset_url=str(asset["browser_download_url"]),
            asset_size=int(asset.get("size") or 0),
            checksum_url=str(checksum["browser_download_url"]) if checksum else None,
            api_digest=str(asset.get("digest")) if asset.get("digest") else None,
            is_newer=_version_tuple(release_version) > _version_tuple(self.current_version),
        )

    def download_and_prepare(
        self, release: ReleaseInfo, progress: Callable[[int, str], None]
    ) -> PreparedUpdate:
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        archive = self.updates_dir / release.asset_name
        temporary = archive.with_suffix(".download")
        temporary.unlink(missing_ok=True)
        self._validate_download_url(release.asset_url)
        request = urllib.request.Request(
            release.asset_url, headers={"User-Agent": "Bob-Archive-Updater"}
        )
        digest = hashlib.sha256()
        received = 0
        progress(2, "Downloading update")
        try:
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if release.asset_size:
                        progress(min(82, 2 + int(received / release.asset_size * 80)), "Downloading update")
            if release.asset_size and received != release.asset_size:
                raise IOError("The downloaded update has an unexpected size.")
            expected = self._expected_checksum(release)
            if not expected:
                raise RuntimeError("The release has no SHA-256 checksum; the update was not installed.")
            if digest.hexdigest().lower() != expected.lower():
                raise IOError("The update checksum does not match the GitHub release.")
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)

        progress(86, "Checking update package")
        self._validate_archive(archive)
        bundle = self._installed_bundle()
        if bundle is None:
            progress(100, "Update downloaded")
            return PreparedUpdate(release.version, archive, None)
        installer = self._prepare_installer(archive, bundle, release.version)
        progress(100, "Update ready to install")
        return PreparedUpdate(release.version, archive, installer)

    def _expected_checksum(self, release: ReleaseInfo) -> str | None:
        if release.api_digest and release.api_digest.startswith("sha256:"):
            return release.api_digest.split(":", 1)[1].strip()
        if not release.checksum_url:
            return None
        self._validate_download_url(release.checksum_url)
        request = urllib.request.Request(
            release.checksum_url, headers={"User-Agent": "Bob-Archive-Updater"}
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            value = response.read(4096).decode("ascii", errors="strict").strip().split()[0]
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise RuntimeError("The release checksum file is invalid.")
        return value

    @staticmethod
    def _validate_download_url(url: str) -> None:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_DOWNLOAD_HOSTS:
            raise RuntimeError("GitHub returned an unsafe update download address.")

    @staticmethod
    def _validate_archive(archive: Path) -> None:
        with zipfile.ZipFile(archive) as package:
            names = package.namelist()
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts:
                    raise RuntimeError("The update archive contains an unsafe path.")
            if not any(
                name.startswith("Bob Archive.app/Contents/MacOS/") for name in names
            ):
                raise RuntimeError("The update archive does not contain Bob Archive.app.")

    @staticmethod
    def _installed_bundle() -> Path | None:
        executable = Path(sys.executable).resolve()
        for parent in (executable, *executable.parents):
            if parent.name.endswith(".app") and (parent / "Contents" / "MacOS").is_dir():
                return parent
        return None

    def _prepare_installer(self, archive: Path, target: Path, version: str) -> Path:
        staging = Path(tempfile.mkdtemp(prefix="bob-archive-update-"))
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(staging)], check=True)
        source = staging / "Bob Archive.app"
        if not (source / "Contents" / "MacOS" / "Bob Archive").is_file():
            raise RuntimeError("The extracted update application is incomplete.")
        installer = staging / "install-update.sh"
        backup = target.with_name(f"{target.name}.previous")
        log = self.updates_dir / "install.log"
        q = shlex.quote
        installer.write_text(
            "#!/bin/sh\n"
            f"while /bin/kill -0 {os.getpid()} 2>/dev/null; do /bin/sleep 1; done\n"
            f"exec >>{q(str(log))} 2>&1\n"
            f"/bin/echo 'Installing Bob Archive {version}'\n"
            f"/bin/rm -rf {q(str(backup))}\n"
            f"if /bin/mv {q(str(target))} {q(str(backup))}; then\n"
            f"  if /usr/bin/ditto {q(str(source))} {q(str(target))}; then\n"
            f"    /bin/rm -rf {q(str(backup))}\n"
            f"    /usr/bin/open {q(str(target))}\n"
            "  else\n"
            f"    /bin/rm -rf {q(str(target))}\n"
            f"    /bin/mv {q(str(backup))} {q(str(target))}\n"
            f"    /usr/bin/open {q(str(target))}\n"
            "    /usr/bin/osascript -e 'display alert \"Bob Archive update failed\" message \"The previous version was restored. See the update log for details.\"'\n"
            "  fi\n"
            "else\n"
            f"  /usr/bin/open {q(str(target))}\n"
            "  /usr/bin/osascript -e 'display alert \"Bob Archive update failed\" message \"The application could not be replaced. Move Bob Archive to a user-writable folder or install the update manually.\"'\n"
            "fi\n"
            f"/bin/rm -rf {q(str(staging))}\n",
            encoding="utf-8",
        )
        installer.chmod(0o700)
        return installer

    @staticmethod
    def launch_installer(update: PreparedUpdate) -> None:
        if not update.installer:
            raise RuntimeError("This copy of Bob Archive cannot replace itself automatically.")
        subprocess.Popen(
            ["/bin/sh", str(update.installer)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
