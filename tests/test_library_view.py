from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QListView

from app.ui.main_window import LibraryPage


def test_library_can_switch_between_large_tiles_and_list(archive, sample_pdf, qtbot):
    _paths, repository, _pdf, library = archive
    library.import_book("Large Tile Book", None, None, [], "", sample_pdf)
    page = LibraryPage(repository, library)
    qtbot.addWidget(page)

    page._set_view_mode("tiles", save=False)
    assert page.books.viewMode() == QListView.IconMode
    assert page.books.iconSize() == QSize(170, 225)
    assert page.books.count() == 1
    assert page.books.item(0).textAlignment() & Qt.AlignHCenter

    page._set_view_mode("list", save=False)
    assert page.books.viewMode() == QListView.ListMode
    assert page.books.iconSize() == QSize(85, 110)
    assert page.books.count() == 1
