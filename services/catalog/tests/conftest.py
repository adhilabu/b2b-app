import pytest
import uuid
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, ARRAY, UUID as PG_UUID
from sqlalchemy.types import UUID as SQL_UUID

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"

@compiles(ARRAY, "sqlite")
def compile_array_sqlite(element, compiler, **kw):
    return "TEXT"

# Patch PG_UUID bind processor to handle string inputs on SQLite
original_pg_bind = PG_UUID.bind_processor
def patched_pg_bind(self, dialect):
    proc = original_pg_bind(self, dialect)
    if proc:
        def new_proc(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except ValueError:
                    pass
            return proc(value)
        return new_proc
    return proc
PG_UUID.bind_processor = patched_pg_bind

# Patch SQL_UUID bind processor to handle string inputs on SQLite
original_sql_bind = SQL_UUID.bind_processor
def patched_sql_bind(self, dialect):
    proc = original_sql_bind(self, dialect)
    if proc:
        def new_proc(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except ValueError:
                    pass
            return proc(value)
        return new_proc
    return proc
SQL_UUID.bind_processor = patched_sql_bind
