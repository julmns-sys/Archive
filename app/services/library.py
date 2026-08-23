from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Callable, Iterable

from app.config import AppPaths
from app.database import ArchiveRepository
from app.models import Book
from app.pdf import PdfService
from app.storage import book_directory_name, safe_title
from app.utilities.atomic import atomic_write_json

LOGGER = logging.getLogger(__name__)
Progress = Callable[[int, str], None]


class LibraryService:
    def __init__(self, paths: AppPaths, repository: ArchiveRepository, pdf: PdfService):
        self.paths = paths
        self.repository = repository
        self.pdf = pdf

    def import_book(
        self,
        title: str,
        storage_id: int | None,
        shelf_id: int | None,
        category_ids: Iterable[int],
        notes: str,
        source_pdf: Path | Iterable[Path],
        progress: Progress | None = None,
        tag_ids: Iterable[int] = (),
        cover_page: int | None = None,
        cover_image: Path | None = None,
    ) -> Book:
        title = self.repository.unique_book_title(title)
        sources = [Path(source_pdf)] if isinstance(source_pdf, (str, Path)) else [Path(path) for path in source_pdf]
        if not sources:
            raise ValueError("Please choose at least one PDF or image file.")
        notify = progress or (lambda _amount, _message: None)
        for index, source in enumerate(sources, 1):
            notify(3 + int(7 * index / len(sources)), f"Checking files ({index}/{len(sources)})")
            if source.suffix.lower() == ".pdf":
                self.pdf.validate(source)
            else:
                self.pdf.validate_image(source)
        working = Path(tempfile.mkdtemp(prefix=".import-", dir=self.paths.books_dir))
        finalized: Path | None = None
        book_id: int | None = None
        try:
            original = working / "original" / "original.pdf"
            current = working / "current" / "book.pdf"
            source_directory = working / "original" / "sources"
            copied_sources: list[Path] = []
            prepared_directory = working / ".prepared"
            for index, source in enumerate(sources, 1):
                notify(10 + int(15 * index / len(sources)), f"Preparing source files ({index}/{len(sources)})")
                suffix = source.suffix.lower() or ".image"
                copied = source_directory / f"source_{index:04d}__{safe_title(source.stem)}{suffix}"
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, copied)
                if suffix == ".pdf":
                    copied_sources.append(copied)
                else:
                    prepared = prepared_directory / f"source_{index:04d}.pdf"
                    prepared.parent.mkdir(parents=True, exist_ok=True)
                    self.pdf.image_to_pdf(copied, prepared)
                    copied_sources.append(prepared)
            notify(28, "Combining pages")
            if len(copied_sources) == 1:
                self.pdf.copy_pdf(copied_sources[0], original)
            else:
                self.pdf.merge_pdfs(copied_sources, original)
            shutil.rmtree(prepared_directory, ignore_errors=True)
            self.pdf.copy_pdf(original, current)
            original_page_count = self.pdf.page_count(original)
            if cover_page is not None or cover_image is not None:
                if cover_page is None:
                    cover_page = 1
                if not 1 <= cover_page <= original_page_count:
                    raise ValueError(f"Cover page must be between 1 and {original_page_count}.")
                notify(32, "Generating cover")
                image = self.pdf.prepare_cover_image(cover_image) if cover_image else None
                self.pdf.add_cover(current, title, cover_page - 1, image)
            notify(35, "Saving book")
            with self.repository.database.transaction() as connection:
                book_id, code = self.repository.insert_book(
                    connection, title, storage_id, shelf_id, notes,
                    "PENDING/original/original.pdf", "PENDING/current/book.pdf", category_ids, tag_ids,
                )
                final_name = book_directory_name(code, title)
                finalized = self.paths.books_dir / final_name
                if finalized.exists():
                    raise FileExistsError(f"The archive folder already exists: {finalized.name}")
                os.replace(working, finalized)
                original_relative = str(Path("books") / final_name / "original" / "original.pdf")
                current_relative = str(Path("books") / final_name / "current" / "book.pdf")
                connection.execute("UPDATE books SET original_pdf_path=?,current_pdf_path=? WHERE id=?", (original_relative, current_relative, book_id))
            self.pdf.generate_thumbnails(finalized / "current" / "book.pdf", finalized / "thumbnails", notify)
            origins: list[int | None] = list(range(original_page_count))
            if cover_page is not None:
                origins.insert(0, None)
            atomic_write_json(finalized / "page_state.json", {"original_page_indices": origins})
            self.write_metadata(book_id)
            notify(100, "Book successfully added")
            return self.repository.get_book(book_id)
        except BaseException:
            LOGGER.exception("Book import failed")
            if book_id is not None:
                try:
                    self.repository.delete_book(book_id)
                except Exception:
                    LOGGER.exception("Could not roll back imported book record")
            if finalized and finalized.exists():
                shutil.rmtree(finalized, ignore_errors=True)
            if working.exists():
                shutil.rmtree(working, ignore_errors=True)
            raise

    def absolute(self, relative: str) -> Path:
        path = (self.paths.root / relative).resolve()
        root = self.paths.root.resolve()
        if path != root and root not in path.parents:
            raise ValueError("Archive path points outside the managed library.")
        return path

    def book_directory(self, book: Book) -> Path:
        return self.absolute(book.current_pdf_path).parent.parent

    def write_metadata(self, book_id: int) -> None:
        book = self.repository.get_book(book_id)
        data = {
            "title": book.title,
            "book_code": book.book_code,
            "storage_place": book.storage_display_name or "No location",
            "physical_location": book.physical_location,
            "shelf": book.shelf_name,
            "categories": [category.name for category in book.categories],
            "tags": [tag.name for tag in book.tags],
            "notes": book.notes,
            "original_pdf": "original/original.pdf",
            "source_pdfs": [str(Path("original") / "sources" / path.name) for path in sorted((self.book_directory(book) / "original" / "sources").glob("*.pdf"))],
            "source_files": [str(Path("original") / "sources" / path.name) for path in sorted((self.book_directory(book) / "original" / "sources").iterdir()) if path.is_file()],
            "current_pdf": "current/book.pdf",
            "created_at": book.created_at,
            "updated_at": book.updated_at,
        }
        atomic_write_json(self.book_directory(book) / "metadata.json", data)

    def refresh_thumbnails(self, book_id: int, progress: Progress | None = None) -> None:
        book = self.repository.get_book(book_id)
        directory = self.book_directory(book) / "thumbnails"
        for item in directory.glob("page_*.jpg"):
            item.unlink()
        self.pdf.generate_thumbnails(self.absolute(book.current_pdf_path), directory, progress)
        self.repository.touch_book(book_id)
        self.write_metadata(book_id)

    def refresh_page_thumbnails(
        self,
        book_id: int,
        page_indices: Iterable[int],
        progress: Progress | None = None,
    ) -> None:
        """Regenerate previews for changed pages without touching the rest."""
        indices = list(dict.fromkeys(page_indices))
        if not indices:
            return
        book = self.repository.get_book(book_id)
        directory = self.book_directory(book) / "thumbnails"
        self.pdf.generate_thumbnails_for_pages(
            self.absolute(book.current_pdf_path), directory, indices, progress
        )
        self.repository.touch_book(book_id)
        self.write_metadata(book_id)

    def add_image_pages(
        self,
        book_id: int,
        image_paths: Iterable[Path],
        after_page_index: int,
        progress: Progress | None = None,
    ) -> None:
        self.add_files_as_pages(book_id, image_paths, after_page_index, progress)

    def add_files_as_pages(
        self,
        book_id: int,
        file_paths: Iterable[Path],
        after_page_index: int,
        progress: Progress | None = None,
    ) -> int:
        paths = [Path(path) for path in file_paths]
        if not paths:
            raise ValueError("Please choose at least one PDF or image file.")
        notify = progress or (lambda _amount, _message: None)
        book = self.repository.get_book(book_id)
        current_pdf = self.absolute(book.current_pdf_path)
        page_count = self.pdf.page_count(current_pdf)
        if not 0 <= after_page_index < page_count:
            raise ValueError("Please select an existing page to insert the new pages after.")
        insertion_index = after_page_index + 1
        origins = self.page_origins(book)
        notify(10, "Preparing new pages")
        added_pages = self.pdf.insert_files_as_pages(current_pdf, paths, insertion_index)
        origins[insertion_index:insertion_index] = [None] * added_pages
        self.write_page_origins(book, origins)
        notify(55, "Updating page previews")
        self.refresh_thumbnails(book_id, progress)
        notify(100, "Pages added")
        return added_pages

    def thumbnail(self, book: Book, page: int = 1) -> Path:
        return self.book_directory(book) / "thumbnails" / f"page_{page:04d}.jpg"

    def page_origins(self, book: Book) -> list[int | None]:
        state_path = self.book_directory(book) / "page_state.json"
        if state_path.exists():
            try:
                with state_path.open(encoding="utf-8") as stream:
                    values = json.load(stream).get("original_page_indices", [])
                if len(values) == self.pdf.page_count(self.absolute(book.current_pdf_path)):
                    return [value if isinstance(value, int) else None for value in values]
            except (OSError, ValueError, json.JSONDecodeError):
                LOGGER.warning("Invalid page state for %s; using positional fallback", book.book_code)
        return list(range(self.pdf.page_count(self.absolute(book.current_pdf_path))))

    def write_page_origins(self, book: Book, origins: list[int | None]) -> None:
        atomic_write_json(self.book_directory(book) / "page_state.json", {"original_page_indices": origins})

    def update_book_info(
        self,
        book_id: int,
        title: str,
        notes: str,
        category_ids: Iterable[int],
        tag_ids: Iterable[int] = (),
        cover_image: Path | None = None,
        cover_page: int | None = None,
        progress: Progress | None = None,
    ) -> None:
        notify = progress or (lambda _amount, _message: None)
        notify(5, "Preparing changes")
        book = self.repository.get_book(book_id)
        clean_title = self.repository.unique_book_title(title, exclude_book_id=book_id)
        origins = self.page_origins(book)
        cover_index = next((index for index, origin in enumerate(origins) if origin is None), None)
        if cover_image is not None and cover_page is not None:
            raise ValueError("Choose either a book page or an image file for the cover photo.")
        if cover_page is not None:
            if not 1 <= cover_page <= len(origins):
                raise ValueError(f"Cover page must be between 1 and {len(origins)}.")
            selected_image = self.pdf.cover_image(self.absolute(book.current_pdf_path), cover_page - 1)
        else:
            selected_image = self.pdf.prepare_cover_image(cover_image) if cover_image else None
        cover_changed = selected_image is not None or (cover_index is not None and clean_title != book.title)

        notify(20, "Saving book information")
        self.repository.update_book_info(book_id, clean_title, notes, category_ids, tag_ids)
        try:
            if cover_index is not None and cover_changed:
                notify(35, "Updating cover")
                image = selected_image or self.pdf.cover_image(self.absolute(book.current_pdf_path), cover_index)
                self.pdf.replace_cover(self.absolute(book.current_pdf_path), cover_index, clean_title, image)
            elif selected_image is not None:
                notify(35, "Creating cover")
                self.pdf.add_cover(self.absolute(book.current_pdf_path), clean_title, 0, selected_image)
                origins.insert(0, None)
                self.write_page_origins(book, origins)
        except BaseException:
            # Keep catalog and cover title in agreement when rebuilding the PDF
            # fails after the database update.
            self.repository.update_book_info(
                book_id,
                book.title,
                book.notes,
                [category.id for category in book.categories],
                [tag.id for tag in book.tags],
            )
            raise

        if cover_changed:
            notify(40, "Updating page previews")
            self.refresh_thumbnails(book_id, progress)
        else:
            notify(80, "Updating archive metadata")
            self.write_metadata(book_id)
        notify(100, "Changes applied")

    def move_book(self, book_id: int, storage_id: int | None, shelf_id: int | None) -> tuple[str, str]:
        old, new = self.repository.move_book(book_id, storage_id, shelf_id)
        # Keep the existing directory stable: database paths never become stale after a move.
        self.write_metadata(book_id)
        return old, new

    def delete_books(self, book_ids: Iterable[int]) -> None:
        ids = list(dict.fromkeys(book_ids))
        if not ids:
            return

        books = [self.repository.get_book(book_id) for book_id in ids]
        directories = [self.book_directory(book) for book in books]
        staging = Path(tempfile.mkdtemp(prefix=".delete-", dir=self.paths.books_dir))
        moved: list[tuple[Path, Path]] = []
        try:
            for directory in directories:
                if directory.exists():
                    staged = staging / directory.name
                    os.replace(directory, staged)
                    moved.append((staged, directory))
            self.repository.delete_books(ids)
        except BaseException:
            for staged, original in reversed(moved):
                if staged.exists():
                    os.replace(staged, original)
            shutil.rmtree(staging, ignore_errors=True)
            raise

        try:
            shutil.rmtree(staging)
        except OSError:
            # The books are already absent from the catalog. Leave the hidden
            # staging folder for later manual cleanup instead of reporting a
            # misleading database failure.
            LOGGER.exception("Could not remove staged files for deleted books")
