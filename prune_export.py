"""
Prune Export — Detailed Preview Export to CSV

This module provides streaming CSV export of all items the prune tool would
delete. It piggybacks on the existing preview counts (no extra queries for
counting) and iterates items category-by-category, writing each row to disk
immediately to keep memory usage flat regardless of data size.

Usage:
    From the interactive CLI, the user is offered an export after preview.
    From the standalone CLI, the --export-preview flag triggers the export.
"""

import csv
import logging
import time
from collections import namedtuple
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional, Set

from sqlalchemy import select, and_, or_, not_

log = logging.getLogger(__name__)

# Import Open WebUI modules using compatibility layer
try:
    from prune_imports import (
        Users, Chat, Chats, File, Files, Note, Notes,
        Prompt, Prompts, Model, Models, Knowledge, Knowledges,
        Function, Functions, Tool, Tools, Skill, Skills,
        Automation, AutomationRun,
        Folder, Folders, ChatMessage,
        get_async_db, get_async_db_context, CACHE_DIR,
    )
except ImportError as e:
    log.error(f"Failed to import Open WebUI modules: {e}")
    raise

from prune_models import PruneDataForm, PrunePreviewResult
from prune_core import VectorDatabaseCleaner
from prune_operations import get_all_folders, stream_rows


# Row format for the exported CSV
ExportRow = namedtuple(
    "ExportRow",
    ["category", "id", "name", "owner_id", "size_bytes", "reason"],
)

# Estimated average bytes per CSV row for size estimation
_AVG_BYTES_PER_ROW = 250


def format_size(size_bytes: int) -> str:
    """Format byte count as human-readable string (e.g. '12 MB', '4.2 GB')."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.0f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


class PreviewExporter:
    """
    Streams preview details to CSV without accumulating in memory.

    All iteration happens through async generator methods that yield one
    ExportRow at a time. The CSV writer flushes each row to disk immediately
    via Python's default 8KB file buffer.
    """

    def __init__(
        self,
        form_data: PruneDataForm,
        vector_cleaner: VectorDatabaseCleaner,
        active_file_ids: Set[str],
        active_kb_ids: Set[str],
        active_user_ids: Set[str],
    ):
        self.form_data = form_data
        self.vector_cleaner = vector_cleaner
        self.active_file_ids = active_file_ids
        self.active_kb_ids = active_kb_ids
        self.active_user_ids = active_user_ids

    @staticmethod
    def estimate_size(preview_result: PrunePreviewResult) -> int:
        """Return estimated CSV file size in bytes from existing preview counts."""
        return preview_result.total_items() * _AVG_BYTES_PER_ROW

    async def export(
        self,
        output_path: Path,
        preview_result: PrunePreviewResult,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> int:
        """
        Stream all orphaned items to a CSV file.

        Args:
            output_path: Where to write the CSV.
            preview_result: Existing preview counts (used for context, not re-queried).
            progress_callback: Called with 1 after each row written (for progress bar).

        Returns:
            Total rows written.
        """
        rows_written = 0

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["category", "id", "name", "owner_id", "size_bytes", "reason"])

            # Each async generator yields ExportRow tuples one at a time.
            # Only one category is iterated at a time — previous batches are GC'd.
            generators = [
                self._iter_inactive_users(),
                self._iter_old_chats(),
                self._iter_orphaned_chats(),
                self._iter_orphaned_files(),
                self._iter_orphaned_workspace_items(),
                self._iter_orphaned_uploads(),
                self._iter_orphaned_vectors(),
                self._iter_orphaned_chat_messages(),
                self._iter_orphaned_automations(),
                self._iter_orphaned_automation_runs(),
                self._iter_audio_cache(),
            ]

            for gen in generators:
                async for row in gen:
                    writer.writerow(row)
                    rows_written += 1
                    if progress_callback:
                        progress_callback(1)

        log.info(f"Exported {rows_written} rows to {output_path}")
        return rows_written

    # ── Per-category async generators ────────────────────────────────────

    async def _iter_inactive_users(self) -> AsyncGenerator[ExportRow, None]:
        """Yield ExportRow for each inactive user."""
        inactive_days = self.form_data.delete_inactive_users_days
        if inactive_days is None:
            return

        cutoff_time = int(time.time()) - (inactive_days * 86400)

        try:
            all_users = (await Users.get_users())["users"]
            for user in all_users:
                if self.form_data.exempt_admin_users and user.role == "admin":
                    continue
                if self.form_data.exempt_pending_users and user.role == "pending":
                    continue
                if user.last_active_at < cutoff_time:
                    yield ExportRow(
                        category="inactive_user",
                        id=user.id,
                        name=user.email,
                        owner_id="",
                        size_bytes="",
                        reason=f"inactive {inactive_days}+ days",
                    )
        except Exception as e:
            log.debug(f"Error iterating inactive users: {e}")

    async def _iter_old_chats(self) -> AsyncGenerator[ExportRow, None]:
        """Yield ExportRow for each old chat (age-based deletion)."""
        if self.form_data.days is None:
            return

        cutoff_time = int(time.time()) - (self.form_data.days * 86400)

        try:
            async with get_async_db() as db:
                conditions = [Chat.updated_at < cutoff_time]

                if self.form_data.exempt_archived_chats:
                    conditions.append(or_(Chat.archived == False, Chat.archived == None))

                if self.form_data.exempt_chats_in_folders:
                    folder_conditions = []
                    if hasattr(Chat, 'folder_id'):
                        folder_conditions.append(Chat.folder_id == None)
                    if hasattr(Chat, 'pinned'):
                        folder_conditions.append(or_(Chat.pinned == False, Chat.pinned == None))
                    if folder_conditions:
                        conditions.append(and_(*folder_conditions))

                async for chat_id, title, user_id in stream_rows(
                    db, Chat.id, Chat.title, Chat.user_id,
                    filter_clause=and_(*conditions)
                ):
                    display_title = (title or "")[:100]
                    yield ExportRow(
                        category="old_chat",
                        id=chat_id,
                        name=display_title,
                        owner_id=user_id or "",
                        size_bytes="",
                        reason=f"older than {self.form_data.days} days",
                    )
        except Exception as e:
            log.debug(f"Error iterating old chats: {e}")

    async def _iter_orphaned_chats(self) -> AsyncGenerator[ExportRow, None]:
        """Yield ExportRow for each orphaned chat (owner no longer exists)."""
        if not self.form_data.delete_orphaned_chats:
            return

        if not self.active_user_ids:
            return

        try:
            async with get_async_db() as db:
                async for chat_id, title, user_id in stream_rows(
                    db, Chat.id, Chat.title, Chat.user_id,
                    filter_clause=not_(Chat.user_id.in_(self.active_user_ids))
                ):
                    display_title = (title or "")[:100]
                    yield ExportRow(
                        category="orphaned_chat",
                        id=chat_id,
                        name=display_title,
                        owner_id=user_id or "",
                        size_bytes="",
                        reason="owner not in active users",
                    )
        except Exception as e:
            log.debug(f"Error iterating orphaned chats: {e}")

    async def _iter_orphaned_files(self) -> AsyncGenerator[ExportRow, None]:
        """Yield ExportRow for each orphaned file record."""
        # Cannot use File.id.in_(active_file_ids) as a SQL filter because
        # active_file_ids can contain 100K+ entries, generating unseemly
        # large queries that OOM both Python and the database.  Instead,
        # stream all file records and check membership in Python.
        try:
            async with get_async_db() as db:
                async for file_id, filename, user_id in stream_rows(
                    db, File.id, File.filename, File.user_id
                ):
                    # Normalize to str — ORM can return uuid.UUID on Postgres
                    file_id_str = str(file_id) if file_id else ""
                    user_id_str = str(user_id) if user_id else ""
                    is_orphaned = (
                        (file_id_str not in self.active_file_ids)
                        or (user_id_str not in self.active_user_ids)
                    )
                    if not is_orphaned:
                        continue

                    reason_parts = []
                    if file_id_str not in self.active_file_ids:
                        reason_parts.append("not referenced")
                    if user_id_str not in self.active_user_ids:
                        reason_parts.append("owner not in active users")

                    yield ExportRow(
                        category="orphaned_file",
                        id=file_id or "",
                        name=filename or "",
                        owner_id=user_id or "",
                        size_bytes="",
                        reason="; ".join(reason_parts) if reason_parts else "orphaned",
                    )
        except Exception as e:
            log.debug(f"Error iterating orphaned files: {e}")

    async def _iter_orphaned_workspace_items(self) -> AsyncGenerator[ExportRow, None]:
        """Yield ExportRow for each orphaned workspace item (tools, functions, etc.)."""
        if not self.active_user_ids:
            return

        # Map: (category_name, orm_class, id_attr, name_attr, user_id_attr, enabled_flag)
        item_types = [
            ("orphaned_kb", Knowledge, "id", "name", "user_id", self.form_data.delete_orphaned_knowledge_bases),
            ("orphaned_tool", Tool, "id", "name", "user_id", self.form_data.delete_orphaned_tools),
            ("orphaned_function", Function, "id", "name", "user_id", self.form_data.delete_orphaned_functions),
            ("orphaned_prompt", Prompt, "command", "command", "user_id", self.form_data.delete_orphaned_prompts),
            ("orphaned_model", Model, "id", "name", "user_id", self.form_data.delete_orphaned_models),
            ("orphaned_note", Note, "id", "title", "user_id", self.form_data.delete_orphaned_notes),
            ("orphaned_skill", Skill, "id", "name", "user_id", self.form_data.delete_orphaned_skills),
        ]

        for category, cls, id_attr, name_attr, uid_attr, enabled in item_types:
            if not enabled:
                continue

            try:
                id_col = getattr(cls, id_attr)
                name_col = getattr(cls, name_attr)
                uid_col = getattr(cls, uid_attr)

                async with get_async_db() as db:
                    async for item_id, item_name, user_id in stream_rows(
                        db, id_col, name_col, uid_col,
                        filter_clause=not_(uid_col.in_(self.active_user_ids))
                    ):
                        yield ExportRow(
                            category=category,
                            id=str(item_id) if item_id else "",
                            name=str(item_name or "")[:100],
                            owner_id=user_id or "",
                            size_bytes="",
                            reason="owner not in active users",
                        )
            except Exception as e:
                log.debug(f"Error iterating {category}: {e}")

        # Orphaned folders (separate because of different API)
        if self.form_data.delete_orphaned_folders:
            try:
                folders = await get_all_folders()
                for folder in folders:
                    if str(folder.user_id) not in self.active_user_ids:
                        yield ExportRow(
                            category="orphaned_folder",
                            id=str(folder.id) if folder.id else "",
                            name=getattr(folder, "name", "") or "",
                            owner_id=folder.user_id or "",
                            size_bytes="",
                            reason="owner not in active users",
                        )
            except Exception as e:
                log.debug(f"Error iterating orphaned folders: {e}")

    async def _iter_orphaned_uploads(self) -> AsyncGenerator[ExportRow, None]:
        """Yield ExportRow for each orphaned physical upload file."""
        upload_dir = Path(CACHE_DIR).parent / "uploads"
        if not upload_dir.exists():
            return

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
                    for active_id in self.active_file_ids:
                        if active_id in filename:
                            file_id = active_id
                            break

                if file_id and file_id not in self.active_file_ids:
                    try:
                        file_size = file_path.stat().st_size
                    except OSError:
                        file_size = ""

                    yield ExportRow(
                        category="orphaned_upload",
                        id=file_id,
                        name=str(file_path),
                        owner_id="",
                        size_bytes=file_size,
                        reason="no matching DB record",
                    )
        except Exception as e:
            log.debug(f"Error iterating orphaned uploads: {e}")

    async def _iter_orphaned_vectors(self) -> AsyncGenerator[ExportRow, None]:
        """Yield ExportRow for each orphaned vector collection/tenant."""
        try:
            for orphaned_id, context in self.vector_cleaner.iter_orphaned_collections(
                self.active_file_ids, self.active_kb_ids, self.active_user_ids
            ):
                yield ExportRow(
                    category="orphaned_vector",
                    id=orphaned_id or "",
                    name=context or "",
                    owner_id="",
                    size_bytes="",
                    reason="no matching active file or knowledge base",
                )
        except Exception as e:
            log.debug(f"Error iterating orphaned vectors: {e}")

    async def _iter_orphaned_chat_messages(self) -> AsyncGenerator[ExportRow, None]:
        """Yield ExportRow for each orphaned chat message."""
        if not self.form_data.delete_orphaned_chat_messages:
            return

        try:
            async with get_async_db() as db:
                async for message_id, chat_id in stream_rows(
                    db, ChatMessage.id, ChatMessage.chat_id,
                    filter_clause=not_(ChatMessage.chat_id.in_(select(Chat.id)))
                ):
                    yield ExportRow(
                        category="orphaned_chat_message",
                        id=message_id or "",
                        name=f"chat: {chat_id}" if chat_id else "",
                        owner_id="",
                        size_bytes="",
                        reason="parent chat no longer exists",
                    )
        except Exception as e:
            log.debug(f"Error iterating orphaned chat messages (table may not exist): {e}")

    async def _iter_orphaned_automations(self) -> AsyncGenerator[ExportRow, None]:
        """Yield ExportRow for each orphaned automation (owner no longer exists).

        Also caches automation ID sets for reuse by _iter_orphaned_automation_runs
        to avoid a redundant second scan of the automations table.
        """
        if not self.form_data.delete_orphaned_automations or Automation is None:
            return

        # Build into local sets first; only cache on success so the runs
        # iterator falls back to a fresh scan if this one fails
        all_ids = set()
        orphaned_ids = set()

        try:
            async with get_async_db() as db:
                # Stream all automations and filter by user ownership in Python
                # to avoid SQLite parameter limits with large active_user_ids
                async for automation_id, name, user_id in stream_rows(
                    db, Automation.id, Automation.name, Automation.user_id
                ):
                    auto_id_str = str(automation_id) if automation_id else ""
                    all_ids.add(auto_id_str)
                    if str(user_id) not in self.active_user_ids:
                        orphaned_ids.add(auto_id_str)
                        yield ExportRow(
                            category="orphaned_automation",
                            id=auto_id_str,
                            name=(name or "")[:100],
                            owner_id=str(user_id) if user_id else "",
                            size_bytes="",
                            reason="owner not in active users",
                        )

            # Cache only after successful scan
            self._all_automation_ids = all_ids
            self._orphaned_automation_ids = orphaned_ids
        except Exception as e:
            log.debug(f"Error iterating orphaned automations (table may not exist): {e}")

    async def _iter_orphaned_automation_runs(self) -> AsyncGenerator[ExportRow, None]:
        """Yield ExportRow for each orphaned automation run.

        Includes runs whose parent automation is missing AND runs attached
        to automations that will be deleted as orphaned (owner-based).

        Reuses ID sets cached by _iter_orphaned_automations to avoid
        a redundant second scan of the automations table.
        """
        if not self.form_data.delete_orphaned_automations or AutomationRun is None or Automation is None:
            return

        # Use cached sets from _iter_orphaned_automations if available
        all_automation_ids = getattr(self, '_all_automation_ids', None)
        orphaned_automation_ids = getattr(self, '_orphaned_automation_ids', None)

        try:
            async with get_async_db() as db:
                # Fall back to a fresh scan if cache is not populated
                if all_automation_ids is None:
                    all_automation_ids = set()
                    orphaned_automation_ids = set()
                    async for auto_id, auto_uid in stream_rows(
                        db, Automation.id, Automation.user_id
                    ):
                        auto_id_str = str(auto_id)
                        all_automation_ids.add(auto_id_str)
                        if str(auto_uid) not in self.active_user_ids:
                            orphaned_automation_ids.add(auto_id_str)

                # Stream runs and filter in Python
                async for run_id, automation_id in stream_rows(
                    db, AutomationRun.id, AutomationRun.automation_id
                ):
                    auto_id_str = str(automation_id) if automation_id else ""
                    # Orphaned if parent missing OR parent will be deleted
                    if auto_id_str not in all_automation_ids:
                        reason = "parent automation no longer exists"
                    elif auto_id_str in orphaned_automation_ids:
                        reason = "parent automation is orphaned (owner deleted)"
                    else:
                        continue

                    yield ExportRow(
                        category="orphaned_automation_run",
                        id=str(run_id) if run_id else "",
                        name=f"automation: {automation_id}" if automation_id else "",
                        owner_id="",
                        size_bytes="",
                        reason=reason,
                    )
        except Exception as e:
            log.debug(f"Error iterating orphaned automation runs (table may not exist): {e}")

    async def _iter_audio_cache(self) -> AsyncGenerator[ExportRow, None]:
        """Yield ExportRow for each old audio cache file."""
        max_age_days = self.form_data.audio_cache_max_age_days
        if max_age_days is None:
            return

        cutoff_time = time.time() - (max_age_days * 86400)

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

                    try:
                        stat = file_path.stat()
                    except OSError:
                        continue

                    if stat.st_mtime < cutoff_time:
                        yield ExportRow(
                            category="audio_cache",
                            id="",
                            name=str(file_path),
                            owner_id="",
                            size_bytes=stat.st_size,
                            reason=f"older than {max_age_days} days",
                        )
            except Exception as e:
                log.debug(f"Error iterating audio cache in {audio_dir}: {e}")
