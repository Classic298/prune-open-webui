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
- Cleaning orphaned data (files, tools, skills, prompts, etc.)
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
- Fine-grained control over what gets deleted

✅ **Database Support**
- SQLite (default)
- PostgreSQL
- Vector databases (community contributions for additional backends welcome!):
  - ChromaDB — full cleanup support
  - PGVector — full cleanup support
  - Milvus — full cleanup support
  - Qdrant — full cleanup support

✅ **Safety Features**
- File-based locking prevents concurrent operations
- Explicit `--execute` flag required — nothing is deleted without it
- Multiple confirmation prompts (interactive mode)
- Admin user protection
- Detailed logging of all operations

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
│   ├── prune_operations.py
│   ├── prune_imports.py
│   ├── prune_models.py
│   └── requirements.txt
└── ...
```

### Method 1: Git Installation (Manual Install)

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

### Method 2: Docker Installation (Recommended)

Running inside the Docker container is the **recommended approach** because all dependencies (including vector database libraries like chromadb) are already installed.

**Step 1: Download the prune tool** (one-time setup)
```bash
git clone https://github.com/Classic298/prune-open-webui.git prune
```

**⚠️ The trailing `prune` in the clone command is required** — it ensures the repository is cloned into a folder named `prune/` instead of `prune-open-webui/`.

**Step 2: Copy files into your Docker container** (one-time setup)
```bash
# Find your Open WebUI container name
docker ps | grep open-webui

# Copy the prune folder into the container
docker cp prune <container-name>:/app/
```

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

**Required Environment Variables:**

⚠️ **IMPORTANT:** The prune script requires a **properly configured** Open WebUI container to function. If you get "*Required environment variable not found*" error, your Open WebUI container is not configured correctly.

**Required variables:**
- `WEBUI_SECRET_KEY` - Secret key for Open WebUI (required)
- `DATABASE_URL` - Database connection string (required)
- `DATA_DIR` - Data directory path (optional, default: `/app/backend/data`)
- `VECTOR_DB` - Vector database type if using RAG (optional)

**How to properly configure your Open WebUI container:**

Using docker-compose.yml (recommended):
```yaml
services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:main
    volumes:
      - open-webui:/app/backend/data
      - ./prune:/app/prune
    environment:
      - WEBUI_SECRET_KEY=${WEBUI_SECRET_KEY}
      - DATABASE_URL=sqlite:////app/backend/data/webui.db
      - DATA_DIR=/app/backend/data
    ports:
      - "3000:8080"
```

Or using docker run:
```bash
docker run -d \
  -e WEBUI_SECRET_KEY="your-secret-key" \
  -e DATABASE_URL="sqlite:////app/backend/data/webui.db" \
  -e DATA_DIR="/app/backend/data" \
  -v open-webui:/app/backend/data \
  -v ./prune:/app/prune \
  -p 3000:8080 \
  --name open-webui \
  ghcr.io/open-webui/open-webui:main
```

### Method 3: Systemd Service Installation

If Open WebUI runs as a systemd service, environment variables like `DATABASE_URL` are **only available to the service**, not to your terminal session.

**⚠️ IMPORTANT:** The prune script needs the same environment variables as Open WebUI. If you get "unable to open database file" or "Failed to connect to database" errors, your environment variables are not set in your shell.

**Solution 1: Export environment variables inline** (Quick fix)
```bash
DATABASE_URL="postgresql://user:password@localhost:5432/openwebui" \
VECTOR_DB="pgvector" \
python /path/to/prune/prune.py --days 60 --dry-run
```

**Solution 2: Create a .env file** (Recommended for repeated use)
```bash
# Create .env file in Open WebUI directory
cat > /opt/openwebui/.env <<'EOF'
DATABASE_URL=postgresql://user:password@localhost:5432/openwebui
VECTOR_DB=pgvector
DATA_DIR=/var/lib/openwebui
CACHE_DIR=/var/lib/openwebui/cache
EOF

# The script will automatically load this .env file
cd /opt/openwebui
python prune/prune.py --days 60 --dry-run
```

**Solution 3: Source systemd environment** (Advanced)
```bash
# Extract and export all environment variables from systemd service
set -a
source <(systemctl show openwebui.service -p Environment | \
  sed 's/^Environment=//; s/ /\n/g')
set +a

# Now run the prune script
python /path/to/prune/prune.py --days 60 --dry-run
```

**Solution 4: Create a wrapper script** (Best for automation)
```bash
# Create /usr/local/bin/openwebui-prune
cat > /usr/local/bin/openwebui-prune <<'EOF'
#!/bin/bash
# Open WebUI Prune Wrapper Script

# Set working directory
cd /opt/openwebui

# Source environment variables from .env file
if [ -f /opt/openwebui/.env ]; then
    export $(cat /opt/openwebui/.env | grep -v '^#' | xargs)
fi

# Run prune script with all arguments
python prune/prune.py "$@"
EOF

# Make executable
chmod +x /usr/local/bin/openwebui-prune

# Now you can run from anywhere
openwebui-prune --days 60 --dry-run
```

**Required Environment Variables for PostgreSQL:**
```bash
DATABASE_URL=postgresql://username:password@host:port/database
VECTOR_DB=pgvector                    # If using pgvector for RAG
DATA_DIR=/var/lib/openwebui           # Data directory path
CACHE_DIR=/var/lib/openwebui/cache    # Cache directory path
```

**Note:** If your password contains special characters, URL-encode them:
- `@` → `%40`
- `:` → `%3A`
- `/` → `%2F`
- `?` → `%3F`
- `#` → `%23`

Example: `password@123` becomes `password%40123`

### Method 4: Pip Installation

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

# Set required environment variables (see Method 3 for .env file alternative)
export DATABASE_URL="postgresql://user:password@localhost:5432/openwebui"
export VECTOR_DB="pgvector"  # or chroma

python prune/prune.py
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

## Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--days N` | int | None | Delete chats older than N days |
| `--exempt-archived-chats` | flag | False | Keep archived chats |
| `--exempt-chats-in-folders` | flag | False | Keep chats in folders/pinned |
| `--delete-inactive-users-days N` | int | None | Delete users inactive N+ days |
| `--exempt-admin-users` | flag | True | Never delete admins (RECOMMENDED) |
| `--exempt-pending-users` | flag | True | Never delete pending users |
| `--delete-orphaned-chats` | flag | True | Clean orphaned chats |
| `--delete-orphaned-tools` | flag | False | Clean orphaned tools |
| `--delete-orphaned-functions` | flag | False | Clean orphaned functions |
| `--delete-orphaned-skills` | flag | False | Clean orphaned skills |
| `--delete-orphaned-prompts` | flag | True | Clean orphaned prompts |
| `--delete-orphaned-knowledge-bases` | flag | True | Clean orphaned KBs |
| `--delete-orphaned-models` | flag | True | Clean orphaned models |
| `--delete-orphaned-notes` | flag | True | Clean orphaned notes |
| `--delete-orphaned-folders` | flag | True | Clean orphaned folders |
| `--audio-cache-max-age-days N` | int | 30 | Clean audio files older than N days |
| `--run-vacuum` | flag | False | Run database optimization (locks DB!) |
| `--dry-run` | flag | True | Preview only (default) |
| `--execute` | flag | - | Actually perform deletions |
| `--verbose, -v` | flag | - | Enable debug logging |
| `--quiet, -q` | flag | - | Suppress non-error output |

### Using Flags

To **disable** a default-true flag, use `--no-` prefix:
```bash
# Delete archived chats too
python prune/prune.py --days 60 --no-exempt-archived-chats --execute

# Include admins in inactive user deletion (NOT RECOMMENDED!)
python prune/prune.py --delete-inactive-users-days 180 --no-exempt-admin-users --execute
```

## Automation

### Cron Job Example

```bash
# Edit crontab
crontab -e

# Weekly cleanup every Sunday at 2 AM
0 2 * * 0 /path/to/run_prune.sh --days 90 --audio-cache-max-age-days 30 --execute >> /var/log/openwebui-prune.log 2>&1

# Monthly full cleanup with VACUUM (first Sunday at 3 AM)
0 3 1-7 * 0 /path/to/run_prune.sh --days 60 --delete-inactive-users-days 180 --run-vacuum --execute >> /var/log/openwebui-prune-monthly.log 2>&1
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
- Prompts, models, notes, folders
- Everything they created

**Recommendations:**
- Use long periods (180+ days minimum)
- **Always** exempt admin users
- Test on staging first

### ⚠️ VACUUM Locks Database

When `--run-vacuum` is enabled:
- **Entire database is locked** during operation
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

**Solution:** See [Method 3: Systemd Service Installation](#method-3-systemd-service-installation) for detailed instructions on configuring environment variables for your setup.

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

**Orphaned Data:**
- Chats/tools/skills/prompts/etc. from deleted users
- Files not referenced in chats/KBs
- Vector collections for deleted files/KBs
- Physical upload files without DB records

**Preserved:**
- Active user accounts
- Referenced files
- Valid vector collections
- Recent data (within retention period)
- Exempted categories (archived, folders, admins)

### Vector Database Support

- **ChromaDB**: Full cleanup — SQLite metadata, directories, and FTS indices
- **PGVector**: Full cleanup — PostgreSQL tables and embeddings
- **Milvus**: Full cleanup — standard and multitenancy modes
- **Qdrant**: Full cleanup — standard and multitenancy modes
- **Others**: Safe no-op (does nothing)
- **Adding support for a new backend**: Subclass `VectorDatabaseCleaner` in `prune_core.py`, implement the abstract methods, and register it in the `get_vector_database_cleaner` factory. Community contributions are welcome!

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
