from __future__ import annotations

import sqlite3
from pathlib import Path

import pymupdf as fitz
from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from app.database import ArchiveRepository
from app.models import Book, Category, StoragePlace
from app.ui.workers import Worker


def error_message(parent: QWidget, title: str, friendly: str, detail: str = "") -> None:
    message = QMessageBox(QMessageBox.Critical, title, friendly, QMessageBox.Close, parent)
    if detail:
        message.setDetailedText(detail)
    message.exec()


class CategoryDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, title: str = "New Category", initial: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(initial)
        self.name.setPlaceholderText("For example: Characters")
        form.addRow("Name:", self.name)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.button(QDialogButtonBox.Save).setText("Create" if not initial else "Save")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "Name needed", "Please enter a category name.")
            return
        self.accept()


class StorageDialog(QDialog):
    def __init__(self, repository: ArchiveRepository, parent: QWidget | None = None, existing: StoragePlace | None = None):
        super().__init__(parent)
        self.repository = repository
        self.existing = existing
        self.setWindowTitle("Edit Storage Place" if existing else "New Storage Place")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.storage_type = QLineEdit(existing.type if existing else "Bookcase")
        self.number = QSpinBox()
        self.number.setRange(1, 9999)
        self.display_name = QLineEdit()
        self.code = QLineEdit()
        self.location = QLineEdit(existing.physical_location if existing else "")
        self.location.setPlaceholderText("For example: Porch")
        self.uses_shelves = QCheckBox("This storage place has shelves")
        shelf_row = QWidget()
        shelf_layout = QHBoxLayout(shelf_row)
        shelf_layout.setContentsMargins(0, 0, 0, 0)
        self.shelves = QListWidget()
        self.shelves.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.shelves.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.shelves.setMaximumHeight(150)
        self.shelves.setToolTip("Double-click a shelf to rename it")
        shelf_buttons = QVBoxLayout()
        self.add_shelf = QPushButton("+ Add Shelf")
        self.remove_shelves = QPushButton("Remove Selected")
        shelf_buttons.addWidget(self.add_shelf)
        shelf_buttons.addWidget(self.remove_shelves)
        shelf_buttons.addSpacing(8)
        shelf_buttons.addWidget(QLabel("Shelf grid"))
        grid_size = QHBoxLayout()
        self.grid_rows = QSpinBox()
        self.grid_rows.setRange(1, 26)
        self.grid_rows.setValue(6)
        self.grid_rows.setPrefix("Rows: ")
        self.grid_columns = QSpinBox()
        self.grid_columns.setRange(1, 99)
        self.grid_columns.setValue(6)
        self.grid_columns.setPrefix("Columns: ")
        grid_size.addWidget(self.grid_rows)
        grid_size.addWidget(self.grid_columns)
        shelf_buttons.addLayout(grid_size)
        self.create_grid = QPushButton("Create Shelf Grid")
        self.create_grid.setToolTip("Create names such as Shelf A1, Shelf A2, Shelf B1")
        shelf_buttons.addWidget(self.create_grid)
        shelf_buttons.addStretch()
        shelf_layout.addWidget(self.shelves, 1)
        shelf_layout.addLayout(shelf_buttons)
        self.shelf_row = shelf_row
        form.addRow("Type:", self.storage_type)
        form.addRow("Number:", self.number)
        form.addRow("Display name:", self.display_name)
        form.addRow("Short code:", self.code)
        form.addRow("Physical location:", self.location)
        form.addRow("", self.uses_shelves)
        form.addRow("Shelves:", shelf_row)
        layout.addLayout(form)
        if existing:
            self.number.setValue(existing.number)
            self.display_name.setText(existing.display_name)
            self.code.setText(existing.code)
            self.uses_shelves.setChecked(existing.uses_shelves)
            for shelf in existing.shelves:
                self._append_shelf(shelf.name)
        else:
            self._suggest()
        self._toggle_shelves(self.uses_shelves.isChecked())
        self.uses_shelves.toggled.connect(self._toggle_shelves)
        self.add_shelf.clicked.connect(self._add_shelf)
        self.remove_shelves.clicked.connect(self._remove_shelves)
        self.create_grid.clicked.connect(self._generate_grid)
        self.storage_type.editingFinished.connect(self._suggest)
        self.number.valueChanged.connect(self._number_changed)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _suggest(self) -> None:
        if self.existing:
            return
        number, name, code = self.repository.suggest_storage(self.storage_type.text())
        self.number.setValue(number)
        self.display_name.setText(name)
        self.code.setText(code)

    def _number_changed(self, number: int) -> None:
        if self.existing:
            return
        storage_type = self.storage_type.text().strip() or "Storage"
        self.display_name.setText(f"{storage_type} {number:02d}")
        prefix = "".join(word[0] for word in storage_type.upper().split()) if " " in storage_type else storage_type.upper()[:2]
        self.code.setText(f"{prefix}{number:02d}")

    def _append_shelf(self, name: str) -> None:
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self.shelves.addItem(item)

    def _toggle_shelves(self, enabled: bool) -> None:
        self.shelf_row.setEnabled(enabled)
        if enabled and self.shelves.count() == 0:
            self._add_shelf()

    def _add_shelf(self) -> None:
        existing = {
            self.shelves.item(index).text().strip().casefold()
            for index in range(self.shelves.count())
        }
        number = 1
        while f"shelf {number}".casefold() in existing:
            number += 1
        self._append_shelf(f"Shelf {number}")
        self.shelves.clearSelection()
        self.shelves.setCurrentRow(self.shelves.count() - 1)

    def _remove_shelves(self) -> None:
        for item in self.shelves.selectedItems():
            self.shelves.takeItem(self.shelves.row(item))

    def _generate_grid(self) -> None:
        current = self.shelf_names()
        automatically_created_default = current == ["Shelf 1"] and not self.existing
        if current and not automatically_created_default:
            answer = QMessageBox.question(
                self,
                "Replace current shelves?",
                "Creating a shelf grid will replace the shelf list currently shown.\n\nContinue?",
                QMessageBox.Cancel | QMessageBox.Yes,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return
        self._set_shelf_grid(self.grid_rows.value(), self.grid_columns.value())

    def _set_shelf_grid(self, rows: int, columns: int) -> None:
        self.uses_shelves.setChecked(True)
        self.shelves.clear()
        for row in range(rows):
            letter = chr(ord("A") + row)
            for column in range(1, columns + 1):
                self._append_shelf(f"Shelf {letter}{column}")
        if self.shelves.count():
            self.shelves.setCurrentRow(0)

    def _save(self) -> None:
        if not all((self.storage_type.text().strip(), self.display_name.text().strip(), self.code.text().strip(), self.location.text().strip())):
            QMessageBox.warning(self, "Information needed", "Please complete type, display name, short code, and physical location.")
            return
        shelves = self.shelf_names()
        if self.uses_shelves.isChecked() and not shelves:
            QMessageBox.warning(self, "Shelves needed", "Please add at least one shelf.")
            return
        if len({name.casefold() for name in shelves}) != len(shelves):
            QMessageBox.warning(self, "Shelf names must be unique", "Please give every shelf a different name.")
            return
        try:
            if self.existing:
                self.repository.update_storage_place(self.existing.id, self.storage_type.text(), self.number.value(), self.display_name.text(), self.code.text(), self.location.text(), shelves)
            else:
                self.repository.create_storage_place(self.storage_type.text(), self.number.value(), self.display_name.text(), self.code.text(), self.location.text(), shelves)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "Code already used", "That storage number or short code is already in use. Please choose another.")
            return
        self.accept()

    def shelf_names(self) -> list[str]:
        if not self.uses_shelves.isChecked():
            return []
        return [
            self.shelves.item(index).text().strip()
            for index in range(self.shelves.count())
            if self.shelves.item(index).text().strip()
        ]


class AddBookDialog(QDialog):
    create_requested = Signal(dict)

    def __init__(self, repository: ArchiveRepository, parent: QWidget | None = None):
        super().__init__(parent)
        self.repository = repository
        self.setAcceptDrops(True)
        self.setWindowTitle("Add Book")
        self.setMinimumSize(680, 720)
        outer = QVBoxLayout(self)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack)
        form_page = QWidget()
        form_layout = QVBoxLayout(form_page)
        heading = QLabel("Add a Book")
        heading.setProperty("heading", True)
        form_layout.addWidget(heading)
        form = QFormLayout()
        self.title = QLineEdit()
        self.title.setPlaceholderText("Book title")
        self.storage = QComboBox()
        self.storage.currentIndexChanged.connect(self._storage_changed)
        self.shelf_label = QLabel("Shelf:")
        self.shelf = QComboBox()
        category_row = QWidget()
        category_layout = QHBoxLayout(category_row)
        category_layout.setContentsMargins(0, 0, 0, 0)
        self.categories = QListWidget()
        self.categories.setSelectionMode(QListWidget.MultiSelection)
        self.categories.setMaximumHeight(115)
        add_category = QPushButton("+ New Category")
        add_category.clicked.connect(self._add_category)
        category_layout.addWidget(self.categories, 1)
        category_layout.addWidget(add_category)
        tag_row = QWidget()
        tag_layout = QHBoxLayout(tag_row)
        tag_layout.setContentsMargins(0, 0, 0, 0)
        self.tags = QListWidget()
        self.tags.setSelectionMode(QListWidget.MultiSelection)
        self.tags.setMaximumHeight(115)
        add_tag = QPushButton("+ New Tag")
        add_tag.clicked.connect(self._add_tag)
        tag_layout.addWidget(self.tags, 1)
        tag_layout.addWidget(add_tag)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Optional notes")
        self.notes.setMaximumHeight(110)
        self.cover_image: Path | None = None
        cover_row = QWidget()
        cover_layout = QVBoxLayout(cover_row)
        cover_layout.setContentsMargins(0, 0, 0, 0)
        self.generate_cover = QCheckBox("Generate a cover")
        self.generate_cover.setChecked(True)
        cover_layout.addWidget(self.generate_cover)
        source_row = QHBoxLayout()
        self.cover_source = QComboBox()
        self.cover_source.addItem("Choose from PDF page", "page")
        self.cover_source.addItem("Upload my own image", "file")
        self.cover_page = QSpinBox()
        self.cover_page.setRange(1, 1)
        self.cover_page.setSuffix(" of 1")
        self.cover_page.setToolTip("The largest photograph on this page will appear below the title")
        self.choose_cover = QPushButton("Choose Image…")
        self.choose_cover.clicked.connect(self._choose_cover_image)
        self.cover_name = QLabel("")
        self.cover_name.setProperty("muted", True)
        source_row.addWidget(self.cover_source, 1)
        source_row.addWidget(self.cover_page)
        source_row.addWidget(self.choose_cover)
        cover_layout.addLayout(source_row)
        cover_layout.addWidget(self.cover_name)
        self.cover_preview = QLabel("Choose PDF files to preview a page")
        self.cover_preview.setAlignment(Qt.AlignCenter)
        self.cover_preview.setFixedSize(190, 250)
        self.cover_preview.setStyleSheet("border: 1px solid #c8c1b7; border-radius: 6px; padding: 4px;")
        cover_layout.addWidget(self.cover_preview, 0, Qt.AlignHCenter)
        self.generate_cover.toggled.connect(self._cover_controls_changed)
        self.cover_source.currentIndexChanged.connect(self._cover_controls_changed)
        self.cover_page.valueChanged.connect(self._schedule_source_scan)
        pdf_row = QWidget()
        pdf_layout = QHBoxLayout(pdf_row)
        pdf_layout.setContentsMargins(0, 0, 0, 0)
        self.pdf_files = QListWidget()
        self.pdf_files.setMaximumHeight(125)
        self.pdf_files.setDragDropMode(QAbstractItemView.InternalMove)
        self.pdf_files.setDefaultDropAction(Qt.MoveAction)
        self.pdf_files.setToolTip("Drag PDFs and photos in this list to change their page order")
        self.pdf_files.model().rowsMoved.connect(self._renumber_pdfs)
        pdf_buttons = QVBoxLayout()
        choose_pdf = QPushButton("Add PDFs / Photos")
        choose_pdf.clicked.connect(self._choose_pdf)
        remove_pdf = QPushButton("Remove Selected")
        remove_pdf.clicked.connect(self._remove_pdf)
        pdf_buttons.addWidget(choose_pdf)
        pdf_buttons.addWidget(remove_pdf)
        self.file_loading = QProgressBar()
        self.file_loading.setRange(0, 0)
        self.file_loading.setFormat("Loading files…")
        self.file_loading.setTextVisible(True)
        self.file_loading.hide()
        pdf_buttons.addWidget(self.file_loading)
        pdf_buttons.addStretch()
        pdf_layout.addWidget(self.pdf_files, 1)
        pdf_layout.addLayout(pdf_buttons)
        form.addRow("Title:", self.title)
        form.addRow("Storage Place:", self.storage)
        form.addRow(self.shelf_label, self.shelf)
        form.addRow("Categories:", category_row)
        form.addRow("Tags:", tag_row)
        form.addRow("Notes:", self.notes)
        form.addRow("Cover:", cover_row)
        form.addRow("Source files:", pdf_row)
        form_layout.addLayout(form)
        form_layout.addStretch()
        controls = QDialogButtonBox(QDialogButtonBox.Cancel)
        create = controls.addButton("Create Book", QDialogButtonBox.AcceptRole)
        create.setProperty("primary", True)
        create.clicked.connect(self._create)
        controls.rejected.connect(self.reject)
        form_layout.addWidget(controls)
        self.stack.addWidget(form_page)
        progress_page = QWidget()
        progress_layout = QVBoxLayout(progress_page)
        progress_layout.addStretch()
        progress_heading = QLabel("Creating book…")
        progress_heading.setProperty("heading", True)
        progress_heading.setAlignment(Qt.AlignCenter)
        self.progress_text = QLabel("Preparing")
        self.progress_text.setAlignment(Qt.AlignCenter)
        self.progress = QProgressBar()
        progress_layout.addWidget(progress_heading)
        progress_layout.addWidget(self.progress_text)
        progress_layout.addWidget(self.progress)
        progress_layout.addStretch()
        self.stack.addWidget(progress_page)
        self._file_scan_generation = 0
        self._file_scan_workers: set[Worker] = set()
        self._file_scan_timer = QTimer(self)
        self._file_scan_timer.setSingleShot(True)
        self._file_scan_timer.setInterval(120)
        self._file_scan_timer.timeout.connect(self._start_source_scan)
        self._cover_controls_changed()
        self._reload()

    def _reload(self, selected_category: int | None = None, selected_tag: int | None = None) -> None:
        previous_categories = {item.data(Qt.UserRole) for item in self.categories.selectedItems()} if hasattr(self, "categories") else set()
        previous_tags = {item.data(Qt.UserRole) for item in self.tags.selectedItems()} if hasattr(self, "tags") else set()
        self.places = self.repository.list_storage_places()
        self.storage.clear()
        for place in self.places:
            self.storage.addItem(place.label, place.id)
        self.storage.addItem("No location (digital only)", None)
        self.categories.clear()
        for category in self.repository.list_categories():
            self.categories.addItem(category.name)
            item = self.categories.item(self.categories.count() - 1)
            item.setData(Qt.UserRole, category.id)
            if category.id == selected_category or category.id in previous_categories:
                item.setSelected(True)
        self.tags.clear()
        for tag in self.repository.list_tags():
            self.tags.addItem(tag.name)
            item = self.tags.item(self.tags.count() - 1)
            item.setData(Qt.UserRole, tag.id)
            if tag.id == selected_tag or tag.id in previous_tags:
                item.setSelected(True)
        self._storage_changed()

    def _storage_changed(self) -> None:
        storage_id = self.storage.currentData()
        place = next((item for item in self.places if item.id == storage_id), None)
        visible = bool(place and place.uses_shelves)
        self.shelf_label.setVisible(visible)
        self.shelf.setVisible(visible)
        self.shelf.clear()
        if place:
            for shelf in place.shelves:
                self.shelf.addItem(shelf.name, shelf.id)

    def _add_category(self) -> None:
        dialog = CategoryDialog(self)
        if dialog.exec():
            try:
                category_id = self.repository.create_category(dialog.name.text())
            except sqlite3.IntegrityError:
                QMessageBox.warning(self, "Category already exists", "A category with that name already exists.")
                return
            self._reload(category_id)

    def _add_tag(self) -> None:
        dialog = CategoryDialog(self, "New Tag")
        dialog.name.setPlaceholderText("For example: drawings")
        if dialog.exec():
            try:
                tag_id = self.repository.create_tag(dialog.name.text())
            except sqlite3.IntegrityError:
                QMessageBox.warning(self, "Tag already exists", "A tag with that name already exists.")
                return
            self._reload(selected_tag=tag_id)

    def _choose_pdf(self) -> None:
        selected, _ = QFileDialog.getOpenFileNames(
            self,
            "Choose PDFs or photos",
            "",
            "PDF and image files (*.pdf *.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff);;All files (*)",
        )
        if selected:
            self._append_pdfs([Path(path) for path in selected])

    def _choose_cover_image(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose a cover photo",
            "",
            "Image files (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)",
        )
        if selected:
            self.cover_image = Path(selected)
            self.cover_name.setText(self.cover_image.name)
            self.cover_name.setToolTip(str(self.cover_image))
            self._update_cover_preview()

    def _cover_controls_changed(self, *_args) -> None:
        enabled = self.generate_cover.isChecked()
        from_page = self.cover_source.currentData() == "page"
        self.cover_source.setEnabled(enabled)
        self.cover_page.setVisible(from_page)
        self.cover_page.setEnabled(enabled and from_page)
        self.choose_cover.setVisible(not from_page)
        self.choose_cover.setEnabled(enabled and not from_page)
        self.cover_name.setVisible(enabled and not from_page)
        self.cover_preview.setVisible(enabled)
        self._update_cover_preview()

    def _update_cover_preview(self, *_args) -> None:
        if not self.generate_cover.isChecked():
            return
        pixmap = QPixmap()
        if self.cover_source.currentData() == "file":
            if self.cover_image:
                pixmap.load(str(self.cover_image))
            empty_text = "Choose an image to see its preview"
        else:
            empty_text = "Choose source files to preview a page"
            if not hasattr(self, "pdf_files"):
                self.cover_preview.setText(empty_text)
                return
            if self.pdf_files.count():
                self._schedule_source_scan()
                return
        if pixmap.isNull():
            self.cover_preview.clear()
            self.cover_preview.setText(empty_text)
        else:
            self.cover_preview.setText("")
            self.cover_preview.setPixmap(
                pixmap.scaled(self.cover_preview.size() - QSize(10, 10), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def _set_pdfs(self, paths: list[Path]) -> None:
        self.pdf_files.clear()
        self._append_pdfs(paths)

    def _append_pdfs(self, paths: list[Path]) -> None:
        for path in paths:
            index = self.pdf_files.count() + 1
            item = QListWidgetItem(f"{index}. {path.name}")
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            self.pdf_files.addItem(item)
        self._update_cover_pages()

    def _remove_pdf(self) -> None:
        for item in self.pdf_files.selectedItems():
            self.pdf_files.takeItem(self.pdf_files.row(item))
        self._renumber_pdfs()

    def _renumber_pdfs(self, *_args) -> None:
        for index in range(self.pdf_files.count()):
            item = self.pdf_files.item(index)
            item.setText(f"{index + 1}. {Path(item.data(Qt.UserRole)).name}")
        self._update_cover_pages()

    def _update_cover_pages(self) -> None:
        self._schedule_source_scan()

    def _schedule_source_scan(self, *_args) -> None:
        if not hasattr(self, "_file_scan_timer"):
            return
        self._file_scan_generation += 1
        if self.pdf_files.count() == 0:
            self._file_scan_timer.stop()
            self.file_loading.hide()
            self.cover_page.blockSignals(True)
            self.cover_page.setMaximum(1)
            self.cover_page.setSuffix(" of 1")
            self.cover_page.blockSignals(False)
            if self.cover_source.currentData() == "page":
                self.cover_preview.clear()
                self.cover_preview.setText("Choose source files to preview a page")
            return
        self.file_loading.show()
        if self.cover_source.currentData() == "page":
            self.cover_preview.clear()
            self.cover_preview.setText("Loading preview…")
        self._file_scan_timer.start()

    def _start_source_scan(self) -> None:
        generation = self._file_scan_generation
        paths = tuple(
            self.pdf_files.item(index).data(Qt.UserRole)
            for index in range(self.pdf_files.count())
        )
        selected_page = self.cover_page.value() - 1

        def operation(progress):
            counts: list[int] = []
            total = len(paths)
            for index, path in enumerate(paths, 1):
                try:
                    with fitz.open(path) as document:
                        counts.append(document.page_count)
                except Exception:
                    counts.append(0)
                progress(int(70 * index / max(1, total)), f"Loading files ({index}/{total})")
            preview = b""
            remaining = selected_page
            for path, count in zip(paths, counts):
                if remaining < count:
                    try:
                        with fitz.open(path) as document:
                            rendered = document[remaining].get_pixmap(
                                matrix=fitz.Matrix(0.45, 0.45), colorspace=fitz.csRGB, alpha=False
                            )
                            preview = rendered.tobytes("png")
                    except Exception:
                        pass
                    break
                remaining -= count
            return paths, sum(counts), preview

        worker = Worker(operation)
        self._file_scan_workers.add(worker)
        worker.signals.succeeded.connect(
            lambda result, current=generation: self._source_scan_done(current, result)
        )
        worker.signals.failed.connect(
            lambda _message, _trace, current=generation: self._source_scan_failed(current)
        )
        worker.signals.finished.connect(
            lambda current_worker=worker: self._file_scan_workers.discard(current_worker)
        )
        QThreadPool.globalInstance().start(worker)

    def _source_scan_done(self, generation: int, result: tuple[tuple[str, ...], int, bytes]) -> None:
        if generation != self._file_scan_generation:
            return
        _paths, page_count, preview = result
        count = max(1, page_count)
        self.cover_page.blockSignals(True)
        self.cover_page.setMaximum(count)
        self.cover_page.setSuffix(f" of {count}")
        self.cover_page.blockSignals(False)
        self.file_loading.hide()
        if self.cover_source.currentData() != "page":
            return
        pixmap = QPixmap()
        if preview:
            pixmap.loadFromData(preview)
        if pixmap.isNull():
            self.cover_preview.clear()
            self.cover_preview.setText("Page preview is unavailable")
        else:
            self.cover_preview.setText("")
            self.cover_preview.setPixmap(
                pixmap.scaled(self.cover_preview.size() - QSize(10, 10), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def _source_scan_failed(self, generation: int) -> None:
        if generation != self._file_scan_generation:
            return
        self.file_loading.hide()
        if self.cover_source.currentData() == "page":
            self.cover_preview.clear()
            self.cover_preview.setText("Page preview is unavailable")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        supported = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")
        if urls and all(url.isLocalFile() and url.toLocalFile().lower().endswith(supported) for url in urls):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        self._append_pdfs([Path(url.toLocalFile()) for url in event.mimeData().urls()])
        event.acceptProposedAction()

    def _create(self) -> None:
        if not self.title.text().strip():
            QMessageBox.warning(self, "Title needed", "Please enter the book title.")
            return
        if self.pdf_files.count() == 0:
            QMessageBox.warning(self, "Files needed", "Please choose one or more PDFs or photos.")
            return
        self.stack.setCurrentIndex(1)
        selected_categories = [item.data(Qt.UserRole) for item in self.categories.selectedItems()]
        selected_tags = [item.data(Qt.UserRole) for item in self.tags.selectedItems()]
        selected_pdfs = [Path(self.pdf_files.item(index).data(Qt.UserRole)) for index in range(self.pdf_files.count())]
        payload = {
            "title": self.title.text().strip(), "storage_id": self.storage.currentData(),
            "shelf_id": self.shelf.currentData() if self.shelf.isVisible() else None,
            "category_ids": selected_categories, "tag_ids": selected_tags,
            "notes": self.notes.toPlainText(), "source_pdf": selected_pdfs,
            "cover_page": self.cover_page.value() if self.generate_cover.isChecked() and self.cover_source.currentData() == "page" else None,
            "cover_image": self.cover_image if self.generate_cover.isChecked() and self.cover_source.currentData() == "file" else None,
        }
        if self.generate_cover.isChecked() and self.cover_source.currentData() == "file" and self.cover_image is None:
            QMessageBox.warning(self, "Cover photo needed", "Choose an image file for the cover, or select a PDF page instead.")
            self.stack.setCurrentIndex(0)
            return
        self.create_requested.emit(payload)

    def update_progress(self, value: int, message: str) -> None:
        self.progress.setValue(value)
        self.progress_text.setText(message)

    def show_failure(self) -> None:
        self.stack.setCurrentIndex(0)
