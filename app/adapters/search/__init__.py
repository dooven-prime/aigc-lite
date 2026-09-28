"""Search backend adapters."""

from .lexical import LexicalSearchBackend
from .sqlite_fts import SQLiteFTS5SearchBackend

__all__ = ["LexicalSearchBackend", "SQLiteFTS5SearchBackend"]
