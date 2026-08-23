from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.config import AppPaths
from app.database import ArchiveRepository
from app.database.connection import Database
from app.database.schema import initialize_schema
from app.services.library import LibraryService
from app.storage import book_directory_name
from app.utilities.atomic import atomic_write_json

LOGGER = logging.getLogger(__name__)
Progress = Callable[[int, str], None]


class BackupService:
    def __init__(self, paths: AppPaths, repository: ArchiveRepository, library: LibraryService):
        self.paths = paths
        self.repository = repository
        self.library = library

    def create(self, destination: Path, progress: Progress | None = None) -> Path:
        destination = destination.expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        notify = progress or (lambda _value, _message: None)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        final = destination / f"BobArchive_Backup_{timestamp}"
        if final.exists():
            raise FileExistsError(f"A backup named {final.name} already exists.")
        temporary = Path(tempfile.mkdtemp(prefix=f".{final.name}-", dir=destination))
        try:
            books = self.repository.list_books()
            notify(3, "Copying catalog")
            self._snapshot_database(temporary / "catalog.sqlite3")
            storage = self.repository.list_storage_places()
            categories = self.repository.list_categories()
            tags = self.repository.list_tags()
            atomic_write_json(
                temporary / "StoragePlaces" / "storage_places.json",
                [{"type": item.type, "number": item.number, "display_name": item.display_name, "code": item.code, "physical_location": item.physical_location, "shelves": [shelf.name for shelf in item.shelves]} for item in storage],
            )
            atomic_write_json(temporary / "Categories" / "categories.json", [{"name": item.name} for item in categories])
            atomic_write_json(temporary / "Tags" / "tags.json", [{"name": item.name} for item in tags])
            manifest_books = []
            for index, book in enumerate(books, 1):
                notify(5 + int(85 * index / max(1, len(books))), f"Copying books ({index}/{len(books)})")
                target = temporary / "Books" / book_directory_name(book.book_code, book.title)
                target.mkdir(parents=True, exist_ok=True)
                current = self.library.absolute(book.current_pdf_path)
                original = self.library.absolute(book.original_pdf_path)
                shutil.copy2(current, target / "book.pdf")
                shutil.copy2(original, target / "original.pdf")
                source_directory = self.library.book_directory(book) / "original" / "sources"
                source_manifest = []
                if source_directory.is_dir():
                    shutil.copytree(source_directory, target / "Sources")
                    source_manifest = [
                        {"filename": path.name, "sha256": self._sha256(path)}
                        for path in sorted((target / "Sources").iterdir()) if path.is_file()
                    ]
                metadata_source = self.library.book_directory(book) / "metadata.json"
                # Refresh the readable sidecar so older archives gain all current fields.
                self.library.write_metadata(book.id)
                shutil.copy2(metadata_source, target / "metadata.json")
                page_state = self.library.book_directory(book) / "page_state.json"
                if page_state.exists():
                    shutil.copy2(page_state, target / "page_state.json")
                manifest_books.append({
                    "folder": target.name,
                    "book_code": book.book_code,
                    "title": book.title,
                    "pdf_sha256": self._sha256(target / "book.pdf"),
                    "source_pdfs": source_manifest,
                })
            manifest = {
                "format": "Bob Archive Backup",
                "format_version": 1,
                "created_at": datetime.now().astimezone().isoformat(),
                "book_count": len(books),
                "storage_place_count": len(storage),
                "category_count": len(categories),
                "tag_count": len(tags),
                "books": manifest_books,
            }
            atomic_write_json(temporary / "backup_manifest.json", manifest)
            self._verify(temporary, len(books))
            notify(96, "Finalizing backup")
            os.replace(temporary, final)
            notify(100, "Backup completed successfully")
            return final
        except BaseException:
            LOGGER.exception("Backup failed")
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def create_file(self, destination: Path, progress: Progress | None = None) -> Path:
        """Create a compact portable backup without staging a second library.

        The .bobbackup extension is deliberately a regular ZIP archive so the
        contents remain accessible even without Bob Archive. Version 2 omits
        the derived original.pdf files: they are rebuilt from the untouched
        source files during restore. Current PDFs, source files, and readable
        metadata are all preserved.
        """
        destination = destination.expanduser().resolve()
        if destination.suffix.lower() != ".bobbackup":
            destination = destination.with_name(destination.name + ".bobbackup")
        destination.parent.mkdir(parents=True, exist_ok=True)
        notify = progress or (lambda _value, _message: None)
        temporary_file = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        catalog_snapshot = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.sqlite3.tmp")
        try:
            books = self.repository.list_books()
            storage = self.repository.list_storage_places()
            categories = self.repository.list_categories()
            tags = self.repository.list_tags()
            notify(2, "Copying catalog")
            self._snapshot_database(catalog_snapshot)
            manifest_books = []
            with zipfile.ZipFile(temporary_file, "w", allowZip64=True) as archive:
                self._write_archive_file(archive, catalog_snapshot, "catalog.sqlite3")
                self._write_archive_json(
                    archive,
                    "StoragePlaces/storage_places.json",
                    [{"type": item.type, "number": item.number, "display_name": item.display_name, "code": item.code, "physical_location": item.physical_location, "shelves": [shelf.name for shelf in item.shelves]} for item in storage],
                )
                self._write_archive_json(archive, "Categories/categories.json", [{"name": item.name} for item in categories])
                self._write_archive_json(archive, "Tags/tags.json", [{"name": item.name} for item in tags])

                for index, book in enumerate(books, 1):
                    notify(4 + int(91 * index / max(1, len(books))), f"Packing books ({index}/{len(books)})")
                    folder = book_directory_name(book.book_code, book.title)
                    prefix = f"Books/{folder}"
                    current = self.library.absolute(book.current_pdf_path)
                    pdf_hash = self._write_archive_file(archive, current, f"{prefix}/book.pdf")
                    source_directory = self.library.book_directory(book) / "original" / "sources"
                    source_manifest = []
                    if source_directory.is_dir():
                        for path in sorted(source_directory.iterdir()):
                            if not path.is_file():
                                continue
                            source_manifest.append({
                                "filename": path.name,
                                "sha256": self._write_archive_file(archive, path, f"{prefix}/Sources/{path.name}"),
                            })
                    if not source_manifest:
                        raise IOError(f"Cannot create a compact backup because {book.book_code} has no preserved source files.")
                    # Refresh the readable sidecar so older libraries gain all current fields.
                    self.library.write_metadata(book.id)
                    book_root = self.library.book_directory(book)
                    self._write_archive_file(archive, book_root / "metadata.json", f"{prefix}/metadata.json")
                    page_state = book_root / "page_state.json"
                    if page_state.exists():
                        self._write_archive_file(archive, page_state, f"{prefix}/page_state.json")
                    manifest_books.append({
                        "folder": folder,
                        "book_code": book.book_code,
                        "title": book.title,
                        "pdf_sha256": pdf_hash,
                        "source_files": source_manifest,
                        "original_storage": "rebuild_from_sources",
                    })

                manifest = {
                    "format": "Bob Archive Backup",
                    "format_version": 2,
                    "created_at": datetime.now().astimezone().isoformat(),
                    "book_count": len(books),
                    "storage_place_count": len(storage),
                    "category_count": len(categories),
                    "tag_count": len(tags),
                    "books": manifest_books,
                }
                self._write_archive_json(archive, "backup_manifest.json", manifest)
            os.replace(temporary_file, destination)
            notify(100, "Backup completed successfully")
            return destination
        except BaseException:
            LOGGER.exception("Backup file creation failed")
            temporary_file.unlink(missing_ok=True)
            raise
        finally:
            catalog_snapshot.unlink(missing_ok=True)

    def restore(self, source: Path, progress: Progress | None = None) -> int:
        """Replace the active library with a verified backup folder or file."""
        source = source.expanduser().resolve()
        notify = progress or (lambda _value, _message: None)
        workspace = Path(tempfile.mkdtemp(prefix=".bob-restore-", dir=self.paths.root.parent))
        try:
            notify(2, "Opening backup")
            backup_root = self._open_backup(source, workspace / "unpacked")
            move_extracted_files = source.is_file()
            manifest = self._read_and_verify_manifest(backup_root)
            notify(12, "Checking backup")
            self._verify(backup_root, int(manifest["book_count"]), int(manifest["format_version"]))

            staged_root = workspace / "library"
            staged_database = staged_root / "database" / self.paths.database_file.name
            staged_books = staged_root / "books"
            staged_database.parent.mkdir(parents=True, exist_ok=True)
            staged_books.mkdir(parents=True, exist_ok=True)
            self._transfer_file(backup_root / "catalog.sqlite3", staged_database, move_extracted_files)
            staged_db = Database(staged_database)
            initialize_schema(staged_db)
            restored_repository = ArchiveRepository(staged_db)
            books = restored_repository.list_books()
            if len(books) != manifest["book_count"]:
                raise IOError("Backup catalog and manifest contain different book counts.")

            entries = {entry["book_code"]: entry for entry in manifest.get("books", [])}
            for index, book in enumerate(books, 1):
                notify(15 + int(70 * index / max(1, len(books))), f"Restoring books ({index}/{len(books)})")
                entry = entries.get(book.book_code)
                if entry is None:
                    raise IOError(f"Backup is missing the manifest entry for {book.book_code}.")
                source_directory = backup_root / "Books" / self._safe_component(entry["folder"])
                current_target = self._safe_relative_target(staged_root, book.current_pdf_path)
                original_target = self._safe_relative_target(staged_root, book.original_pdf_path)
                if current_target.parent.parent != original_target.parent.parent:
                    raise IOError(f"Invalid catalog paths for {book.book_code}.")
                current_target.parent.mkdir(parents=True, exist_ok=True)
                original_target.parent.mkdir(parents=True, exist_ok=True)
                self._transfer_file(source_directory / "book.pdf", current_target, move_extracted_files)
                sources = source_directory / "Sources"
                if sources.is_dir():
                    if move_extracted_files:
                        os.replace(sources, original_target.parent / "sources")
                    else:
                        shutil.copytree(sources, original_target.parent / "sources")
                if manifest["format_version"] == 1:
                    self._transfer_file(source_directory / "original.pdf", original_target, move_extracted_files)
                else:
                    self._rebuild_original(original_target.parent / "sources", original_target)
                book_root = current_target.parent.parent
                for name in ("metadata.json", "page_state.json"):
                    if (source_directory / name).is_file():
                        self._transfer_file(source_directory / name, book_root / name, move_extracted_files)
                self.library.pdf.generate_thumbnails(current_target, book_root / "thumbnails")

            self._validate_staged_library(staged_database, staged_root, len(books))
            notify(90, "Activating restored library")
            self._activate(staged_database, staged_books)
            notify(100, "Library restored successfully")
            return len(books)
        except BaseException:
            LOGGER.exception("Backup restore failed")
            raise
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def _open_backup(self, source: Path, destination: Path) -> Path:
        if source.is_dir():
            return self._locate_backup_root(source)
        if not source.is_file() or not zipfile.is_zipfile(source):
            raise ValueError("Please choose a Bob Archive .bobbackup file or backup folder.")
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source) as archive:
            root = destination.resolve()
            for info in archive.infolist():
                target = (destination / info.filename).resolve()
                if target != root and root not in target.parents:
                    raise ValueError("The backup contains an unsafe file path.")
            archive.extractall(destination)
        return self._locate_backup_root(destination)

    @staticmethod
    def _locate_backup_root(root: Path) -> Path:
        if (root / "backup_manifest.json").is_file():
            return root
        children = [path for path in root.iterdir() if path.is_dir()]
        if len(children) == 1 and (children[0] / "backup_manifest.json").is_file():
            return children[0]
        raise ValueError("This is not a Bob Archive backup.")

    @staticmethod
    def _read_and_verify_manifest(root: Path) -> dict:
        try:
            with (root / "backup_manifest.json").open(encoding="utf-8") as stream:
                manifest = json.load(stream)
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("The backup manifest is missing or damaged.") from error
        if manifest.get("format") != "Bob Archive Backup" or manifest.get("format_version") not in (1, 2):
            raise ValueError("This backup format is not supported.")
        if not isinstance(manifest.get("book_count"), int) or manifest["book_count"] < 0:
            raise ValueError("The backup manifest contains an invalid book count.")
        if not isinstance(manifest.get("books"), list):
            raise ValueError("The backup manifest contains an invalid book list.")
        return manifest

    @staticmethod
    def _safe_component(value: object) -> str:
        if not isinstance(value, str) or not value or Path(value).name != value or value in (".", ".."):
            raise ValueError("The backup contains an unsafe book folder name.")
        return value

    @staticmethod
    def _safe_relative_target(root: Path, value: str) -> Path:
        relative = Path(value)
        if relative.is_absolute():
            raise ValueError("The backup catalog contains an absolute file path.")
        target = (root / relative).resolve()
        resolved_root = root.resolve()
        if target == resolved_root or resolved_root not in target.parents:
            raise ValueError("The backup catalog contains an unsafe file path.")
        return target

    @staticmethod
    def _validate_staged_library(database: Path, root: Path, expected_books: int) -> None:
        connection = sqlite3.connect(database)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise IOError(f"The restored catalog failed its integrity check: {result}")
            rows = connection.execute("SELECT original_pdf_path, current_pdf_path FROM books").fetchall()
        finally:
            connection.close()
        if len(rows) != expected_books:
            raise IOError("The restored catalog has the wrong number of books.")
        for original, current in rows:
            for value in (original, current):
                target = BackupService._safe_relative_target(root, value)
                if not target.is_file():
                    raise IOError(f"A restored PDF is missing: {value}")

    def _activate(self, staged_database: Path, staged_books: Path) -> None:
        token = uuid.uuid4().hex
        old_database = self.paths.database_dir / f".before-restore-{token}.sqlite3"
        old_books = self.paths.root / f".books-before-restore-{token}"
        database_moved = False
        books_moved = False
        new_database_moved = False
        new_books_moved = False
        try:
            connection = self.repository.database.connect()
            try:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                connection.close()
            for suffix in ("-wal", "-shm"):
                Path(str(self.paths.database_file) + suffix).unlink(missing_ok=True)
            if self.paths.database_file.exists():
                os.replace(self.paths.database_file, old_database)
                database_moved = True
            if self.paths.books_dir.exists():
                os.replace(self.paths.books_dir, old_books)
                books_moved = True
            os.replace(staged_database, self.paths.database_file)
            new_database_moved = True
            os.replace(staged_books, self.paths.books_dir)
            new_books_moved = True
        except BaseException:
            if new_books_moved and self.paths.books_dir.exists():
                shutil.rmtree(self.paths.books_dir, ignore_errors=True)
            if new_database_moved:
                self.paths.database_file.unlink(missing_ok=True)
            if books_moved and old_books.exists():
                os.replace(old_books, self.paths.books_dir)
            if database_moved and old_database.exists():
                os.replace(old_database, self.paths.database_file)
            raise
        else:
            try:
                old_database.unlink(missing_ok=True)
                if old_books.exists():
                    shutil.rmtree(old_books)
            except OSError:
                # The restored library is already active. Failure to remove the
                # private rollback copy must not be reported as a restore failure.
                LOGGER.exception("Could not remove the pre-restore rollback copy")

    def _snapshot_database(self, target: Path) -> None:
        source = self.repository.database.connect()
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

    def _rebuild_original(self, source_directory: Path, destination: Path) -> None:
        sources = sorted(path for path in source_directory.iterdir() if path.is_file())
        if not sources:
            raise IOError("A compact backup is missing the source files needed to rebuild an original PDF.")
        prepared_directory = Path(tempfile.mkdtemp(prefix=".rebuild-original-", dir=destination.parent))
        prepared: list[Path] = []
        try:
            for index, source in enumerate(sources, 1):
                if source.suffix.lower() == ".pdf":
                    self.library.pdf.validate(source)
                    prepared.append(source)
                else:
                    converted = prepared_directory / f"source_{index:04d}.pdf"
                    self.library.pdf.image_to_pdf(source, converted)
                    prepared.append(converted)
            if len(prepared) == 1:
                self.library.pdf.copy_pdf(prepared[0], destination)
            else:
                self.library.pdf.merge_pdfs(prepared, destination)
        finally:
            shutil.rmtree(prepared_directory, ignore_errors=True)

    @staticmethod
    def _transfer_file(source: Path, destination: Path, move: bool) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if move:
            os.replace(source, destination)
        else:
            shutil.copy2(source, destination)

    @staticmethod
    def _write_archive_json(archive: zipfile.ZipFile, name: str, data: object) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        archive.writestr(name, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)

    @staticmethod
    def _write_archive_file(archive: zipfile.ZipFile, source: Path, name: str) -> str:
        """Stream one file into a ZIP while calculating its checksum."""
        info = zipfile.ZipInfo.from_file(source, arcname=name)
        # Scanned PDFs and raster images are already compressed. Storing them
        # avoids hours of ineffective recompression on large libraries.
        if source.suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            info.compress_type = zipfile.ZIP_STORED
        else:
            info.compress_type = zipfile.ZIP_DEFLATED
        digest = hashlib.sha256()
        with source.open("rb") as incoming, archive.open(info, "w", force_zip64=True) as outgoing:
            while chunk := incoming.read(1024 * 1024):
                digest.update(chunk)
                outgoing.write(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _verify(root: Path, expected_books: int, format_version: int = 1) -> None:
        required = [root / "catalog.sqlite3", root / "backup_manifest.json", root / "StoragePlaces" / "storage_places.json", root / "Categories" / "categories.json", root / "Tags" / "tags.json"]
        if any(not path.is_file() for path in required):
            raise IOError("Backup verification failed: a required catalog file is missing.")
        with (root / "backup_manifest.json").open(encoding="utf-8") as stream:
            manifest = json.load(stream)
        if manifest.get("book_count") != expected_books:
            raise IOError("Backup verification failed: the book count is incorrect.")
        book_directories = list((root / "Books").iterdir()) if (root / "Books").exists() else []
        if len(book_directories) != expected_books:
            raise IOError("Backup verification failed: not every book was copied.")
        for directory in book_directories:
            required_book_files = ["book.pdf", "metadata.json"]
            if format_version == 1:
                required_book_files.append("original.pdf")
            if not all((directory / name).is_file() for name in required_book_files):
                raise IOError(f"Backup verification failed for {directory.name}.")
        for entry in manifest.get("books", []):
            directory = root / "Books" / entry["folder"]
            if BackupService._sha256(directory / "book.pdf") != entry["pdf_sha256"]:
                raise IOError(f"Backup verification failed: PDF checksum mismatch for {entry['book_code']}.")
            sources = entry.get("source_files", []) if format_version == 2 else entry.get("source_pdfs", [])
            if format_version == 2 and not sources:
                raise IOError(f"Backup verification failed: no source files for {entry['book_code']}.")
            for source in sources:
                source_path = directory / "Sources" / source["filename"]
                if not source_path.is_file() or BackupService._sha256(source_path) != source["sha256"]:
                    raise IOError(f"Backup verification failed: source file mismatch for {entry['book_code']}.")
