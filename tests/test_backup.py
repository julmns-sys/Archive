from __future__ import annotations

import json

import pytest

from app.services import BackupService


def test_backup_contains_complete_readable_archive(archive, sample_pdf, tmp_path):
    paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch", ["Shelf 1"])
    category = repository.create_category("Characters")
    tag = repository.create_tag("Drawings")
    shelf = repository.get_storage_place(storage).shelves[0].id
    library.import_book("Spindrome", storage, shelf, [category], "Need to rescan page 18.", sample_pdf, tag_ids=[tag])
    service = BackupService(paths, repository, library)
    backup = service.create(tmp_path / "destination")
    manifest = json.loads((backup / "backup_manifest.json").read_text())
    assert manifest["book_count"] == 1
    book_dir = next((backup / "Books").iterdir())
    assert (book_dir / "book.pdf").is_file()
    assert (book_dir / "original.pdf").is_file()
    assert len(list((book_dir / "Sources").glob("*.pdf"))) == 1
    assert json.loads((book_dir / "metadata.json").read_text())["book_code"] == "BC01-B01"
    assert json.loads((book_dir / "metadata.json").read_text())["tags"] == ["Drawings"]
    assert json.loads((backup / "Tags" / "tags.json").read_text()) == [{"name": "Drawings"}]


def test_backup_file_restores_complete_library(archive, sample_pdf, tmp_path):
    paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch", ["Shelf 1"])
    category = repository.create_category("Characters")
    tag = repository.create_tag("Drawings")
    shelf = repository.get_storage_place(storage).shelves[0].id
    original = library.import_book("Spindrome", storage, shelf, [category], "A note", sample_pdf, tag_ids=[tag])
    service = BackupService(paths, repository, library)
    backup_file = service.create_file(tmp_path / "library.bobbackup")

    library.delete_books([original.id])
    repository.delete_category(category)
    repository.delete_tag(tag)
    assert repository.list_books() == []

    restored_count = service.restore(backup_file)

    assert restored_count == 1
    restored = repository.list_books()[0]
    assert restored.title == "Spindrome"
    assert [item.name for item in restored.categories] == ["Characters"]
    assert [item.name for item in restored.tags] == ["Drawings"]
    assert library.absolute(restored.current_pdf_path).is_file()
    assert library.thumbnail(restored).is_file()


def test_invalid_backup_does_not_change_library(archive, sample_pdf, tmp_path):
    paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Cabinet", 1, "Cabinet 01", "CB01", "Office", [])
    book = library.import_book("Keep Me", storage, None, [], "", sample_pdf)
    invalid = tmp_path / "damaged.bobbackup"
    invalid.write_bytes(b"not a backup")

    with pytest.raises(ValueError):
        BackupService(paths, repository, library).restore(invalid)

    assert repository.get_book(book.id).title == "Keep Me"
    assert library.absolute(repository.get_book(book.id).current_pdf_path).is_file()


def test_backup_restores_a_book_without_a_location(archive, sample_pdf, tmp_path):
    paths, repository, _pdf, library = archive
    book = library.import_book("Digital photos", None, None, [], "", sample_pdf)
    service = BackupService(paths, repository, library)
    backup_file = service.create_file(tmp_path / "digital-library.bobbackup")
    library.delete_books([book.id])

    assert service.restore(backup_file) == 1

    restored = repository.list_books()[0]
    assert restored.storage_place_id is None
    assert restored.location == "No location"
    assert restored.book_code == "NL-B01"
