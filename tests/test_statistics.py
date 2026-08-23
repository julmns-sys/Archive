from __future__ import annotations

from app.ui.main_window import StatisticsPage


def test_statistics_page_reports_archive_totals(archive, sample_pdf, qtbot):
    _paths, repository, pdf, library = archive
    storage = repository.create_storage_place(
        "Bookcase", 1, "Bookcase 01", "BC01", "Porch", ["Top", "Bottom"]
    )
    category = repository.create_category("Art")
    repository.create_tag("Favorite")
    first = library.import_book("First", storage, None, [category], "", sample_pdf)
    library.import_book("Second", storage, None, [], "", sample_pdf)
    library.add_image_pages(first.id, [library.thumbnail(first)], after_page_index=0)

    page = StatisticsPage(repository, library, pdf)
    qtbot.addWidget(page)
    page.reload()

    assert page.stat_values["books"].text() == "2"
    assert page.stat_values["pages"].text() == "7"
    assert page.stat_values["storage"].text() == "1"
    assert page.stat_values["shelves"].text() == "2"
    assert page.stat_values["categories"].text() == "1"
    assert page.stat_values["tags"].text() == "1"
    assert "Bookcase 01" in page.storage_counts.item(0).text()
    assert "2 books" in page.storage_counts.item(0).text()
    assert "Art" in page.category_counts.item(0).text()
