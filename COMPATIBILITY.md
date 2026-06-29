# Compatibility Matrix

This document tracks which minimum version of the Prune Tool is required for each Open WebUI version. Use the latest prune release that meets the minimum for your Open WebUI version.

> [!NOTE]
> Find your Open WebUI version in the left column. The right column shows the oldest prune tool release that supports it. Always use the **latest** available prune script version that still supports your version of Open WebUI. You can find all releases in the [Releases](https://github.com/Classic298/prune-open-webui/releases) section — check the release notes to see which Open WebUI versions each release supports.

| Open WebUI Version | Minimum Prune Version Number | Notes |
|---|---|---|
| v0.9.6+ | v1.2.2 | Adds channel and channel-message pruning: age-based channel-message expiry (the channel itself is kept), orphaned-channel cleanup (deleted-user channels with their messages and files), and orphaned channel-message cleanup. Additive only — works on any Open WebUI build with channels and raises no new requirement beyond v1.2.1 (newer channel tables are cleaned when present, skipped otherwise). [Release](https://github.com/Classic298/prune-open-webui/releases/tag/v1.2.2) |
| v0.9.6+ | v1.2.1 | Adds knowledge base age-retention, orphaned KB-metadata cleanup, orphaned-memories cleanup, and a separate `--exempt-pinned-chats` flag. Minimum raised to 0.9.6 because orphaned-memories cleanup on Qdrant relies on Open WebUI's memory delete-by-id fix (shipped in 0.9.6); KB age-retention and KB-metadata cleanup also assume 0.9.6 KB-deletion semantics. [Release](https://github.com/Classic298/prune-open-webui/releases/tag/v1.2.1) |
| v0.9.0+ | v1.2.0 | Adds automation and automation_run table cleanup and full async rewrite — required by Open WebUI 0.9.0's async data layer — [Release](https://github.com/Classic298/prune-open-webui/releases/tag/v1.2.0)  |
| v0.8.0+ | v1.0.1 | [Release](https://github.com/Classic298/prune-open-webui/releases/tag/v1.0.1) |
| v0.7.X  | v1.0.0 | Initial release |
