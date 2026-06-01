# Changelog

All notable changes to the Open WebUI Prune Tool are documented here. Each entry lists the minimum Open WebUI version it is compatible with; see [COMPATIBILITY.md](COMPATIBILITY.md) for the full matrix, and the [Releases](https://github.com/Classic298/prune-open-webui/releases) page for downloads.

## [v1.2.1.1] - 2026-06-02

Compatible with Open WebUI v0.9.6

### Fixed

- **Active users' memories are no longer deleted during vector cleanup.** On the non-multitenancy backends (Chroma, the default, plus PGVector, Milvus, and standalone Qdrant), the orphan sweep built its set of expected collections from active files and knowledge bases only. It never accounted for per-user `user-memory-{id}` collections, so it treated every memory collection as orphaned and deleted it, including those of active users. A normal prune run could therefore wipe live memories.

  The cleaner now preserves `user-memory-{id}` for every active user and removes only a deleted user's memory collection. This matches the multitenancy cleaners (Qdrant and Milvus multitenancy), which already scoped memory collections to active users and were not affected.

### Who should upgrade

- Anyone on v1.2.1 using the Memories feature with Chroma, PGVector, Milvus, or standalone Qdrant should upgrade before their next run. Multitenancy deployments were unaffected.

## [v1.2.1] - 2026-06-02

Compatible with Open WebUI v0.9.6

### ⚠️ Breaking changes

- **Pinned-chat exemption is now a separate flag.** Previously `--exempt-chats-in-folders` also preserved pinned chats. It now preserves foldered chats only, and pinned chats are kept by the new `--exempt-pinned-chats` flag (off by default).
  - **Impact:** this changes behaviour silently for non-interactive runs (cron, `docker exec`, systemd). If a command relied on `--exempt-chats-in-folders` to keep pinned chats, add `--exempt-pinned-chats`, otherwise old pinned chats will be deleted on the next run. Interactive mode is unaffected in practice: it now asks about pinned and foldered chats as two separate prompts.

### Added

- **Age-based knowledge base retention (destructive, opt-in).** New `--delete-knowledge-bases-older-than-days N`, with `--knowledge-bases-age-field {created_at,updated_at}` (default `created_at`). This deletes knowledge bases purely by age, even when the owner is active and the KB is in use. It mirrors Open WebUI's own KB deletion: it removes the KB's vector collection, deletes the KB record, removes the KB's search-metadata embedding, and de-references the KB from any model that points at it (so referencing models keep working). The KB's now-unreferenced files, uploads, and per-file vector collections are reclaimed by the orphan sweep. Interactive mode adds a dedicated Knowledge Base Retention (DANGEROUS) configuration section.
- **Orphaned KB metadata cleanup.** New `--delete-orphaned-kb-metadata` (default on, `--no-` to disable). Removes leftover entries in the shared `knowledge-bases` search collection whose knowledge base no longer exists (e.g. KBs deleted outside the tool or by an older version). The same removal is folded into both the age-based and orphaned KB deletion paths so no ghost is left behind.
- **Orphaned memories cleanup.** New `--delete-orphaned-memories` (default on, `--no-` to disable). Reconciles each active user's `user-memory-{id}` collection against the `memory` table and removes memories left in the vector store after a user deleted them, fixing leftover memories that keep getting injected into chat context via RAG. On Qdrant this relies on the companion Open WebUI fix that deletes memories by point id (see Compatibility); Chroma and PGVector were already correct.
- **`--exempt-pinned-chats`** flag to preserve pinned chats during age-based deletion, wired through interactive config, the settings summary, preview counts, deletion, and CSV export.
- **New CSV export categories**: `old_knowledge_base`, `orphaned_kb_metadata`, `orphaned_memory`, so every new deletion type is visible in `--export-preview` before you execute.

### Changed

- **Documentation regrouped by deletion philosophy.** Configuration Options is now split into three categories, ordered safest to most destructive: Orphaned Data Cleanup (owner no longer exists), Age-Based Deletion (bounded blast radius), and Retention Policy (deletes live, owned, in-use data), plus an Execution & Output group.
- **`--run-vacuum` now reflects that it covers the main and vector databases.** Help text, the interactive VACUUM warning, and the README warning were updated accordingly.
- `count_old_chats` gained a defensive `exempt_pinned=False` default so existing/external callers do not break.

### Fixed

- **VACUUM no longer runs unconditionally during routine prunes.** The PGVector cleaner previously executed `VACUUM ANALYZE document_chunk` on every `--execute` run as part of orphan-chunk cleanup, locking that table even when `--run-vacuum` was not set. Vector-database VACUUM now happens only when `--run-vacuum` is enabled, where it already vacuums the whole vector database. Routine prunes are faster and no longer lock the table; reclaiming dead space is deferred to your maintenance window, as intended.

### Compatibility

- The orphaned-memories cleanup on Qdrant requires the Open WebUI release that deletes memories by point id (Chroma and PGVector need no change). Other features work against current Open WebUI; see [COMPATIBILITY.md](COMPATIBILITY.md).

### Upgrade notes

- Review the breaking change above and add `--exempt-pinned-chats` to any scripted command that was relying on `--exempt-chats-in-folders` to keep pinned chats.
- The new retention flag (`--delete-knowledge-bases-older-than-days`) deletes live data and has no undo. Always run with `--dry-run` (optionally `--export-preview`) first.

## Earlier releases

See the [Releases](https://github.com/Classic298/prune-open-webui/releases) page for v1.2.0 and earlier, and [COMPATIBILITY.md](COMPATIBILITY.md) for the Open WebUI version each requires.
