# Watchdog Repository Merge Task List

**Goal:** Combine `watchdog` (Pi) and `watchdog-app` (Desktop) into a single `watchdog-product` repository.

**Time Estimate:** 2-3 hours  
**Risk Level:** Low (no code changes, just reorganization)

---

## Ground Rules

1. **Never delete original repos** until Phase 4 is fully validated
2. **Commit after every task** so you can undo
3. **Test after every task** - don't batch
4. **If anything breaks, stop** and investigate before continuing

---

## PHASE 0: Preparation

### TASK 0.1: Create Backups

**Why:** Safety net before touching anything.

**Do this:**
```bash
# Create dated backups of both repos
cp -r watchdog watchdog-backup-$(date +%Y%m%d)
cp -r watchdog-app watchdog-app-backup-$(date +%Y%m%d)
```

**Verify:**
- [ ] Both backup folders exist
- [ ] Backup folders contain all files

---

### TASK 0.2: Document Current Working State

**Why:** You need to know what "working" looks like to verify later.

**Do this:** Create `VALIDATION_CHECKLIST.md`:

```markdown
# Watchdog Validation Checklist

## Pi System (watchdog)
- [ ] `python ble_watchdog.py` starts without errors
- [ ] Sensors detected within 60 seconds (if sensors available)
- [ ] `streamlit run app.py` launches dashboard on port 8501
- [ ] Dashboard shows sensor readings
- [ ] Historical charts render

## Desktop App (watchdog-app)
- [ ] `python watchdog_app.py` launches window
- [ ] Discovery shows setup dialog (or finds Pi if on network)
- [ ] Window closes cleanly
- [ ] Build script completes without errors

## Integration
- [ ] Desktop app can connect to running Pi dashboard
```

**Verify:**
- [ ] Run through checklist NOW, confirm everything passes
- [ ] Save this file - use it after every phase

---

## PHASE 1: Create New Repository Structure

### TASK 1.1: Create New Repository

**Why:** Fresh repo prevents git history confusion.

**Do this:**
```bash
mkdir watchdog-product
cd watchdog-product
git init

mkdir -p monitor
mkdir -p desktop
mkdir -p docs
mkdir -p releases

touch README.md
touch VERSION.txt
echo "1.0.0" > VERSION.txt
```

**Verify:**
- [ ] `watchdog-product/` directory exists with subdirectories
- [ ] `git status` shows clean new repo

---

### TASK 1.2: Create Top-Level README

**Why:** Single entry point explaining the product.

**Do this:** Create `watchdog-product/README.md`:

```markdown
# Watchdog Environmental Monitor

Production-grade environmental monitoring for serious hobbyists.

## Repository Structure

```
watchdog-product/
├── monitor/      # Raspberry Pi monitoring system
├── desktop/      # Desktop launcher application
├── docs/         # User documentation
└── releases/     # Build outputs
```

## Components

### Monitor (Raspberry Pi)
The core monitoring system. Runs on Raspberry Pi, scans for Govee BLE sensors,
stores data in SQLite, serves web dashboard.

See [monitor/README.md](monitor/README.md)

### Desktop App
Native desktop application for easy access to your Watchdog dashboard.
Auto-discovers Watchdog on your network.

See [desktop/README.md](desktop/README.md)

## Version
Current version: 1.0.0
```

**Verify:**
- [ ] README.md is readable
- [ ] Commit: `git add . && git commit -m "Initial repository structure"`

---

## PHASE 2: Move Pi Codebase

### TASK 2.1: Copy Monitor Files

**Why:** Move Pi codebase into new structure.

**Do this:**
```bash
cd watchdog-product

# Core Python files
cp ../watchdog/__init__.py monitor/
cp ../watchdog/alerts.py monitor/
cp ../watchdog/app.py monitor/
cp ../watchdog/ble_scanner.py monitor/
cp ../watchdog/ble_watchdog.py monitor/
cp ../watchdog/config.py monitor/
cp ../watchdog/logging_config.py monitor/
cp ../watchdog/process_manager.py monitor/
cp ../watchdog/storage.py monitor/
cp ../watchdog/weather_api.py monitor/

# Config and docs
cp ../watchdog/requirements.txt monitor/
cp ../watchdog/README.md monitor/
cp ../watchdog/CHANGELOG.md monitor/
cp ../watchdog/VERSION.txt monitor/
cp ../watchdog/.gitignore monitor/

# Shell scripts
cp ../watchdog/setup.sh monitor/
cp ../watchdog/check_health.sh monitor/
cp ../watchdog/deploy_pi.sh monitor/
cp ../watchdog/deploy_watchdog.sh monitor/

# Deploy directory
mkdir -p monitor/deploy
cp ../watchdog/deploy/watchdog-monitor.service monitor/deploy/
cp ../watchdog/deploy/watchdog-dashboard.service monitor/deploy/
cp ../watchdog/deploy/install-services.sh monitor/deploy/
cp ../watchdog/deploy/uninstall-services.sh monitor/deploy/
```

**Expected structure:**
```
monitor/
├── __init__.py
├── alerts.py
├── app.py
├── ble_scanner.py
├── ble_watchdog.py
├── config.py
├── logging_config.py
├── process_manager.py
├── storage.py
├── weather_api.py
├── requirements.txt
├── README.md
├── CHANGELOG.md
├── VERSION.txt
├── .gitignore
├── setup.sh
├── check_health.sh
├── deploy_pi.sh
├── deploy_watchdog.sh
└── deploy/
    ├── watchdog-monitor.service
    ├── watchdog-dashboard.service
    ├── install-services.sh
    └── uninstall-services.sh
```

**Verify:**
```bash
cd monitor
python3 -c "import ble_watchdog; print('OK')"
python3 -c "import app; print('OK')"
```
- [ ] Both import tests pass
- [ ] Commit: `git add . && git commit -m "Add monitor (Pi) codebase"`

---

### TASK 2.2: Test Monitor Standalone

**Why:** Confirm the move didn't break anything.

**Do this:**
```bash
cd watchdog-product/monitor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Test all critical imports
python -c "import ble_watchdog; print('ble_watchdog OK')"
python -c "import app; print('app OK')"
python -c "from config import load_config; print('config OK')"
python -c "from storage import get_connection; print('storage OK')"
python -c "from alerts import check_alerts; print('alerts OK')"
```

**Verify:**
- [ ] All import tests pass
- [ ] (Optional, if on Pi): `python ble_watchdog.py` starts

---

## PHASE 3: Move Desktop Codebase

### TASK 3.1: Copy Desktop App Files

**Why:** Move desktop app into new structure.

**Do this:**
```bash
cd watchdog-product

# Python files
cp ../watchdog-app/watchdog_app.py desktop/
cp ../watchdog-app/config.py desktop/
cp ../watchdog-app/discovery.py desktop/
cp ../watchdog-app/requirements.txt desktop/

# Build scripts
cp ../watchdog-app/build_mac.sh desktop/
cp ../watchdog-app/build_linux.sh desktop/
cp ../watchdog-app/build_windows.bat desktop/

# Docs
cp ../watchdog-app/README.md desktop/
cp ../watchdog-app/.gitignore desktop/

# Resources (icons, etc)
mkdir -p desktop/resources
cp -r ../watchdog-app/resources/* desktop/resources/ 2>/dev/null || echo "No resources to copy"
```

**Expected structure:**
```
desktop/
├── watchdog_app.py
├── config.py
├── discovery.py
├── requirements.txt
├── build_mac.sh
├── build_linux.sh
├── build_windows.bat
├── README.md
├── .gitignore
└── resources/
```

**Verify:**
```bash
cd desktop
python3 -c "import watchdog_app; print('OK')"
```
- [ ] Import test passes
- [ ] Commit: `git add . && git commit -m "Add desktop app codebase"`

---

### TASK 3.2: Rename Desktop config.py

**Why:** Both repos have `config.py` - rename desktop version to avoid confusion.

**Do this:**
```bash
cd watchdog-product/desktop
mv config.py app_config.py
```

**Then edit `watchdog_app.py`:**

Find this line (near top):
```python
from config import Config
```

Change to:
```python
from app_config import Config
```

**Verify:**
```bash
python3 -c "from watchdog_app import WatchdogApp; print('OK')"
```
- [ ] Import test passes
- [ ] Commit: `git add . && git commit -m "Rename desktop config to app_config"`

---

### TASK 3.3: Test Desktop App Standalone

**Why:** Confirm the move didn't break anything.

**Do this:**
```bash
cd watchdog-product/desktop
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python watchdog_app.py
# Window should open - close it manually
```

**Verify:**
- [ ] App window opens
- [ ] Shows setup dialog
- [ ] Window closes cleanly

---

## PHASE 4: Cleanup & Finalize

### TASK 4.1: Create Combined .gitignore

**Why:** Single gitignore for whole repo.

**Do this:** Create `watchdog-product/.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
venv/
env/
*.egg-info/
.eggs/

# Build outputs
build/
dist/
*.spec
*.app
*.exe
*.dmg

# Data (never commit)
*.sqlite3
*.db
data/

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Logs
*.log
logs/

# Secrets
.env
secrets.toml
```

**Verify:**
- [ ] Commit: `git add . && git commit -m "Add combined gitignore"`

---

### TASK 4.2: Delete Redundant Root-Level Service Files from Monitor

**Why:** There are duplicate service files - keep only the ones in `deploy/` folder.

**Do this:**
```bash
cd watchdog-product/monitor

# Remove root-level duplicates (deploy/ versions are authoritative)
rm -f watchdog-monitor.service
rm -f watchdog-dashboard.service
```

**Note:** The root-level `watchdog-monitor.service` references `ble_core` which doesn't exist. The `deploy/` versions are correct.

**Verify:**
- [ ] Only `deploy/*.service` files remain
- [ ] Commit: `git add . && git commit -m "Remove duplicate service files"`

---

### TASK 4.3: Full Validation

**Why:** Final check that everything works.

**Do this:** Run through your `VALIDATION_CHECKLIST.md` using new paths:

From `watchdog-product/monitor/`:
- [ ] `source venv/bin/activate && python ble_watchdog.py` starts
- [ ] `streamlit run app.py` launches

From `watchdog-product/desktop/`:
- [ ] `source venv/bin/activate && python watchdog_app.py` opens window

**Verify:**
- [ ] All checklist items pass
- [ ] Commit: `git add . && git commit -m "Merge complete - validated"`

---

### TASK 4.4: Push to GitHub

**Do this:**
```bash
cd watchdog-product
git remote add origin https://github.com/YOUR_USERNAME/watchdog-product.git
git branch -M main
git push -u origin main
```

**Verify:**
- [ ] Repo visible on GitHub
- [ ] All files present

---

## Post-Merge Notes

**Keep original repos for 2+ weeks** until you're confident the merge is stable.

**Next steps after merge:**
1. Apply code fixes (see CODE-FIXES.md)
2. Update deployment scripts for new paths
3. Create user documentation
4. Set up build automation

---

## Troubleshooting

**Import errors after move:**
- Check you're in the right directory
- Check virtual environment is activated
- Check all files were copied

**Git issues:**
- Each task should be one commit
- If something breaks, `git log` to see history, `git checkout <hash>` to go back

**Desktop app can't find config:**
- Verify you renamed `config.py` to `app_config.py`
- Verify you updated the import in `watchdog_app.py`
