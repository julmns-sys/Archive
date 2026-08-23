from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.services.updater import UpdateService, _version_tuple


def test_version_comparison_accepts_github_tags() -> None:
    assert _version_tuple("v1.12.3") > _version_tuple("1.9.9")
    assert _version_tuple("1.2.0") == (1, 2, 0)


def test_version_comparison_rejects_unexpected_values() -> None:
    with pytest.raises(ValueError):
        _version_tuple("latest")


def test_update_archive_must_contain_application(tmp_path: Path) -> None:
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("readme.txt", "not an application")

    with pytest.raises(RuntimeError, match="does not contain"):
        UpdateService._validate_archive(archive)


def test_update_archive_rejects_parent_path(tmp_path: Path) -> None:
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../Bob Archive.app/Contents/MacOS/Bob Archive", "bad")

    with pytest.raises(RuntimeError, match="unsafe path"):
        UpdateService._validate_archive(archive)


def test_update_archive_accepts_expected_bundle(tmp_path: Path) -> None:
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("Bob Archive.app/Contents/MacOS/Bob Archive", "binary")

    UpdateService._validate_archive(archive)
