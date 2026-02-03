# Open WebUI Prune Tool

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
- Cleaning orphaned data (files, tools, prompts, etc.)
- Removing old audio cache files
- Optimizing database performance

It runs independently of the web server and can be scheduled for automated maintenance.

## Features

✅ **Two Operation Modes**
- **Interactive Mode**: Beautiful terminal UI with step-by-step wizard
- **Non-Interactive Mode**: Command-line interface for automation

✅ **Complete Configurability**
- All 17 configuration options are fully configurable
- Preview mode to see what will be deleted before execution
- Fine-grained control over what gets deleted

✅ **Database Support**
- SQLite (default)
- PostgreSQL
- Vector databases: ChromaDB, PGVector, Milvus, Qdrant

✅ **Safety Features**
- File-based locking prevents concurrent operations
- Dry-run mode by default
- Multiple confirmation prompts (interactive mode)
- Admin user protection
- Detailed logging of all operations

## Installation

### Prerequisites

- Open WebUI installation
- Python 3.11+
- Access to Open WebUI's Python environment and database
- All Open WebUI dependencies installed (including vector database libraries like chromadb)

### Method 1: Git Installation (Manual Install)

**One-time setup:**
```bash
cd ~/path/to/open-webui        # Navigate to your Open WebUI directory
source venv/bin/activate        # Activate your Python virtual environment
pip install -r backend/requirements.txt  # Install backend dependencies
pip install rich                # For interactive mode (optional)
```

**Ready to use:**
```bash
cd ~/path/to/open-webui        # Must run from repo root
source venv/bin/activate
python prune/prune.py          # Launch interactive mode
```

### Method 2: Docker Installation (Recommended)

Running inside the Docker container is the **recommended approach** because all dependencies (including vector database libraries like chromadb) are already installed.

**Step 1: Download the prune folder** (one-time setup)
```bash
# Clone only the prune folder using sparse checkout
git clone --filter=blob:none --no-checkout https://github.com/Classic298/open-webui.git
cd open-webui
git sparse-checkout init --cone
git sparse-checkout set prune
git checkout claude/analyze-prune-router-01C8wACN95WDtT67c8ULXdkr
```

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

If Open WebUI runs as a systemd service (common for production installations), environment variables like `DATABASE_URL` are **only available to the service**, not to your terminal session.

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

# Ensure all dependencies are installed
pip install -r backend/requirements.txt

# Find installation location
pip show open-webui | grep Location

# Run from that location
cd <location>

# Set required environment variables
export DATABASE_URL="postgresql://user:password@localhost:5432/openwebui"
export VECTOR_DB="pgvector"  # or chroma, milvus, qdrant

python -m open_webui.prune  # Or appropriate path
```

**For repeated use, create a .env file:**
```bash
# Create .env in your Open WebUI directory
cat > .env <<'EOF'
DATABASE_URL=postgresql://user:password@localhost:5432/openwebui
VECTOR_DB=pgvector
DATA_DIR=/path/to/data
EOF

# The script will automatically load it
python -m open_webui.prune --days 60 --dry-run
```

### Method 5: Wrapper Script

Create a file `run_prune.sh`:

```bash
#!/bin/bash
cd /path/to/open-webui
source .venv/bin/activate

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Run prune script
python prune/prune.py "$@"
```

Make executable:
```bash
chmod +x run_prune.sh
```

## Quick Start

### Interactive Mode (Recommended for First-Time Users)

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

**Preview Mode (Safe):**
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

# Include admins in deletion (NOT RECOMMENDED!)
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
3. **Create backups before pruning** (automated backup script)
4. **Monitor logs regularly** for errors
5. **Start conservative**, adjust gradually
6. **Never use VACUUM** during active hours

## Important Warnings

### ⚠️ CRITICAL: This is a Destructive Operation

**Deleted data cannot be recovered. Always create backups before executing.**

```bash
# For SQLite
cp data/webui.db data/webui.db.backup

# For PostgreSQL
pg_dump openwebui > backup.sql

# Backup files
tar -czf backup.tar.gz data/uploads data/vector_db
```

### ⚠️ User Deletion Cascades

When you delete a user, **ALL their data is deleted**:
- Chats and messages
- Files and uploads
- Custom tools and functions
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

This is the **most common issue** for systemd and pip installations!

**Problem:** The script tries to connect to SQLite but you're using PostgreSQL, **OR** environment variables from your systemd service file are not available in your terminal session.

**Symptoms:**
```
sqlite3.OperationalError: unable to open database file
Failed to connect to database
```

**Root Cause:** Environment variables like `DATABASE_URL` are set in your systemd service file but are **NOT** exported to your shell session.

**Solution 1: Quick Test - Export inline**
```bash
# Replace with YOUR actual database credentials
DATABASE_URL="postgresql://user:password@localhost:5432/openwebui" \
python prune/prune.py --days 60 --dry-run
```

**Solution 2: Create .env file (Recommended)**
```bash
# Create .env file in Open WebUI directory
cat > /opt/openwebui/.env <<'EOF'
DATABASE_URL=postgresql://openwebui:your_password@localhost:5432/openwebui
VECTOR_DB=pgvector
DATA_DIR=/var/lib/openwebui
CACHE_DIR=/var/lib/openwebui/cache
EOF

# Run prune script (it will auto-load .env)
cd /opt/openwebui
python prune/prune.py --days 60 --dry-run
```

**Solution 3: Check your systemd service file**
```bash
# View your systemd service environment
systemctl show openwebui.service -p Environment

# You should see DATABASE_URL and other variables
# Copy those to a .env file or export them manually
```

**For SQLite installations:**
```bash
# Check environment variable
echo $DATABASE_URL

# Verify file exists
ls -la /path/to/webui.db

# Load from .env if needed
export $(cat .env | grep -v '^#' | xargs)
```

**Important Notes:**
- If your password contains special characters (`@`, `:`, `/`, `?`, `#`), you must URL-encode them
- Example: `password@123` → `password%40123`
- The prune script needs the **exact same** environment variables as your Open WebUI service
- See [Method 3: Systemd Service Installation](#method-3-systemd-service-installation) for complete setup guide

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

## How It Works

The script accesses Open WebUI's backend directly:
- Imports from `backend.open_webui` modules
- Uses same database connection (`DATABASE_URL`)
- Uses same vector database configuration
- Reuses all prune logic from the API router

**This means:**
- ✅ No code duplication
- ✅ Same behavior as web UI
- ✅ Automatically gets updates
- ✅ Battle-tested code

**But requires:**
- Same Python environment as Open WebUI
- Access to database
- Ability to import backend modules

## Technical Details

### What Gets Deleted

**By Age:**
- Chats older than specified days (based on `updated_at`)
- Users inactive for specified days (based on `last_active_at`)
- Audio cache files (based on file `mtime`)

**Orphaned Data:**
- Chats/tools/prompts/etc. from deleted users
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

Fully supports cleanup for:
- **ChromaDB**: SQLite metadata + directories + FTS indices
- **PGVector**: PostgreSQL tables + embeddings
- **Milvus**: Standard and multitenancy modes
- **Qdrant**: Standard and multitenancy modes
- **Others**: Safe no-op (does nothing)

### Safety Features

- **File-based locking** prevents concurrent runs
- **Dry-run by default** requires explicit `--execute`
- **Admin protection** enabled by default
- **Stale lock detection** automatic cleanup
- **Error handling** per-item with graceful degradation
- **Comprehensive logging** all operations tracked

## Support

For issues or questions:
- Review this README
- Check logs: `tail -f /var/log/openwebui-prune.log`
- Test in staging environment
- Open an issue

---

**Remember:** With great power comes great responsibility. Always preview first, create backups, and start with conservative settings!
