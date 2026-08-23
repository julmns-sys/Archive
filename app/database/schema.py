from __future__ import annotations

from .connection import Database


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS storage_places (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL COLLATE NOCASE,
    number INTEGER NOT NULL CHECK(number > 0),
    display_name TEXT NOT NULL,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    physical_location TEXT NOT NULL,
    uses_shelves INTEGER NOT NULL DEFAULT 0 CHECK(uses_shelves IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(type, number)
);
CREATE TABLE IF NOT EXISTS shelves (
    id INTEGER PRIMARY KEY,
    storage_place_id INTEGER NOT NULL REFERENCES storage_places(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position > 0),
    UNIQUE(storage_place_id, name),
    UNIQUE(storage_place_id, position)
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    book_code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    storage_place_id INTEGER REFERENCES storage_places(id) ON DELETE RESTRICT,
    shelf_id INTEGER REFERENCES shelves(id) ON DELETE SET NULL,
    notes TEXT NOT NULL DEFAULT '',
    original_pdf_path TEXT NOT NULL,
    current_pdf_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS book_categories (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY(book_id, category_id)
);
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS book_tags (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY(book_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_books_title ON books(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_books_storage ON books(storage_place_id);
CREATE INDEX IF NOT EXISTS idx_book_categories_category ON book_categories(category_id);
CREATE INDEX IF NOT EXISTS idx_book_tags_tag ON book_tags(tag_id);
"""


def initialize_schema(database: Database) -> None:
    with database.transaction() as connection:
        connection.executescript(SCHEMA)
        storage_column = next(
            (row for row in connection.execute("PRAGMA table_info(books)") if row[1] == "storage_place_id"),
            None,
        )
        if storage_column is not None and storage_column[3]:
            # SQLite cannot remove NOT NULL in place. Rebuild the catalog table
            # while preserving its category and tag relationships.
            category_links = [tuple(row) for row in connection.execute("SELECT book_id,category_id FROM book_categories")]
            tag_links = [tuple(row) for row in connection.execute("SELECT book_id,tag_id FROM book_tags")]
            connection.execute(
                """CREATE TABLE books_optional_storage (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    book_code TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    storage_place_id INTEGER REFERENCES storage_places(id) ON DELETE RESTRICT,
                    shelf_id INTEGER REFERENCES shelves(id) ON DELETE SET NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    original_pdf_path TEXT NOT NULL,
                    current_pdf_path TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                """INSERT INTO books_optional_storage
                   (id,title,book_code,storage_place_id,shelf_id,notes,original_pdf_path,current_pdf_path,created_at,updated_at)
                   SELECT id,title,book_code,storage_place_id,shelf_id,notes,original_pdf_path,current_pdf_path,created_at,updated_at FROM books"""
            )
            connection.execute("DROP TABLE books")
            connection.execute("ALTER TABLE books_optional_storage RENAME TO books")
            connection.executemany("INSERT INTO book_categories(book_id,category_id) VALUES(?,?)", category_links)
            connection.executemany("INSERT INTO book_tags(book_id,tag_id) VALUES(?,?)", tag_links)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_books_title ON books(title COLLATE NOCASE)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_books_storage ON books(storage_place_id)")
        count = connection.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
        if count == 0:
            connection.execute("INSERT INTO schema_version(version) VALUES (3)")
        else:
            connection.execute("UPDATE schema_version SET version = 3")
