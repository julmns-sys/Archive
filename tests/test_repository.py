from __future__ import annotations

import sqlite3

import pytest


def test_book_codes_increment_per_storage(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch", ["Shelf 1"])
    shelf = repository.get_storage_place(storage).shelves[0].id
    first = library.import_book("First", storage, shelf, [], "", sample_pdf)
    second = library.import_book("Second", storage, shelf, [], "", sample_pdf)
    assert first.book_code == "BC01-B01"
    assert second.book_code == "BC01-B02"


def test_books_can_be_created_without_a_location(archive, sample_pdf):
    _paths, repository, _pdf, library = archive

    first = library.import_book("Digital photos", None, None, [], "", sample_pdf)
    second = library.import_book("Digital scans", None, None, [], "", sample_pdf)

    assert first.storage_place_id is None
    assert first.location == "No location"
    assert first.book_code == "NL-B01"
    assert second.book_code == "NL-B02"
    assert [book.id for book in repository.list_books(storage_id="no_location")] == [first.id, second.id]


def test_duplicate_book_titles_receive_incrementing_numbers(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")

    first = library.import_book("Atlas", storage, None, [], "", sample_pdf)
    second = library.import_book("atlas", storage, None, [], "", sample_pdf)
    third = library.import_book("Atlas", storage, None, [], "", sample_pdf)

    assert first.title == "Atlas"
    assert second.title == "atlas 2"
    assert third.title == "Atlas 3"


def test_renaming_book_to_duplicate_title_adds_number(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    library.import_book("Atlas", storage, None, [], "", sample_pdf)
    renamed = library.import_book("Dictionary", storage, None, [], "", sample_pdf)

    library.update_book_info(renamed.id, "Atlas", "", [], [])

    assert repository.get_book(renamed.id).title == "Atlas 2"


def test_reusing_a_storage_prefix_does_not_duplicate_an_old_book_code(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    original_storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    original_book = library.import_book("Original", original_storage, None, [], "", sample_pdf)
    repository.update_storage_place(
        original_storage, "Bookcase", 1, "Bookcase 01", "NEW01", "Porch", []
    )
    reused_code_storage = repository.create_storage_place("Cabinet", 1, "Cabinet 01", "BC01", "Office")

    new_book = library.import_book("New", reused_code_storage, None, [], "", sample_pdf)

    assert original_book.book_code == "BC01-B01"
    assert new_book.book_code == "BC01-B02"


def test_database_rejects_duplicate_book_code(archive):
    _paths, repository, _pdf, _library = archive
    storage = repository.create_storage_place("Suitcase", 1, "Suitcase 01", "SC01", "Shed")
    with repository.database.transaction() as connection:
        connection.execute("INSERT INTO books(title,book_code,storage_place_id,notes,original_pdf_path,current_pdf_path) VALUES('One','SC01-B01',?,'','a','b')", (storage,))
    with pytest.raises(sqlite3.IntegrityError):
        with repository.database.transaction() as connection:
            connection.execute("INSERT INTO books(title,book_code,storage_place_id,notes,original_pdf_path,current_pdf_path) VALUES('Two','SC01-B01',?,'','c','d')", (storage,))


def test_deleting_category_does_not_delete_book(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    category = repository.create_category("Characters")
    book = library.import_book("Spindrome", storage, None, [category], "", sample_pdf)
    repository.delete_category(category)
    assert repository.get_book(book.id).title == "Spindrome"
    assert repository.get_book(book.id).categories == []


def test_move_book_updates_code_and_location(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    suitcase = repository.create_storage_place("Suitcase", 1, "Suitcase 01", "SC01", "Shed")
    bookcase = repository.create_storage_place("Bookcase", 2, "Bookcase 02", "BC02", "Porch", ["Shelf 1"])
    book = library.import_book("Spindrome", suitcase, None, [], "", sample_pdf)
    shelf = repository.get_storage_place(bookcase).shelves[0].id
    old, new = library.move_book(book.id, bookcase, shelf)
    moved = repository.get_book(book.id)
    assert (old, new) == ("SC01-B01", "BC02-B01")
    assert moved.book_code == "BC02-B01"
    assert moved.location == "Bookcase 02 (Porch), Shelf 1"


def test_renaming_shelf_keeps_book_assigned(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch", ["Shelf 1"])
    shelf_id = repository.get_storage_place(storage).shelves[0].id
    book = library.import_book("Spindrome", storage, shelf_id, [], "", sample_pdf)

    repository.update_storage_place(storage, "Bookcase", 1, "Bookcase 01", "BC01", "Porch", ["Top shelf"])

    updated = repository.get_book(book.id)
    assert updated.shelf_id == shelf_id
    assert updated.shelf_name == "Top shelf"


def test_removing_shelf_does_not_move_books_to_another_shelf(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place(
        "Bookcase", 1, "Bookcase 01", "BC01", "Porch", ["Top", "Bottom"]
    )
    shelves = repository.get_storage_place(storage).shelves
    top_book = library.import_book("Top book", storage, shelves[0].id, [], "", sample_pdf)
    bottom_book = library.import_book("Bottom book", storage, shelves[1].id, [], "", sample_pdf)

    repository.update_storage_place(storage, "Bookcase", 1, "Bookcase 01", "BC01", "Porch", ["Bottom"])

    assert repository.get_book(top_book.id).shelf_id is None
    assert repository.get_book(bottom_book.id).shelf_id == shelves[1].id


def test_tags_can_be_added_searched_filtered_and_deleted(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    tag = repository.create_tag("Needs Rescan")
    book = library.import_book("Spindrome", storage, None, [], "", sample_pdf, tag_ids=[tag])
    assert [item.name for item in repository.get_book(book.id).tags] == ["Needs Rescan"]
    assert [item.id for item in repository.list_books(search="rescan")] == [book.id]
    assert [item.id for item in repository.list_books(search="#Needs Rescan")] == [book.id]
    assert [item.id for item in repository.list_books(tag_id=tag)] == [book.id]
    repository.delete_tag(tag)
    assert repository.get_book(book.id).title == "Spindrome"
    assert repository.get_book(book.id).tags == []
