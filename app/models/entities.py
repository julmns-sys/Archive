from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Shelf:
    id: int
    storage_place_id: int
    name: str
    position: int


@dataclass(slots=True)
class StoragePlace:
    id: int
    type: str
    number: int
    display_name: str
    code: str
    physical_location: str
    uses_shelves: bool
    shelves: list[Shelf] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.display_name} ({self.physical_location})"


@dataclass(slots=True)
class Category:
    id: int
    name: str


@dataclass(slots=True)
class Tag:
    id: int
    name: str


@dataclass(slots=True)
class Book:
    id: int
    title: str
    book_code: str
    storage_place_id: int | None
    shelf_id: int | None
    notes: str
    original_pdf_path: str
    current_pdf_path: str
    created_at: str
    updated_at: str
    storage_display_name: str = ""
    physical_location: str = ""
    shelf_name: str | None = None
    categories: list[Category] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    thumbnail_path: str | None = None

    @property
    def location(self) -> str:
        if self.storage_place_id is None:
            return "No location"
        base = self.storage_display_name
        if self.physical_location:
            base += f" ({self.physical_location})"
        if self.shelf_name:
            base += f", {self.shelf_name}"
        return base
