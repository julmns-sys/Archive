from __future__ import annotations

import pytest

from app.ui.main_window import DeleteBooksDialog


def _import_books(archive, sample_pdf):
    _paths, repository, _pdf, library = archive
    storage = repository.create_storage_place("Bookcase", 1, "Bookcase 01", "BC01", "Porch")
    first = library.import_book("First", storage, None, [], "", sample_pdf)
    second = library.import_book("Second", storage, None, [], "", sample_pdf)
    return repository, library, first, second


def test_multiple_books_and_their_files_are_deleted(archive, sample_pdf):
    repository, library, first, second = _import_books(archive, sample_pdf)
    directories = [library.book_directory(first), library.book_directory(second)]

    library.delete_books([first.id, second.id])

    assert repository.list_books() == []
    assert all(not directory.exists() for directory in directories)


def test_failed_database_delete_restores_book_files(archive, sample_pdf, monkeypatch):
    repository, library, first, second = _import_books(archive, sample_pdf)
    directories = [library.book_directory(first), library.book_directory(second)]

    def fail_delete(_book_ids):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(repository, "delete_books", fail_delete)
    with pytest.raises(RuntimeError, match="simulated database failure"):
        library.delete_books([first.id, second.id])

    assert {book.id for book in repository.list_books()} == {first.id, second.id}
    assert all(directory.exists() for directory in directories)


def test_delete_confirmation_requires_lowercase_bob(archive, sample_pdf, qtbot):
    _repository, _library, first, _second = _import_books(archive, sample_pdf)
    dialog = DeleteBooksDialog([first])
    qtbot.addWidget(dialog)

    dialog.confirmation.setText("Bob")
    assert not dialog.delete_button.isEnabled()
    dialog.confirmation.setText("bob")
    assert dialog.delete_button.isEnabled()
