from __future__ import annotations

import re


def safe_title(title: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", title.strip(), flags=re.UNICODE).strip("_.")
    return (cleaned or "Untitled")[:limit]


def book_directory_name(code: str, title: str) -> str:
    return f"{code}__{safe_title(title)}"

