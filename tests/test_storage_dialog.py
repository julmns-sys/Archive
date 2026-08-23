from __future__ import annotations

from PySide6.QtCore import Qt
from PIL import Image

from app.ui.dialogs import AddBookDialog, StorageDialog


def test_shelf_buttons_create_automatic_names(archive, qtbot):
    _paths, repository, _pdf, _library = archive
    dialog = StorageDialog(repository)
    qtbot.addWidget(dialog)

    assert dialog.shelf_names() == []
    dialog.uses_shelves.setChecked(True)
    assert dialog.shelf_names() == ["Shelf 1"]

    dialog.add_shelf.click()
    dialog.add_shelf.click()
    assert dialog.shelf_names() == ["Shelf 1", "Shelf 2", "Shelf 3"]

    dialog.shelves.clearSelection()
    dialog.shelves.item(1).setSelected(True)
    dialog.remove_shelves.click()
    assert dialog.shelf_names() == ["Shelf 1", "Shelf 3"]


def test_shelf_grid_creates_matrix_names(archive, qtbot):
    _paths, repository, _pdf, _library = archive
    dialog = StorageDialog(repository)
    qtbot.addWidget(dialog)

    dialog.uses_shelves.setChecked(True)
    dialog.grid_rows.setValue(2)
    dialog.grid_columns.setValue(3)
    dialog.create_grid.click()

    assert dialog.shelf_names() == [
        "Shelf A1", "Shelf A2", "Shelf A3",
        "Shelf B1", "Shelf B2", "Shelf B3",
    ]


def test_adding_more_pdfs_appends_them_to_the_list(archive, sample_pdf, tmp_path, qtbot):
    _paths, repository, _pdf, _library = archive
    second_pdf = tmp_path / "second.pdf"
    second_pdf.write_bytes(sample_pdf.read_bytes())
    dialog = AddBookDialog(repository)
    qtbot.addWidget(dialog)

    dialog._append_pdfs([sample_pdf])
    dialog._append_pdfs([second_pdf])

    assert dialog.pdf_files.count() == 2
    assert dialog.pdf_files.item(0).data(Qt.UserRole) == str(sample_pdf)
    assert dialog.pdf_files.item(1).data(Qt.UserRole) == str(second_pdf)
    assert dialog.pdf_files.item(0).text().startswith("1.")
    assert dialog.pdf_files.item(1).text().startswith("2.")


def test_add_book_dialog_allows_no_location_without_storage_places(archive, sample_pdf, qtbot):
    _paths, repository, _pdf, _library = archive
    dialog = AddBookDialog(repository)
    qtbot.addWidget(dialog)
    payloads = []
    dialog.create_requested.connect(payloads.append)
    dialog.title.setText("Digital photos")
    dialog._append_pdfs([sample_pdf])

    assert dialog.storage.currentText() == "No location (digital only)"
    assert dialog.storage.currentData() is None
    dialog._create()

    assert payloads[0]["storage_id"] is None
    assert payloads[0]["shelf_id"] is None


def test_many_photos_show_background_loading_animation(archive, tmp_path, qtbot):
    _paths, repository, _pdf, _library = archive
    dialog = AddBookDialog(repository)
    qtbot.addWidget(dialog)
    dialog.show()
    photos = []
    for index in range(8):
        path = tmp_path / f"photo-{index}.jpg"
        Image.new("RGB", (240, 320), (20 * index, 80, 140)).save(path)
        photos.append(path)

    dialog._append_pdfs(photos)

    assert not dialog.file_loading.isHidden()
    qtbot.waitUntil(dialog.file_loading.isHidden, timeout=5000)
    assert dialog.cover_page.maximum() == 8
    assert dialog.cover_preview.pixmap() is not None
