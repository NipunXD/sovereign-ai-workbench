"""SQLModel base classes and shared column types."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel

# Explicit constraint naming, so Alembic autogenerate produces stable migration
# names instead of anonymous constraints that churn on every regeneration.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)
SQLModel.metadata = metadata


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp that is always timezone-aware UTC on the way in and out.

    Postgres will happily store a naive datetime and hand it back naive, which
    then compares incorrectly against aware ones. Normalising here removes a
    whole class of subtle time bugs from the audit log and token expiry.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            from datetime import UTC

            return value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            from datetime import UTC

            return value.replace(tzinfo=UTC)
        return value


#: Postgres JSONB for every structured column. Indexable, and unlike JSON it
#: does not re-parse text on each read.
JSONBColumn = JSONB
