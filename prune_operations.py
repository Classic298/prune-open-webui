"""
Prune Operations - All Helper Functions

This module contains all the helper functions from backend/open_webui/routers/prune.py
that perform the actual pruning operations, counting, and cleanup.
"""

import asyncio
import inspect
import logging
import time
from pathlib import Path
from typing import AsyncGenerator, Optional, Set, Callable, Any
from sqlalchemy import select, text, func, and_, or_, not_, delete
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Exception types raised for missing tables across database dialects.
# SQLite raises OperationalError; PostgreSQL raises ProgrammingError.
_TABLE_MISSING_ERRORS = (OperationalError, ProgrammingError)


def _is_table_missing_error(exc: Exception) -> bool:
    """Return True if the exception indicates a missing table or relation."""
    msg = str(exc).lower()
    # SQLite: "no such table: xxx"
    # PostgreSQL: 'relation "xxx" does not exist' / 'undefined table'
    return (
        'no such table' in msg
        or ('relation' in msg and 'does not exist' in msg)
        or 'undefined table' in msg
    )


async def retry_on_db_lock(func: Callable, max_retries: int = 3, base_delay: float = 0.5) -> Any:
    """
    Retry an async database operation if it fails due to database lock.
    Uses exponential backoff: 0.5s, 1s, 2s

    Args:
        func: Async function to retry
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds (doubles each retry)

    Returns:
        Result from the function

    Raises:
        Last exception if all retries fail
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except OperationalError as e:
            last_exception = e
            if 'database is locked' in str(e).lower() and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                log.warning(f"Database locked, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(delay)
            else:
                raise

    # This should never be reached, but just in case
    raise last_exception


async def stream_rows(db, *columns, filter_clause=None, batch_size=5000):
    """
    Yield rows in batches using keyset pagination on the first column.

    Unlike stream_results=True (server-side cursors), this approach
    guarantees bounded memory regardless of DB driver or transaction
    configuration.  Each batch executes a fresh LIMIT query.

    IMPORTANT: The first column is used as the keyset cursor and must be
    both **unique** and **non-nullable**.  Non-unique keys can cause rows
    to be silently skipped at batch boundaries (WHERE col > last_key skips
    remaining rows with the same value).  NULLs are excluded automatically
    to prevent infinite re-fetch.

    Args:
        db: SQLAlchemy async session
        *columns: One or more ORM column descriptors to SELECT.
                  The first column is used for ordering/keysetting
                  and MUST be unique (typically a primary key).
        filter_clause: Optional SQLAlchemy filter expression
        batch_size: Number of rows per batch (default 5000)

    Raises:
        ValueError: If no columns are provided or batch_size is invalid

    Yields:
        Row tuples from the query
    """
    if not columns:
        raise ValueError("stream_rows requires at least one column")
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    order_col = columns[0]
    base_stmt = select(*columns).where(order_col.isnot(None))
    if filter_clause is not None:
        base_stmt = base_stmt.where(filter_clause)
    base_stmt = base_stmt.order_by(order_col)

    last_key = None
    while True:
        stmt = base_stmt
        if last_key is not None:
            stmt = stmt.where(order_col > last_key)
        stmt = stmt.limit(batch_size)
        result = await db.execute(stmt)
        batch = result.fetchall()
        if not batch:
            break
        for row in batch:
            yield row
        last_key = batch[-1][0]
        if len(batch) < batch_size:
            break


# Import Open WebUI modules using compatibility layer (handles pip/docker/git installs)
try:
    from prune_imports import (
        Users, Chat, Chats, ChatFile, ChatMessage, Message, File, Files, Note, Notes,
        Prompt, Prompts, Model, Models, Knowledge, Knowledges,
        Function, Functions, Tool, Tools, Skill, Skills,
        Automation, AutomationRun, Automations, AutomationRuns,
        Folder, Folders, FolderModel, Storage,
        get_async_db, get_async_db_context, CACHE_DIR
    )
except ImportError as e:
    log.error(f"Failed to import Open WebUI modules: {e}")
    log.error("This module requires Open WebUI backend modules to be importable")
    raise

from prune_models import PruneDataForm
from prune_core import collect_file_ids_from_dict


async def get_kb_user_map() -> dict:
    """Return {kb_id: user_id} from the knowledge table using lightweight SQL.

    This replaces Knowledges.get_knowledge_bases() which can OOM on large
    databases because SQLAlchemy eager-loads File objects through the
    knowledge_file relationship, pulling hundreds of MB of JSONB into memory.

    Raises on failure — callers (prune execution) must not proceed with an
    empty preservation set, as that could cause over-deletion.
    """
    async def _scan():
        result = {}
        async with get_async_db() as db:
            async for kb_id, uid in stream_rows(db, Knowledge.id, Knowledge.user_id):
                result[str(kb_id)] = str(uid)
        return result
    return await retry_on_db_lock(_scan)


# API Compatibility Helpers
async def get_all_folders(db: Optional[AsyncSession] = None):
    """
    Get all folders from database.
    Compatibility helper for newer Folders API that doesn't have get_all_folders().

    Args:
        db: Optional database session to reuse (for efficient bulk operations)
    """
    try:
        # Try new API first - if get_all_folders exists, use it
        if hasattr(Folders, 'get_all_folders'):
            # Check if the method supports db parameter
            if 'db' in inspect.signature(Folders.get_all_folders).parameters:
                return await Folders.get_all_folders(db=db)
            else:
                return await Folders.get_all_folders()

        # Otherwise query directly from database
        async with get_async_db_context(db) as session:
            result = await session.execute(select(Folder))
            folders = result.scalars().all()
            # Convert to FolderModel instances
            return [FolderModel.model_validate(f) for f in folders]
    except Exception as e:
        log.error(f"Error getting all folders: {e}")
        return []


async def count_inactive_users(
    inactive_days: Optional[int], exempt_admin: bool, exempt_pending: bool, all_users=None
) -> int:
    """Count users that would be deleted for inactivity.

    Args:
        inactive_days: Number of days of inactivity before deletion
        exempt_admin: Whether to exempt admin users
        exempt_pending: Whether to exempt pending users
        all_users: Optional pre-fetched list of users to avoid duplicate queries
    """
    if inactive_days is None:
        return 0

    cutoff_time = int(time.time()) - (inactive_days * 86400)
    count = 0

    try:
        if all_users is None:
            all_users = (await Users.get_users())["users"]
        for user in all_users:
            if exempt_admin and user.role == "admin":
                continue
            if exempt_pending and user.role == "pending":
                continue
            if user.last_active_at < cutoff_time:
                count += 1
    except Exception as e:
        log.debug(f"Error counting inactive users: {e}")

    return count


async def count_old_chats(
    days: Optional[int], exempt_archived: bool, exempt_in_folders: bool
) -> int:
    """Count chats that would be deleted by age.

    Uses a SQL COUNT query instead of loading full ORM objects,
    avoiding the expensive deserialization of large JSONB chat columns.
    """
    if days is None:
        return 0

    cutoff_time = int(time.time()) - (days * 86400)

    try:
        async with get_async_db_context() as db:
            # Build filter conditions
            conditions = [Chat.updated_at < cutoff_time]

            if exempt_archived:
                conditions.append(or_(Chat.archived == False, Chat.archived == None))

            if exempt_in_folders:
                folder_conditions = []
                if hasattr(Chat, 'folder_id'):
                    folder_conditions.append(Chat.folder_id == None)
                if hasattr(Chat, 'pinned'):
                    folder_conditions.append(or_(Chat.pinned == False, Chat.pinned == None))
                if folder_conditions:
                    conditions.append(and_(*folder_conditions))

            result = await db.execute(
                select(func.count(Chat.id)).where(and_(*conditions))
            )
            return result.scalar_one_or_none() or 0
    except Exception as e:
        log.debug(f"Error counting old chats: {e}")
        return 0


async def count_orphaned_records(
    form_data: PruneDataForm,
    active_file_ids: Set[str],
    active_user_ids: Set[str]
) -> dict:
    """Count orphaned database records that would be deleted.

    Uses SQL COUNT queries instead of loading full ORM objects,
    avoiding the expensive deserialization of large JSONB columns
    (chat history, tool specs, function content, etc.).
    """
    counts = {
        "chats": 0,
        "files": 0,
        "tools": 0,
        "functions": 0,
        "prompts": 0,
        "knowledge_bases": 0,
        "models": 0,
        "notes": 0,
        "skills": 0,
        "folders": 0,
        "chat_messages": 0,
        "automations": 0,
        "automation_runs": 0,
    }

    try:
        async with get_async_db_context() as db:
            # Count orphaned files.
            # A file is orphaned when it is not in the active_file_ids set OR
            # its owner is not in active_user_ids.
            #
            # Stream id+user_id and check membership in Python to avoid any
            # SQL IN() clauses — active_file_ids can be 100K+ entries and
            # active_user_ids can exceed SQLite's ~999 parameter limit on
            # large instances.
            orphaned_file_count = 0
            async for fid, uid in stream_rows(db, File.id, File.user_id):
                if str(fid) not in active_file_ids or str(uid) not in active_user_ids:
                    orphaned_file_count += 1
            counts["files"] = orphaned_file_count

            # Count other orphaned records by user ownership
            _table_flag_map = [
                ("chats",          Chat,      Chat.user_id,      form_data.delete_orphaned_chats),
                ("tools",          Tool,      Tool.user_id,      form_data.delete_orphaned_tools),
                ("functions",      Function,  Function.user_id,  form_data.delete_orphaned_functions),
                ("prompts",        Prompt,    Prompt.user_id,    form_data.delete_orphaned_prompts),
                ("knowledge_bases", Knowledge, Knowledge.user_id, form_data.delete_orphaned_knowledge_bases),
                ("models",         Model,     Model.user_id,     form_data.delete_orphaned_models),
                ("notes",          Note,      Note.user_id,      form_data.delete_orphaned_notes),
                ("skills",         Skill,     Skill.user_id,     form_data.delete_orphaned_skills),
                ("folders",        Folder,    Folder.user_id,    form_data.delete_orphaned_folders),
            ]

            for key, table_cls, user_id_col, enabled in _table_flag_map:
                if enabled and active_user_ids:
                    result = await db.execute(
                        select(func.count()).select_from(table_cls).where(
                            not_(user_id_col.in_(active_user_ids))
                        )
                    )
                    counts[key] = result.scalar_one_or_none() or 0

            # Count orphaned chat_messages (chat_id references a chat that no longer exists)
            if form_data.delete_orphaned_chat_messages:
                try:
                    result = await db.execute(
                        select(func.count(ChatMessage.id)).where(
                            not_(ChatMessage.chat_id.in_(select(Chat.id)))
                        )
                    )
                    counts["chat_messages"] = result.scalar_one_or_none() or 0
                except _TABLE_MISSING_ERRORS as e:
                    if _is_table_missing_error(e):
                        log.debug(f"chat_message table does not exist: {e}")
                    else:
                        raise

            # Count orphaned automations
            if Automation is not None:
                try:
                    orphaned_auto_count = 0
                    async for auto_id, auto_uid in stream_rows(
                        db, Automation.id, Automation.user_id
                    ):
                        if str(auto_uid) not in active_user_ids:
                            orphaned_auto_count += 1
                    counts["automations"] = orphaned_auto_count
                except _TABLE_MISSING_ERRORS as e:
                    if _is_table_missing_error(e):
                        log.debug(f"automation table does not exist: {e}")
                    else:
                        raise

            # Count orphaned automation_runs
            if AutomationRun is not None and Automation is not None:
                try:
                    result = await db.execute(
                        select(func.count(AutomationRun.id)).where(
                            or_(
                                AutomationRun.automation_id.is_(None),
                                not_(AutomationRun.automation_id.in_(
                                    select(Automation.id)
                                ))
                            )
                        )
                    )
                    counts["automation_runs"] = result.scalar_one_or_none() or 0
                except _TABLE_MISSING_ERRORS as e:
                    if _is_table_missing_error(e):
                        log.debug(f"automation_run table does not exist: {e}")
                    else:
                        raise

    except Exception as e:
        log.debug(f"Error counting orphaned records: {e}")

    return counts


async def count_orphaned_chat_messages() -> int:
    """Count orphaned chat_message rows whose parent chat no longer exists.

    These are left behind on SQLite because it does not enforce
    ON DELETE CASCADE unless PRAGMA foreign_keys is enabled.
    """
    try:
        async with get_async_db_context() as db:
            result = await db.execute(
                select(func.count(ChatMessage.id)).where(
                    not_(ChatMessage.chat_id.in_(select(Chat.id)))
                )
            )
            return result.scalar_one_or_none() or 0
    except Exception as e:
        log.debug(f"Error counting orphaned chat_messages: {e}")
        return 0


async def delete_orphaned_chat_messages() -> int:
    """Delete chat_message rows whose parent chat no longer exists.

    Returns the number of rows deleted.
    """
    try:
        async with get_async_db_context() as db:
            # Collect orphaned IDs first
            orphaned_ids = []
            result = await db.execute(
                select(ChatMessage.id).where(
                    not_(ChatMessage.chat_id.in_(select(Chat.id)))
                )
            )
            orphaned_ids = [row[0] for row in result.fetchall()]

            if not orphaned_ids:
                return 0

            # Delete in batches to avoid SQLite variable limits
            deleted = 0
            batch_size = 500
            for i in range(0, len(orphaned_ids), batch_size):
                batch = orphaned_ids[i:i + batch_size]
                result = await db.execute(
                    delete(ChatMessage).where(ChatMessage.id.in_(batch))
                )
                deleted += result.rowcount
            await db.commit()

            if deleted > 0:
                log.info(f"Deleted {deleted} orphaned chat_message rows")
            return deleted
    except Exception as e:
        log.error(f"Error deleting orphaned chat_messages: {e}")
        return 0


def count_orphaned_uploads(active_file_ids: Set[str]) -> int:
    """Count orphaned files in uploads directory."""
    upload_dir = Path(CACHE_DIR).parent / "uploads"
    if not upload_dir.exists():
        return 0

    count = 0
    try:
        for file_path in upload_dir.iterdir():
            if not file_path.is_file():
                continue

            filename = file_path.name
            file_id = None

            # Extract file ID from filename patterns
            if len(filename) > 36:
                potential_id = filename[:36]
                if potential_id.count("-") == 4:
                    file_id = potential_id

            if not file_id and filename.count("-") == 4 and len(filename) == 36:
                file_id = filename

            if not file_id:
                for active_id in active_file_ids:
                    if active_id in filename:
                        file_id = active_id
                        break

            if file_id and file_id not in active_file_ids:
                count += 1
    except Exception as e:
        log.debug(f"Error counting orphaned uploads: {e}")

    return count


def count_audio_cache_files(max_age_days: Optional[int]) -> int:
    """Count audio cache files that would be deleted."""
    if max_age_days is None:
        return 0

    cutoff_time = time.time() - (max_age_days * 86400)
    count = 0

    audio_dirs = [
        Path(CACHE_DIR) / "audio" / "speech",
        Path(CACHE_DIR) / "audio" / "transcriptions",
    ]

    for audio_dir in audio_dirs:
        if not audio_dir.exists():
            continue

        try:
            for file_path in audio_dir.iterdir():
                if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                    count += 1
        except Exception as e:
            log.debug(f"Error counting audio files in {audio_dir}: {e}")

    return count


async def get_active_file_ids(knowledge_bases=None, active_user_ids=None) -> Set[str]:
    """
    Get all file IDs that are actively referenced by knowledge bases, chats, folders, messages, and models.

    Uses lightweight SQL queries (streaming only IDs / small columns) to avoid
    loading full ORM objects with large JSONB payload into memory.

    Args:
        knowledge_bases: Deprecated, ignored.  Kept for call-site compatibility.
        active_user_ids: Optional set of active user IDs to filter knowledge bases
    """
    active_file_ids = set()

    # Defensively normalize to Set[str] — callers may pass UUID objects
    if active_user_ids is not None:
        active_user_ids = {str(uid) for uid in active_user_ids}

    try:
        # Preload all valid file IDs to avoid N database queries during validation.
        # Stream only IDs — never load full File ORM objects (which include large
        # JSONB data/meta columns that cause OOM on large databases).
        async def _load_file_ids():
            async with get_async_db() as db:
                return {str(fid) async for (fid,) in stream_rows(db, File.id)}
        all_file_ids = await retry_on_db_lock(_load_file_ids)
        log.debug(f"Preloaded {len(all_file_ids)} file IDs for validation")

        # Build active KB IDs using lightweight SQL (just id + user_id).
        # Knowledges.get_knowledge_bases() must NOT be used here — on databases
        # with many files it eager-loads File objects through the knowledge_file
        # relationship, pulling hundreds of MB of JSONB into memory.
        kb_user_map = await get_kb_user_map()
        active_kb_ids = set()
        for kb_id, user_id in kb_user_map.items():
            # CRITICAL: Skip KBs owned by inactive/deleted users to maintain
            # consistency with active_kb_ids filtering. This prevents false positives
            # where files are considered "active" but their KB is marked as orphaned,
            # leading to incorrectly deleted vector collections.
            if active_user_ids is None or user_id in active_user_ids:
                active_kb_ids.add(kb_id)
        log.debug(f"Found {len(active_kb_ids)} active knowledge bases")

        # Query the knowledge_file junction table directly for file IDs.
        # This replaces the N+1 pattern of Knowledges.get_files_by_id() per KB,
        # and avoids loading full File ORM objects (large JSONB data/meta columns).
        async def scan_knowledge_files():
            async with get_async_db() as db:
                result = await db.execute(text("SELECT knowledge_id, file_id FROM knowledge_file"))
                kf_count = 0
                while True:
                    rows = result.fetchmany(5000)
                    if not rows:
                        break
                    for kb_id, file_id in rows:
                        kf_count += 1
                        # Normalize to str — text() queries can return
                        # driver-native types (e.g. uuid.UUID on Postgres)
                        file_id_str = str(file_id) if file_id else None
                        kb_id_str = str(kb_id) if kb_id else None
                        if kb_id_str in active_kb_ids and file_id_str in all_file_ids:
                            active_file_ids.add(file_id_str)
                log.debug(f"Scanned {kf_count} knowledge_file entries for file references")

        try:
            await retry_on_db_lock(scan_knowledge_files)
        except _TABLE_MISSING_ERRORS as e:
            if _is_table_missing_error(e):
                log.debug(f"knowledge_file table does not exist (pre-v0.6.41 schema): {e}")
            else:
                raise  # Transient DB errors must abort, not produce incomplete sets

        # Scan chat_file junction table (cheap — just UUIDs, no JSONB).
        # Since v0.6.41+ chat files are stored in a dedicated junction table.
        # Use fetchmany (not stream_rows) because chat_file.file_id is
        # non-unique — keyset pagination requires a unique cursor column.
        async def scan_chat_files():
            async with get_async_db() as db:
                result = await db.execute(text("SELECT file_id FROM chat_file"))
                chat_file_count = 0
                while True:
                    rows = result.fetchmany(5000)
                    if not rows:
                        break
                    for (file_id,) in rows:
                        chat_file_count += 1
                        # Normalize to str — text() queries can return
                        # driver-native types (e.g. uuid.UUID on Postgres)
                        file_id_str = str(file_id) if file_id else None
                        if file_id_str and file_id_str in all_file_ids:
                            active_file_ids.add(file_id_str)
                log.debug(f"Scanned {chat_file_count} chat_file entries for file references")

        try:
            await retry_on_db_lock(scan_chat_files)
        except _TABLE_MISSING_ERRORS as e:
            if _is_table_missing_error(e):
                log.debug(f"chat_file table does not exist (pre-v0.6.41 schema): {e}")
            else:
                raise  # Transient DB errors must abort, not produce incomplete sets

        # Always scan legacy chat.chat JSON as well — during upgrades from
        # pre-v0.6.41 databases, some file references may exist only in the
        # JSON column while newer chats use chat_file.  Skipping this when
        # chat_file is non-empty is unsafe for partially-migrated schemas.
        # Each row's JSONB can be megabytes, so use a small batch size.
        async def scan_chats():
            chat_count = 0
            async with get_async_db() as db:
                async for chat_id, chat_dict in stream_rows(
                    db, Chat.id, Chat.chat, batch_size=50
                ):
                    chat_count += 1
                    if not chat_dict or not isinstance(chat_dict, dict):
                        continue
                    try:
                        collect_file_ids_from_dict(chat_dict, active_file_ids, all_file_ids)
                    except Exception as e:
                        log.debug(f"Error processing chat {chat_id} for file references: {e}")
            return chat_count

        chat_count = await retry_on_db_lock(scan_chats)
        log.debug(f"Scanned {chat_count} chats (legacy JSON) for file references")

        # Scan folders for file references
        # Pre-check ORM attributes — Folder.items/data may not exist on older schemas
        has_folder_items = hasattr(Folder, 'items')
        has_folder_data = hasattr(Folder, 'data')
        if has_folder_items or has_folder_data:
            async def scan_folders():
                async with get_async_db() as db:
                    columns = [Folder.id]
                    if has_folder_items:
                        columns.append(Folder.items)
                    if has_folder_data:
                        columns.append(Folder.data)
                    async for row in stream_rows(db, *columns, batch_size=100):
                        folder_id = row[0]
                        col_idx = 1
                        if has_folder_items:
                            items_dict = row[col_idx]
                            col_idx += 1
                            if items_dict:
                                try:
                                    collect_file_ids_from_dict(items_dict, active_file_ids, all_file_ids)
                                except Exception as e:
                                    log.debug(f"Error processing folder {folder_id} items: {e}")
                        if has_folder_data:
                            data_dict = row[col_idx]
                            if data_dict:
                                try:
                                    collect_file_ids_from_dict(data_dict, active_file_ids, all_file_ids)
                                except Exception as e:
                                    log.debug(f"Error processing folder {folder_id} data: {e}")

            try:
                await retry_on_db_lock(scan_folders)
            except _TABLE_MISSING_ERRORS as e:
                if _is_table_missing_error(e):
                    log.debug(f"Folder scan skipped (table missing): {e}")
                else:
                    raise
        else:
            log.debug("Folder.items/data attributes not present — skipping folder scan")

        # Scan standalone messages for file references
        if hasattr(Message, 'data'):
            async def scan_messages():
                async with get_async_db() as db:
                    async for message_id, message_data_dict in stream_rows(
                        db, Message.id, Message.data,
                        filter_clause=Message.data.isnot(None), batch_size=100
                    ):
                        if message_data_dict:
                            try:
                                collect_file_ids_from_dict(message_data_dict, active_file_ids, all_file_ids)
                            except Exception as e:
                                log.debug(f"Error processing message {message_id} data: {e}")

            try:
                await retry_on_db_lock(scan_messages)
            except _TABLE_MISSING_ERRORS as e:
                if _is_table_missing_error(e):
                    log.debug(f"Message scan skipped (table missing): {e}")
                else:
                    raise
        else:
            log.debug("Message.data attribute not present — skipping message scan")

        # Scan models for file references in params and meta fields
        has_model_params = hasattr(Model, 'params')
        has_model_meta = hasattr(Model, 'meta')
        if has_model_params or has_model_meta:
            async def scan_models():
                async with get_async_db() as db:
                    columns = [Model.id]
                    if has_model_params:
                        columns.append(Model.params)
                    if has_model_meta:
                        columns.append(Model.meta)
                    model_count = 0
                    async for row in stream_rows(db, *columns, batch_size=100):
                        model_count += 1
                        model_id = row[0]
                        col_idx = 1
                        if has_model_params:
                            params_dict = row[col_idx]
                            col_idx += 1
                            if params_dict and isinstance(params_dict, dict):
                                try:
                                    collect_file_ids_from_dict(params_dict, active_file_ids, all_file_ids)
                                except Exception as e:
                                    log.debug(f"Error processing model {model_id} params: {e}")
                        if has_model_meta:
                            meta_dict = row[col_idx]
                            if meta_dict and isinstance(meta_dict, dict):
                                try:
                                    collect_file_ids_from_dict(meta_dict, active_file_ids, all_file_ids)
                                except Exception as e:
                                    log.debug(f"Error processing model {model_id} meta: {e}")
                    log.debug(f"Scanned {model_count} models for file references")

            try:
                await retry_on_db_lock(scan_models)
            except _TABLE_MISSING_ERRORS as e:
                if _is_table_missing_error(e):
                    log.debug(f"Model scan skipped (table missing): {e}")
                else:
                    raise
        else:
            log.debug("Model.params/meta attributes not present — skipping model scan")

    except Exception:
        # Do NOT return an empty set — callers use this for deletion decisions.
        # An empty preservation set would mark ALL files as orphaned.
        raise

    log.info(f"Found {len(active_file_ids)} active file IDs")
    return active_file_ids


async def safe_delete_file_by_id(file_id: str, vector_cleaner, db: Optional[AsyncSession] = None) -> bool:
    """
    Safely delete a file record and its associated vector collections and physical storage.

    This function mirrors the cleanup logic from Open WebUI's delete_file_by_id endpoint:
    1. Cleans KB vector embeddings (filter by file_id and hash)
    2. Deletes the standalone file-{id} vector collection
    3. Deletes the file record from DB (CASCADE handles chat_file, channel_file, knowledge_file)
    4. Deletes the physical file from storage

    Args:
        file_id: The file ID to delete
        vector_cleaner: Vector database cleaner instance
        db: Optional database session to reuse (for efficient bulk operations)

    Returns:
        True if deletion succeeded, False otherwise
    """
    try:
        async with get_async_db_context(db) as session:
            file_record = await Files.get_file_by_id(file_id, db=session)
            if not file_record:
                return True

            # Clean KB vector embeddings (mirrors delete_file_by_id endpoint logic)
            # This removes embeddings from knowledge base collections that reference this file
            try:
                knowledges = await Knowledges.get_knowledges_by_file_id(file_id, db=session)
                for kb in knowledges:
                    try:
                        # Delete by file_id filter
                        vector_cleaner.delete(collection_name=kb.id, filter={"file_id": file_id})
                        # Also delete by hash if available (covers hash-based lookups)
                        if file_record.hash:
                            vector_cleaner.delete(collection_name=kb.id, filter={"hash": file_record.hash})
                    except Exception as e:
                        log.debug(f"KB embedding cleanup for {kb.id}: {e}")
            except Exception as e:
                log.debug(f"Error getting knowledges for file {file_id}: {e}")

            # Delete standalone file vector collection
            collection_name = f"file-{file_id}"
            vector_cleaner.delete_collection(collection_name)

            # Delete from DB - CASCADE handles chat_file, channel_file, knowledge_file
            await Files.delete_file_by_id(file_id, db=session)

            # Delete physical file from storage
            if file_record.path:
                try:
                    Storage.delete_file(file_record.path)
                except Exception as e:
                    log.debug(f"Error deleting physical file {file_record.path}: {e}")

            return True

    except Exception as e:
        log.error(f"Error deleting file {file_id}: {e}")
        return False


async def delete_user_files(user_id: str, vector_cleaner, db: Optional[AsyncSession] = None) -> int:
    """
    Delete all files owned by a user.

    This should be called before deleting an inactive user to ensure proper cleanup
    of file-related data (vector embeddings, physical storage, etc.).

    Args:
        user_id: The user ID whose files should be deleted
        vector_cleaner: Vector database cleaner instance
        db: Optional database session to reuse

    Returns:
        Number of files successfully deleted
    """
    deleted_count = 0
    try:
        files = await Files.get_files_by_user_id(user_id, db=db)
        log.debug(f"Found {len(files)} files for user {user_id}")

        for file in files:
            if await safe_delete_file_by_id(file.id, vector_cleaner, db=db):
                deleted_count += 1

        if deleted_count > 0:
            log.info(f"Deleted {deleted_count} files for user {user_id}")

    except Exception as e:
        log.error(f"Error deleting files for user {user_id}: {e}")

    return deleted_count


def cleanup_orphaned_uploads(active_file_ids: Set[str]) -> int:
    """
    Clean up orphaned files in the uploads directory.

    Returns the number of files deleted.
    """
    upload_dir = Path(CACHE_DIR).parent / "uploads"
    if not upload_dir.exists():
        return 0

    deleted_count = 0

    try:
        for file_path in upload_dir.iterdir():
            if not file_path.is_file():
                continue

            filename = file_path.name
            file_id = None

            # Extract file ID from filename patterns
            if len(filename) > 36:
                potential_id = filename[:36]
                if potential_id.count("-") == 4:
                    file_id = potential_id

            if not file_id and filename.count("-") == 4 and len(filename) == 36:
                file_id = filename

            if not file_id:
                for active_id in active_file_ids:
                    if active_id in filename:
                        file_id = active_id
                        break

            if file_id and file_id not in active_file_ids:
                try:
                    file_path.unlink()
                    deleted_count += 1
                except Exception as e:
                    log.error(f"Failed to delete upload file {filename}: {e}")

    except Exception as e:
        log.error(f"Error cleaning uploads directory: {e}")

    if deleted_count > 0:
        log.info(f"Deleted {deleted_count} orphaned upload files")

    return deleted_count


async def delete_inactive_users(
    inactive_days: int,
    vector_cleaner=None,
    exempt_admin: bool = True,
    exempt_pending: bool = True
) -> int:
    """
    Delete users who have been inactive for the specified number of days.

    If vector_cleaner is provided, also cleans up user files (embeddings, physical storage)
    before deleting the user.

    Args:
        inactive_days: Number of days of inactivity before deletion
        vector_cleaner: Optional vector database cleaner for file cleanup
        exempt_admin: Whether to exempt admin users from deletion
        exempt_pending: Whether to exempt pending users from deletion

    Returns the number of users deleted.
    """
    if inactive_days is None:
        return 0

    cutoff_time = int(time.time()) - (inactive_days * 86400)
    deleted_count = 0
    total_files_deleted = 0

    try:
        users_to_delete = []

        # Get all users and check activity
        all_users = (await Users.get_users())["users"]

        for user in all_users:
            # Skip if user is exempt
            if exempt_admin and user.role == "admin":
                continue
            if exempt_pending and user.role == "pending":
                continue

            # Check if user is inactive based on last_active_at
            if user.last_active_at < cutoff_time:
                users_to_delete.append(user)

        # Delete inactive users with shared database session
        async with get_async_db() as db:
            for user in users_to_delete:
                try:
                    # Delete user's files first (if vector_cleaner provided)
                    # This ensures proper cleanup of embeddings, physical storage, etc.
                    if vector_cleaner is not None:
                        files_deleted = await delete_user_files(user.id, vector_cleaner, db=db)
                        total_files_deleted += files_deleted

                    # Delete user's automations and their runs
                    await delete_user_automations(user.id, db=db)

                    # Delete the user - CASCADE handles remaining associations
                    await Users.delete_user_by_id(user.id, db=db)
                    deleted_count += 1
                    log.info(
                        f"Deleted inactive user: {user.email} (last active: {user.last_active_at})"
                    )
                except Exception as e:
                    await db.rollback()
                    log.error(f"Failed to delete user {user.id}: {e}")

    except Exception as e:
        log.error(f"Error during inactive user deletion: {e}")

    if total_files_deleted > 0:
        log.info(f"Total files deleted from inactive users: {total_files_deleted}")

    return deleted_count


async def delete_user_automations(user_id: str, db: Optional[AsyncSession] = None) -> int:
    """
    Delete all automations and their runs for a given user.

    Called during user deletion to ensure automation data is cleaned up
    before the user row is removed.

    Args:
        user_id: The user ID whose automations should be deleted
        db: Optional database session to reuse

    Returns:
        Number of automations deleted
    """
    if Automation is None:
        return 0

    owns_session = db is None
    deleted_count = 0
    try:
        async with get_async_db_context(db) as session:
            result = await session.execute(
                select(Automation.id).where(Automation.user_id == user_id)
            )
            automation_ids = [row[0] for row in result.fetchall()]

            if not automation_ids:
                return 0

            # Delete runs for these automations first (batched for SQLite)
            batch_size = 500
            if AutomationRun is not None:
                runs_deleted = 0
                for i in range(0, len(automation_ids), batch_size):
                    batch = automation_ids[i:i + batch_size]
                    result = await session.execute(
                        delete(AutomationRun).where(
                            AutomationRun.automation_id.in_(batch)
                        )
                    )
                    runs_deleted += result.rowcount
            else:
                log.warning("AutomationRun model not available, skipping run cleanup")
                runs_deleted = 0

            # Delete the automations themselves
            result = await session.execute(
                delete(Automation).where(Automation.user_id == user_id)
            )
            deleted_count = result.rowcount

            # Only commit if we own the session; let the caller commit otherwise
            if owns_session:
                await session.commit()

            if deleted_count > 0:
                log.info(
                    f"Deleted {deleted_count} automations and "
                    f"{runs_deleted} automation runs for user {user_id}"
                )

    except _TABLE_MISSING_ERRORS as e:
        if _is_table_missing_error(e):
            log.debug(f"Automation tables do not exist: {e}")
        elif not owns_session:
            raise  # Let caller handle transaction policy
        else:
            log.warning(f"Error deleting automations for user {user_id}: {e}")
    except Exception as e:
        if not owns_session:
            raise  # Let caller handle transaction policy
        log.warning(f"Error deleting automations for user {user_id}: {e}")

    return deleted_count


async def delete_orphaned_automations(active_user_ids: Set[str]) -> int:
    """
    Delete automation rows whose owner user no longer exists.

    Also deletes associated automation_run rows to avoid leaving
    doubly-orphaned records.

    Args:
        active_user_ids: Set of user IDs that still exist

    Returns:
        Number of automations deleted
    """
    if Automation is None:
        return 0

    try:
        async with get_async_db_context() as db:
            # Stream automations and filter by user ownership in Python
            # to avoid SQLite parameter limits with large active_user_ids
            orphaned_ids = []
            async for auto_id, auto_uid in stream_rows(
                db, Automation.id, Automation.user_id
            ):
                if str(auto_uid) not in active_user_ids:
                    orphaned_ids.append(str(auto_id))

            if not orphaned_ids:
                return 0

            # Delete runs for these automations first (batched for SQLite)
            batch_size = 500
            if AutomationRun is not None:
                runs_deleted = 0
                for i in range(0, len(orphaned_ids), batch_size):
                    batch = orphaned_ids[i:i + batch_size]
                    result = await db.execute(
                        delete(AutomationRun).where(
                            AutomationRun.automation_id.in_(batch)
                        )
                    )
                    runs_deleted += result.rowcount
            else:
                log.warning("AutomationRun model not available, skipping run cleanup")
                runs_deleted = 0

            # Delete the automations themselves
            deleted = 0
            for i in range(0, len(orphaned_ids), batch_size):
                batch = orphaned_ids[i:i + batch_size]
                result = await db.execute(
                    delete(Automation).where(Automation.id.in_(batch))
                )
                deleted += result.rowcount

            await db.commit()

            if deleted > 0:
                log.info(
                    f"Deleted {deleted} orphaned automations and "
                    f"{runs_deleted} associated automation runs"
                )
            return deleted

    except _TABLE_MISSING_ERRORS as e:
        if _is_table_missing_error(e):
            log.debug(f"Automation tables do not exist: {e}")
        else:
            log.error(f"Error deleting orphaned automations: {e}")
        return 0
    except Exception as e:
        log.error(f"Error deleting orphaned automations: {e}")
        return 0


async def delete_orphaned_automation_runs() -> int:
    """
    Delete automation_run rows whose parent automation no longer exists.

    These can be left behind if an automation was deleted without cleaning
    up its runs, or on SQLite where FK CASCADE is not enforced.

    Returns:
        Number of automation_run rows deleted
    """
    if AutomationRun is None or Automation is None:
        return 0

    try:
        async with get_async_db_context() as db:
            # Stream orphaned IDs in chunks to avoid bulk materialization
            orphaned_ids = []
            async for (run_id,) in stream_rows(
                db, AutomationRun.id,
                filter_clause=or_(
                    AutomationRun.automation_id.is_(None),
                    not_(AutomationRun.automation_id.in_(
                        select(Automation.id)
                    ))
                )
            ):
                orphaned_ids.append(str(run_id))

            if not orphaned_ids:
                return 0

            # Delete in batches to avoid SQLite variable limits
            deleted = 0
            batch_size = 500
            for i in range(0, len(orphaned_ids), batch_size):
                batch = orphaned_ids[i:i + batch_size]
                result = await db.execute(
                    delete(AutomationRun).where(AutomationRun.id.in_(batch))
                )
                deleted += result.rowcount
            await db.commit()

            if deleted > 0:
                log.info(f"Deleted {deleted} orphaned automation_run rows")
            return deleted

    except _TABLE_MISSING_ERRORS as e:
        if _is_table_missing_error(e):
            log.debug(f"Automation tables do not exist: {e}")
        else:
            log.error(f"Error deleting orphaned automation_runs: {e}")
        return 0
    except Exception as e:
        log.error(f"Error deleting orphaned automation_runs: {e}")
        return 0


def cleanup_audio_cache(max_age_days: Optional[int] = 30) -> int:
    """
    Clean up audio cache files older than specified days.

    Returns:
        Number of files deleted
    """
    if max_age_days is None:
        log.info("Skipping audio cache cleanup (max_age_days is None)")
        return 0

    cutoff_time = time.time() - (max_age_days * 86400)
    deleted_count = 0
    total_size_deleted = 0

    audio_dirs = [
        Path(CACHE_DIR) / "audio" / "speech",
        Path(CACHE_DIR) / "audio" / "transcriptions",
    ]

    for audio_dir in audio_dirs:
        if not audio_dir.exists():
            continue

        try:
            for file_path in audio_dir.iterdir():
                if not file_path.is_file():
                    continue

                stat_info = file_path.stat()
                file_mtime = stat_info.st_mtime
                if file_mtime < cutoff_time:
                    try:
                        file_size = stat_info.st_size
                        file_path.unlink()
                        deleted_count += 1
                        total_size_deleted += file_size
                        log.debug(f"Deleted audio cache file: {file_path} ({file_size} bytes)")
                    except Exception as e:
                        log.error(f"Failed to delete audio file {file_path}: {e}")

        except Exception as e:
            log.error(f"Error cleaning audio directory {audio_dir}: {e}")

    log.info(f"Audio cache cleanup: deleted {deleted_count} files, freed {total_size_deleted} bytes")
    return deleted_count
