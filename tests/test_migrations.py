from __future__ import annotations

import sqlite3

from app.database import Database
from app.database.schema import initialize_schema


def test_existing_version_one_database_is_migrated_to_tags(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version(version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_version(version) VALUES (1)")
    connection.commit()
    connection.close()

    database = Database(path)
    initialize_schema(database)
    with database.read() as migrated:
        assert migrated.execute("SELECT version FROM schema_version").fetchone()[0] == 3
        tables = {row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tags", "book_tags"}.issubset(tables)


def test_existing_books_are_migrated_to_optional_storage_without_losing_links(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_version(version INTEGER NOT NULL);
        INSERT INTO schema_version(version) VALUES (2);
        CREATE TABLE storage_places (
            id INTEGER PRIMARY KEY, type TEXT NOT NULL, number INTEGER NOT NULL,
            display_name TEXT NOT NULL, code TEXT NOT NULL UNIQUE,
            physical_location TEXT NOT NULL, uses_shelves INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE shelves (
            id INTEGER PRIMARY KEY, storage_place_id INTEGER NOT NULL REFERENCES storage_places(id),
            name TEXT NOT NULL, position INTEGER NOT NULL
        );
        CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
        CREATE TABLE books (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, book_code TEXT NOT NULL UNIQUE,
            storage_place_id INTEGER NOT NULL REFERENCES storage_places(id) ON DELETE RESTRICT,
            shelf_id INTEGER REFERENCES shelves(id) ON DELETE SET NULL, notes TEXT NOT NULL DEFAULT '',
            original_pdf_path TEXT NOT NULL, current_pdf_path TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE book_categories (
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            PRIMARY KEY(book_id, category_id)
        );
        CREATE TABLE book_tags (
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY(book_id, tag_id)
        );
        INSERT INTO storage_places(id,type,number,display_name,code,physical_location,uses_shelves)
            VALUES(1,'Box',1,'Box 01','BX01','Office',0);
        INSERT INTO categories(id,name) VALUES(1,'Art');
        INSERT INTO tags(id,name) VALUES(1,'Digital');
        INSERT INTO books(id,title,book_code,storage_place_id,notes,original_pdf_path,current_pdf_path)
            VALUES(1,'Existing','BX01-B01',1,'','original.pdf','current.pdf');
        INSERT INTO book_categories(book_id,category_id) VALUES(1,1);
        INSERT INTO book_tags(book_id,tag_id) VALUES(1,1);
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    initialize_schema(database)

    with database.read() as migrated:
        storage_column = next(row for row in migrated.execute("PRAGMA table_info(books)") if row[1] == "storage_place_id")
        assert storage_column[3] == 0
        assert migrated.execute("SELECT title FROM books").fetchone()[0] == "Existing"
        assert migrated.execute("SELECT * FROM book_categories").fetchone() is not None
        assert migrated.execute("SELECT * FROM book_tags").fetchone() is not None
        migrated.execute(
            "INSERT INTO books(title,book_code,storage_place_id,notes,original_pdf_path,current_pdf_path) VALUES('Digital','NL-B01',NULL,'','o.pdf','c.pdf')"
        )
