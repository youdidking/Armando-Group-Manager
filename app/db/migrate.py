"""Lightweight, additive schema migrations.

The project intentionally avoids a full Alembic setup for single-file
deployment simplicity, but schema changes are still versioned and idempotent.
Every step is written so that:

* running it twice is harmless,
* existing user data is never dropped,
* a failed step rolls back only that step.
"""

from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("armando.db.migrate")

# Version -> list of SQL statements (executed once, in order).
MIGRATIONS: dict[int, list[str]] = {
    1: [
        "CREATE INDEX IF NOT EXISTS ix_chat_members_warns ON chat_members (chat_id, warn_count)",
        "CREATE INDEX IF NOT EXISTS ix_warnings_active ON warnings (chat_id, active)",
    ],
    2: [
        "CREATE INDEX IF NOT EXISTS ix_mod_actions_expires ON moderation_actions (expires_at)",
        "CREATE INDEX IF NOT EXISTS ix_filters_chat_blocklist ON filters (chat_id, is_blocklist)",
    ],
    3: [
        "CREATE INDEX IF NOT EXISTS ix_notes_chat_name ON notes (chat_id, name)",
        "CREATE INDEX IF NOT EXISTS ix_ban_records_user ON ban_records (user_id, active)",
    ],
}

# SQLite has no "ADD COLUMN IF NOT EXISTS", so this step inspects the table
# first: a plain statement list would raise (and log a warning) on every
# single start-up once the columns exist.
async def _migration_4(engine: AsyncEngine) -> None:
    """Sticky ban, identity-change notices and auto-approved join requests."""
    await add_column(engine, "chat_settings", "sticky_ban",
                     "BOOLEAN NOT NULL DEFAULT 1")
    await add_column(engine, "chat_settings", "announce_name_change",
                     "BOOLEAN NOT NULL DEFAULT 1")
    await add_column(engine, "chat_settings", "announce_username_change",
                     "BOOLEAN NOT NULL DEFAULT 1")
    await add_column(engine, "chat_settings", "auto_approve_join_requests",
                     "BOOLEAN NOT NULL DEFAULT 0")


MIGRATIONS[4] = _migration_4

CURRENT_VERSION = max(MIGRATIONS) if MIGRATIONS else 0


async def _ensure_version_table(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            " id INTEGER PRIMARY KEY,"
            " version INTEGER NOT NULL DEFAULT 0,"
            " applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        result = await conn.execute(text("SELECT COUNT(*) FROM schema_version"))
        if (result.scalar() or 0) == 0:
            await conn.execute(text("INSERT INTO schema_version (id, version, applied_at) VALUES (1, 0, CURRENT_TIMESTAMP)"))


async def _current_version(engine: AsyncEngine) -> int:
    await _ensure_version_table(engine)
    async with engine.begin() as conn:
        result = await conn.execute(text("SELECT version FROM schema_version WHERE id = 1"))
        return int(result.scalar() or 0)


async def run_migrations(engine: AsyncEngine) -> int:
    version = await _current_version(engine)
    applied = 0
    for target in sorted(v for v in MIGRATIONS if v > version):
        step = MIGRATIONS[target]
        try:
            if callable(step):
                await step(engine)
                async with engine.begin() as conn:
                    await conn.execute(
                        text("UPDATE schema_version SET version = :v WHERE id = 1"),
                        {"v": target})
            else:
                async with engine.begin() as conn:
                    for statement in step:
                        await conn.execute(text(statement))
                    await conn.execute(
                        text("UPDATE schema_version SET version = :v WHERE id = 1"),
                        {"v": target})
            applied += 1
            logger.info("migration %s applied", target)
        except Exception as exc:  # noqa: BLE001 - never fail startup on a duplicate step
            logger.warning("migration %s skipped: %s", target, exc)
    return applied


async def check_missing_columns(engine: AsyncEngine, table: str, columns: list[str]) -> list[str]:
    """Helper used at runtime to add columns introduced after initial deploy."""

    def _inspect(sync_conn):
        inspector = inspect(sync_conn)
        if not inspector.has_table(table):
            return list(columns)
        existing = {c["name"] for c in inspector.get_columns(table)}
        return [c for c in columns if c not in existing]

    async with engine.begin() as conn:
        return await conn.run_sync(_inspect)


async def add_column(engine: AsyncEngine, table: str, column: str, ddl: str) -> bool:
    missing = await check_missing_columns(engine, table, [column])
    if not missing:
        return False
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        logger.info("column added: %s.%s", table, column)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not add %s.%s: %s", table, column, exc)
        return False


REGISTERED_RUNTIME_FIXUPS: list[Callable] = []
