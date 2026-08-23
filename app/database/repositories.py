from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable

from app.models import Book, Category, Shelf, StoragePlace, Tag

from .connection import Database


def _prefix(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value.upper())
    if not words:
        return "ST"
    return ("".join(word[0] for word in words) if len(words) > 1 else words[0][:2]).ljust(2, "X")


class ArchiveRepository:
    def __init__(self, database: Database):
        self.database = database

    def suggest_storage(self, storage_type: str) -> tuple[int, str, str]:
        storage_type = storage_type.strip() or "Storage"
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(number), 0) + 1 FROM storage_places WHERE type = ? COLLATE NOCASE",
                (storage_type,),
            ).fetchone()
        number = int(row[0])
        prefix = _prefix(storage_type)
        return number, f"{storage_type} {number:02d}", f"{prefix}{number:02d}"

    def create_storage_place(
        self,
        storage_type: str,
        number: int,
        display_name: str,
        code: str,
        physical_location: str,
        shelf_names: Iterable[str] = (),
    ) -> int:
        shelf_names = [name.strip() for name in shelf_names if name.strip()]
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """INSERT INTO storage_places
                   (type, number, display_name, code, physical_location, uses_shelves)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (storage_type.strip(), number, display_name.strip(), code.strip().upper(), physical_location.strip(), bool(shelf_names)),
            )
            storage_id = int(cursor.lastrowid)
            connection.executemany(
                "INSERT INTO shelves(storage_place_id, name, position) VALUES (?, ?, ?)",
                [(storage_id, name, position) for position, name in enumerate(shelf_names, 1)],
            )
        return storage_id

    def update_storage_place(
        self,
        storage_id: int,
        storage_type: str,
        number: int,
        display_name: str,
        code: str,
        physical_location: str,
        shelf_names: Iterable[str],
    ) -> None:
        shelf_names = [name.strip() for name in shelf_names if name.strip()]
        with self.database.transaction() as connection:
            connection.execute(
                """UPDATE storage_places SET type=?, number=?, display_name=?, code=?,
                   physical_location=?, uses_shelves=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (storage_type.strip(), number, display_name.strip(), code.strip().upper(), physical_location.strip(), bool(shelf_names), storage_id),
            )
            existing = connection.execute(
                "SELECT id,name,position FROM shelves WHERE storage_place_id=? ORDER BY position",
                (storage_id,),
            ).fetchall()
            available = list(existing)
            assignments: dict[int, sqlite3.Row] = {}
            # First retain shelves whose names are unchanged. This ensures that
            # removing one shelf does not shift books onto a neighbouring one.
            for index, name in enumerate(shelf_names):
                match = next((row for row in available if row["name"].casefold() == name.casefold()), None)
                if match is not None:
                    assignments[index] = match
                    available.remove(match)
            # Remaining rows represent renames; reuse them in visible order so
            # books assigned to a renamed shelf keep that assignment.
            for index in range(len(shelf_names)):
                if index not in assignments and available:
                    assignments[index] = available.pop(0)

            position_offset = max((row["position"] for row in existing), default=0) + len(existing) + 1
            for row in existing:
                connection.execute(
                    "UPDATE shelves SET name=?,position=? WHERE id=?",
                    (f"__editing_shelf_{row['id']}__", row["position"] + position_offset, row["id"]),
                )
            for index, name in enumerate(shelf_names):
                if index in assignments:
                    connection.execute(
                        "UPDATE shelves SET name=?,position=? WHERE id=?",
                        (name, index + 1, assignments[index]["id"]),
                    )
                else:
                    connection.execute(
                        "INSERT INTO shelves(storage_place_id,name,position) VALUES(?,?,?)",
                        (storage_id, name, index + 1),
                    )
            assigned_ids = {row["id"] for row in assignments.values()}
            for row in existing:
                if row["id"] in assigned_ids:
                    continue
                connection.execute("DELETE FROM shelves WHERE id=?", (row["id"],))

    def list_storage_places(self) -> list[StoragePlace]:
        with self.database.read() as connection:
            storage_rows = connection.execute("SELECT * FROM storage_places ORDER BY type COLLATE NOCASE, number").fetchall()
            shelf_rows = connection.execute("SELECT * FROM shelves ORDER BY storage_place_id, position").fetchall()
        shelves: dict[int, list[Shelf]] = {}
        for row in shelf_rows:
            shelves.setdefault(row["storage_place_id"], []).append(Shelf(row["id"], row["storage_place_id"], row["name"], row["position"]))
        return [
            StoragePlace(row["id"], row["type"], row["number"], row["display_name"], row["code"], row["physical_location"], bool(row["uses_shelves"]), shelves.get(row["id"], []))
            for row in storage_rows
        ]

    def get_storage_place(self, storage_id: int) -> StoragePlace:
        for place in self.list_storage_places():
            if place.id == storage_id:
                return place
        raise KeyError(storage_id)

    def delete_storage_place(self, storage_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM storage_places WHERE id=?", (storage_id,))

    def create_category(self, name: str) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute("INSERT INTO categories(name) VALUES (?)", (name.strip(),))
            return int(cursor.lastrowid)

    def rename_category(self, category_id: int, name: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE categories SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (name.strip(), category_id))

    def delete_category(self, category_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM categories WHERE id=?", (category_id,))

    def list_categories(self) -> list[Category]:
        with self.database.read() as connection:
            rows = connection.execute("SELECT id,name FROM categories ORDER BY name COLLATE NOCASE").fetchall()
        return [Category(row["id"], row["name"]) for row in rows]

    def create_tag(self, name: str) -> int:
        with self.database.transaction() as connection:
            cursor = connection.execute("INSERT INTO tags(name) VALUES (?)", (name.strip(),))
            return int(cursor.lastrowid)

    def rename_tag(self, tag_id: int, name: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE tags SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (name.strip(), tag_id))

    def delete_tag(self, tag_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM tags WHERE id=?", (tag_id,))

    def list_tags(self) -> list[Tag]:
        with self.database.read() as connection:
            rows = connection.execute("SELECT id,name FROM tags ORDER BY name COLLATE NOCASE").fetchall()
        return [Tag(row["id"], row["name"]) for row in rows]

    @staticmethod
    def next_book_code(connection: sqlite3.Connection, storage_id: int | None) -> str:
        if storage_id is None:
            prefix = "NL-B"
        else:
            storage = connection.execute("SELECT code FROM storage_places WHERE id=?", (storage_id,)).fetchone()
            if storage is None:
                raise ValueError("The selected storage place no longer exists.")
            prefix = storage["code"] + "-B"
        # Book codes are globally unique. A storage short code can be changed
        # and later reused by another storage place, while older books keep
        # their original codes. Scan the whole catalog so a reused prefix can
        # never generate a code that an older book already owns.
        rows = connection.execute("SELECT book_code FROM books").fetchall()
        numbers = []
        for row in rows:
            match = re.fullmatch(re.escape(prefix) + r"(\d+)", row["book_code"], re.IGNORECASE)
            if match:
                numbers.append(int(match.group(1)))
        return f"{prefix}{max(numbers, default=0) + 1:02d}"

    def insert_book(
        self,
        connection: sqlite3.Connection,
        title: str,
        storage_id: int | None,
        shelf_id: int | None,
        notes: str,
        original_pdf_path: str,
        current_pdf_path: str,
        category_ids: Iterable[int],
        tag_ids: Iterable[int] = (),
    ) -> tuple[int, str]:
        if shelf_id is not None:
            shelf = connection.execute("SELECT storage_place_id FROM shelves WHERE id=?", (shelf_id,)).fetchone()
            if shelf is None or shelf[0] != storage_id:
                raise ValueError("The selected shelf does not belong to the storage place.")
        code = self.next_book_code(connection, storage_id)
        cursor = connection.execute(
            """INSERT INTO books(title,book_code,storage_place_id,shelf_id,notes,original_pdf_path,current_pdf_path)
               VALUES(?,?,?,?,?,?,?)""",
            (title.strip(), code, storage_id, shelf_id, notes.strip(), original_pdf_path, current_pdf_path),
        )
        book_id = int(cursor.lastrowid)
        connection.executemany(
            "INSERT INTO book_categories(book_id,category_id) VALUES(?,?)",
            [(book_id, category_id) for category_id in dict.fromkeys(category_ids)],
        )
        connection.executemany(
            "INSERT INTO book_tags(book_id,tag_id) VALUES(?,?)",
            [(book_id, tag_id) for tag_id in dict.fromkeys(tag_ids)],
        )
        return book_id, code

    def _book_from_row(self, row: sqlite3.Row, categories: list[Category], tags: list[Tag]) -> Book:
        return Book(
            id=row["id"], title=row["title"], book_code=row["book_code"], storage_place_id=row["storage_place_id"],
            shelf_id=row["shelf_id"], notes=row["notes"], original_pdf_path=row["original_pdf_path"], current_pdf_path=row["current_pdf_path"],
            created_at=row["created_at"], updated_at=row["updated_at"], storage_display_name=row["display_name"] or "",
            physical_location=row["physical_location"] or "", shelf_name=row["shelf_name"], categories=categories, tags=tags,
        )

    def get_book(self, book_id: int) -> Book:
        with self.database.read() as connection:
            row = connection.execute(
                """SELECT b.*,s.display_name,s.physical_location,sh.name AS shelf_name
                   FROM books b LEFT JOIN storage_places s ON s.id=b.storage_place_id
                   LEFT JOIN shelves sh ON sh.id=b.shelf_id WHERE b.id=?""", (book_id,),
            ).fetchone()
            if row is None:
                raise KeyError(book_id)
            category_rows = connection.execute(
                "SELECT c.id,c.name FROM categories c JOIN book_categories bc ON bc.category_id=c.id WHERE bc.book_id=? ORDER BY c.name", (book_id,),
            ).fetchall()
            tag_rows = connection.execute(
                "SELECT t.id,t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=? ORDER BY t.name", (book_id,),
            ).fetchall()
        return self._book_from_row(
            row,
            [Category(item["id"], item["name"]) for item in category_rows],
            [Tag(item["id"], item["name"]) for item in tag_rows],
        )

    def unique_book_title(self, title: str, exclude_book_id: int | None = None) -> str:
        """Return a catalog-wide title, adding a numeric suffix when needed."""
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Please enter a title.")
        query = "SELECT title FROM books"
        arguments: tuple[int, ...] = ()
        if exclude_book_id is not None:
            query += " WHERE id<>?"
            arguments = (exclude_book_id,)
        with self.database.read() as connection:
            used_titles = {row["title"].strip().casefold() for row in connection.execute(query, arguments)}
        if clean_title.casefold() not in used_titles:
            return clean_title
        number = 2
        while f"{clean_title} {number}".casefold() in used_titles:
            number += 1
        return f"{clean_title} {number}"

    def list_books(self, search: str = "", category_id: int | None = None, storage_id: int | str | None = None, tag_id: int | None = None) -> list[Book]:
        conditions: list[str] = []
        arguments: list[object] = []
        if search.strip():
            # A leading # is optional, so both "drawings" and "#drawings" find a tag.
            search_text = search.strip()
            normalized_search = search_text[1:].strip() if search_text.startswith("#") else search_text
            term = f"%{normalized_search}%"
            conditions.append("(b.title LIKE ? OR b.book_code LIKE ? OR s.display_name LIKE ? OR s.physical_location LIKE ? OR sh.name LIKE ? OR EXISTS (SELECT 1 FROM book_categories bx JOIN categories cx ON cx.id=bx.category_id WHERE bx.book_id=b.id AND cx.name LIKE ?) OR EXISTS (SELECT 1 FROM book_tags tx JOIN tags t ON t.id=tx.tag_id WHERE tx.book_id=b.id AND t.name LIKE ?))")
            arguments.extend([term] * 7)
        if category_id is not None:
            conditions.append("EXISTS (SELECT 1 FROM book_categories bf WHERE bf.book_id=b.id AND bf.category_id=?)")
            arguments.append(category_id)
        if storage_id == "no_location":
            conditions.append("b.storage_place_id IS NULL")
        elif storage_id is not None:
            conditions.append("b.storage_place_id=?")
            arguments.append(storage_id)
        if tag_id is not None:
            conditions.append("EXISTS (SELECT 1 FROM book_tags tf WHERE tf.book_id=b.id AND tf.tag_id=?)")
            arguments.append(tag_id)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        with self.database.read() as connection:
            rows = connection.execute(
                f"""SELECT b.*,s.display_name,s.physical_location,sh.name AS shelf_name
                    FROM books b LEFT JOIN storage_places s ON s.id=b.storage_place_id
                    LEFT JOIN shelves sh ON sh.id=b.shelf_id {where}
                    ORDER BY b.title COLLATE NOCASE""", arguments,
            ).fetchall()
            categories_by_book: dict[int, list[Category]] = {}
            for item in connection.execute("SELECT bc.book_id,c.id,c.name FROM book_categories bc JOIN categories c ON c.id=bc.category_id ORDER BY c.name"):
                categories_by_book.setdefault(item["book_id"], []).append(Category(item["id"], item["name"]))
            tags_by_book: dict[int, list[Tag]] = {}
            for item in connection.execute("SELECT bt.book_id,t.id,t.name FROM book_tags bt JOIN tags t ON t.id=bt.tag_id ORDER BY t.name"):
                tags_by_book.setdefault(item["book_id"], []).append(Tag(item["id"], item["name"]))
        return [self._book_from_row(row, categories_by_book.get(row["id"], []), tags_by_book.get(row["id"], [])) for row in rows]

    def update_book_info(self, book_id: int, title: str, notes: str, category_ids: Iterable[int], tag_ids: Iterable[int] = ()) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE books SET title=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (title.strip(), notes.strip(), book_id))
            connection.execute("DELETE FROM book_categories WHERE book_id=?", (book_id,))
            connection.executemany("INSERT INTO book_categories(book_id,category_id) VALUES(?,?)", [(book_id, value) for value in dict.fromkeys(category_ids)])
            connection.execute("DELETE FROM book_tags WHERE book_id=?", (book_id,))
            connection.executemany("INSERT INTO book_tags(book_id,tag_id) VALUES(?,?)", [(book_id, value) for value in dict.fromkeys(tag_ids)])

    def preview_move_code(self, book_id: int, storage_id: int | None) -> str:
        with self.database.read() as connection:
            book = connection.execute("SELECT storage_place_id,book_code FROM books WHERE id=?", (book_id,)).fetchone()
            if book is None:
                raise KeyError(book_id)
            return book["book_code"] if book["storage_place_id"] == storage_id else self.next_book_code(connection, storage_id)

    def move_book(self, book_id: int, storage_id: int | None, shelf_id: int | None) -> tuple[str, str]:
        with self.database.transaction() as connection:
            old = connection.execute("SELECT book_code,storage_place_id FROM books WHERE id=?", (book_id,)).fetchone()
            if old is None:
                raise KeyError(book_id)
            new_code = old["book_code"] if old["storage_place_id"] == storage_id else self.next_book_code(connection, storage_id)
            if shelf_id is not None:
                row = connection.execute("SELECT storage_place_id FROM shelves WHERE id=?", (shelf_id,)).fetchone()
                if row is None or row[0] != storage_id:
                    raise ValueError("The selected shelf does not belong to the storage place.")
            connection.execute("UPDATE books SET storage_place_id=?,shelf_id=?,book_code=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (storage_id, shelf_id, new_code, book_id))
            return old["book_code"], new_code

    def delete_book(self, book_id: int) -> None:
        self.delete_books([book_id])

    def delete_books(self, book_ids: Iterable[int]) -> None:
        ids = list(dict.fromkeys(book_ids))
        if not ids:
            return
        with self.database.transaction() as connection:
            connection.executemany("DELETE FROM books WHERE id=?", [(book_id,) for book_id in ids])

    def touch_book(self, book_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE books SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (book_id,))
