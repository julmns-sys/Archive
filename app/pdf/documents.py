from __future__ import annotations

import io
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

import pymupdf as fitz
from PIL import Image, ImageOps, UnidentifiedImageError


Progress = Callable[[int, str], None]


class PdfService:
    _SERIF_FONT_CANDIDATES = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf"),
        Path("C:/Windows/Fonts/georgiab.ttf"),
        Path("C:/Windows/Fonts/timesbd.ttf"),
        Path("/System/Library/Fonts/Supplemental/Georgia Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"),
    )

    def validate(self, path: Path) -> int:
        if path.suffix.lower() != ".pdf" or not path.is_file():
            raise ValueError("Please choose a PDF file.")
        try:
            with fitz.open(path) as document:
                if document.needs_pass:
                    raise ValueError("Password-protected PDFs are not supported.")
                if document.page_count < 1:
                    raise ValueError("The PDF does not contain any pages.")
                return document.page_count
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("The PDF is damaged or unreadable.") from error

    def generate_thumbnails(self, pdf_path: Path, directory: Path, progress: Progress | None = None) -> int:
        directory.mkdir(parents=True, exist_ok=True)
        with fitz.open(pdf_path) as document:
            count = document.page_count
            for index, page in enumerate(document):
                pixmap = page.get_pixmap(matrix=fitz.Matrix(0.28, 0.28), colorspace=fitz.csRGB, alpha=False)
                target = directory / f"page_{index + 1:04d}.jpg"
                pixmap.save(target, jpg_quality=82)
                if progress:
                    progress(40 + int(50 * (index + 1) / count), f"Generating thumbnails ({index + 1}/{count})")
        return count

    def generate_thumbnails_for_pages(
        self,
        pdf_path: Path,
        directory: Path,
        page_indices: list[int],
        progress: Progress | None = None,
    ) -> int:
        """Replace thumbnails only for selected zero-based PDF pages."""
        indices = list(dict.fromkeys(page_indices))
        directory.mkdir(parents=True, exist_ok=True)
        with fitz.open(pdf_path) as document:
            for index in indices:
                if not 0 <= index < document.page_count:
                    raise IndexError(index)
            total = len(indices)
            for completed, index in enumerate(indices, 1):
                pixmap = document[index].get_pixmap(
                    matrix=fitz.Matrix(0.28, 0.28), colorspace=fitz.csRGB, alpha=False
                )
                pixmap.save(directory / f"page_{index + 1:04d}.jpg", jpg_quality=82)
                if progress:
                    progress(
                        60 + int(30 * completed / max(1, total)),
                        f"Updating page preview ({completed}/{total})",
                    )
        return total

    def render_page(self, pdf_path: Path, page_index: int, zoom: float = 1.4) -> fitz.Pixmap:
        with fitz.open(pdf_path) as document:
            if not 0 <= page_index < document.page_count:
                raise IndexError(page_index)
            return document[page_index].get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)

    def page_count(self, pdf_path: Path) -> int:
        with fitz.open(pdf_path) as document:
            return document.page_count

    def merge_pdfs(self, sources: list[Path], destination: Path) -> None:
        if not sources:
            raise ValueError("At least one PDF is required.")
        with fitz.open() as output:
            first_metadata = None
            for source_path in sources:
                with fitz.open(source_path) as source:
                    if first_metadata is None:
                        first_metadata = source.metadata
                    output.insert_pdf(source)
            if first_metadata:
                output.set_metadata({key: value for key, value in first_metadata.items() if isinstance(value, str)})
            self._atomic_save(output, destination)

    def add_cover(self, pdf_path: Path, title: str, source_page_index: int, image_bytes: bytes | None = None) -> None:
        """Prepend a printable cover using an image from one scanned page."""
        output = fitz.open()
        try:
            with fitz.open(pdf_path) as source:
                if not 0 <= source_page_index < source.page_count:
                    raise ValueError("The selected cover page does not exist in the combined PDF.")

                source_page = source[source_page_index]
                width, height = source_page.rect.width, source_page.rect.height
                selected_image = image_bytes or self._cover_image(source, source_page)
                self._new_cover_page(output, width, height, title, selected_image)
                output.insert_pdf(source)
                metadata = dict(source.metadata)
                metadata["title"] = title
                output.set_metadata({key: value for key, value in metadata.items() if isinstance(value, str)})

            # The source is closed before replacement, which is required on
            # platforms that lock open PDF files (notably Windows).
            self._atomic_save(output, pdf_path)
        finally:
            output.close()

    def replace_cover(self, pdf_path: Path, cover_page_index: int, title: str, image_bytes: bytes) -> None:
        """Rebuild one generated cover while preserving every other PDF page."""
        output = fitz.open()
        try:
            with fitz.open(pdf_path) as source:
                if not 0 <= cover_page_index < source.page_count:
                    raise ValueError("The generated cover page could not be found.")
                if cover_page_index:
                    output.insert_pdf(source, from_page=0, to_page=cover_page_index - 1)
                size = source[cover_page_index].rect
                self._new_cover_page(output, size.width, size.height, title, image_bytes)
                if cover_page_index + 1 < source.page_count:
                    output.insert_pdf(source, from_page=cover_page_index + 1, to_page=source.page_count - 1)
                metadata = dict(source.metadata)
                metadata["title"] = title
                output.set_metadata({key: value for key, value in metadata.items() if isinstance(value, str)})
            self._atomic_save(output, pdf_path)
        finally:
            output.close()

    def cover_image(self, pdf_path: Path, page_index: int) -> bytes:
        """Extract the photograph from an existing generated cover."""
        with fitz.open(pdf_path) as document:
            if not 0 <= page_index < document.page_count:
                raise ValueError("The generated cover page could not be found.")
            return self._cover_image(document, document[page_index])

    @staticmethod
    def prepare_cover_image(path: Path) -> bytes:
        """Validate and normalize a user-selected raster image for the PDF."""
        if not path.is_file():
            raise ValueError("Please choose an image file for the cover.")
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((4000, 4000), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=92, optimize=True)
                return output.getvalue()
        except (UnidentifiedImageError, OSError) as error:
            raise ValueError("The selected cover image is damaged or unsupported.") from error

    @classmethod
    def _new_cover_page(cls, document: fitz.Document, width: float, height: float, title: str, image_bytes: bytes) -> None:
        cover = document.new_page(width=width, height=height)
        font_name = cls._insert_cover_font(cover)
        ink = (0.10, 0.08, 0.07)
        margin = width * 0.085

        # The edge title doubles as a spine label when the sheet is folded or
        # trimmed, avoiding a second cover print.
        edge_rect = fitz.Rect(margin, height * 0.018, width - margin, height * 0.075)
        cls._fit_text(cover, edge_rect, title.upper(), font_name, width * 0.027, ink)
        cover.draw_line(
            fitz.Point(margin, height * 0.092),
            fitz.Point(width - margin, height * 0.092),
            color=(0.72, 0.68, 0.62),
            width=max(0.5, width / 900),
        )
        title_rect = fitz.Rect(margin, height * 0.29, width - margin, height * 0.48)
        cls._fit_text(cover, title_rect, title, font_name, width * 0.085, ink)
        image_rect = fitz.Rect(margin, height * 0.52, width - margin, height * 0.90)
        cover.insert_image(image_rect, stream=image_bytes, keep_proportion=True)

    @staticmethod
    def _cover_image(document: fitz.Document, page: fitz.Page) -> bytes:
        """Use the largest embedded photograph, falling back to the whole page."""
        images = page.get_images(full=True)
        if images:
            largest = max(images, key=lambda item: item[2] * item[3])
            try:
                extracted = document.extract_image(largest[0])
                if extracted.get("image"):
                    return extracted["image"]
            except Exception:
                pass
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), colorspace=fitz.csRGB, alpha=False)
        return pixmap.tobytes("jpeg", jpg_quality=88)

    @classmethod
    def _insert_cover_font(cls, page: fitz.Page) -> str:
        for path in cls._SERIF_FONT_CANDIDATES:
            if path.is_file():
                page.insert_font(fontname="CoverSerif", fontfile=str(path))
                return "CoverSerif"
        return "tiro"

    @staticmethod
    def _fit_text(
        page: fitz.Page,
        rect: fitz.Rect,
        text: str,
        font_name: str,
        starting_size: float,
        color: tuple[float, float, float],
    ) -> None:
        size = starting_size
        minimum = max(8.0, starting_size * 0.36)
        while size >= minimum:
            result = page.insert_textbox(
                rect,
                text,
                fontname=font_name,
                fontsize=size,
                color=color,
                align=fitz.TEXT_ALIGN_CENTER,
                lineheight=1.08,
            )
            if result >= 0:
                return
            size *= 0.9
        page.insert_textbox(
            rect, text, fontname=font_name, fontsize=minimum,
            color=color, align=fitz.TEXT_ALIGN_CENTER,
        )

    def rebuild(self, pdf_path: Path, order: list[int], rotations: dict[int, int] | None = None) -> None:
        rotations = rotations or {}
        with fitz.open(pdf_path) as source, fitz.open() as output:
            if not order:
                raise ValueError("A book must keep at least one page.")
            for original_index in order:
                if not 0 <= original_index < source.page_count:
                    raise IndexError(original_index)
                output.insert_pdf(source, from_page=original_index, to_page=original_index)
                if original_index in rotations:
                    page = output[-1]
                    page.set_rotation((page.rotation + rotations[original_index]) % 360)
            self._atomic_save(output, pdf_path)

    def replace_page_with_image(self, pdf_path: Path, page_index: int, image_bytes: bytes, width: int, height: int) -> None:
        with fitz.open(pdf_path) as source, fitz.open() as output:
            if not 0 <= page_index < source.page_count:
                raise IndexError(page_index)
            if page_index:
                output.insert_pdf(source, from_page=0, to_page=page_index - 1)
            page = output.new_page(width=width, height=height)
            page.insert_image(page.rect, stream=image_bytes)
            if page_index + 1 < source.page_count:
                output.insert_pdf(source, from_page=page_index + 1, to_page=source.page_count - 1)
            self._atomic_save(output, pdf_path)

    def insert_image_pages(self, pdf_path: Path, image_paths: list[Path], insertion_index: int) -> None:
        """Insert raster images as regular PDF pages at a zero-based position."""
        self.insert_files_as_pages(pdf_path, image_paths, insertion_index)

    def insert_files_as_pages(self, pdf_path: Path, file_paths: list[Path], insertion_index: int) -> int:
        """Insert every page from PDFs and one page per raster image."""
        if not file_paths:
            raise ValueError("Please choose at least one PDF or image file.")
        prepared: list[tuple[str, Path | bytes, int]] = []
        total_pages = 0
        for value in file_paths:
            path = Path(value)
            if path.suffix.lower() == ".pdf":
                count = self.validate(path)
                prepared.append(("pdf", path, count))
                total_pages += count
            else:
                prepared.append(("image", self._prepare_page_image(path), 1))
                total_pages += 1
        with fitz.open(pdf_path) as source, fitz.open() as output:
            if not 0 <= insertion_index <= source.page_count:
                raise IndexError(insertion_index)
            reference_index = min(max(insertion_index - 1, 0), source.page_count - 1)
            page_size = source[reference_index].rect
            if insertion_index:
                output.insert_pdf(source, from_page=0, to_page=insertion_index - 1)
            for kind, prepared_source, _count in prepared:
                if kind == "pdf":
                    with fitz.open(prepared_source) as added_pdf:
                        output.insert_pdf(added_pdf)
                else:
                    page = output.new_page(width=page_size.width, height=page_size.height)
                    page.insert_image(page.rect, stream=prepared_source, keep_proportion=True)
            if insertion_index < source.page_count:
                output.insert_pdf(source, from_page=insertion_index, to_page=source.page_count - 1)
            metadata = dict(source.metadata)
            output.set_metadata({key: value for key, value in metadata.items() if isinstance(value, str)})
            self._atomic_save(output, pdf_path)
        return total_pages

    def image_to_pdf(self, image_path: Path, destination: Path) -> None:
        """Convert one raster image into a printable one-page PDF."""
        image_bytes = self._prepare_page_image(image_path)
        pixmap = fitz.Pixmap(image_bytes)
        width, height = (842.0, 595.0) if pixmap.width > pixmap.height else (595.0, 842.0)
        with fitz.open() as output:
            page = output.new_page(width=width, height=height)
            page.insert_image(page.rect, stream=image_bytes, keep_proportion=True)
            self._atomic_save(output, destination)

    def validate_image(self, image_path: Path) -> None:
        self._prepare_page_image(image_path)

    @staticmethod
    def _prepare_page_image(path: Path) -> bytes:
        if not path.is_file():
            raise ValueError("Please choose an image file.")
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")
                image.thumbnail((6000, 6000), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=94, optimize=True)
                return output.getvalue()
        except (UnidentifiedImageError, OSError) as error:
            raise ValueError(f"The image {path.name} is damaged or unsupported.") from error

    def reset_page(self, original_pdf: Path, current_pdf: Path, page_index: int, original_page_index: int | None = None) -> None:
        original_page_index = page_index if original_page_index is None else original_page_index
        with fitz.open(original_pdf) as original, fitz.open(current_pdf) as current, fitz.open() as output:
            if not 0 <= original_page_index < original.page_count:
                raise ValueError("This page cannot be reset because it was not present in the original PDF.")
            if page_index:
                output.insert_pdf(current, from_page=0, to_page=page_index - 1)
            output.insert_pdf(original, from_page=original_page_index, to_page=original_page_index)
            if page_index + 1 < current.page_count:
                output.insert_pdf(current, from_page=page_index + 1, to_page=current.page_count - 1)
            self._atomic_save(output, current_pdf)

    @staticmethod
    def _atomic_save(document: fitz.Document, destination: Path) -> None:
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".pdf", dir=destination.parent)
        os.close(fd)
        try:
            document.save(temporary, garbage=4, deflate=True)
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    @staticmethod
    def copy_pdf(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.copying")
        try:
            with source.open("rb") as incoming, temporary.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
