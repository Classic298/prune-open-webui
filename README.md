# Open WebUI Prune Tool

> [!IMPORTANT]
> **This is a community-built tool.** It is not an official part of the Open WebUI project. It is developed and maintained independently by community contributors on a best-effort basis. While we strive for correctness and safety, there are no guarantees of functionality, compatibility, or continued maintenance.

> [!CAUTION]
> **This tool performs irreversible, destructive operations on your database and file system.** Deleted data cannot be recovered. There is no undo. Always create a full backup or snapshot of your server, database, and data directory before running any pruning operations. Test on a staging environment before running against production. The authors and contributors of this tool accept **no liability** for data loss, corruption, or any other damage caused by its use, whether due to bugs, misconfiguration, or user error. **You use this tool entirely at your own risk.**

A standalone command-line tool for cleaning up your Open WebUI instance, reclaiming disk space, and maintaining optimal performance.

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Installation](#installation)
   - [Environment Variables](#environment-variables)
   - [Method 1: Docker (Recommended)](#method-1-docker-installation-recommended)
   - [Method 2: Systemd Service](#method-2-systemd-service-installation)
   - [Method 3: Pip](#method-3-pip-installation)
   - [Method 4: Git (Manual)](#method-4-git-installation-manual-install)
4. [Quick Start](#quick-start)
5. [Usage](#usage)
6. [Configuration Options](#configuration-options)
7. [Automation](#automation)
8. [Important Warnings](#important-warnings)
9. [Troubleshooting](#troubleshooting)
10. [Support](#support)

## Overview

The Prune Tool provides a safe and powerful way to clean up your Open WebUI instance by:
- Deleting old chats and conversations
- Removing inactive user accounts
- Cleaning orphaned data (files, tools, skills, automations, prompts, etc.)
- Removing old audio cache files
- Optimizing database performance

It runs independently of the web server and can be scheduled for automated maintenance.

## Features

✅ **Two Operation Modes**
- **Interactive Mode**: Beautiful terminal UI with step-by-step wizard
- **Non-Interactive Mode**: Command-line interface for automation

✅ **Complete Configurability**
- All configuration options are fully configurable
- Preview mode to see what will be deleted before execution
- Export detailed preview to CSV for auditing before execution
- Fine-grained control over what gets deleted

✅ **Database Support**
- SQLite (default)
- PostgreSQL
- Vector databases (community contributions for additional backends welcome!):
  - ChromaDB — full cleanup support
  - PGVector — full cleanup support
  - Milvus — full cleanup support
  - Qdrant — full cleanup support

✅ **Storage Backend Support**
- Local filesystem (`data/uploads`)
- Amazon S3 (and S3-compatible endpoints) — honors `S3_KEY_PREFIX`
- Google Cloud Storage
- Azure Blob Storage

Orphaned storage objects (files with no matching `File` row in the database) are detected and removed from whichever backend Open WebUI is configured to use.

✅ **Safety Features**
- File-based locking prevents concurrent operations
- Explicit `--execute` flag required — nothing is deleted without it
- Multiple confirmation prompts (interactive mode)
- Admin user protection
- Detailed logging of all operations

### Known Limitations

- **Channels are not pruned.** The tool does not delete or clean up channels or channel messages. There has been no demand for this feature so far. If you need it, feel free to open a discussion.

### Compatibility

See [COMPATIBILITY.md](COMPATIBILITY.md) for the full version compatibility matrix.

## Installation

### Prerequisites

- Open WebUI installation
- Python 3.11+
- Access to Open WebUI's Python environment and database
- All Open WebUI dependencies installed (including vector database libraries like chromadb)

### Folder Structure

The prune tool is designed to live as a `prune/` subfolder inside your Open WebUI installation directory. All commands in this README assume that structure:

```
open-webui/              # Your Open WebUI root
├── backend/
├── prune/               # This repository, cloned here
│   ├── prune.py         # Main entry point
│   ├── prune_cli_interactive.py
│   ├── prune_core.py
│   ├── prune_export.py
│   ├── prune_operations.py
│   ├── prune_imports.py
│   ├── prune_models.py
│   └── requirements.txt
└── ...
```

### Environment Variables

The prune tool reads the **same environment variables as Open WebUI** so it can locate the database, data directory, and vector store. The variables are identical across every install method; only *how* you supply them differs (see each method below).

| Variable | Required | Description |
|---|---|---|
| `WEBUI_SECRET_KEY` | Yes | Secret key for Open WebUI |
| `DATABASE_URL` | Yes | Database connection string (SQLite or PostgreSQL) |
| `DATA_DIR` | No | Data directory path (default: `/app/backend/data`) |
| `CACHE_DIR` | No | Cache directory path (audio cache and the lock file) |
| `VECTOR_DB` | No | Vector database type if using RAG (e.g. `chroma`, `pgvector`) |

**SQLite (default):**
```bash
DATABASE_URL=sqlite:////app/backend/data/webui.db
```

**PostgreSQL:**
```bash
DATABASE_URL=postgresql://username:password@host:port/database
VECTOR_DB=pgvector
```

> **PostgreSQL passwords with special characters** must be URL-encoded: `@` becomes `%40`, `:` becomes `%3A`, `/` becomes `%2F`, `?` becomes `%3F`, `#` becomes `%23`. So `password@123` becomes `password%40123`.

### Providing the variables

The prune tool reads variables from the current environment and, if present, from a `.env` file in the working directory. Use whichever method matches your setup:

- **Docker:** set them in your `docker-compose.yml` `environment:` block (or with `-e` flags on `docker run`). A correctly configured Open WebUI container already has them, and `docker exec` inherits them automatically, so no extra setup is needed.
- **`.env` file (best for repeated, native, or systemd use):** create a `.env` in the Open WebUI directory and it is loaded automatically.
  ```bash
  cat > /opt/openwebui/.env <<'EOF'
  DATABASE_URL=postgresql://user:password@localhost:5432/openwebui
  VECTOR_DB=pgvector
  DATA_DIR=/var/lib/openwebui
  CACHE_DIR=/var/lib/openwebui/cache
  EOF
  ```
- **Systemd service:** a unit's variables are not exported to your shell, so source them before running the tool:
  ```bash
  set -a
  source <(systemctl show openwebui.service -p Environment | sed 's/^Environment=//; s/ /\n/g')
  set +a
  ```

> **These errors mean the variables are not set:** "Required environment variable not found", "unable to open database file", or "Failed to connect to database". Provide them with one of the methods above.

---

## Method 1: Docker Installation (Recommended)

Running inside the Docker container is the **recommended approach** because all dependencies (including vector database libraries like chromadb) are already installed.

**Step 1: Download the prune tool** (one-time setup)
```bash
git clone https://github.com/Classic298/prune-open-webui.git prune
```

**⚠️ The trailing `prune` in the clone command is required** — it ensures the repository is cloned into a folder named `prune/` instead of `prune-open-webui/`.

**Step 2: Make the prune tool available in the container** (one-time setup)

**A volume mount is the recommended approach.** Mounting the `prune/` folder into the container (rather than copying it in with `docker cp`) keeps the scripts in sync with your local copy, so you never have to re-copy after pulling updates. Add the mount to your `docker-compose.yml`:

```yaml
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    volumes:
      - open-webui:/app/backend/data
      - ./prune:/app/prune          # Mount the prune tool
```

Then recreate the container so the mount takes effect:
```bash
docker compose up -d
```

> **Alternative (one-off copy):** If you cannot edit your compose file, copy the folder in instead, but you will have to re-copy after every update.
> ```bash
> # Find your Open WebUI container name
> docker ps | grep open-webui
>
> # Copy the prune folder into the container
> docker cp prune <container-name>:/app/
> ```

**Step 3: Run the prune script**

**Option A: Run interactively inside container (Recommended)**
```bash
docker exec -it <container-name> bash
cd /app
python prune/prune.py
```

**Option B: Direct execution from host**
```bash
docker exec <container-name> python /app/prune/prune.py --days 90 --execute
```

**Option C: Preview mode from host**
```bash
docker exec <container-name> python /app/prune/prune.py --days 90 --dry-run
```

**Important Notes:**
- **Always execute inside the container** to ensure all dependencies are available
- Environment variables are automatically inherited from the container
- All vector database dependencies (chromadb, etc.) are pre-installed in the container
- Data persists in your Docker volumes

> A correctly configured Open WebUI container already has the required variables and `docker exec` inherits them, so no extra setup is needed. See [Environment Variables](#environment-variables).

---

## Method 2: Systemd Service Installation

If Open WebUI runs as a systemd service, its environment variables (like `DATABASE_URL`) are **only available to the service**, not to your terminal session.

Install the prune tool itself as shown in [Method 3: Pip](#method-3-pip-installation) or [Method 4: Git](#method-4-git-installation-manual-install). Because the service's variables are not in your shell, you must provide them before running the tool, for example with a `.env` file or by sourcing them from the unit.

See [Providing the variables](#providing-the-variables) for the `.env` file and systemd-sourcing approaches, and [Environment Variables](#environment-variables) for the full list.

---

## Method 3: Pip Installation

**Important:** Ensure all Open WebUI dependencies are installed, including vector database libraries (chromadb, etc.). The prune script requires the same dependencies as the main Open WebUI application.

```bash
# Activate environment where open-webui is installed
source venv/bin/activate

# Ensure all dependencies of Open WebUI are installed
pip install -r backend/requirements.txt
# Also install all dependencies of the prune script
pip install -r prune/requirements.txt

# Find installation location
pip show open-webui | grep Location

# Run from that location
cd <location>

# Set the required environment variables first (see Environment Variables above)
python prune/prune.py
```

---

## Method 4: Git Installation (Manual Install)

**One-time setup:**
```bash
cd ~/path/to/open-webui        # Navigate to your Open WebUI directory
git clone https://github.com/Classic298/prune-open-webui.git prune
source venv/bin/activate        # Activate your Python virtual environment
pip install -r prune/requirements.txt  # Install prune dependencies
pip install rich                # For interactive mode (optional)
```

**⚠️ The trailing `prune` in the clone command is required** — it ensures the repository is cloned into a folder named `prune/` instead of `prune-open-webui/`.

**Ready to use:**
```bash
cd ~/path/to/open-webui        # Must run from repo root
source venv/bin/activate
python prune/prune.py          # Launch interactive mode
```

## Quick Start

### Interactive Mode

```bash
python prune/prune.py
```

Features beautiful terminal UI with:
- Step-by-step configuration wizard
- Visual preview of what will be deleted
- Multiple safety confirmations
- Progress bars and status updates

### Non-Interactive Mode

```bash
# Preview what would be deleted (safe, no changes)
python prune/prune.py --days 90 --dry-run

# Delete chats older than 90 days
python prune/prune.py --days 90 --execute

# Full cleanup with optimization
python prune/prune.py \
  --days 90 \
  --delete-inactive-users-days 180 \
  --audio-cache-max-age-days 30 \
  --run-vacuum \
  --execute
```

### Export Preview to CSV

Before running a destructive operation, export a detailed list of every item that would be deleted to a CSV file for auditing:

```bash
# Export preview to CSV (non-interactive)
python prune/prune.py --days 90 --dry-run --export-preview preview.csv
```

The interactive mode also offers export after each preview — with size estimation and a progress bar.

The CSV contains one row per item with columns: `category`, `id`, `name`, `owner_id`, `size_bytes`, `reason`. The `size_bytes` column is populated for physical files (uploads and audio cache); other categories leave it empty.

## Usage

### Basic Patterns

**Preview Mode / --dry-run (Safe):**
```bash
python prune/prune.py --days 60 --dry-run
```

**Conservative Cleanup:**
```bash
python prune/prune.py \
  --days 180 \
  --exempt-archived-chats \
  --exempt-pinned-chats \
  --exempt-chats-in-folders \
  --execute
```

**Orphaned Data Only:**
```bash
python prune/prune.py \
  --delete-orphaned-chats \
  --delete-orphaned-knowledge-bases \
  --execute
```

**Inactive Users:**
```bash
python prune/prune.py \
  --delete-inactive-users-days 180 \
  --exempt-admin-users \
  --exempt-pending-users \
  --execute
```

**Retention Policy — expire old knowledge bases (DESTRUCTIVE):**
```bash
# Deletes KBs created more than 365 days ago, even if their owner is active
# and the KB is still in use. Always preview with --dry-run first.
python prune/prune.py \
  --delete-knowledge-bases-older-than-days 365 \
  --dry-run
```

## Configuration Options

The deletion flags fall into three categories, ordered from safest to most destructive by *what they target*:

1. **Orphaned Data Cleanup** — removes data whose owner no longer exists. This is the tool's core, safe purpose: it only touches data nobody can reach anymore.
2. **Age-Based Deletion** — removes data by age with a bounded blast radius (a user's own old chats, inactive accounts, derived audio cache). Time-based, but self-contained.
3. **Retention Policy** — removes **live, owned, in-use** data purely by age. Destructive and opt-in: it deletes things an active user still relies on.

A fourth group (**Execution & Output**) controls preview, execution, optimization, and logging.

### 1. Orphaned Data Cleanup (owner no longer exists)

| Option | Type | Default | Negate with | Description |
|--------|------|---------|-------------|-------------|
| `--delete-orphaned-chats` | flag | True | `--no-delete-orphaned-chats` | Clean orphaned chats |
| `--delete-orphaned-knowledge-bases` | flag | True | `--no-delete-orphaned-knowledge-bases` | Clean orphaned KBs |
| `--delete-orphaned-kb-metadata` | flag | True | `--no-delete-orphaned-kb-metadata` | Clean KB search-metadata embeddings whose KB no longer exists |
| `--delete-orphaned-memory-points` | flag | True | `--no-delete-orphaned-memory-points` | Clean memory vector points whose memory was deleted by an active user |
| `--delete-orphaned-prompts` | flag | True | `--no-delete-orphaned-prompts` | Clean orphaned prompts |
| `--delete-orphaned-models` | flag | True | `--no-delete-orphaned-models` | Clean orphaned models |
| `--delete-orphaned-notes` | flag | True | `--no-delete-orphaned-notes` | Clean orphaned notes |
| `--delete-orphaned-folders` | flag | True | `--no-delete-orphaned-folders` | Clean orphaned folders |
| `--delete-orphaned-chat-messages` | flag | True | `--no-delete-orphaned-chat-messages` | Clean orphaned chat_message rows |
| `--delete-orphaned-automations` | flag | True | `--no-delete-orphaned-automations` | Clean orphaned automations and automation runs |
| `--delete-orphaned-tools` | flag | False | — | Clean orphaned tools |
| `--delete-orphaned-functions` | flag | False | — | Clean orphaned functions |
| `--delete-orphaned-skills` | flag | False | — | Clean orphaned skills |

### 2. Age-Based Deletion (bounded blast radius)

| Option | Type | Default | Negate with | Description |
|--------|------|---------|-------------|-------------|
| `--days N` | int | None | — | Delete chats older than N days (by last-updated) |
| `--exempt-archived-chats` | flag | False | — | Keep archived chats even if old |
| `--exempt-pinned-chats` | flag | False | — | Keep pinned chats even if old |
| `--exempt-chats-in-folders` | flag | False | — | Keep chats in folders even if old |
| `--delete-inactive-users-days N` | int | None | — | Delete users inactive N+ days (cascades all their data) |
| `--exempt-admin-users` | flag | True | `--no-exempt-admin-users` | Never delete admins (RECOMMENDED) |
| `--exempt-pending-users` | flag | True | `--no-exempt-pending-users` | Never delete pending users |
| `--audio-cache-max-age-days N` | int | None | — | Clean audio cache files older than N days |

### 3. Retention Policy (DESTRUCTIVE — deletes live, owned, in-use data)

**These flags delete data that is still owned by an active user and may be in active use.** This is a data-retention/expiry policy, not orphan cleanup. There is no undo. Only enable it if your policy genuinely requires expiring live content by age.

| Option | Type | Default | Negate with | Description |
|--------|------|---------|-------------|-------------|
| `--delete-knowledge-bases-older-than-days N` | int | None | — | Delete knowledge bases older than N days, **even if the owner exists and the KB is in use** |
| `--knowledge-bases-age-field` | `created_at`/`updated_at` | `created_at` | — | Timestamp used to measure KB age |

When a knowledge base is deleted this way, the tool mirrors Open WebUI's own KB deletion: it removes the KB's vector collection, deletes the KB record, removes the KB's search-metadata embedding from the shared `knowledge-bases` collection, and **de-references the KB from any model that points at it** (so referencing models keep working). The KB's now-unreferenced files, uploads, and per-file vector collections are reclaimed by the orphan sweep that runs immediately afterwards.

### Execution & Output

| Option | Type | Default | Negate with | Description |
|--------|------|---------|-------------|-------------|
| `--run-vacuum` | flag | False | — | Run database optimization on main and vector DBs (locks DB!) |
| `--dry-run` | flag | True | — | Preview only (default) |
| `--execute` | flag | — | — | Actually perform deletions |
| `--verbose, -v` | flag | — | — | Enable debug logging |
| `--quiet, -q` | flag | — | — | Suppress non-error output |
| `--export-preview PATH` | str | None | — | Export detailed preview to CSV (requires `--dry-run`) |

### Using Flags

Every flag that defaults to **True** can be disabled with the `--no-` prefix. This lets you selectively run only specific cleanup categories. Flags that default to **False** (e.g. `--delete-orphaned-tools`, `--delete-orphaned-functions`, `--delete-orphaned-skills`) do not have a `--no-` variant — they are already off unless you explicitly enable them.

```bash
# Disable all default-on orphan cleanup except chats — process one category at a time
python prune/prune.py \
  --no-delete-orphaned-prompts \
  --no-delete-orphaned-knowledge-bases \
  --no-delete-orphaned-models \
  --no-delete-orphaned-notes \
  --no-delete-orphaned-folders \
  --no-delete-orphaned-chat-messages \
  --no-delete-orphaned-automations \
  --execute

# Delete archived chats too (overrides default exemption)
python prune/prune.py --days 60 --no-exempt-archived-chats --execute

# Include admins in inactive user deletion (NOT RECOMMENDED!)
python prune/prune.py --delete-inactive-users-days 180 --no-exempt-admin-users --execute
```

## Automation

### Cron Job Example (Native / Systemd / pip Installs)

```bash
# Edit crontab
crontab -e

# Weekly cleanup every Sunday at 2 AM
0 2 * * 0 /path/to/run_prune.sh --days 90 --audio-cache-max-age-days 30 --execute >> /var/log/openwebui-prune.log 2>&1

# Monthly full cleanup with VACUUM (first Sunday at 3 AM)
0 3 1-7 * 0 /path/to/run_prune.sh --days 60 --delete-inactive-users-days 180 --run-vacuum --execute >> /var/log/openwebui-prune-monthly.log 2>&1
```

### Docker Automation (Host-Side Cron)

Standard Open WebUI Docker containers **do not include cron**. The recommended approach is to schedule the cron job on your **host machine** and use `docker exec` to run the headless prune script inside the running container.

This works because `docker exec` runs a command inside an already-running container, inheriting its environment variables, Python dependencies, and database access — everything the prune script needs.

**Step 1: Mount the prune folder into your container**

Make sure your `docker-compose.yml` mounts the prune directory:

```yaml
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    volumes:
      - open-webui:/app/backend/data
      - ./prune:/app/prune          # Mount the prune tool
    environment:
      - WEBUI_SECRET_KEY=${WEBUI_SECRET_KEY}
      - DATABASE_URL=sqlite:////app/backend/data/webui.db
    ports:
      - "3000:8080"
```

> **Tip:** Mounting the folder (instead of `docker cp`) keeps the prune scripts in sync with your local copy and avoids having to re-copy after updates.

**Step 2: Verify it works manually**

```bash
# Preview mode first (safe, no changes)
docker exec open-webui python /app/prune/prune.py --days 90 --dry-run

# If the preview looks good, run with --execute
docker exec open-webui python /app/prune/prune.py --days 90 --execute
```

**Step 3: Add a cron job on the host**

```bash
# Edit the HOST machine's crontab (not inside the container)
crontab -e

# Weekly cleanup every Sunday at 2 AM
0 2 * * 0 docker exec open-webui python /app/prune/prune.py --days 90 --audio-cache-max-age-days 30 --execute >> /var/log/openwebui-prune.log 2>&1

# Monthly full cleanup with VACUUM (first Sunday at 3 AM)
0 3 1-7 * 0 docker exec open-webui python /app/prune/prune.py --days 60 --delete-inactive-users-days 180 --run-vacuum --execute >> /var/log/openwebui-prune-monthly.log 2>&1
```

> **Note:** Replace `open-webui` with your actual container name. Find it with `docker ps | grep open-webui`.

**Optional: Host-side wrapper script**

For cleaner cron entries, create a small wrapper script on the host:

```bash
#!/bin/bash
# /usr/local/bin/openwebui-prune-docker
# Wrapper to run prune inside the Open WebUI container from the host

CONTAINER_NAME="${OPENWEBUI_CONTAINER:-open-webui}"

# Check if the container is running
if ! docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null | grep -q true; then
    echo "ERROR: Container '$CONTAINER_NAME' is not running" >&2
    exit 1
fi

# Run prune script inside the container, forwarding all arguments
docker exec "$CONTAINER_NAME" python /app/prune/prune.py "$@"
```

Make it executable and use it in cron:

```bash
chmod +x /usr/local/bin/openwebui-prune-docker

# Now cron entries are cleaner:
0 2 * * 0 /usr/local/bin/openwebui-prune-docker --days 90 --execute >> /var/log/openwebui-prune.log 2>&1
```

### Best Practices for Automation

1. **Always test manually first** with `--dry-run`
2. **Schedule during low-usage hours** (2-4 AM)
3. **Create backups before pruning**
4. **Monitor logs regularly** for errors
5. **Start conservative**, adjust gradually
6. **Never use VACUUM** during active hours

## Important Warnings

### ⚠️ User Deletion Cascades

When you delete a user, **ALL their data is deleted**:
- Chats and messages
- Files and uploads
- Custom tools, functions, and skills
- Knowledge bases and embeddings
- Automations and automation run history
- Prompts, models, notes, folders
- Everything they created

**Recommendations:**
- Use long periods (180+ days minimum)
- **Always** exempt admin users
- Test on staging first

### ⚠️ VACUUM Locks Database

When `--run-vacuum` is enabled:
- **Main and vector databases may be locked** during operation
- **All users will experience errors**
- Can take **5-30+ minutes** (or longer)
- **Only use during scheduled maintenance windows**

### ⚠️ Preview First

**Always run with `--dry-run` first** to see what will be deleted:

```bash
# Safe preview
python prune/prune.py --days 60 --dry-run

# Review output, then execute
python prune/prune.py --days 60 --execute
```

## Troubleshooting

### Error: "Failed to import Open WebUI modules"

**Solution:** Must run from Open WebUI root directory

```bash
cd /path/to/open-webui  # Go to repo root, NOT prune/
python prune/prune.py
```

### Error: "Failed to connect to database" or "unable to open database file"

This is the **most common issue** for systemd and pip installations.

**Problem:** The script tries to connect to SQLite but you're using PostgreSQL, **OR** environment variables from your systemd service file are not available in your terminal session.

**Symptoms:**
```
sqlite3.OperationalError: unable to open database file
Failed to connect to database
```

**Root Cause:** Environment variables like `DATABASE_URL` are set in your systemd service file but are **NOT** exported to your shell session.

**Solution:** See [Method 2: Systemd Service Installation](#method-2-systemd-service-installation) for detailed instructions on configuring environment variables for your setup.

### Error: "Operation already in progress"

**Solution:** Remove stale lock file (if >2 hours old)

```bash
# Check lock file age
ls -la cache/.prune.lock

# Remove if stale
rm cache/.prune.lock
```

### Error: "No module named 'rich'"

**Solution:** Install optional dependency for interactive mode

```bash
pip install rich
```

### Error: "NoneType object has no attribute 'lower'" (VECTOR_DB issue)

**Problem:** Vector database dependencies are not installed.

**Solution:** Run inside Docker container (recommended), or install all Open WebUI dependencies:
```bash
pip install -r backend/requirements.txt
```

For non-Docker installations, ensure `VECTOR_DB` matches your configuration and the corresponding library is installed (chromadb, pgvector, etc.).

### Performance Issues

If operations are very slow:
- Check database size: `du -h data/webui.db`
- Run during off-hours
- Consider breaking into smaller operations
- Monitor with `htop` during execution

## Technical Details

### What Gets Deleted

**By Age:**
- Chats older than specified days (based on `updated_at`, ensuring only long-unused chats are deleted)
- Users inactive for specified days (based on `last_active_at`)
- Audio cache files (based on file `mtime`)

**By Retention Policy (destructive, opt-in):**
- Knowledge bases older than specified days (by `created_at` or `updated_at`), **even when the owner is active and the KB is in use**. Referencing models are de-referenced; the KB's vector collection, search-metadata embedding, record, and now-unreferenced files are removed.

**Orphaned Data:**
- Chats/tools/skills/automations/prompts/etc. from deleted users
- Automation runs from deleted automations
- Chat messages (analytics metadata) from deleted chats — see note below
- Files not referenced in chats/KBs (storage objects are deleted from the configured backend — local, S3, GCS, or Azure)
- Vector collections for deleted files/KBs
- Knowledge base search-metadata embeddings (in the shared `knowledge-bases` collection) whose knowledge base no longer exists. Open WebUI stores one small embedding per KB (its name and description) for semantic search across knowledge bases; this removes the leftover entry when a KB was deleted outside the tool or by an older version. Toggle with `--delete-orphaned-kb-metadata` / `--no-delete-orphaned-kb-metadata`.
- Memory vector points (in each active user's `user-memory-{id}` collection) whose memory row no longer exists. When a user deletes an individual memory, the vector point can be left behind and keep getting injected into chat context via RAG; this reconciles each active user's memory collection against the `memory` table and removes the orphans. Toggle with `--delete-orphaned-memory-points` / `--no-delete-orphaned-memory-points`.
- Storage objects (local uploads, S3/GCS/Azure blobs) whose path does not match any `File` row in the database

> [!NOTE]
> SQLite does not enforce `ON DELETE CASCADE` by default. This means deleting a chat may leave behind orphaned rows in the `chat_message` table (used by the Analytics feature). The prune tool detects and removes these automatically. PostgreSQL users are not affected.

**Preserved:**
- Active user accounts
- Referenced files
- Valid vector collections
- Recent data (within retention period)
- Exempted categories (archived, pinned, folders, admins)
- Knowledge bases, unless age-based retention (`--delete-knowledge-bases-older-than-days`) is explicitly enabled

### Vector Database Support

- **ChromaDB**: Full cleanup — SQLite metadata, directories, and FTS indices
- **PGVector**: Full cleanup — PostgreSQL tables and embeddings
- **Milvus**: Full cleanup — standard and multitenancy modes
- **Qdrant**: Full cleanup — standard and multitenancy modes
- **Others**: Safe no-op (does nothing)
- **Adding support for a new backend**: Subclass `VectorDatabaseCleaner` in `prune_core.py`, implement the abstract methods, and register it in the `get_vector_database_cleaner` factory. Community contributions are welcome!

### Storage Backend Support

The prune tool uses Open WebUI's existing `Storage` abstraction and its already-configured clients — no extra credentials or configuration are required beyond what Open WebUI itself needs. Whichever backend `STORAGE_PROVIDER` is set to, the prune tool will:

1. List every object in that backend
2. Compare each object's path against the set of active `File.path` values in the database
3. Delete objects whose path does not match any active `File` row

| `STORAGE_PROVIDER` | Scan target | Notes |
|---|---|---|
| `local` (default) | `data/uploads/` | Walks the directory with `Path.iterdir()` |
| `s3` | S3 bucket | Honors `S3_KEY_PREFIX` — objects outside the prefix are never touched |
| `gcs` | GCS bucket | Scans the entire bucket |
| `azure` | Azure container | Scans the entire container |
| unknown | — | Orphan storage scan is skipped with a warning |

> [!WARNING]
> For GCS and Azure, Open WebUI does not expose a key-prefix option. The prune tool scans the **entire** configured bucket/container and will flag any object with no matching `File` row as orphaned. If you share the bucket with other applications, either give Open WebUI a dedicated bucket or run with `--dry-run` / `--export-preview` first to review what would be deleted.

### Safety Features

- **File-based locking** prevents concurrent runs
- **Explicit execution** requires `--execute` flag — nothing is deleted without it
- **Admin protection** enabled by default
- **Stale lock detection** automatic cleanup
- **Error handling** per-item with graceful degradation
- **Comprehensive logging** all operations tracked

## Support

- Review this README and the [Troubleshooting](#troubleshooting) section
- Check logs: `tail -f /var/log/openwebui-prune.log`
- Test in a staging environment first
- **Questions, feature requests, and general discussion**: [Discussions](https://github.com/Classic298/prune-open-webui/discussions)
- **Reproducible bugs only**: [Open an issue](https://github.com/Classic298/prune-open-webui/issues)

---

> [!CAUTION]
> **With great power comes great responsibility.** Always preview first, create backups, and start with conservative settings. This tool is not responsible for your lack of backups.
