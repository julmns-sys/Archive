from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QProgressDialog, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

from app.image_processing import perspective_correct
from app.models import Book
from app.pdf import PdfService
from app.services import LibraryService
from app.ui.workers import Worker


def pixmap_to_qimage(pixmap) -> QImage:
    fmt = QImage.Format_RGBA8888 if pixmap.alpha else QImage.Format_RGB888
    image = QImage(pixmap.samples, pixmap.width, pixmap.height, pixmap.stride, fmt)
    return image.copy()


class EditCanvas(QWidget):
    changed = Signal()

    def __init__(self, image: QImage, parent: QWidget | None = None):
        super().__init__(parent)
        self.image = image.convertToFormat(QImage.Format_RGB888)
        self.mode = "crop"
        margin_x = self.image.width() * 0.04
        margin_y = self.image.height() * 0.04
        self.points = [
            QPointF(margin_x, margin_y), QPointF(self.image.width() - margin_x, margin_y),
            QPointF(self.image.width() - margin_x, self.image.height() - margin_y), QPointF(margin_x, self.image.height() - margin_y),
        ]
        self.dragging: int | None = None
        self.last_image_point: QPointF | None = None
        self.brush_color = QColor("white")
        self.brush_size = 30
        self.setMinimumSize(500, 420)
        self.setMouseTracking(True)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.setCursor(Qt.CrossCursor if mode in ("white", "marker") else Qt.ArrowCursor)
        self.update()

    def reset_crop(self) -> None:
        margin_x, margin_y = self.image.width() * 0.04, self.image.height() * 0.04
        self.points = [QPointF(margin_x, margin_y), QPointF(self.image.width() - margin_x, margin_y), QPointF(self.image.width() - margin_x, self.image.height() - margin_y), QPointF(margin_x, self.image.height() - margin_y)]
        self.update()

    def image_rect(self) -> QRectF:
        scale = min(self.width() / self.image.width(), self.height() / self.image.height())
        width, height = self.image.width() * scale, self.image.height() * scale
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def to_widget(self, point: QPointF) -> QPointF:
        rect = self.image_rect()
        return QPointF(rect.x() + point.x() * rect.width() / self.image.width(), rect.y() + point.y() * rect.height() / self.image.height())

    def to_image(self, point: QPointF) -> QPointF:
        rect = self.image_rect()
        return QPointF(
            min(max((point.x() - rect.x()) * self.image.width() / rect.width(), 0), self.image.width() - 1),
            min(max((point.y() - rect.y()) * self.image.height() / rect.height(), 0), self.image.height() - 1),
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#303337"))
        rect = self.image_rect()
        painter.drawImage(rect, self.image)
        if self.mode == "crop":
            widget_points = [self.to_widget(point) for point in self.points]
            path = QPainterPath(widget_points[0])
            for point in widget_points[1:]:
                path.lineTo(point)
            path.closeSubpath()
            painter.setPen(QPen(QColor("#00a9ff"), 4))
            painter.drawPath(path)
            painter.setPen(QPen(Qt.white, 3))
            painter.setBrush(QColor("#087fb8"))
            for point in widget_points:
                painter.drawEllipse(point, 13, 13)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.image_rect().contains(event.position()):
            return
        if self.mode == "crop":
            distances = [math.hypot((self.to_widget(point) - event.position()).x(), (self.to_widget(point) - event.position()).y()) for point in self.points]
            closest = min(range(4), key=distances.__getitem__)
            if distances[closest] <= 35:
                self.dragging = closest
        else:
            self.last_image_point = self.to_image(event.position())
            self._draw(self.last_image_point, self.last_image_point)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.mode == "crop" and self.dragging is not None:
            self.points[self.dragging] = self.to_image(event.position())
            self.update()
        elif self.mode in ("white", "marker") and self.last_image_point is not None:
            current = self.to_image(event.position())
            self._draw(self.last_image_point, current)
            self.last_image_point = current

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        if self.dragging is not None or self.last_image_point is not None:
            self.changed.emit()
        self.dragging = None
        self.last_image_point = None

    def _draw(self, start: QPointF, end: QPointF) -> None:
        painter = QPainter(self.image)
        color = QColor("white") if self.mode == "white" else self.brush_color
        painter.setPen(QPen(color, self.brush_size, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawLine(start, end)
        painter.end()
        self.update()

    def cropped(self) -> QImage:
        image = self.image.convertToFormat(QImage.Format_RGB888)
        array = np.frombuffer(image.bits(), dtype=np.uint8).reshape((image.height(), image.bytesPerLine()))[:, : image.width() * 3].reshape((image.height(), image.width(), 3)).copy()
        points = np.array([[point.x(), point.y()] for point in self.points], dtype=np.float32)
        corrected = perspective_correct(array, points)
        return QImage(corrected.data, corrected.shape[1], corrected.shape[0], corrected.strides[0], QImage.Format_RGB888).copy()


class PageImageEditor(QDialog):
    saved = Signal()

    def __init__(self, book: Book, page_index: int, library: LibraryService, pdf: PdfService, parent: QWidget | None = None):
        super().__init__(parent)
        self.book, self.page_index, self.library, self.pdf = book, page_index, library, pdf
        self.setWindowTitle(f"Edit Page {page_index + 1}")
        self.resize(1050, 800)
        pixmap = pdf.render_page(library.absolute(book.current_pdf_path), page_index, zoom=2.0)
        self.original_image = pixmap_to_qimage(pixmap)
        self.undo_images: list[QImage] = []
        outer = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.crop = QPushButton("Crop")
        self.white = QPushButton("White Out")
        self.marker = QPushButton("Marker")
        color = QPushButton("Color")
        self.brush = QSlider(Qt.Horizontal)
        self.brush.setRange(5, 100)
        self.brush.setValue(30)
        self.brush.setMaximumWidth(150)
        undo = QPushButton("Undo")
        reset_crop = QPushButton("Reset Handles")
        for widget in (self.crop, self.white, self.marker, color, QLabel("Brush size:"), self.brush, undo, reset_crop):
            toolbar.addWidget(widget)
        toolbar.addStretch()
        outer.addLayout(toolbar)
        self.canvas = EditCanvas(self.original_image)
        outer.addWidget(self.canvas, 1)
        controls = QHBoxLayout()
        cancel = QPushButton("Cancel")
        reset_page = QPushButton("Reset Page")
        apply = QPushButton("Apply and Save")
        apply.setProperty("primary", True)
        controls.addWidget(reset_page)
        controls.addStretch()
        controls.addWidget(cancel)
        controls.addWidget(apply)
        outer.addLayout(controls)
        self.crop.clicked.connect(lambda: self.canvas.set_mode("crop"))
        self.white.clicked.connect(lambda: self.canvas.set_mode("white"))
        self.marker.clicked.connect(lambda: self.canvas.set_mode("marker"))
        color.clicked.connect(self._color)
        self.brush.valueChanged.connect(lambda value: setattr(self.canvas, "brush_size", value))
        undo.clicked.connect(self._undo)
        reset_crop.clicked.connect(self.canvas.reset_crop)
        reset_page.clicked.connect(self._reset_page)
        cancel.clicked.connect(self.reject)
        apply.clicked.connect(self._apply)
        self.canvas.changed.connect(self._remember_after_change)

    def _remember_after_change(self) -> None:
        # Keep a bounded snapshot history. The first snapshot is the unedited page.
        if not self.undo_images:
            self.undo_images.append(self.original_image.copy())
        if len(self.undo_images) > 10:
            self.undo_images.pop(0)

    def _undo(self) -> None:
        if self.undo_images:
            self.canvas.image = self.undo_images.pop()
            self.canvas.update()

    def _color(self) -> None:
        color = QColorDialog.getColor(self.canvas.brush_color, self, "Choose marker color")
        if color.isValid():
            self.canvas.brush_color = color

    def _apply(self) -> None:
        try:
            image = self.canvas.cropped() if self.canvas.mode == "crop" else self.canvas.image
            buffer = image.bits().tobytes()
            array = np.frombuffer(buffer, dtype=np.uint8).reshape((image.height(), image.bytesPerLine()))[:, : image.width() * 3].reshape((image.height(), image.width(), 3))
            success, encoded = cv2.imencode(".png", cv2.cvtColor(array, cv2.COLOR_RGB2BGR))
            if not success:
                raise ValueError("The edited page could not be encoded.")
            encoded_bytes = encoded.tobytes()
        except Exception as error:
            QMessageBox.critical(self, "Page could not be saved", f"The page was not changed.\n\n{error}")
            return
        def operation(progress):
            progress(15, "Rebuilding PDF")
            # Pixel dimensions at 144 dpi become PDF points at half-size.
            self.pdf.replace_page_with_image(self.library.absolute(self.book.current_pdf_path), self.page_index, encoded_bytes, image.width() / 2, image.height() / 2)
            progress(55, "Updating page preview")
            self.library.refresh_page_thumbnails(self.book.id, [self.page_index], progress)
        self._run_save(operation, "Saving edited page…")

    def _reset_page(self) -> None:
        answer = QMessageBox.question(self, "Reset page?", "Restore this page from the original imported PDF?", QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return
        def operation(progress):
            progress(15, "Restoring original page")
            origins = self.library.page_origins(self.book)
            original_index = origins[self.page_index] if self.page_index < len(origins) else None
            if original_index is None:
                raise ValueError("This page has no matching page in the original imported PDF.")
            self.pdf.reset_page(self.library.absolute(self.book.original_pdf_path), self.library.absolute(self.book.current_pdf_path), self.page_index, original_index)
            progress(55, "Updating page preview")
            self.library.refresh_page_thumbnails(self.book.id, [self.page_index], progress)
        self._run_save(operation, "Resetting page…")

    def _run_save(self, operation, label: str) -> None:
        self.progress_dialog = QProgressDialog(label, "", 0, 100, self)
        self.progress_dialog.setCancelButton(None)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.worker = Worker(operation)
        self.worker.signals.progress.connect(lambda value, message: (self.progress_dialog.setValue(value), self.progress_dialog.setLabelText(message)))
        self.worker.signals.failed.connect(self._save_failed)
        self.worker.signals.succeeded.connect(self._save_succeeded)
        QThreadPool.globalInstance().start(self.worker)

    def _save_failed(self, message: str, _trace: str) -> None:
        self.progress_dialog.close()
        QMessageBox.critical(self, "Page could not be saved", f"The existing PDF was preserved where possible.\n\n{message}")

    def _save_succeeded(self, _result) -> None:
        self.progress_dialog.setValue(100)
        self.saved.emit()
        self.accept()


class PageOrganizer(QDialog):
    saved = Signal()

    def __init__(self, book: Book, library: LibraryService, pdf: PdfService, parent: QWidget | None = None):
        super().__init__(parent)
        self.book, self.library, self.pdf = book, library, pdf
        self.rotations: dict[int, int] = {}
        self.setWindowTitle("Edit Pages")
        self.resize(950, 650)
        layout = QVBoxLayout(self)
        explanation = QLabel("Drag thumbnails to change page order. Select a page to rotate, delete, or edit it.")
        layout.addWidget(explanation)
        self.pages = QListWidget()
        self.pages.setViewMode(QListWidget.IconMode)
        self.pages.setIconSize(QPixmap(140, 190).size())
        self.pages.setGridSize(QPixmap(165, 235).size())
        self.pages.setDragDropMode(QListWidget.InternalMove)
        self.pages.setDefaultDropAction(Qt.MoveAction)
        self.pages.setMovement(QListWidget.Snap)
        self.pages.setWrapping(True)
        layout.addWidget(self.pages, 1)
        toolbar = QHBoxLayout()
        add_photos = QPushButton("Add PDF / Photos…")
        edit = QPushButton("Crop / Draw")
        left = QPushButton("Rotate Left")
        right = QPushButton("Rotate Right")
        delete = QPushButton("Delete Page")
        delete.setProperty("danger", True)
        cancel = QPushButton("Cancel")
        save = QPushButton("Save Page Changes")
        save.setProperty("primary", True)
        for widget in (add_photos, edit, left, right, delete):
            toolbar.addWidget(widget)
        toolbar.addStretch()
        toolbar.addWidget(cancel)
        toolbar.addWidget(save)
        layout.addLayout(toolbar)
        add_photos.clicked.connect(self._add_photos)
        edit.clicked.connect(self._edit)
        left.clicked.connect(lambda: self._rotate(-90))
        right.clicked.connect(lambda: self._rotate(90))
        delete.clicked.connect(self._delete)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(lambda: self._save())
        self._load()

    def _load(self) -> None:
        self.pages.clear()
        count = self.pdf.page_count(self.library.absolute(self.book.current_pdf_path))
        for index in range(count):
            thumbnail = QPixmap.fromImage(QImage(str(self.library.thumbnail(self.book, index + 1)))).scaled(
                140, 190, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            item = QListWidgetItem(QIcon(thumbnail), f"Page {index + 1}")
            item.setData(Qt.UserRole, index)
            self.pages.addItem(item)
        if count:
            self.pages.setCurrentRow(0)

    def _selected(self) -> QListWidgetItem | None:
        item = self.pages.currentItem()
        if item is None:
            QMessageBox.information(self, "Select a page", "Please select a page first.")
        return item

    def _rotate(self, degrees: int) -> None:
        item = self._selected()
        if not item:
            return
        original = item.data(Qt.UserRole)
        self.rotations[original] = (self.rotations.get(original, 0) + degrees) % 360
        pixmap = item.icon().pixmap(140, 190)
        item.setIcon(pixmap.transformed(QTransform().rotate(degrees), Qt.SmoothTransformation))

    def _delete(self) -> None:
        item = self._selected()
        if not item:
            return
        if self.pages.count() == 1:
            QMessageBox.warning(self, "Cannot delete page", "A book must keep at least one page.")
            return
        answer = QMessageBox.question(self, "Delete page?", f"Delete {item.text()}? This is not saved until you press Save Page Changes.", QMessageBox.Cancel | QMessageBox.Yes, QMessageBox.Cancel)
        if answer == QMessageBox.Yes:
            self.pages.takeItem(self.pages.row(item))

    def _edit(self) -> None:
        item = self._selected()
        if not item:
            return
        # Save pending order/rotation before opening pixel editing so page indices are unambiguous.
        if self._has_structure_changes():
            QMessageBox.information(self, "Save page changes first", "Press Save Page Changes before opening the page editor, then open Edit Pages again.")
            return
        editor = PageImageEditor(self.book, self.pages.row(item), self.library, self.pdf, self)
        if editor.exec():
            self._load()
            self.saved.emit()

    def _add_photos(self) -> None:
        item = self._selected()
        if not item:
            return
        if self._has_structure_changes():
            QMessageBox.information(
                self,
                "Save page changes first",
                "Press Save Page Changes before adding files, then open Edit Pages again.",
            )
            return
        selected, _filter = QFileDialog.getOpenFileNames(
            self,
            "Choose PDFs or photos",
            "",
            "PDF and image files (*.pdf *.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff);;All files (*)",
        )
        if not selected:
            return
        after_page_index = self.pages.row(item)

        def operation(progress):
            return self.library.add_files_as_pages(
                self.book.id,
                [Path(path) for path in selected],
                after_page_index,
                progress,
            )

        self.progress_dialog = QProgressDialog("Adding pages…", "", 0, 100, self)
        self.progress_dialog.setCancelButton(None)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.worker = Worker(operation)
        self.worker.signals.progress.connect(
            lambda value, message: (
                self.progress_dialog.setValue(value),
                self.progress_dialog.setLabelText(message),
            )
        )
        self.worker.signals.failed.connect(self._save_failed)
        self.worker.signals.succeeded.connect(
            lambda added_pages: self._photos_added(after_page_index + added_pages)
        )
        QThreadPool.globalInstance().start(self.worker)

    def _photos_added(self, selected_row: int) -> None:
        self.progress_dialog.setValue(100)
        self._load()
        self.pages.setCurrentRow(min(selected_row, self.pages.count() - 1))
        self.saved.emit()

    def _has_structure_changes(self) -> bool:
        return bool(self.rotations) or [self.pages.item(index).data(Qt.UserRole) for index in range(self.pages.count())] != list(range(self.pages.count()))

    def _save(self) -> None:
        order = [self.pages.item(index).data(Qt.UserRole) for index in range(self.pages.count())]
        previous_origins = self.library.page_origins(self.book)
        new_origins = [previous_origins[index] if index < len(previous_origins) else None for index in order]
        structure_changed = order != list(range(len(previous_origins)))
        rotated_pages = [index for index, degrees in self.rotations.items() if degrees % 360]
        def operation(progress):
            progress(10, "Rebuilding PDF")
            self.pdf.rebuild(self.library.absolute(self.book.current_pdf_path), order, self.rotations)
            self.library.write_page_origins(self.book, new_origins)
            if structure_changed:
                progress(55, "Updating page previews")
                self.library.refresh_thumbnails(self.book.id, progress)
            elif rotated_pages:
                progress(55, "Updating changed page previews")
                self.library.refresh_page_thumbnails(self.book.id, rotated_pages, progress)
            else:
                progress(100, "No page previews need updating")
        self.progress_dialog = QProgressDialog("Saving page changes…", "", 0, 100, self)
        self.progress_dialog.setCancelButton(None)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.worker = Worker(operation)
        self.worker.signals.progress.connect(lambda value, message: (self.progress_dialog.setValue(value), self.progress_dialog.setLabelText(message)))
        self.worker.signals.failed.connect(self._save_failed)
        self.worker.signals.succeeded.connect(self._save_done)
        QThreadPool.globalInstance().start(self.worker)

    def _save_failed(self, message: str, _trace: str) -> None:
        self.progress_dialog.close()
        QMessageBox.critical(self, "Pages could not be saved", f"The existing PDF was preserved where possible.\n\n{message}")

    def _save_done(self, _result) -> None:
        self.rotations.clear()
        self.progress_dialog.setValue(100)
        self.saved.emit()
        self.accept()
