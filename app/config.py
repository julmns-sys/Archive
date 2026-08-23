from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root: Path
    database_dir: Path
    books_dir: Path
    backups_dir: Path
    logs_dir: Path
    database_file: Path
    log_file: Path

    @classmethod
    def discover(cls) -> "AppPaths":
        configured = os.environ.get("BOB_ARCHIVE_HOME")
        root = Path(configured).expanduser() if configured else Path.home() / "Documents" / "BobArchiveLibrary"
        return cls(
            root=root,
            database_dir=root / "database",
            books_dir=root / "books",
            backups_dir=root / "backups",
            logs_dir=root / "logs",
            database_file=root / "database" / "bob_archive.sqlite3",
            log_file=root / "logs" / "bob_archive.log",
        )

    def create(self) -> None:
        for directory in (self.root, self.database_dir, self.books_dir, self.backups_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)

