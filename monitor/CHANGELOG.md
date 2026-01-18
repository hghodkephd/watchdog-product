# Changelog

## [1.0.0] - 2026-01-10

### Major Architecture Update
- Rebuilt on validated OSS foundation
- Watchdog now builds ON TOP OF ble_scanner.py instead of reimplementing it

### Critical Bug Fixes
- **Device Detection:** Added 3-level fallback (local_name → device.name → device.address)
- **Thread Safety:** Proper locking with threading.Lock throughout
- **Chart Rendering:** Fixed timezone bug (UTC-aware timestamps)
- **Chart Rendering:** Fixed sparse dots issue (validated resampling)

### New Features
- Modular architecture with clear layer separation
- Automatic inheritance of OSS bug fixes
- Validated chart rendering from OSS
- Improved error handling throughout

### Changed
- ble_core.py → ble_watchdog.py (wraps OSS scanner)
- app.py chart rendering updated to use OSS validated code
- requirements.txt simplified

### Tested
- ✅ Device detection on multiple Bluetooth stacks
- ✅ Thread safety under stress testing
- ✅ Chart rendering with 5 sensors, 200+ readings
- ✅ Watchdog auto-recovery from Bluetooth failures
- ✅ Database persistence over 24+ hours

---

## [0.x] - Previous Versions

Pre-architecture-update versions with various bugs and issues.
See git history for details.
