#!/usr/bin/env python3
"""
Open WebUI Standalone Prune Script

This is a standalone command-line script that replicates the full logic and
configurability of the prune.py API router, but runs independently without
requiring the web server to be running.

Usage:
    python standalone_prune.py --help
    python standalone_prune.py --dry-run  # Preview what will be deleted
    python standalone_prune.py --days 60 --run-vacuum  # Delete chats older than 60 days

Requirements:
    - Must be run from Open WebUI installation directory or have PYTHONPATH set
    - Requires same environment variables as Open WebUI (DATABASE_URL, etc.)
    - Requires same Python dependencies as Open WebUI backend
"""

import asyncio
import sys
import os
import argparse
import logging
from pathlib import Path

# Add parent directory to path to import Open WebUI modules
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

# Now we can import from prune modules
try:
    from prune_models import PruneDataForm, PrunePreviewResult
    from prune_core import (
        PruneLock,
        get_vector_database_cleaner,
        ChromaDatabaseCleaner,
        PGVectorDatabaseCleaner
    )
    from prune_operations import (
        count_inactive_users,
        count_old_chats,
        count_orphaned_records,
        count_orphaned_uploads,
        count_audio_cache_files,
        get_active_file_ids,
        get_kb_user_map,
        get_all_folders,
        safe_delete_file_by_id,
        cleanup_orphaned_uploads,
        delete_inactive_users,
        cleanup_audio_cache,
        delete_orphaned_chat_messages,
        delete_orphaned_automations,
        delete_orphaned_automation_runs,
        stream_rows,
    )
    from prune_imports import (
        Users, Chat, Chats, File, Notes, Prompts, Models, Knowledges, Functions,
        Tools, Skills, Folders, get_async_db, CACHE_DIR,
        ENABLE_QDRANT_MULTITENANCY_MODE, ENABLE_MILVUS_MULTITENANCY_MODE,
        sync_engine,
    )
    try:
        from prune_imports import VECTOR_DB, VECTOR_DB_CLIENT
    except ImportError:
        VECTOR_DB = None
        VECTOR_DB_CLIENT = None
    from sqlalchemy import text, or_
    import time
    import sqlite3
except ImportError as e:
    print(f"ERROR: Failed to import Open WebUI modules: {e}", file=sys.stderr)
    print("\nThis script must be run with access to Open WebUI's backend modules.", file=sys.stderr)
    print("Try one of the following:", file=sys.stderr)
    print("  1. Run from the Open WebUI installation directory", file=sys.stderr)
    print("  2. Set PYTHONPATH to include the Open WebUI directory", file=sys.stderr)
    print("  3. Install Open WebUI as a package", file=sys.stderr)
    sys.exit(1)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Open WebUI Standalone Data Pruning Script',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what will be deleted (safe, no changes)
  %(prog)s --dry-run

  # Delete chats older than 60 days
  %(prog)s --days 60

  # Delete inactive users (90+ days) and their data
  %(prog)s --delete-inactive-users-days 90

  # Full cleanup with VACUUM optimization
  %(prog)s --days 90 --delete-inactive-users-days 180 --run-vacuum

  # Clean orphaned data only (no age-based deletion)
  %(prog)s --delete-orphaned-chats --delete-orphaned-files

  # Audio cache cleanup
  %(prog)s --audio-cache-max-age-days 30

Safety Features:
  - Uses file-based locking to prevent concurrent runs
  - Dry-run mode enabled by default (use --execute to actually delete)
  - Detailed logging of all operations
  - Preserves archived chats and folder-organized chats by default
  - Admin users exempted from deletion by default
        """
    )

    # Execution mode
    parser.add_argument(
        '--dry-run',
        action='store_true',
        default=False,
        help='Preview what will be deleted without making changes (default if --execute not specified)'
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        default=False,
        help='Actually perform deletions (required for real cleanup)'
    )

    # Age-based deletion
    parser.add_argument(
        '--days',
        type=int,
        default=None,
        metavar='N',
        help='Delete chats older than N days (based on last update time)'
    )
    parser.add_argument(
        '--exempt-archived-chats',
        action='store_true',
        default=True,
        help='Keep archived chats even if old (default: True)'
    )
    parser.add_argument(
        '--no-exempt-archived-chats',
        action='store_false',
        dest='exempt_archived_chats',
        help='Include archived chats in age-based deletion'
    )
    parser.add_argument(
        '--exempt-chats-in-folders',
        action='store_true',
        default=False,
        help='Keep chats in folders/pinned even if old'
    )

    # Inactive user deletion
    parser.add_argument(
        '--delete-inactive-users-days',
        type=int,
        default=None,
        metavar='N',
        help='Delete users inactive for more than N days (DESTRUCTIVE)'
    )
    parser.add_argument(
        '--exempt-admin-users',
        action='store_true',
        default=True,
        help='Never delete admin users (default: True, STRONGLY RECOMMENDED)'
    )
    parser.add_argument(
        '--no-exempt-admin-users',
        action='store_false',
        dest='exempt_admin_users',
        help='Include admin users in inactivity deletion (NOT RECOMMENDED)'
    )
    parser.add_argument(
        '--exempt-pending-users',
        action='store_true',
        default=True,
        help='Never delete pending users (default: True)'
    )
    parser.add_argument(
        '--no-exempt-pending-users',
        action='store_false',
        dest='exempt_pending_users',
        help='Include pending users in inactivity deletion'
    )

    # Orphaned data deletion
    parser.add_argument(
        '--delete-orphaned-chats',
        action='store_true',
        default=True,
        help='Delete orphaned chats from deleted users (default: True)'
    )
    parser.add_argument(
        '--no-delete-orphaned-chats',
        action='store_false',
        dest='delete_orphaned_chats',
        help='Skip orphaned chat deletion'
    )
    parser.add_argument(
        '--delete-orphaned-tools',
        action='store_true',
        default=False,
        help='Delete orphaned tools from deleted users'
    )
    parser.add_argument(
        '--delete-orphaned-functions',
        action='store_true',
        default=False,
        help='Delete orphaned functions from deleted users'
    )
    parser.add_argument(
        '--delete-orphaned-skills',
        action='store_true',
        default=False,
        help='Delete orphaned skills from deleted users'
    )
    parser.add_argument(
        '--delete-orphaned-prompts',
        action='store_true',
        default=True,
        help='Delete orphaned prompts from deleted users (default: True)'
    )
    parser.add_argument(
        '--no-delete-orphaned-prompts',
        action='store_false',
        dest='delete_orphaned_prompts',
        help='Skip orphaned prompt deletion'
    )
    parser.add_argument(
        '--delete-orphaned-knowledge-bases',
        action='store_true',
        default=True,
        help='Delete orphaned knowledge bases from deleted users (default: True)'
    )
    parser.add_argument(
        '--no-delete-orphaned-knowledge-bases',
        action='store_false',
        dest='delete_orphaned_knowledge_bases',
        help='Skip orphaned knowledge base deletion'
    )
    parser.add_argument(
        '--delete-orphaned-models',
        action='store_true',
        default=True,
        help='Delete orphaned models from deleted users (default: True)'
    )
    parser.add_argument(
        '--no-delete-orphaned-models',
        action='store_false',
        dest='delete_orphaned_models',
        help='Skip orphaned model deletion'
    )
    parser.add_argument(
        '--delete-orphaned-notes',
        action='store_true',
        default=True,
        help='Delete orphaned notes from deleted users (default: True)'
    )
    parser.add_argument(
        '--no-delete-orphaned-notes',
        action='store_false',
        dest='delete_orphaned_notes',
        help='Skip orphaned note deletion'
    )
    parser.add_argument(
        '--delete-orphaned-folders',
        action='store_true',
        default=True,
        help='Delete orphaned folders from deleted users (default: True)'
    )
    parser.add_argument(
        '--no-delete-orphaned-folders',
        action='store_false',
        dest='delete_orphaned_folders',
        help='Skip orphaned folder deletion'
    )
    parser.add_argument(
        '--delete-orphaned-chat-messages',
        action='store_true',
        default=True,
        help='Delete orphaned chat_message rows from deleted chats (default: True)'
    )
    parser.add_argument(
        '--no-delete-orphaned-chat-messages',
        action='store_false',
        dest='delete_orphaned_chat_messages',
        help='Skip orphaned chat_message deletion'
    )
    parser.add_argument(
        '--delete-orphaned-automations',
        action='store_true',
        default=True,
        help='Delete orphaned automations and their run history from deleted users (default: True)'
    )
    parser.add_argument(
        '--no-delete-orphaned-automations',
        action='store_false',
        dest='delete_orphaned_automations',
        help='Skip orphaned automation deletion'
    )

    # Audio cache cleanup
    parser.add_argument(
        '--audio-cache-max-age-days',
        type=int,
        default=None,
        metavar='N',
        help='Delete audio cache files (TTS/STT) older than N days'
    )

    # Database optimization
    parser.add_argument(
        '--run-vacuum',
        action='store_true',
        default=False,
        help='Run VACUUM to reclaim disk space (LOCKS DATABASE, use during maintenance)'
    )

    # Logging
    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        default=False,
        help='Enable verbose debug logging'
    )
    parser.add_argument(
        '--quiet',
        '-q',
        action='store_true',
        default=False,
        help='Suppress all output except errors'
    )

    # Export
    parser.add_argument(
        '--export-preview',
        type=str,
        default=None,
        metavar='PATH',
        help='Export detailed preview to CSV file (requires --dry-run)'
    )

    args = parser.parse_args()

    # If neither --dry-run nor --execute specified, default to dry-run
    if not args.dry_run and not args.execute:
        args.dry_run = True
        log.info("No execution mode specified, defaulting to --dry-run (preview mode)")

    # Can't have both dry-run and execute
    if args.dry_run and args.execute:
        parser.error("Cannot specify both --dry-run and --execute")

    # --export-preview requires --dry-run
    if args.export_preview and not args.dry_run:
        parser.error("--export-preview requires --dry-run mode")

    return args


def configure_logging(verbose: bool, quiet: bool):
    """Configure logging level based on arguments."""
    if quiet:
        logging.getLogger().setLevel(logging.ERROR)
    elif verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)


def create_prune_form(args) -> PruneDataForm:
    """Create PruneDataForm from command-line arguments."""
    return PruneDataForm(
        days=args.days,
        exempt_archived_chats=args.exempt_archived_chats,
        exempt_chats_in_folders=args.exempt_chats_in_folders,
        delete_orphaned_chats=args.delete_orphaned_chats,
        delete_orphaned_tools=args.delete_orphaned_tools,
        delete_orphaned_functions=args.delete_orphaned_functions,
        delete_orphaned_skills=args.delete_orphaned_skills,
        delete_orphaned_prompts=args.delete_orphaned_prompts,
        delete_orphaned_knowledge_bases=args.delete_orphaned_knowledge_bases,
        delete_orphaned_models=args.delete_orphaned_models,
        delete_orphaned_notes=args.delete_orphaned_notes,
        delete_orphaned_folders=args.delete_orphaned_folders,
        delete_orphaned_chat_messages=args.delete_orphaned_chat_messages,
        delete_orphaned_automations=args.delete_orphaned_automations,
        audio_cache_max_age_days=args.audio_cache_max_age_days,
        delete_inactive_users_days=args.delete_inactive_users_days,
        exempt_admin_users=args.exempt_admin_users,
        exempt_pending_users=args.exempt_pending_users,
        run_vacuum=args.run_vacuum,
        dry_run=args.dry_run,
    )


def print_preview_results(result: PrunePreviewResult):
    """Pretty-print preview results."""
    print("\n" + "="*70)
    print("  PRUNE PREVIEW - What will be deleted")
    print("="*70)

    total_items = 0

    if result.inactive_users > 0:
        print(f"\n👤 Inactive Users:")
        print(f"   {result.inactive_users} user accounts")
        total_items += result.inactive_users

    if result.old_chats > 0 or result.orphaned_chats > 0 or result.orphaned_chat_messages > 0:
        print(f"\n💬 Chats:")
        if result.old_chats > 0:
            print(f"   {result.old_chats} old chats (age-based)")
        if result.orphaned_chats > 0:
            print(f"   {result.orphaned_chats} orphaned chats")
        if result.orphaned_chat_messages > 0:
            print(f"   {result.orphaned_chat_messages} orphaned chat messages")
        total_items += result.old_chats + result.orphaned_chats + result.orphaned_chat_messages

    if result.orphaned_files > 0:
        print(f"\n📁 Files:")
        print(f"   {result.orphaned_files} orphaned file records")
        total_items += result.orphaned_files

    workspace_total = (result.orphaned_tools + result.orphaned_functions +
                       result.orphaned_prompts + result.orphaned_knowledge_bases +
                       result.orphaned_models + result.orphaned_notes +
                       result.orphaned_skills)
    if workspace_total > 0:
        print(f"\n🔧 Workspace Items:")
        if result.orphaned_tools > 0:
            print(f"   {result.orphaned_tools} orphaned tools")
        if result.orphaned_functions > 0:
            print(f"   {result.orphaned_functions} orphaned functions")
        if result.orphaned_prompts > 0:
            print(f"   {result.orphaned_prompts} orphaned prompts")
        if result.orphaned_knowledge_bases > 0:
            print(f"   {result.orphaned_knowledge_bases} orphaned knowledge bases")
        if result.orphaned_models > 0:
            print(f"   {result.orphaned_models} orphaned models")
        if result.orphaned_notes > 0:
            print(f"   {result.orphaned_notes} orphaned notes")
        if result.orphaned_skills > 0:
            print(f"   {result.orphaned_skills} orphaned skills")
        total_items += workspace_total

    if result.orphaned_folders > 0:
        print(f"\n📂 Folders:")
        print(f"   {result.orphaned_folders} orphaned folders")
        total_items += result.orphaned_folders

    automation_total = result.orphaned_automations + result.orphaned_automation_runs
    if automation_total > 0:
        print(f"\n⚙️  Automations:")
        if result.orphaned_automations > 0:
            print(f"   {result.orphaned_automations} orphaned automations")
        if result.orphaned_automation_runs > 0:
            print(f"   {result.orphaned_automation_runs} orphaned automation runs")
        total_items += automation_total

    if result.orphaned_uploads > 0 or result.orphaned_vector_collections > 0:
        print(f"\n💾 Storage:")
        if result.orphaned_uploads > 0:
            print(f"   {result.orphaned_uploads} orphaned upload files")
        if result.orphaned_vector_collections > 0:
            print(f"   {result.orphaned_vector_collections} orphaned vector collections")
        total_items += result.orphaned_uploads + result.orphaned_vector_collections

    if result.audio_cache_files > 0:
        print(f"\n🔊 Audio Cache:")
        print(f"   {result.audio_cache_files} old audio cache files")
        total_items += result.audio_cache_files

    print("\n" + "="*70)
    print(f"  TOTAL ITEMS TO DELETE: {total_items}")
    print("="*70)

    if total_items == 0:
        print("\n✅ Nothing to delete - your database is clean!")
    else:
        print("\n⚠️  Run with --execute to perform actual deletion")
        print("   (This is a preview only, no changes were made)")
    print()


async def run_prune(form_data: PruneDataForm, export_preview_path: str = None):
    """
    Execute the prune operation with the given configuration.
    This replicates the logic from prune.py's prune_data function.
    """
    # Acquire lock to prevent concurrent operations
    if not PruneLock.acquire():
        log.error("A prune operation is already in progress. Please wait for it to complete.")
        return False

    try:
        # Get vector database cleaner based on configuration
        vector_cleaner = get_vector_database_cleaner(
            VECTOR_DB, VECTOR_DB_CLIENT, Path(CACHE_DIR),
            enable_milvus_multitenancy=ENABLE_MILVUS_MULTITENANCY_MODE,
            enable_qdrant_multitenancy=ENABLE_QDRANT_MULTITENANCY_MODE,
        )

        if form_data.dry_run:
            log.info("Starting data pruning preview (dry run)")

            # Get counts for all enabled operations
            kb_map = await get_kb_user_map()
            all_users = (await Users.get_users())["users"]
            active_user_ids = {str(user.id) for user in all_users}
            active_kb_ids = {
                kb_id
                for kb_id, uid in kb_map.items()
                if uid in active_user_ids
            }
            active_file_ids = await get_active_file_ids(active_user_ids=active_user_ids)

            orphaned_counts = await count_orphaned_records(form_data, active_file_ids, active_user_ids)

            result = PrunePreviewResult(
                inactive_users=await count_inactive_users(
                    form_data.delete_inactive_users_days,
                    form_data.exempt_admin_users,
                    form_data.exempt_pending_users,
                    all_users,
                ),
                old_chats=await count_old_chats(
                    form_data.days,
                    form_data.exempt_archived_chats,
                    form_data.exempt_chats_in_folders,
                ),
                orphaned_chats=orphaned_counts["chats"],
                orphaned_files=orphaned_counts["files"],
                orphaned_tools=orphaned_counts["tools"],
                orphaned_functions=orphaned_counts["functions"],
                orphaned_prompts=orphaned_counts["prompts"],
                orphaned_knowledge_bases=orphaned_counts["knowledge_bases"],
                orphaned_models=orphaned_counts["models"],
                orphaned_notes=orphaned_counts["notes"],
                orphaned_skills=orphaned_counts["skills"],
                orphaned_folders=orphaned_counts["folders"],
                orphaned_uploads=count_orphaned_uploads(active_file_ids),
                orphaned_vector_collections=vector_cleaner.count_orphaned_collections(
                    active_file_ids, active_kb_ids, active_user_ids
                ),
                audio_cache_files=count_audio_cache_files(
                    form_data.audio_cache_max_age_days
                ),
                orphaned_chat_messages=orphaned_counts["chat_messages"],
                orphaned_automations=orphaned_counts["automations"],
                orphaned_automation_runs=orphaned_counts["automation_runs"],
            )

            log.info("Data pruning preview completed")
            print_preview_results(result)

            # Export detailed preview to CSV if requested
            if export_preview_path and result.has_items():
                from prune_export import PreviewExporter, format_size

                exporter = PreviewExporter(
                    form_data=form_data,
                    vector_cleaner=vector_cleaner,
                    active_file_ids=active_file_ids,
                    active_kb_ids=active_kb_ids,
                    active_user_ids=active_user_ids,
                )

                estimated_human = format_size(exporter.estimate_size(result))
                log.info(
                    f"Exporting {result.total_items()} items (~{estimated_human}) "
                    f"to {export_preview_path}"
                )
                rows = await exporter.export(Path(export_preview_path), result)
                log.info(f"Exported {rows} rows to {export_preview_path}")

            return True

        # Actual deletion logic (dry_run=False)
        log.info("Starting data pruning process (ACTUAL DELETION)")

        # Stage 0: Delete inactive users (if enabled)
        deleted_users = 0
        if form_data.delete_inactive_users_days is not None:
            log.info(
                f"Deleting users inactive for more than {form_data.delete_inactive_users_days} days"
            )
            deleted_users = await delete_inactive_users(
                form_data.delete_inactive_users_days,
                vector_cleaner,
                form_data.exempt_admin_users,
                form_data.exempt_pending_users,
            )
            if deleted_users > 0:
                log.info(f"Deleted {deleted_users} inactive users")
            else:
                log.info("No inactive users found to delete")
        else:
            log.info("Skipping inactive user deletion (disabled)")

        # Stage 1: Delete old chats — stream IDs only to avoid loading full chat JSON
        if form_data.days is not None:
            cutoff_time = int(time.time()) - (form_data.days * 86400)

            async with get_async_db() as db:
                conditions = Chat.updated_at < cutoff_time
                if form_data.exempt_archived_chats:
                    conditions &= or_(Chat.archived == False, Chat.archived == None)
                if form_data.exempt_chats_in_folders:
                    if hasattr(Chat, 'folder_id'):
                        conditions &= Chat.folder_id == None
                    if hasattr(Chat, 'pinned'):
                        conditions &= or_(Chat.pinned == False, Chat.pinned == None)

                deleted = 0
                async for (chat_id,) in stream_rows(db, Chat.id, filter_clause=conditions):
                    await Chats.delete_chat_by_id(chat_id, db=db)
                    deleted += 1
                if deleted > 0:
                    log.info(f"Deleting {deleted} old chats (older than {form_data.days} days)")
                else:
                    log.info(f"No chats found older than {form_data.days} days")
        else:
            log.info("Skipping chat deletion (days parameter is None)")

        # Stage 2: Build preservation set
        log.info("Building preservation set")

        active_user_ids = {str(user.id) for user in (await Users.get_users())["users"]}
        log.info(f"Found {len(active_user_ids)} active users")

        kb_map = await get_kb_user_map()
        active_kb_ids = {kb_id for kb_id, uid in kb_map.items() if uid in active_user_ids}
        log.info(f"Found {len(active_kb_ids)} active knowledge bases")

        active_file_ids = await get_active_file_ids(active_user_ids=active_user_ids)

        # Stage 3: Delete orphaned database records
        log.info("Deleting orphaned database records")

        deleted_files = 0
        # Stream id+user_id only, iterate directly — keyset pagination uses
        # fresh queries per batch, so deletions don't disrupt iteration
        async with get_async_db() as db:
            async for fid, uid in stream_rows(db, File.id, File.user_id):
                if str(fid) not in active_file_ids or str(uid) not in active_user_ids:
                    if await safe_delete_file_by_id(fid, vector_cleaner, db=db):
                        deleted_files += 1

        if deleted_files > 0:
            log.info(f"Deleted {deleted_files} orphaned files")

        deleted_kbs = 0
        if form_data.delete_orphaned_knowledge_bases:
            async with get_async_db() as db:
                for kb in await Knowledges.get_knowledge_bases(db=db):
                    if str(kb.user_id) not in active_user_ids:
                        if vector_cleaner.delete_collection(kb.id):
                            await Knowledges.delete_knowledge_by_id(kb.id, db=db)
                            deleted_kbs += 1

            if deleted_kbs > 0:
                log.info(f"Deleted {deleted_kbs} orphaned knowledge bases")
        else:
            log.info("Skipping knowledge base deletion (disabled)")

        deleted_others = 0

        # Chats — stream IDs + user_ids, filter via Python set membership
        # to avoid SQLite's ~999 parameter limit with NOT IN clauses
        if form_data.delete_orphaned_chats:
            chats_deleted = 0
            async with get_async_db() as db:
                async for chat_id, chat_uid in stream_rows(db, Chat.id, Chat.user_id):
                    if str(chat_uid) not in active_user_ids:
                        await Chats.delete_chat_by_id(chat_id, db=db)
                        chats_deleted += 1
                        deleted_others += 1
            if chats_deleted > 0:
                log.info(f"Deleted {chats_deleted} orphaned chats")
        else:
            log.info("Skipping orphaned chat deletion (disabled)")

        if form_data.delete_orphaned_tools:
            tools_deleted = 0
            async with get_async_db() as db:
                for tool in await Tools.get_tools(db=db):
                    if str(tool.user_id) not in active_user_ids:
                        await Tools.delete_tool_by_id(tool.id, db=db)
                        tools_deleted += 1
                        deleted_others += 1
            if tools_deleted > 0:
                log.info(f"Deleted {tools_deleted} orphaned tools")
        else:
            log.info("Skipping tool deletion (disabled)")

        if form_data.delete_orphaned_functions:
            functions_deleted = 0
            async with get_async_db() as db:
                for function in await Functions.get_functions(db=db):
                    if str(function.user_id) not in active_user_ids:
                        await Functions.delete_function_by_id(function.id, db=db)
                        functions_deleted += 1
                        deleted_others += 1
            if functions_deleted > 0:
                log.info(f"Deleted {functions_deleted} orphaned functions")
        else:
            log.info("Skipping function deletion (disabled)")

        if form_data.delete_orphaned_notes:
            notes_deleted = 0
            async with get_async_db() as db:
                for note in await Notes.get_notes(db=db):
                    if str(note.user_id) not in active_user_ids:
                        await Notes.delete_note_by_id(note.id, db=db)
                        notes_deleted += 1
                        deleted_others += 1
            if notes_deleted > 0:
                log.info(f"Deleted {notes_deleted} orphaned notes")
        else:
            log.info("Skipping note deletion (disabled)")

        if form_data.delete_orphaned_skills:
            skills_deleted = 0
            async with get_async_db() as db:
                for skill in await Skills.get_skills(db=db):
                    if str(skill.user_id) not in active_user_ids:
                        await Skills.delete_skill_by_id(skill.id, db=db)
                        skills_deleted += 1
                        deleted_others += 1
            if skills_deleted > 0:
                log.info(f"Deleted {skills_deleted} orphaned skills")
        else:
            log.info("Skipping skill deletion (disabled)")

        if form_data.delete_orphaned_prompts:
            prompts_deleted = 0
            async with get_async_db() as db:
                for prompt in await Prompts.get_prompts(db=db):
                    if str(prompt.user_id) not in active_user_ids:
                        await Prompts.delete_prompt_by_command(prompt.command, db=db)
                        prompts_deleted += 1
                        deleted_others += 1
            if prompts_deleted > 0:
                log.info(f"Deleted {prompts_deleted} orphaned prompts")
        else:
            log.info("Skipping prompt deletion (disabled)")

        if form_data.delete_orphaned_models:
            models_deleted = 0
            async with get_async_db() as db:
                for model in await Models.get_all_models(db=db):
                    if str(model.user_id) not in active_user_ids:
                        await Models.delete_model_by_id(model.id, db=db)
                        models_deleted += 1
                        deleted_others += 1
            if models_deleted > 0:
                log.info(f"Deleted {models_deleted} orphaned models")
        else:
            log.info("Skipping model deletion (disabled)")

        if form_data.delete_orphaned_folders:
            folders_deleted = 0
            async with get_async_db() as db:
                for folder in await get_all_folders(db=db):
                    if str(folder.user_id) not in active_user_ids:
                        await Folders.delete_folder_by_id_and_user_id(
                            folder.id, folder.user_id, db=db
                        )
                        folders_deleted += 1
                        deleted_others += 1
            if folders_deleted > 0:
                log.info(f"Deleted {folders_deleted} orphaned folders")
        else:
            log.info("Skipping folder deletion (disabled)")

        if deleted_others > 0:
            log.info(f"Total other orphaned records deleted: {deleted_others}")

        # Stage 3b: Delete orphaned chat messages
        if form_data.delete_orphaned_chat_messages:
            deleted_chat_messages = await delete_orphaned_chat_messages()
            if deleted_chat_messages > 0:
                log.info(f"Deleted {deleted_chat_messages} orphaned chat messages")
        else:
            log.info("Skipping orphaned chat_message deletion (disabled)")

        # Stage 3c: Delete orphaned automations and automation runs
        if form_data.delete_orphaned_automations:
            deleted_automations = await delete_orphaned_automations(active_user_ids)
            if deleted_automations > 0:
                log.info(f"Deleted {deleted_automations} orphaned automations")

            deleted_automation_runs = await delete_orphaned_automation_runs()
            if deleted_automation_runs > 0:
                log.info(f"Deleted {deleted_automation_runs} orphaned automation runs")
        else:
            log.info("Skipping orphaned automation deletion (disabled)")

        # Stage 4: Clean up orphaned physical files and vector collections.
        # Recompute preservation sets after Stage 3 deletions — files that
        # were only referenced by now-deleted chats/KBs should no longer
        # be considered active.  This is safe with the streaming-based
        # get_active_file_ids() that replaced the OOM-prone ORM version.
        log.info("Recomputing preservation sets after deletions")
        active_user_ids = {str(user.id) for user in (await Users.get_users())["users"]}
        kb_map = await get_kb_user_map()
        active_kb_ids = {kb_id for kb_id, uid in kb_map.items() if uid in active_user_ids}
        active_file_ids = await get_active_file_ids(active_user_ids=active_user_ids)

        log.info("Cleaning up orphaned physical files")

        deleted_uploads = cleanup_orphaned_uploads(active_file_ids)
        if deleted_uploads > 0:
            log.info(f"Deleted {deleted_uploads} orphaned upload files")

        # Audio cache cleanup
        if form_data.audio_cache_max_age_days is not None:
            log.info(f"Cleaning audio cache files older than {form_data.audio_cache_max_age_days} days")
            cleanup_audio_cache(form_data.audio_cache_max_age_days)

        # Use modular vector database cleanup
        warnings = []
        deleted_vector_count, vector_error = vector_cleaner.cleanup_orphaned_collections(
            active_file_ids, active_kb_ids, active_user_ids
        )
        if vector_error:
            warnings.append(f"Vector cleanup warning: {vector_error}")
            log.warning(f"Vector cleanup completed with errors: {vector_error}")

        # Stage 5: Database optimization (optional)
        #
        # VACUUM is a DDL/maintenance command that CANNOT run inside a
        # transaction.  The async engine always opens a transaction, so we
        # use the sync engine directly with a raw DBAPI connection in
        # autocommit mode.  The sync engine is retained by Open WebUI
        # specifically for startup and maintenance tasks.
        if form_data.run_vacuum:
            log.info("Optimizing database with VACUUM (this may take a while and lock the database)")

            try:
                # Use the public engine.connect() API with a per-connection
                # isolation level override.  This avoids touching pool
                # internals and is stable across SQLAlchemy versions.
                with sync_engine.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as conn:
                    if 'postgresql' in str(sync_engine.url):
                        conn.execute(text("VACUUM ANALYZE"))
                        log.info("Vacuumed PostgreSQL main database")
                    else:
                        conn.execute(text("VACUUM"))
                        log.info("Vacuumed SQLite main database")
            except Exception as e:
                log.error(f"Failed to vacuum main database: {e}")

            # Vector database-specific optimization
            if isinstance(vector_cleaner, ChromaDatabaseCleaner):
                try:
                    with sqlite3.connect(str(vector_cleaner.chroma_db_path)) as conn:
                        conn.execute("VACUUM")
                        log.info("Vacuumed ChromaDB database")
                except Exception as e:
                    log.error(f"Failed to vacuum ChromaDB database: {e}")
            elif (
                isinstance(vector_cleaner, PGVectorDatabaseCleaner)
                and vector_cleaner.session
            ):
                try:
                    pg_engine = vector_cleaner.session.get_bind()
                    with pg_engine.connect().execution_options(
                        isolation_level="AUTOCOMMIT"
                    ) as pg_conn:
                        pg_conn.execute(text("VACUUM ANALYZE"))
                        log.info("Executed VACUUM ANALYZE on PostgreSQL vector database")
                except Exception as e:
                    log.error(f"Failed to vacuum PostgreSQL vector database: {e}")
        else:
            log.info("Skipping VACUUM optimization (not enabled)")

        # Log any warnings collected during pruning
        if warnings:
            log.warning(f"Data pruning completed with warnings: {'; '.join(warnings)}")

        log.info("Data pruning completed successfully")
        return True

    except Exception as e:
        log.exception(f"Error during data pruning: {e}")
        return False
    finally:
        # Always release lock, even if operation fails
        PruneLock.release()


async def async_main():
    """Async main entry point for standalone prune script."""
    args = parse_arguments()
    configure_logging(args.verbose, args.quiet)

    log.info("="*70)
    log.info("  Open WebUI Standalone Prune Script")
    log.info("="*70)

    # Verify environment
    log.info("Checking environment configuration...")

    # Check if we can access database
    try:
        users = await Users.get_users()
        log.info(f"✓ Database connection successful ({len(users['users'])} users found)")
    except Exception as e:
        log.error(f"✗ Failed to connect to database: {e}")
        log.error("  Make sure DATABASE_URL environment variable is set correctly")
        return 1

    # Initialize prune lock system
    PruneLock.init(Path(CACHE_DIR))

    # Create prune configuration
    form_data = create_prune_form(args)

    # Log configuration
    log.info("\nPrune Configuration:")
    if form_data.dry_run:
        log.info("  Mode: DRY RUN (preview only, no changes)")
    else:
        log.info("  Mode: EXECUTE (actual deletion)")

    if form_data.days is not None:
        log.info(f"  Delete chats older than: {form_data.days} days")
        log.info(f"    Exempt archived chats: {form_data.exempt_archived_chats}")
        log.info(f"    Exempt chats in folders: {form_data.exempt_chats_in_folders}")

    if form_data.delete_inactive_users_days is not None:
        log.info(f"  Delete inactive users: {form_data.delete_inactive_users_days} days")
        log.info(f"    Exempt admin users: {form_data.exempt_admin_users}")
        log.info(f"    Exempt pending users: {form_data.exempt_pending_users}")

    log.info("  Orphaned data deletion:")
    log.info(f"    Chats: {form_data.delete_orphaned_chats}")
    log.info(f"    Tools: {form_data.delete_orphaned_tools}")
    log.info(f"    Functions: {form_data.delete_orphaned_functions}")
    log.info(f"    Skills: {form_data.delete_orphaned_skills}")
    log.info(f"    Prompts: {form_data.delete_orphaned_prompts}")
    log.info(f"    Knowledge Bases: {form_data.delete_orphaned_knowledge_bases}")
    log.info(f"    Models: {form_data.delete_orphaned_models}")
    log.info(f"    Notes: {form_data.delete_orphaned_notes}")
    log.info(f"    Folders: {form_data.delete_orphaned_folders}")
    log.info(f"    Automations: {form_data.delete_orphaned_automations}")

    if form_data.audio_cache_max_age_days is not None:
        log.info(f"  Audio cache cleanup: {form_data.audio_cache_max_age_days} days")

    if form_data.run_vacuum:
        log.info("  Database VACUUM: ENABLED (will lock database!)")

    log.info("")

    # Run the prune operation
    success = await run_prune(form_data, export_preview_path=args.export_preview)

    if success:
        log.info("\n✓ Prune operation completed successfully")
        return 0
    else:
        log.error("\n✗ Prune operation failed")
        return 1


def main():
    """Synchronous wrapper that runs the async main via asyncio."""
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
