from __future__ import annotations

import logging
import sqlite3
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QImage, QPageLayout, QPainter, QPixmap
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton, QScrollArea,
    QSpinBox, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from app.database import ArchiveRepository
from app.models import Book, StoragePlace
from app.pdf import PdfService
from app.services import BackupService, LibraryService

from .dialogs import AddBookDialog, CategoryDialog, StorageDialog, error_message
from .page_editor import PageOrganizer, pixmap_to_qimage
from .workers import Worker

LOGGER = logging.getLogger(__name__)


class DeleteBooksDialog(QDialog):
    def __init__(self, books: list[Book], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Permanently Delete Books")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        count = len(books)
        heading = QLabel(f"Delete {count} book{'s' if count != 1 else ''} permanently?")
        heading.setProperty("subheading", True)
        layout.addWidget(heading)
        names = "\n".join(f"• {book.title} ({book.book_code})" for book in books[:8])
        if count > 8:
            names += f"\n• …and {count - 8} more"
        selection = QLabel(names)
        selection.setWordWrap(True)
        layout.addWidget(selection)
        warning = QLabel(
            "This removes the catalog records, original PDFs, edited PDFs, and thumbnails. "
            "This action cannot be undone.\n\nType bob to confirm:"
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        self.confirmation = QLineEdit()
        self.confirmation.setPlaceholderText("bob")
        layout.addWidget(self.confirmation)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.delete_button = buttons.addButton("Delete Permanently", QDialogButtonBox.AcceptRole)
        self.delete_button.setProperty("danger", True)
        self.delete_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.confirmation.textChanged.connect(
            lambda text: self.delete_button.setEnabled(text.strip() == "bob")
        )
        layout.addWidget(buttons)


class LibraryPage(QWidget):
    open_book = Signal(int)
    add_book = Signal()

    def __init__(self, repository: ArchiveRepository, library: LibraryService):
        super().__init__()
        self.repository, self.library = repository, library
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        top = QHBoxLayout()
        title = QLabel("Bob Archive")
        title.setProperty("heading", True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search books or type a tag, for example #drawings…")
        self.search.setClearButtonEnabled(True)
        add = QPushButton("+ Add Book")
        add.setProperty("primary", True)
        self.delete = QPushButton("Delete Selected")
        self.delete.setProperty("danger", True)
        self.delete.setEnabled(False)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.search, 2)
        top.addWidget(add)
        top.addWidget(self.delete)
        layout.addLayout(top)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Category:"))
        self.category = QComboBox()
        filters.addWidget(self.category)
        filters.addWidget(QLabel("Storage Place:"))
        self.storage = QComboBox()
        filters.addWidget(self.storage)
        filters.addStretch()
        layout.addLayout(filters)
        self.empty = QLabel("No books have been added yet.\n\nPress “+ Add Book” to add the first one.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setProperty("emptyState", True)
        self.books = QListWidget()
        self.books.setProperty("bookList", True)
        self.books.setSpacing(4)
        self.books.setIconSize(QSize(85, 110))
        self.books.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.empty, 1)
        layout.addWidget(self.books, 1)
        self.search.textChanged.connect(self._delayed_refresh)
        self.category.currentIndexChanged.connect(self.refresh)
        self.storage.currentIndexChanged.connect(self.refresh)
        self.books.itemActivated.connect(lambda item: self.open_book.emit(item.data(Qt.UserRole)))
        self.books.itemDoubleClicked.connect(lambda item: self.open_book.emit(item.data(Qt.UserRole)))
        self.books.itemSelectionChanged.connect(
            lambda: self.delete.setEnabled(bool(self.books.selectedItems()))
        )
        add.clicked.connect(self.add_book.emit)
        self.delete.clicked.connect(self._delete_selected)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)

    def _delayed_refresh(self) -> None:
        self.timer.start()

    def reload_filters(self) -> None:
        category_value = self.category.currentData()
        storage_value = self.storage.currentData()
        self.category.blockSignals(True)
        self.storage.blockSignals(True)
        self.category.clear()
        self.category.addItem("All categories", None)
        for category in self.repository.list_categories():
            self.category.addItem(category.name, category.id)
        self.storage.clear()
        self.storage.addItem("All storage places", None)
        for place in self.repository.list_storage_places():
            self.storage.addItem(place.label, place.id)
        self.storage.addItem("No location", "no_location")
        category_index = self.category.findData(category_value)
        storage_index = self.storage.findData(storage_value)
        self.category.setCurrentIndex(max(0, category_index))
        self.storage.setCurrentIndex(max(0, storage_index))
        self.category.blockSignals(False)
        self.storage.blockSignals(False)

    def refresh(self) -> None:
        books = self.repository.list_books(self.search.text(), self.category.currentData(), self.storage.currentData())
        self.books.clear()
        for book in books:
            categories = ", ".join(category.name for category in book.categories) or "No category"
            tags = " ".join(f"#{tag.name}" for tag in book.tags)
            text = f"{book.title.upper()}\n{book.book_code}\n{book.location}\n{categories}" + (f"\n{tags}" if tags else "")
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, book.id)
            thumbnail = self.library.thumbnail(book)
            if thumbnail.exists():
                item.setIcon(QPixmap(str(thumbnail)).scaled(85, 110, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            # A zero-height hint makes valid database rows completely invisible.
            item.setSizeHint(QSize(0, 135))
            self.books.addItem(item)
        no_results = not books
        self.books.setVisible(not no_results)
        self.empty.setVisible(no_results)
        if no_results:
            self.empty.setText("No books found." if self.search.text() or self.category.currentData() or self.storage.currentData() else "No books have been added yet.\n\nPress “+ Add Book” to add the first one.")
    def reload(self) -> None:
        self.reload_filters()
        self.refresh()

    def show_all(self) -> None:
        """Clear anything that could hide a newly imported book."""
        self.search.blockSignals(True)
        self.category.blockSignals(True)
        self.storage.blockSignals(True)
        self.search.clear()
        self.category.setCurrentIndex(0)
        self.storage.setCurrentIndex(0)
        self.search.blockSignals(False)
        self.category.blockSignals(False)
        self.storage.blockSignals(False)
        self.timer.stop()
        self.refresh()

    def select_book(self, book_id: int) -> None:
        for index in range(self.books.count()):
            item = self.books.item(index)
            if item.data(Qt.UserRole) == book_id:
                self.books.setCurrentItem(item)
                self.books.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                return

    def _delete_selected(self) -> None:
        ids = [item.data(Qt.UserRole) for item in self.books.selectedItems()]
        if not ids:
            return
        try:
            books = [self.repository.get_book(book_id) for book_id in ids]
        except Exception as error:
            LOGGER.exception("Could not load books selected for deletion")
            error_message(self, "Books could not be deleted", str(error))
            return
        if not DeleteBooksDialog(books, self).exec():
            return
        try:
            self.library.delete_books(ids)
        except Exception as error:
            LOGGER.exception("Book deletion failed")
            error_message(self, "Books could not be deleted", str(error))
            return
        self.refresh()


class ManagementPage(QWidget):
    changed = Signal()

    def __init__(self, title_text: str):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(28, 24, 28, 24)
        self.layout.setSpacing(16)
        title = QLabel(title_text)
        title.setProperty("heading", True)
        self.layout.addWidget(title)
        self.list = QListWidget()
        self.layout.addWidget(self.list, 1)
        self.controls = QHBoxLayout()
        self.layout.addLayout(self.controls)


class StoragePage(ManagementPage):
    def __init__(self, repository: ArchiveRepository):
        super().__init__("Storage Places")
        self.repository = repository
        add = QPushButton("+ Add Storage Place")
        add.setProperty("primary", True)
        edit = QPushButton("Edit")
        delete = QPushButton("Delete")
        delete.setProperty("danger", True)
        self.controls.addWidget(add)
        self.controls.addWidget(edit)
        self.controls.addStretch()
        self.controls.addWidget(delete)
        add.clicked.connect(self._add)
        edit.clicked.connect(self._edit)
        delete.clicked.connect(self._delete)
        self.list.itemDoubleClicked.connect(lambda _item: self._edit())

    def reload(self) -> None:
        self.places = self.repository.list_storage_places()
        self.list.clear()
        for place in self.places:
            shelf_names = [shelf.name for shelf in place.shelves]
            if len(shelf_names) > 8:
                shelves = f"{', '.join(shelf_names[:6])}, … ({len(shelf_names)} total)"
            else:
                shelves = ", ".join(shelf_names) if shelf_names else "No shelves"
            item = QListWidgetItem(f"{place.label}\nCode: {place.code}    {shelves}")
            item.setData(Qt.UserRole, place.id)
            self.list.addItem(item)

    def _selected(self) -> StoragePlace | None:
        item = self.list.currentItem()
        return next((place for place in self.places if item and place.id == item.data(Qt.UserRole)), None)

    def _add(self) -> None:
        if StorageDialog(self.repository, self).exec():
            self.reload(); self.changed.emit()

    def _edit(self) -> None:
        place = self._selected()
        if not place:
            QMessageBox.information(self, "Select a storage place", "Please select a storage place first.")
            return
        if StorageDialog(self.repository, self, place).exec():
            self.reload(); self.changed.emit()

    def _delete(self) -> None:
        place = self._selected()
        if not place:
            return
        answer = QMessageBox.question(self, "Delete storage place?", f"Delete {place.label}?\n\nA storage place containing books cannot be deleted.", QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        try:
            self.repository.delete_storage_place(place.id)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "Storage place is in use", "Move its books elsewhere before deleting this storage place.")
            return
        self.reload(); self.changed.emit()


class CategoriesPage(ManagementPage):
    def __init__(self, repository: ArchiveRepository):
        super().__init__("Categories and Tags")
        self.repository = repository
        add = QPushButton("+ Add Category")
        add.setProperty("primary", True)
        rename = QPushButton("Rename")
        delete = QPushButton("Delete")
        delete.setProperty("danger", True)
        self.controls.addWidget(add); self.controls.addWidget(rename); self.controls.addStretch(); self.controls.addWidget(delete)
        add.clicked.connect(self._add); rename.clicked.connect(self._rename); delete.clicked.connect(self._delete)
        self.list.itemDoubleClicked.connect(lambda _item: self._rename())
        tag_heading = QLabel("Tags")
        tag_heading.setProperty("subheading", True)
        self.layout.addWidget(tag_heading)
        self.tag_list = QListWidget()
        self.layout.addWidget(self.tag_list, 1)
        tag_controls = QHBoxLayout()
        add_tag = QPushButton("+ Add Tag")
        add_tag.setProperty("primary", True)
        rename_tag = QPushButton("Rename Tag")
        delete_tag = QPushButton("Delete Tag")
        delete_tag.setProperty("danger", True)
        tag_controls.addWidget(add_tag); tag_controls.addWidget(rename_tag); tag_controls.addStretch(); tag_controls.addWidget(delete_tag)
        self.layout.addLayout(tag_controls)
        add_tag.clicked.connect(self._add_tag); rename_tag.clicked.connect(self._rename_tag); delete_tag.clicked.connect(self._delete_tag)
        self.tag_list.itemDoubleClicked.connect(lambda _item: self._rename_tag())

    def reload(self) -> None:
        self.categories = self.repository.list_categories()
        self.list.clear()
        for category in self.categories:
            item = QListWidgetItem(category.name); item.setData(Qt.UserRole, category.id); self.list.addItem(item)
        self.tags = self.repository.list_tags()
        self.tag_list.clear()
        for tag in self.tags:
            item = QListWidgetItem(tag.name); item.setData(Qt.UserRole, tag.id); self.tag_list.addItem(item)

    def _selected(self):
        item = self.list.currentItem()
        return next((category for category in self.categories if item and category.id == item.data(Qt.UserRole)), None)

    def _selected_tag(self):
        item = self.tag_list.currentItem()
        return next((tag for tag in self.tags if item and tag.id == item.data(Qt.UserRole)), None)

    def _add(self) -> None:
        dialog = CategoryDialog(self)
        if dialog.exec():
            try: self.repository.create_category(dialog.name.text())
            except sqlite3.IntegrityError: QMessageBox.warning(self, "Category exists", "That category already exists."); return
            self.reload(); self.changed.emit()

    def _rename(self) -> None:
        category = self._selected()
        if not category: return
        dialog = CategoryDialog(self, "Rename Category", category.name)
        if dialog.exec():
            try: self.repository.rename_category(category.id, dialog.name.text())
            except sqlite3.IntegrityError: QMessageBox.warning(self, "Category exists", "That category name is already used."); return
            self.reload(); self.changed.emit()

    def _delete(self) -> None:
        category = self._selected()
        if not category: return
        answer = QMessageBox.question(self, "Delete category?", f"Delete “{category.name}”?\n\nBooks in this category will not be deleted.", QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
        if answer == QMessageBox.Yes:
            self.repository.delete_category(category.id); self.reload(); self.changed.emit()

    def _add_tag(self) -> None:
        dialog = CategoryDialog(self, "New Tag")
        dialog.name.setPlaceholderText("For example: drawings")
        if dialog.exec():
            try: self.repository.create_tag(dialog.name.text())
            except sqlite3.IntegrityError: QMessageBox.warning(self, "Tag exists", "That tag already exists."); return
            self.reload(); self.changed.emit()

    def _rename_tag(self) -> None:
        tag = self._selected_tag()
        if not tag: return
        dialog = CategoryDialog(self, "Rename Tag", tag.name)
        if dialog.exec():
            try: self.repository.rename_tag(tag.id, dialog.name.text())
            except sqlite3.IntegrityError: QMessageBox.warning(self, "Tag exists", "That tag name is already used."); return
            self.reload(); self.changed.emit()

    def _delete_tag(self) -> None:
        tag = self._selected_tag()
        if not tag: return
        answer = QMessageBox.question(self, "Delete tag?", f"Delete “{tag.name}”?\n\nBooks with this tag will not be deleted.", QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
        if answer == QMessageBox.Yes:
            self.repository.delete_tag(tag.id); self.reload(); self.changed.emit()


class EditBookDialog(QDialog):
    def __init__(self, book: Book, repository: ArchiveRepository, page_thumbnails: list[Path], current_page: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.book, self.repository = book, repository
        self.page_thumbnails = page_thumbnails
        page_count = len(page_thumbnails)
        self.setWindowTitle("Edit Book Information")
        self.setMinimumWidth(580)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(book.title)
        self.storage = QComboBox()
        self.places = repository.list_storage_places()
        for place in self.places: self.storage.addItem(place.label, place.id)
        self.storage.addItem("No location (digital only)", None)
        self.storage.setCurrentIndex(self.storage.findData(book.storage_place_id))
        self.shelf = QComboBox()
        self.categories = QListWidget(); self.categories.setSelectionMode(QListWidget.MultiSelection); self.categories.setMaximumHeight(130)
        selected = {category.id for category in book.categories}
        for category in repository.list_categories():
            item = QListWidgetItem(category.name); item.setData(Qt.UserRole, category.id); self.categories.addItem(item); item.setSelected(category.id in selected)
        tag_row = QWidget(); tag_layout = QHBoxLayout(tag_row); tag_layout.setContentsMargins(0, 0, 0, 0)
        self.tags = QListWidget(); self.tags.setSelectionMode(QListWidget.MultiSelection); self.tags.setMaximumHeight(130)
        selected_tags = {tag.id for tag in book.tags}
        for tag in repository.list_tags():
            item = QListWidgetItem(tag.name); item.setData(Qt.UserRole, tag.id); self.tags.addItem(item); item.setSelected(tag.id in selected_tags)
        add_tag = QPushButton("+ New Tag"); add_tag.clicked.connect(self._add_tag)
        tag_layout.addWidget(self.tags, 1); tag_layout.addWidget(add_tag)
        self.notes = QPlainTextEdit(book.notes); self.notes.setMaximumHeight(140)
        self.cover_image: Path | None = None
        cover_row = QWidget(); cover_layout = QVBoxLayout(cover_row); cover_layout.setContentsMargins(0, 0, 0, 0)
        self.cover_source = QComboBox()
        self.cover_source.addItem("Keep current photo", "keep")
        self.cover_source.addItem("Choose from book page", "page")
        self.cover_source.addItem("Upload my own image", "file")
        cover_controls = QHBoxLayout(); cover_controls.addWidget(self.cover_source, 1)
        self.cover_page = QSpinBox(); self.cover_page.setRange(1, max(1, page_count)); self.cover_page.setSuffix(f" of {max(1, page_count)}")
        self.cover_page.setValue(min(max(1, current_page), max(1, page_count)))
        self.cover_page.setToolTip("The largest photograph on this page will be used on the cover")
        cover_controls.addWidget(self.cover_page)
        self.choose_cover = QPushButton("Choose Image…"); self.choose_cover.clicked.connect(self._choose_cover)
        cover_controls.addWidget(self.choose_cover)
        self.cover_name = QLabel(""); self.cover_name.setProperty("muted", True)
        self.cover_preview = QLabel("Current cover photo will be kept")
        self.cover_preview.setAlignment(Qt.AlignCenter)
        self.cover_preview.setFixedSize(190, 250)
        self.cover_preview.setStyleSheet("border: 1px solid #c8c1b7; border-radius: 6px; padding: 4px;")
        cover_layout.addLayout(cover_controls); cover_layout.addWidget(self.cover_name); cover_layout.addWidget(self.cover_preview, 0, Qt.AlignHCenter)
        self.cover_source.currentIndexChanged.connect(self._cover_source_changed)
        self.cover_page.valueChanged.connect(self._update_cover_preview)
        self._cover_source_changed()
        form.addRow("Title:", self.title); form.addRow("Storage Place:", self.storage); form.addRow("Shelf:", self.shelf); form.addRow("Categories:", self.categories); form.addRow("Tags:", tag_row); form.addRow("Cover photo:", cover_row); form.addRow("Notes:", self.notes)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.storage.currentIndexChanged.connect(self._shelves); self._shelves()

    def _shelves(self) -> None:
        self.shelf.clear(); self.shelf.addItem("No shelf", None)
        place = next((place for place in self.places if place.id == self.storage.currentData()), None)
        for shelf in place.shelves if place else []: self.shelf.addItem(shelf.name, shelf.id)
        if place and place.id == self.book.storage_place_id: self.shelf.setCurrentIndex(max(0, self.shelf.findData(self.book.shelf_id)))
        self.shelf.setEnabled(bool(place and place.uses_shelves))

    def _add_tag(self) -> None:
        dialog = CategoryDialog(self, "New Tag")
        if not dialog.exec(): return
        try: tag_id = self.repository.create_tag(dialog.name.text())
        except sqlite3.IntegrityError: QMessageBox.warning(self, "Tag exists", "That tag already exists."); return
        item = QListWidgetItem(dialog.name.text().strip()); item.setData(Qt.UserRole, tag_id); item.setSelected(True); self.tags.addItem(item)

    def _choose_cover(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose a new cover photo",
            "",
            "Image files (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)",
        )
        if selected:
            self.cover_image = Path(selected)
            self.cover_name.setText(self.cover_image.name)
            self.cover_name.setToolTip(str(self.cover_image))
            self._update_cover_preview()

    def _cover_source_changed(self, *_args) -> None:
        source = self.cover_source.currentData()
        self.cover_page.setVisible(source == "page")
        self.choose_cover.setVisible(source == "file")
        self.cover_name.setVisible(source == "file")
        self._update_cover_preview()

    def _update_cover_preview(self, *_args) -> None:
        source = self.cover_source.currentData()
        pixmap = QPixmap()
        if source == "page":
            index = self.cover_page.value() - 1
            if 0 <= index < len(self.page_thumbnails):
                pixmap = QPixmap.fromImage(QImage(str(self.page_thumbnails[index])))
            empty_text = "Page preview is unavailable"
        elif source == "file":
            if self.cover_image:
                pixmap.load(str(self.cover_image))
            empty_text = "Choose an image to see its preview"
        else:
            empty_text = "Current cover photo will be kept"
        if pixmap.isNull():
            self.cover_preview.clear()
            self.cover_preview.setText(empty_text)
        else:
            self.cover_preview.setText("")
            self.cover_preview.setPixmap(
                pixmap.scaled(self.cover_preview.size() - QSize(10, 10), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def values(self) -> tuple[str, int | None, int | None, str, list[int], list[int], Path | None, int | None]:
        source = self.cover_source.currentData()
        return self.title.text(), self.storage.currentData(), self.shelf.currentData(), self.notes.toPlainText(), [item.data(Qt.UserRole) for item in self.categories.selectedItems()], [item.data(Qt.UserRole) for item in self.tags.selectedItems()], self.cover_image if source == "file" else None, self.cover_page.value() if source == "page" else None


class BookView(QWidget):
    back = Signal()
    changed = Signal()
    start_worker = Signal(object)

    def __init__(self, repository: ArchiveRepository, library: LibraryService, pdf: PdfService):
        super().__init__()
        self.repository, self.library, self.pdf = repository, library, pdf
        self.book: Book | None = None
        self._edit_worker: Worker | None = None
        self._edit_progress: QProgressDialog | None = None
        self.page_index = 0
        self.zoom = 1.0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(26, 22, 26, 22)
        outer.setSpacing(16)
        top = QHBoxLayout()
        back = QPushButton("‹ Library")
        self.title = QLabel(); self.title.setProperty("heading", True)
        self.code = QLabel(); self.location = QLabel(); self.categories = QLabel(); self.tags = QLabel()
        for label in (self.code, self.location, self.categories, self.tags): label.setProperty("muted", True)
        info = QVBoxLayout(); info.addWidget(self.title); info.addWidget(self.code); info.addWidget(self.location); info.addWidget(self.categories); info.addWidget(self.tags)
        edit_info = QPushButton("Edit Info")
        edit_pages = QPushButton("Edit Pages")
        top.addWidget(back); top.addLayout(info, 1); top.addWidget(edit_info); top.addWidget(edit_pages)
        outer.addLayout(top)
        splitter = QSplitter()
        self.thumbnails = QListWidget(); self.thumbnails.setMaximumWidth(190); self.thumbnails.setIconSize(QPixmap(100, 135).size()); self.thumbnails.setProperty("thumbnailList", True)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setObjectName("readerScroll")
        self.page_container = QWidget()
        self.page_container.setObjectName("readerCanvas")
        page_layout = QHBoxLayout(self.page_container)
        page_layout.setContentsMargins(10, 10, 10, 10)
        page_layout.setSpacing(12)
        self.page = QLabel(); self.page.setAlignment(Qt.AlignCenter)
        self.page_right = QLabel(); self.page_right.setAlignment(Qt.AlignCenter); self.page_right.hide()
        page_layout.addWidget(self.page, 1, Qt.AlignCenter)
        page_layout.addWidget(self.page_right, 1, Qt.AlignCenter)
        self.scroll.setWidget(self.page_container)
        self.two_page_mode = False
        splitter.addWidget(self.thumbnails); splitter.addWidget(self.scroll); splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter, 1)
        controls = QHBoxLayout(); previous = QPushButton("Previous Page"); next_page = QPushButton("Next Page"); self.page_number = QLabel(); print_page = QPushButton("Print Page…"); zoom_out = QPushButton("Zoom −"); zoom_in = QPushButton("Zoom +"); fit = QPushButton("Fit Page"); width = QPushButton("Fit Width"); self.two_pages = QPushButton("Two Pages"); self.two_pages.setCheckable(True)
        controls.addWidget(previous); controls.addWidget(next_page); controls.addWidget(self.page_number); controls.addStretch(); controls.addWidget(print_page); controls.addWidget(self.two_pages); controls.addWidget(zoom_out); controls.addWidget(zoom_in); controls.addWidget(fit); controls.addWidget(width); outer.addLayout(controls)
        back.clicked.connect(self.back.emit); edit_info.clicked.connect(self._edit_info); edit_pages.clicked.connect(self._edit_pages)
        previous.clicked.connect(lambda: self.show_page(self.page_index - (2 if self.two_page_mode else 1))); next_page.clicked.connect(lambda: self.show_page(self.page_index + (2 if self.two_page_mode else 1))); self.thumbnails.currentRowChanged.connect(self.show_page)
        self.two_pages.toggled.connect(self._toggle_two_pages)
        print_page.clicked.connect(self._print_page)
        zoom_out.clicked.connect(lambda: self._zoom(0.8)); zoom_in.clicked.connect(lambda: self._zoom(1.25)); fit.clicked.connect(self._fit_page); width.clicked.connect(self._fit_width)

    def open(self, book_id: int) -> None:
        self.book = self.repository.get_book(book_id)
        self.page_index = 0
        self.zoom = 1.0
        self.reload()
        # The view is still hidden while open() runs. Fit on the next event-loop
        # turn, after QStackedWidget has given the viewer its real dimensions.
        QTimer.singleShot(0, self._fit_page)

    def reload(self) -> None:
        if not self.book: return
        self.book = self.repository.get_book(self.book.id)
        self.title.setText(self.book.title.upper()); self.code.setText(f"Book Code:  {self.book.book_code}"); self.location.setText(f"Location:  {self.book.location}"); self.categories.setText("Categories:  " + (", ".join(category.name for category in self.book.categories) or "None"))
        self.tags.setText("Tags:  " + (" ".join(f"#{tag.name}" for tag in self.book.tags) or "None"))
        self.thumbnails.blockSignals(True); self.thumbnails.clear()
        count = self.pdf.page_count(self.library.absolute(self.book.current_pdf_path))
        for index in range(count):
            item = QListWidgetItem(f"Page {index + 1}")
            item.setIcon(QPixmap.fromImage(QImage(str(self.library.thumbnail(self.book, index + 1)))))
            self.thumbnails.addItem(item)
        self.thumbnails.setCurrentRow(min(self.page_index, count - 1)); self.thumbnails.blockSignals(False); self.show_page(min(self.page_index, count - 1))

    def show_page(self, index: int) -> None:
        if not self.book: return
        count = self.pdf.page_count(self.library.absolute(self.book.current_pdf_path))
        if not 0 <= index < count: return
        self.page_index = index
        pixmap = self.pdf.render_page(self.library.absolute(self.book.current_pdf_path), index, max(0.05, self.zoom))
        self.page.setPixmap(QPixmap.fromImage(pixmap_to_qimage(pixmap)))
        if self.two_page_mode and index + 1 < count:
            right_pixmap = self.pdf.render_page(self.library.absolute(self.book.current_pdf_path), index + 1, max(0.05, self.zoom))
            self.page_right.setPixmap(QPixmap.fromImage(pixmap_to_qimage(right_pixmap)))
            self.page_right.show()
            self.page_number.setText(f"Pages {index + 1}–{index + 2} of {count}")
        else:
            self.page_right.clear()
            self.page_right.setVisible(self.two_page_mode)
            self.page_number.setText(f"Page {index + 1} of {count}")
        if self.thumbnails.currentRow() != index:
            self.thumbnails.blockSignals(True); self.thumbnails.setCurrentRow(index); self.thumbnails.blockSignals(False)

    def _zoom(self, factor: float) -> None:
        self.zoom = min(4.0, max(0.05, self.zoom * factor)); self.show_page(self.page_index)

    def _toggle_two_pages(self, enabled: bool) -> None:
        self.two_page_mode = enabled
        self.two_pages.setText("One Page" if enabled else "Two Pages")
        self.page_right.setVisible(enabled)
        self.show_page(self.page_index)
        QTimer.singleShot(0, self._fit_page)

    def _fit_page(self) -> None:
        if not self.book: return
        sample = self.pdf.render_page(self.library.absolute(self.book.current_pdf_path), self.page_index, 1.0)
        total_width = sample.width
        maximum_height = sample.height
        count = self.pdf.page_count(self.library.absolute(self.book.current_pdf_path))
        if self.two_page_mode and self.page_index + 1 < count:
            right = self.pdf.render_page(self.library.absolute(self.book.current_pdf_path), self.page_index + 1, 1.0)
            total_width += right.width + 12
            maximum_height = max(maximum_height, right.height)
        self.zoom = min((self.scroll.viewport().width() - 40) / total_width, (self.scroll.viewport().height() - 40) / maximum_height)
        self.show_page(self.page_index)

    def _fit_width(self) -> None:
        if not self.book: return
        sample = self.pdf.render_page(self.library.absolute(self.book.current_pdf_path), self.page_index, 1.0)
        total_width = sample.width
        count = self.pdf.page_count(self.library.absolute(self.book.current_pdf_path))
        if self.two_page_mode and self.page_index + 1 < count:
            right = self.pdf.render_page(self.library.absolute(self.book.current_pdf_path), self.page_index + 1, 1.0)
            total_width += right.width + 12
        self.zoom = (self.scroll.viewport().width() - 40) / total_width
        self.show_page(self.page_index)

    def _print_page(self) -> None:
        if not self.book:
            return
        pdf_path = self.library.absolute(self.book.current_pdf_path)
        try:
            sample = self.pdf.render_page(pdf_path, self.page_index, 1.0)
            printer = QPrinter(QPrinter.HighResolution)
            printer.setDocName(f"{self.book.title} — page {self.page_index + 1}")
            printer.setPageOrientation(
                QPageLayout.Landscape if sample.width > sample.height else QPageLayout.Portrait
            )
            dialog = QPrintDialog(printer, self)
            dialog.setWindowTitle(f"Print Page {self.page_index + 1}")
            if dialog.exec() != QDialog.Accepted:
                return

            rendered = self.pdf.render_page(pdf_path, self.page_index, 300 / 72)
            image = pixmap_to_qimage(rendered)
            painter = QPainter()
            if not painter.begin(printer):
                raise RuntimeError("The selected printer could not start the print job.")
            try:
                printable = printer.pageRect(QPrinter.DevicePixel)
                scale = min(printable.width() / image.width(), printable.height() / image.height())
                width = image.width() * scale
                height = image.height() * scale
                target = QRectF(
                    printable.x() + (printable.width() - width) / 2,
                    printable.y() + (printable.height() - height) / 2,
                    width,
                    height,
                )
                painter.drawImage(target, image)
            finally:
                painter.end()
        except Exception as error:
            LOGGER.exception("Page printing failed")
            QMessageBox.critical(self, "Page could not be printed", str(error))

    def _edit_info(self) -> None:
        if not self.book: return
        page_count = self.pdf.page_count(self.library.absolute(self.book.current_pdf_path))
        page_thumbnails = [self.library.thumbnail(self.book, index + 1) for index in range(page_count)]
        dialog = EditBookDialog(self.book, self.repository, page_thumbnails, self.page_index + 1, self)
        if not dialog.exec(): return
        title, storage_id, shelf_id, notes, categories, tags, cover_image, cover_page = dialog.values()
        if not title.strip(): QMessageBox.warning(self, "Title needed", "Please enter a title."); return
        if dialog.cover_source.currentData() == "file" and cover_image is None:
            QMessageBox.warning(self, "Cover photo needed", "Choose an image file, or select a book page instead.")
            return
        if cover_image is not None:
            try:
                self.pdf.prepare_cover_image(cover_image)
            except ValueError as error:
                QMessageBox.warning(self, "Cover photo could not be used", str(error))
                return
        if storage_id != self.book.storage_place_id:
            new_code = self.repository.preview_move_code(self.book.id, storage_id)
            destination = "No location" if storage_id is None else next(
                place.label for place in self.repository.list_storage_places() if place.id == storage_id
            )
            answer = QMessageBox.question(self, "Change book location?", f"This book is currently {self.book.book_code}.\n\nChanging its location to {destination} will create the new code {new_code}.\n\nContinue?", QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
            if answer != QMessageBox.Yes: return
        book_id = self.book.id
        self._edit_progress = QProgressDialog("Preparing changes…", "", 0, 100, self)
        self._edit_progress.setWindowTitle("Applying Book Changes")
        self._edit_progress.setCancelButton(None)
        self._edit_progress.setWindowModality(Qt.WindowModal)
        self._edit_progress.setMinimumDuration(0)
        self._edit_progress.setAutoClose(False)
        self._edit_progress.setValue(0)
        self._edit_progress.show()

        def apply_changes(progress) -> None:
            progress(5, "Saving book location")
            self.repository.move_book(book_id, storage_id, shelf_id)
            self.library.update_book_info(
                book_id, title, notes, categories, tags, cover_image, cover_page, progress
            )

        worker = Worker(apply_changes)
        self._edit_worker = worker
        worker.signals.progress.connect(self._edit_progress_changed)
        worker.signals.succeeded.connect(self._edit_succeeded)
        worker.signals.failed.connect(self._edit_failed)
        self.start_worker.emit(worker)

    def _edit_progress_changed(self, value: int, message: str) -> None:
        if self._edit_progress:
            self._edit_progress.setValue(value)
            self._edit_progress.setLabelText(message)

    def _close_edit_progress(self) -> None:
        if self._edit_progress:
            self._edit_progress.close()
            self._edit_progress.deleteLater()
            self._edit_progress = None
        self._edit_worker = None

    def _edit_succeeded(self, _result: object) -> None:
        self._close_edit_progress()
        self.reload()
        self.changed.emit()

    def _edit_failed(self, message: str, trace: str) -> None:
        self._close_edit_progress()
        error_message(self, "Book could not be updated", message, trace)

    def _edit_pages(self) -> None:
        if not self.book: return
        dialog = PageOrganizer(self.book, self.library, self.pdf, self)
        dialog.saved.connect(self.reload)
        if dialog.exec(): self.reload(); self.changed.emit()


class BackupPage(QWidget):
    create_requested = Signal(Path)
    restore_requested = Signal(Path)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self); layout.setContentsMargins(28, 24, 28, 24); layout.setSpacing(16); title = QLabel("Backup"); title.setProperty("heading", True); layout.addWidget(title)
        explanation = QLabel("Save the complete library as one portable .bobbackup file, or restore a library by choosing an existing backup file.\n\nRestoring replaces the library currently open in the application."); explanation.setWordWrap(True); layout.addWidget(explanation)
        self.status = QLabel(""); self.status.setWordWrap(True); layout.addWidget(self.status); layout.addStretch()
        button = QPushButton("Create Backup File"); button.setProperty("primary", True); button.setMinimumHeight(60); button.clicked.connect(self._choose); layout.addWidget(button)
        restore = QPushButton("Restore Library from Backup"); restore.setMinimumHeight(52); restore.clicked.connect(self._choose_restore); layout.addWidget(restore)

    def _choose(self) -> None:
        selected, _filter = QFileDialog.getSaveFileName(self, "Save backup file", "BobArchive_Backup.bobbackup", "Bob Archive Backup (*.bobbackup)")
        if selected: self.create_requested.emit(Path(selected))

    def _choose_restore(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(self, "Choose backup file", "", "Bob Archive Backup (*.bobbackup *.zip);;All files (*)")
        if selected: self.restore_requested.emit(Path(selected))


class StatisticsPage(QWidget):
    def __init__(self, repository: ArchiveRepository, library: LibraryService, pdf: PdfService):
        super().__init__()
        self.repository, self.library, self.pdf = repository, library, pdf
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)
        title = QLabel("Statistics")
        title.setProperty("heading", True)
        outer.addWidget(title)
        subtitle = QLabel("A live overview of the books and organization of your archive.")
        subtitle.setProperty("muted", True)
        outer.addWidget(subtitle)

        cards = QGridLayout()
        cards.setHorizontalSpacing(12)
        cards.setVerticalSpacing(12)
        self.stat_values: dict[str, QLabel] = {}
        card_definitions = (
            ("books", "Books"),
            ("pages", "PDF Pages"),
            ("storage", "Storage Places"),
            ("shelves", "Shelves"),
            ("categories", "Categories"),
            ("tags", "Tags"),
        )
        for index, (key, label) in enumerate(card_definitions):
            card = QFrame()
            card.setObjectName("statCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 14, 18, 14)
            value = QLabel("0")
            value.setProperty("statValue", True)
            caption = QLabel(label)
            caption.setProperty("muted", True)
            card_layout.addWidget(value)
            card_layout.addWidget(caption)
            self.stat_values[key] = value
            cards.addWidget(card, index // 3, index % 3)
        outer.addLayout(cards)

        details = QHBoxLayout()
        details.setSpacing(12)
        storage_panel, storage_layout = self._panel("Books by Storage Place")
        self.storage_counts = QListWidget()
        self.storage_counts.setProperty("statisticsList", True)
        storage_layout.addWidget(self.storage_counts)
        details.addWidget(storage_panel, 1)

        category_panel, category_layout = self._panel("Most Used Categories")
        self.category_counts = QListWidget()
        self.category_counts.setProperty("statisticsList", True)
        category_layout.addWidget(self.category_counts)
        details.addWidget(category_panel, 1)

        recent_panel, recent_layout = self._panel("Recently Added")
        self.recent_books = QListWidget()
        self.recent_books.setProperty("statisticsList", True)
        recent_layout.addWidget(self.recent_books)
        details.addWidget(recent_panel, 1)
        outer.addLayout(details, 1)

        self.note = QLabel("")
        self.note.setProperty("muted", True)
        outer.addWidget(self.note)

    @staticmethod
    def _panel(title: str) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setObjectName("statisticsPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        heading = QLabel(title)
        heading.setProperty("subheading", True)
        layout.addWidget(heading)
        return panel, layout

    def reload(self) -> None:
        books = self.repository.list_books()
        storage_places = self.repository.list_storage_places()
        categories = self.repository.list_categories()
        tags = self.repository.list_tags()

        total_pages = 0
        unreadable_books = 0
        for book in books:
            try:
                total_pages += self.pdf.page_count(self.library.absolute(book.current_pdf_path))
            except Exception:
                unreadable_books += 1

        values = {
            "books": len(books),
            "pages": total_pages,
            "storage": len(storage_places),
            "shelves": sum(len(place.shelves) for place in storage_places),
            "categories": len(categories),
            "tags": len(tags),
        }
        for key, value in values.items():
            self.stat_values[key].setText(f"{value:,}")

        books_by_storage = Counter(book.storage_place_id for book in books)
        self.storage_counts.clear()
        if books_by_storage[None]:
            count = books_by_storage[None]
            self.storage_counts.addItem(f"No location\n{count} book{'s' if count != 1 else ''}")
        for place in sorted(storage_places, key=lambda item: (-books_by_storage[item.id], item.display_name.casefold())):
            count = books_by_storage[place.id]
            self.storage_counts.addItem(f"{place.display_name}\n{count} book{'s' if count != 1 else ''}")
        if not storage_places:
            self.storage_counts.addItem("No storage places yet")

        books_by_category = Counter(category.id for book in books for category in book.categories)
        self.category_counts.clear()
        ranked_categories = sorted(categories, key=lambda item: (-books_by_category[item.id], item.name.casefold()))
        for category in ranked_categories[:8]:
            count = books_by_category[category.id]
            self.category_counts.addItem(f"{category.name}\n{count} book{'s' if count != 1 else ''}")
        if not categories:
            self.category_counts.addItem("No categories yet")

        self.recent_books.clear()
        recent = sorted(books, key=lambda book: (book.created_at, book.id), reverse=True)[:8]
        for book in recent:
            self.recent_books.addItem(f"{book.title}\n{book.book_code}")
        if not recent:
            self.recent_books.addItem("No books yet")

        self.note.setText(
            f"Page count excludes {unreadable_books} book{'s' if unreadable_books != 1 else ''} whose PDF could not be read."
            if unreadable_books else "Statistics are updated automatically from the current archive."
        )


class MainWindow(QMainWindow):
    def __init__(self, repository: ArchiveRepository, library: LibraryService, pdf: PdfService, backup: BackupService):
        super().__init__()
        self.repository, self.library, self.pdf, self.backup = repository, library, pdf, backup
        self.pool = QThreadPool.globalInstance()
        self._active_workers: set[Worker] = set()
        self.add_book_dialog: AddBookDialog | None = None
        self.setWindowTitle("Bob Archive"); self.resize(1250, 820); self.setMinimumSize(900, 650)
        central = QWidget(); self.setCentralWidget(central); layout = QHBoxLayout(central); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        sidebar = QWidget(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(215)
        nav = QVBoxLayout(sidebar); nav.setContentsMargins(18, 28, 18, 22); nav.setSpacing(8)
        nav_title = QLabel("BOB ARCHIVE"); nav_title.setObjectName("brandTitle"); nav.addWidget(nav_title)
        nav_subtitle = QLabel("Your personal library"); nav_subtitle.setObjectName("brandSubtitle"); nav.addWidget(nav_subtitle); nav.addSpacing(22)
        self.nav_buttons = []
        self.stack = QStackedWidget(); self.library_page = LibraryPage(repository, library); self.statistics_page = StatisticsPage(repository, library, pdf); self.storage_page = StoragePage(repository); self.categories_page = CategoriesPage(repository); self.backup_page = BackupPage(); self.book_view = BookView(repository, library, pdf)
        for label, page in (("Library", self.library_page), ("Statistics", self.statistics_page), ("Storage Places", self.storage_page), ("Categories && Tags", self.categories_page), ("Backup", self.backup_page)):
            button = QPushButton(label); button.setProperty("nav", True); button.setCheckable(True); button.clicked.connect(lambda _checked=False, widget=page: self._show(widget)); nav.addWidget(button); self.nav_buttons.append(button); self.stack.addWidget(page)
        nav.addStretch(); layout.addWidget(sidebar); layout.addWidget(self.stack, 1); self.stack.addWidget(self.book_view)
        self.library_page.add_book.connect(self._add_book); self.library_page.open_book.connect(self._open_book); self.book_view.back.connect(lambda: self._show(self.library_page)); self.book_view.changed.connect(self._reload_all); self.book_view.start_worker.connect(self._start_worker); self.storage_page.changed.connect(self._reload_all); self.categories_page.changed.connect(self._reload_all); self.backup_page.create_requested.connect(self._create_backup); self.backup_page.restore_requested.connect(self._confirm_restore)
        self._reload_all(); self._show(self.library_page)

    def _show(self, page: QWidget) -> None:
        self.stack.setCurrentWidget(page)
        pages = (self.library_page, self.statistics_page, self.storage_page, self.categories_page, self.backup_page)
        for button, target in zip(self.nav_buttons, pages):
            button.setChecked(target is page)
        if page is self.library_page: self.library_page.reload()
        elif page is self.statistics_page: self.statistics_page.reload()
        elif page is self.storage_page: self.storage_page.reload()
        elif page is self.categories_page: self.categories_page.reload()

    def _reload_all(self) -> None:
        self.library_page.reload(); self.storage_page.reload(); self.categories_page.reload()

    def _add_book(self) -> None:
        if self.add_book_dialog is not None:
            self.add_book_dialog.raise_()
            self.add_book_dialog.activateWindow()
            return
        dialog = AddBookDialog(self.repository, self)
        self.add_book_dialog = dialog
        dialog.setModal(True)
        dialog.create_requested.connect(lambda payload, current=dialog: self._start_import(current, payload))
        dialog.finished.connect(lambda _result, current=dialog: self._add_book_dialog_finished(current))
        dialog.open()

    def _add_book_dialog_finished(self, dialog: AddBookDialog) -> None:
        if self.add_book_dialog is dialog:
            self.add_book_dialog = None
        dialog.deleteLater()

    def _start_worker(self, worker: Worker) -> None:
        """Keep Python ownership until Qt has delivered the final signal."""
        self._active_workers.add(worker)
        worker.signals.finished.connect(lambda current=worker: self._active_workers.discard(current))
        self.pool.start(worker)

    def _start_import(self, dialog: AddBookDialog, payload: dict) -> None:
        worker = Worker(lambda progress: self.library.import_book(progress=progress, **payload))
        worker.signals.progress.connect(dialog.update_progress)
        worker.signals.failed.connect(lambda message, trace: self._import_failed(dialog, message, trace))
        worker.signals.succeeded.connect(lambda book: self._import_succeeded(dialog, book))
        self._start_worker(worker)

    def _import_failed(self, dialog: AddBookDialog, message: str, trace: str) -> None:
        dialog.show_failure(); error_message(dialog, "Book could not be imported", f"The PDF could not be imported.\n\nThe original file has not been changed.\n\n{message}", trace)

    def _import_succeeded(self, dialog: AddBookDialog, book: Book) -> None:
        dialog.accept()
        self._reload_all()
        self.library_page.show_all()
        self.library_page.select_book(book.id)
        self.stack.setCurrentWidget(self.library_page)
        result = QMessageBox.question(self, "Book successfully added", f"{book.title} was added as {book.book_code}.\n\nOpen the book now?", QMessageBox.No | QMessageBox.Yes, QMessageBox.Yes)
        if result == QMessageBox.Yes: self._open_book(book.id)

    def _open_book(self, book_id: int) -> None:
        try: self.book_view.open(book_id)
        except Exception as error: LOGGER.exception("Book open failed"); error_message(self, "Book could not be opened", f"The PDF may be missing or damaged.\n\n{error}"); return
        for button in self.nav_buttons: button.setChecked(False)
        self.stack.setCurrentWidget(self.book_view)

    def _create_backup(self, destination: Path) -> None:
        self.backup_page.status.setText("Creating backup…")
        worker = Worker(lambda progress: self.backup.create_file(destination, progress))
        worker.signals.progress.connect(lambda value, message: self.backup_page.status.setText(f"{message} — {value}%"))
        worker.signals.failed.connect(lambda message, trace: self._backup_failed(message, trace))
        worker.signals.succeeded.connect(lambda path: self._backup_succeeded(path))
        self._start_worker(worker)

    def _backup_failed(self, message: str, trace: str) -> None:
        self.backup_page.status.setText("Backup was not completed. The previous backup was not changed."); error_message(self, "Backup could not be created", message, trace)

    def _backup_succeeded(self, path: Path) -> None:
        self.backup_page.status.setText(f"Backup completed successfully.\n\n{path}"); QMessageBox.information(self, "Backup complete", f"The complete archive was backed up to:\n\n{path}")

    def _confirm_restore(self, source: Path) -> None:
        answer = QMessageBox.warning(
            self,
            "Replace current library?",
            "Restoring this backup will replace every book, storage place, category, and tag in the current library.\n\nContinue?",
            QMessageBox.Cancel | QMessageBox.Yes,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self.setEnabled(False)
        self.backup_page.status.setText("Checking backup…")
        worker = Worker(lambda progress: self.backup.restore(source, progress))
        worker.signals.progress.connect(lambda value, message: self.backup_page.status.setText(f"{message} — {value}%"))
        worker.signals.failed.connect(lambda message, trace: self._restore_failed(message, trace))
        worker.signals.succeeded.connect(self._restore_succeeded)
        self._start_worker(worker)

    def _restore_failed(self, message: str, trace: str) -> None:
        self.setEnabled(True)
        self.backup_page.status.setText("Library was not restored. The current library was not changed.")
        error_message(self, "Library could not be restored", message, trace)

    def _restore_succeeded(self, book_count: int) -> None:
        self.setEnabled(True)
        self._reload_all()
        self.library_page.show_all()
        self._show(self.library_page)
        self.backup_page.status.setText(f"Library restored successfully ({book_count} books).")
        QMessageBox.information(self, "Library restored", f"The backup was restored successfully.\n\nBooks in library: {book_count}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._active_workers:
            QMessageBox.information(self, "Please wait", "Bob Archive is still saving data. Please wait for the current operation to finish before closing the application.")
            event.ignore()
            return
        event.accept()
