from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest

from app.config import AppPaths
from app.database import ArchiveRepository, Database
from app.database.schema import initialize_schema
from app.pdf import PdfService
from app.services import LibraryService


@pytest.fixture
def archive(tmp_path: Path):
    root = tmp_path / "BobArchiveLibrary"
    paths = AppPaths(root, root / "database", root / "books", root / "backups", root / "logs", root / "database" / "bob_archive.sqlite3", root / "logs" / "bob_archive.log")
    paths.create()
    database = Database(paths.database_file)
    initialize_schema(database)
    repository = ArchiveRepository(database)
    pdf = PdfService()
    library = LibraryService(paths, repository, pdf)
    return paths, repository, pdf, library


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    document = fitz.open()
    for number in range(1, 4):
        page = document.new_page(width=300, height=400)
        page.insert_text((72, 72), f"Page {number}", fontsize=22)
    document.save(path)
    document.close()
    return path
