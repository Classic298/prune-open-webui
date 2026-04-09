"""
Prune Operations - All Helper Functions

This module contains all the helper functions from backend/open_webui/routers/prune.py
that perform the actual pruning operations, counting, and cleanup.
"""

import inspect
import logging
import time
from pathlib import Path
from typing import Optional, Set, Callable, Any
from sqlalchemy import select, text, func, and_, or_, not_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def retry_on_db_lock(func: Callable, max_retries: int = 3, base_delay: float = 0.5) -> Any:
    """
    Retry a database operation if it fails due to database lock.
    Uses exponential backoff: 0.5s, 1s, 2s

    Args:
        func: Function to retry
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
            return func()
        except OperationalError as e:
            last_exception = e
            if 'database is locked' in str(e).lower() and attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                log.warning(f"Database locked, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                raise

    # This should never be reached, but just in case
    raise last_exception


def stream_rows(db, *columns, filter_clause=None, batch_size=5000):
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
        db: SQLAlchemy session
        *columns: One or more ORM column descriptors to SELECT.
                  The first column is used for ordering/keysetting
                  and MUST be unique (typically a primary key).
        filter_clause: Optional SQLAlchemy filter expression
        batch_size: Number of rows per batch (default 5000)

    Yields:
        Row tuples from the query
    """
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
        batch = db.execute(stmt).fetchall()
        if not batch:
            break
        yield from batch
        last_key = batch[-1][0]
        if len(batch) < batch_size:
            break


# Import Open WebUI modules using compatibility layer (handles pip/docker/git installs)
try:
    from prune_imports import (
        Users, Chat, Chats, ChatFile, ChatMessage, Message, File, Files, Note, Notes,
        Prompt, Prompts, Model, Models, Knowledge, Knowledges,
        Function, Functions, Tool, Tools, Skill, Skills,
        Folder, Folders, FolderModel, Storage,
        get_db, get_db_context, CACHE_DIR
    )
except ImportError as e:
    log.error(f"Failed to import Open WebUI modules: {e}")
    log.error("This module requires Open WebUI backend modules to be importable")
    raise

from prune_models import PruneDataForm
from prune_core import collect_file_ids_from_dict


def get_kb_user_map() -> dict:
    """Return {kb_id: user_id} from the knowledge table using lightweight SQL.

    This replaces Knowledges.get_knowledge_bases() which can OOM on large
    databases because SQLAlchemy eager-loads File objects through the
    knowledge_file relationship, pulling hundreds of MB of JSONB into memory.

    Raises on failure — callers (prune execution) must not proceed with an
    empty preservation set, as that could cause over-deletion.
    """
    with get_db() as db:
        rows = db.execute(
            select(Knowledge.id, Knowledge.user_id)
        ).fetchall()
        return {kb_id: uid for kb_id, uid in rows}


# API Compatibility Helpers
def get_all_folders(db: Optional[Session] = None):
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
                return Folders.get_all_folders(db=db)
            else:
                return Folders.get_all_folders()

        # Otherwise query directly from database
        with get_db_context(db) as session:
            folders = session.query(Folder).all()
            # Convert to FolderModel instances
            return [FolderModel.model_validate(f) for f in folders]
    except Exception as e:
        log.error(f"Error getting all folders: {e}")
        return []


def count_inactive_users(
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
            all_users = Users.get_users()["users"]
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


def count_old_chats(
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
        with get_db_context() as db:
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

            count = db.query(func.count(Chat.id)).filter(*conditions).scalar()
            return count or 0
    except Exception as e:
        log.debug(f"Error counting old chats: {e}")
        return 0


def count_orphaned_records(
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
    }

    try:
        with get_db_context() as db:
            # Count orphaned files.
            # A file is orphaned when it is not in the active_file_ids set OR
            # its owner is not in active_user_ids.
            #
            # Stream id+user_id and check membership in Python to avoid any
            # SQL IN() clauses — active_file_ids can be 100K+ entries and
            # active_user_ids can exceed SQLite's ~999 parameter limit on
            # large instances.
            orphaned_file_count = 0
            for fid, uid in stream_rows(db, File.id, File.user_id):
                if fid not in active_file_ids or uid not in active_user_ids:
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
                    counts[key] = db.query(func.count()).select_from(table_cls).filter(
                        not_(user_id_col.in_(active_user_ids))
                    ).scalar() or 0

            # Count orphaned chat_messages (chat_id references a chat that no longer exists)
            if form_data.delete_orphaned_chat_messages:
                try:
                    counts["chat_messages"] = db.query(
                        func.count(ChatMessage.id)
                    ).filter(
                        not_(ChatMessage.chat_id.in_(
                            select(Chat.id)
                        ))
                    ).scalar() or 0
                except OperationalError as e:
                    error_msg = str(e).lower()
                    if 'no such table' in error_msg or 'does not exist' in error_msg or 'undefined table' in error_msg:
                        log.debug(f"chat_message table does not exist: {e}")
                    else:
                        raise

    except Exception as e:
        log.debug(f"Error counting orphaned records: {e}")

    return counts


def count_orphaned_chat_messages() -> int:
    """Count orphaned chat_message rows whose parent chat no longer exists.

    These are left behind on SQLite because it does not enforce
    ON DELETE CASCADE unless PRAGMA foreign_keys is enabled.
    """
    try:
        with get_db_context() as db:
            return db.query(
                func.count(ChatMessage.id)
            ).filter(
                not_(ChatMessage.chat_id.in_(select(Chat.id)))
            ).scalar() or 0
    except Exception as e:
        log.debug(f"Error counting orphaned chat_messages: {e}")
        return 0


def delete_orphaned_chat_messages() -> int:
    """Delete chat_message rows whose parent chat no longer exists.

    Returns the number of rows deleted.
    """
    try:
        with get_db_context() as db:
            orphaned_ids = db.query(ChatMessage.id).filter(
                not_(ChatMessage.chat_id.in_(select(Chat.id)))
            ).all()
            orphan_id_list = [r.id for r in orphaned_ids]

            if not orphan_id_list:
                return 0

            # Delete in batches to avoid SQLite variable limits
            deleted = 0
            batch_size = 500
            for i in range(0, len(orphan_id_list), batch_size):
                batch = orphan_id_list[i:i + batch_size]
                deleted += db.query(ChatMessage).filter(
                    ChatMessage.id.in_(batch)
                ).delete()
            db.commit()

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


def get_active_file_ids(active_user_ids=None) -> Set[str]:
    """
    Get all file IDs that are actively referenced by knowledge bases, chats, folders, messages, and models.

    Uses lightweight SQL queries (streaming only IDs / small columns) to avoid
    loading full ORM objects with large JSONB payload into memory.

    Args:
        active_user_ids: Optional set of active user IDs to filter knowledge bases
    """
    active_file_ids = set()

    try:
        # Preload all valid file IDs to avoid N database queries during validation.
        # Stream only IDs — never load full File ORM objects (which include large
        # JSONB data/meta columns that cause OOM on large databases).
        def _load_file_ids():
            with get_db() as db:
                return {fid for (fid,) in stream_rows(db, File.id)}
        all_file_ids = retry_on_db_lock(_load_file_ids)
        log.debug(f"Preloaded {len(all_file_ids)} file IDs for validation")

        # Build active KB IDs using lightweight SQL (just id + user_id).
        # Knowledges.get_knowledge_bases() must NOT be used here — on databases
        # with many files it eager-loads File objects through the knowledge_file
        # relationship, pulling hundreds of MB of JSONB into memory.
        kb_user_map = get_kb_user_map()
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
        try:
            with get_db() as db:
                # knowledge_file is a junction table without a dedicated ORM model,
                # so use raw SQL with batched fetching to stay memory-safe
                result = db.execute(text("SELECT knowledge_id, file_id FROM knowledge_file"))
                kf_count = 0
                while True:
                    rows = result.fetchmany(5000)
                    if not rows:
                        break
                    for kb_id, file_id in rows:
                        kf_count += 1
                        if kb_id in active_kb_ids and file_id in all_file_ids:
                            active_file_ids.add(file_id)
                log.debug(f"Scanned {kf_count} knowledge_file entries for file references")
        except OperationalError as e:
            # Table may not exist on pre-v0.6.41 schemas — safe to skip
            error_msg = str(e).lower()
            if 'no such table' in error_msg or 'does not exist' in error_msg or 'undefined table' in error_msg:
                log.debug(f"knowledge_file table does not exist (pre-v0.6.41 schema): {e}")
            else:
                raise  # Transient DB errors must abort, not produce incomplete sets

        # Scan chat_file junction table (cheap — just UUIDs, no JSONB).
        # Since v0.6.41+ chat files are stored in a dedicated junction table.
        # Use fetchmany (not stream_rows) because chat_file.file_id is
        # non-unique — keyset pagination requires a unique cursor column.
        try:
            with get_db() as db:
                result = db.execute(text("SELECT file_id FROM chat_file"))
                chat_file_count = 0
                while True:
                    rows = result.fetchmany(5000)
                    if not rows:
                        break
                    for (file_id,) in rows:
                        chat_file_count += 1
                        if file_id and file_id in all_file_ids:
                            active_file_ids.add(file_id)
                log.debug(f"Scanned {chat_file_count} chat_file entries for file references")
        except OperationalError as e:
            # Table may not exist on pre-v0.6.41 schemas — safe to skip
            error_msg = str(e).lower()
            if 'no such table' in error_msg or 'does not exist' in error_msg or 'undefined table' in error_msg:
                log.debug(f"chat_file table does not exist (pre-v0.6.41 schema): {e}")
            else:
                raise  # Transient DB errors must abort, not produce incomplete sets

        # Always scan legacy chat.chat JSON as well — during upgrades from
        # pre-v0.6.41 databases, some file references may exist only in the
        # JSON column while newer chats use chat_file.  Skipping this when
        # chat_file is non-empty is unsafe for partially-migrated schemas.
        # Each row's JSONB can be megabytes, so use a small batch size.
        def scan_chats():
            chat_count = 0
            with get_db() as db:
                for chat_id, chat_dict in stream_rows(
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

        chat_count = retry_on_db_lock(scan_chats)
        log.debug(f"Scanned {chat_count} chats (legacy JSON) for file references")

        # Scan folders for file references
        try:
            with get_db() as db:
                for folder_id, items_dict, data_dict in stream_rows(
                    db, Folder.id, Folder.items, Folder.data, batch_size=100
                ):
                    if items_dict:
                        try:
                            collect_file_ids_from_dict(items_dict, active_file_ids, all_file_ids)
                        except Exception as e:
                            log.debug(f"Error processing folder {folder_id} items: {e}")
                    if data_dict:
                        try:
                            collect_file_ids_from_dict(data_dict, active_file_ids, all_file_ids)
                        except Exception as e:
                            log.debug(f"Error processing folder {folder_id} data: {e}")
        except OperationalError as e:
            error_msg = str(e).lower()
            if 'no such table' in error_msg or 'does not exist' in error_msg or 'undefined table' in error_msg:
                log.debug(f"Folder table does not exist: {e}")
            else:
                raise

        # Scan standalone messages for file references
        try:
            with get_db() as db:
                for message_id, message_data_dict in stream_rows(
                    db, Message.id, Message.data,
                    filter_clause=Message.data.isnot(None), batch_size=100
                ):
                    if message_data_dict:
                        try:
                            collect_file_ids_from_dict(message_data_dict, active_file_ids, all_file_ids)
                        except Exception as e:
                            log.debug(f"Error processing message {message_id} data: {e}")
        except OperationalError as e:
            error_msg = str(e).lower()
            if 'no such table' in error_msg or 'does not exist' in error_msg or 'undefined table' in error_msg:
                log.debug(f"Message table does not exist: {e}")
            else:
                raise

        # Scan models for file references in params and meta fields
        try:
            with get_db() as db:
                model_count = 0
                for model_id, params_dict, meta_dict in stream_rows(
                    db, Model.id, Model.params, Model.meta, batch_size=100
                ):
                    model_count += 1
                    if params_dict and isinstance(params_dict, dict):
                        try:
                            collect_file_ids_from_dict(params_dict, active_file_ids, all_file_ids)
                        except Exception as e:
                            log.debug(f"Error processing model {model_id} params: {e}")
                    if meta_dict and isinstance(meta_dict, dict):
                        try:
                            collect_file_ids_from_dict(meta_dict, active_file_ids, all_file_ids)
                        except Exception as e:
                            log.debug(f"Error processing model {model_id} meta: {e}")
                log.debug(f"Scanned {model_count} models for file references")
        except OperationalError as e:
            error_msg = str(e).lower()
            if 'no such table' in error_msg or 'does not exist' in error_msg or 'undefined table' in error_msg:
                log.debug(f"Model table does not exist: {e}")
            else:
                raise

    except Exception:
        # Do NOT return an empty set — callers use this for deletion decisions.
        # An empty preservation set would mark ALL files as orphaned.
        raise

    log.info(f"Found {len(active_file_ids)} active file IDs")
    return active_file_ids


def safe_delete_file_by_id(file_id: str, vector_cleaner, db: Optional[Session] = None) -> bool:
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
        with get_db_context(db) as session:
            file_record = Files.get_file_by_id(file_id, db=session)
            if not file_record:
                return True

            # Clean KB vector embeddings (mirrors delete_file_by_id endpoint logic)
            # This removes embeddings from knowledge base collections that reference this file
            try:
                knowledges = Knowledges.get_knowledges_by_file_id(file_id, db=session)
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
            Files.delete_file_by_id(file_id, db=session)

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


def delete_user_files(user_id: str, vector_cleaner, db: Optional[Session] = None) -> int:
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
        files = Files.get_files_by_user_id(user_id, db=db)
        log.debug(f"Found {len(files)} files for user {user_id}")

        for file in files:
            if safe_delete_file_by_id(file.id, vector_cleaner, db=db):
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


def delete_inactive_users(
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
        all_users = Users.get_users()["users"]

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
        with get_db() as db:
            for user in users_to_delete:
                try:
                    # Delete user's files first (if vector_cleaner provided)
                    # This ensures proper cleanup of embeddings, physical storage, etc.
                    if vector_cleaner is not None:
                        files_deleted = delete_user_files(user.id, vector_cleaner, db=db)
                        total_files_deleted += files_deleted

                    # Delete the user - CASCADE handles remaining associations
                    Users.delete_user_by_id(user.id, db=db)
                    deleted_count += 1
                    log.info(
                        f"Deleted inactive user: {user.email} (last active: {user.last_active_at})"
                    )
                except Exception as e:
                    log.error(f"Failed to delete user {user.id}: {e}")

    except Exception as e:
        log.error(f"Error during inactive user deletion: {e}")

    if total_files_deleted > 0:
        log.info(f"Total files deleted from inactive users: {total_files_deleted}")

    return deleted_count


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
