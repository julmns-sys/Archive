from __future__ import annotations

import io
import json

import pymupdf as fitz
import pytest
from PIL import Image


def test_import_copies_original_and_writes_metadata(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    book = library.import_book("Spindrome", storage, None, [], "Note", sample_pdf)
    original = library.absolute(book.original_pdf_path)
    current = library.absolute(book.current_pdf_path)
    assert original.read_bytes() == sample_pdf.read_bytes()
    assert current.is_file()
    assert (library.book_directory(book) / "metadata.json").is_file()
    assert library.thumbnail(book, 1).is_file()


def test_reorder_and_rotation_persist(archive, sample_pdf):
    _paths, repository, pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    book = library.import_book("Pages", storage, None, [], "", sample_pdf)
    current = library.absolute(book.current_pdf_path)
    pdf.rebuild(current, [2, 0, 1], {2: 90})
    with fitz.open(current) as document:
        assert document.page_count == 3
        assert "Page 3" in document[0].get_text()
        assert document[0].rotation == 90


def test_photos_can_be_inserted_as_pdf_pages(archive, sample_pdf, tmp_path):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    book = library.import_book("Pages", storage, None, [], "", sample_pdf)
    photo = tmp_path / "photo.png"
    Image.new("RGB", (800, 600), (220, 30, 30)).save(photo)

    library.add_image_pages(book.id, [photo], after_page_index=0)

    with fitz.open(library.absolute(book.current_pdf_path)) as current:
        assert current.page_count == 4
        assert "Page 1" in current[0].get_text()
        assert current[1].get_images()
        assert "Page 2" in current[2].get_text()
    with fitz.open(library.absolute(book.original_pdf_path)) as original:
        assert original.page_count == 3
    assert library.page_origins(book) == [0, None, 1, 2]
    assert library.thumbnail(book, 4).is_file()


def test_multiple_photos_are_inserted_in_selected_order(archive, sample_pdf, tmp_path):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    book = library.import_book("Pages", storage, None, [], "", sample_pdf)
    red_photo = tmp_path / "red.png"
    blue_photo = tmp_path / "blue.png"
    Image.new("RGB", (400, 600), (240, 20, 20)).save(red_photo)
    Image.new("RGB", (400, 600), (20, 20, 240)).save(blue_photo)

    library.add_image_pages(book.id, [red_photo, blue_photo], after_page_index=2)

    with fitz.open(library.absolute(book.current_pdf_path)) as current:
        assert current.page_count == 5
        colors = []
        for page_index in (3, 4):
            image_info = current[page_index].get_images(full=True)[0]
            image = Image.open(io.BytesIO(current.extract_image(image_info[0])["image"]))
            colors.append(image.convert("RGB").resize((1, 1)).getpixel((0, 0)))
    assert colors[0][0] > colors[0][2]
    assert colors[1][2] > colors[1][0]
    assert library.page_origins(book) == [0, 1, 2, None, None]


def test_reset_uses_original_page_identity_after_reorder(archive, sample_pdf):
    _paths, repository, pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    book = library.import_book("Pages", storage, None, [], "", sample_pdf)
    current = library.absolute(book.current_pdf_path)
    origins = library.page_origins(book)
    order = [2, 0, 1]
    pdf.rebuild(current, order)
    library.write_page_origins(book, [origins[index] for index in order])
    # Reset current page zero: it must remain original Page 3, not original Page 1.
    pdf.reset_page(library.absolute(book.original_pdf_path), current, 0, library.page_origins(book)[0])
    with fitz.open(current) as document:
        assert "Page 3" in document[0].get_text()


def test_failed_import_removes_database_record_and_partial_files(archive, sample_pdf, monkeypatch):
    paths, repository, pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")

    def fail_thumbnails(*_args, **_kwargs):
        raise RuntimeError("simulated thumbnail failure")

    monkeypatch.setattr(pdf, "generate_thumbnails", fail_thumbnails)
    with pytest.raises(RuntimeError, match="simulated"):
        library.import_book("Incomplete", storage, None, [], "", sample_pdf)
    assert repository.list_books() == []
    assert list(paths.books_dir.iterdir()) == []


def test_multiple_pdfs_are_combined_in_selected_order_and_sources_preserved(archive, sample_pdf, tmp_path):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    second_pdf = tmp_path / "second scan.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=400)
    page.insert_text((72, 72), "Second PDF page")
    document.save(second_pdf)
    document.close()

    book = library.import_book("Combined", storage, None, [], "", [second_pdf, sample_pdf])
    with fitz.open(library.absolute(book.current_pdf_path)) as combined:
        assert combined.page_count == 4
        assert "Second PDF page" in combined[0].get_text()
        assert "Page 1" in combined[1].get_text()

    sources = sorted((library.book_directory(book) / "original" / "sources").glob("*.pdf"))
    assert len(sources) == 2
    assert sources[0].read_bytes() == second_pdf.read_bytes()
    assert sources[1].read_bytes() == sample_pdf.read_bytes()
    metadata = json.loads((library.book_directory(book) / "metadata.json").read_text())
    assert len(metadata["source_pdfs"]) == 2


def test_book_can_be_created_from_photos_and_pdfs(archive, sample_pdf, tmp_path):
    _paths, repository, _pdf, library = archive
    photo = tmp_path / "first page.png"
    Image.new("RGB", (700, 1000), (30, 180, 80)).save(photo)

    book = library.import_book("Mixed sources", None, None, [], "", [photo, sample_pdf])

    with fitz.open(library.absolute(book.original_pdf_path)) as original:
        assert original.page_count == 4
        assert original[0].get_images()
        assert "Page 1" in original[1].get_text()
    source_files = sorted((library.book_directory(book) / "original" / "sources").iterdir())
    assert [path.suffix for path in source_files] == [".png", ".pdf"]
    metadata = json.loads((library.book_directory(book) / "metadata.json").read_text())
    assert len(metadata["source_files"]) == 2


def test_pdf_and_photo_can_be_inserted_as_pages_together(archive, sample_pdf, tmp_path):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    book = library.import_book("Pages", storage, None, [], "", sample_pdf)
    photo = tmp_path / "extra.jpg"
    Image.new("RGB", (900, 600), (200, 80, 30)).save(photo)

    added = library.add_files_as_pages(book.id, [sample_pdf, photo], after_page_index=0)

    assert added == 4
    with fitz.open(library.absolute(book.current_pdf_path)) as current:
        assert current.page_count == 7
        assert "Page 1" in current[0].get_text()
        assert "Page 1" in current[1].get_text()
        assert "Page 3" in current[3].get_text()
        assert current[4].get_images()
        assert "Page 2" in current[5].get_text()
    assert library.page_origins(book) == [0, None, None, None, None, 1, 2]


def test_generated_cover_is_prepended_and_keeps_original_unchanged(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")

    book = library.import_book("Название книги", storage, None, [], "", sample_pdf, cover_page=2)

    with fitz.open(library.absolute(book.original_pdf_path)) as original:
        assert original.page_count == 3
        assert "Page 1" in original[0].get_text()
    with fitz.open(library.absolute(book.current_pdf_path)) as current:
        assert current.page_count == 4
        assert "Название книги" in current[0].get_text()
        assert current[0].get_images()
        assert "Page 1" in current[1].get_text()
    assert library.page_origins(book) == [None, 0, 1, 2]


def test_renaming_book_updates_generated_cover_title(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    book = library.import_book("Old title", storage, None, [], "", sample_pdf, cover_page=1)

    library.update_book_info(book.id, "New title", "", [], [])

    with fitz.open(library.absolute(book.current_pdf_path)) as current:
        assert "New title" in current[0].get_text()
        assert "Old title" not in current[0].get_text()
        assert "Page 1" in current[1].get_text()
    assert library.page_origins(book) == [None, 0, 1, 2]


def test_cover_photo_can_be_changed_or_added(archive, sample_pdf, tmp_path):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    book = library.import_book("No cover yet", storage, None, [], "", sample_pdf)
    red_photo = tmp_path / "red.png"
    Image.new("RGB", (640, 480), (240, 20, 20)).save(red_photo)

    library.update_book_info(book.id, "Photo cover", "", [], [], red_photo)

    with fitz.open(library.absolute(book.current_pdf_path)) as current:
        assert current.page_count == 4
        assert "Photo cover" in current[0].get_text()
        image_info = max(current[0].get_images(full=True), key=lambda item: item[2] * item[3])
        extracted = current.extract_image(image_info[0])["image"]
    photo = Image.open(io.BytesIO(extracted)).convert("RGB").resize((1, 1))
    red, green, blue = photo.getpixel((0, 0))
    assert red > green * 4 and red > blue * 4
    assert library.page_origins(book) == [None, 0, 1, 2]

    blue_photo = tmp_path / "blue.jpg"
    Image.new("RGB", (480, 640), (20, 20, 240)).save(blue_photo)
    library.update_book_info(book.id, "Photo cover", "", [], [], blue_photo)

    with fitz.open(library.absolute(book.current_pdf_path)) as current:
        assert current.page_count == 4
        image_info = max(current[0].get_images(full=True), key=lambda item: item[2] * item[3])
        extracted = current.extract_image(image_info[0])["image"]
    photo = Image.open(io.BytesIO(extracted)).convert("RGB").resize((1, 1))
    red, green, blue = photo.getpixel((0, 0))
    assert blue > red * 4 and blue > green * 4


def test_cover_photo_can_be_selected_from_a_book_page(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    book = library.import_book("Page photo", storage, None, [], "", sample_pdf)

    library.update_book_info(book.id, "Page photo", "", [], [], cover_page=2)

    with fitz.open(library.absolute(book.current_pdf_path)) as current:
        assert current.page_count == 4
        assert "Page photo" in current[0].get_text()
        assert current[0].get_images()
        assert "Page 2" in current[2].get_text()
    assert library.page_origins(book) == [None, 0, 1, 2]


def test_editing_book_reports_progress(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    book = library.import_book("Before", storage, None, [], "", sample_pdf)
    updates: list[tuple[int, str]] = []

    library.update_book_info(book.id, "After", "", [], [], progress=lambda value, message: updates.append((value, message)))

    assert updates[0] == (5, "Preparing changes")
    assert updates[-1] == (100, "Changes applied")


def test_import_cover_can_use_an_uploaded_image(archive, sample_pdf, tmp_path):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    green_photo = tmp_path / "green.png"
    Image.new("RGB", (640, 480), (20, 240, 20)).save(green_photo)

    book = library.import_book("Uploaded photo", storage, None, [], "", sample_pdf, cover_image=green_photo)

    with fitz.open(library.absolute(book.current_pdf_path)) as current:
        image_info = max(current[0].get_images(full=True), key=lambda item: item[2] * item[3])
        extracted = current.extract_image(image_info[0])["image"]
    photo = Image.open(io.BytesIO(extracted)).convert("RGB").resize((1, 1))
    red, green, blue = photo.getpixel((0, 0))
    assert green > red * 4 and green > blue * 4
    assert library.page_origins(book) == [None, 0, 1, 2]


def test_single_page_thumbnail_refresh_does_not_regenerate_the_book(archive, sample_pdf, monkeypatch):
    _paths, repository, pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    book = library.import_book("Thumbnails", storage, None, [], "", sample_pdf)
    requested: list[int] = []
    generate_selected = pdf.generate_thumbnails_for_pages

    def record_selected(pdf_path, directory, page_indices, progress=None):
        requested.extend(page_indices)
        return generate_selected(pdf_path, directory, page_indices, progress)

    def fail_full_refresh(*_args, **_kwargs):
        raise AssertionError("Full thumbnail generation should not run")

    monkeypatch.setattr(pdf, "generate_thumbnails_for_pages", record_selected)
    monkeypatch.setattr(pdf, "generate_thumbnails", fail_full_refresh)

    library.refresh_page_thumbnails(book.id, [1])

    assert requested == [1]
    assert library.thumbnail(book, 1).is_file()
    assert library.thumbnail(book, 2).is_file()
    assert library.thumbnail(book, 3).is_file()
