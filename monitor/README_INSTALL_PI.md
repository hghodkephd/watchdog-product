# Watchdog – Raspberry Pi Installation Guide

This document describes how to install and run the Watchdog monitoring
system on a Raspberry Pi using systemd services.

This guide is intended for development and controlled deployments.
It assumes comfort with the command line and system administration.

---

## Supported Platform

- Raspberry Pi OS (Lite or Desktop)
- Python 3.9+
- systemd
- Bluetooth enabled
- Network access (WiFi or Ethernet)
- mDNS / Avahi (for watchdog.local discovery)

---

## Directory Layout (on Pi)

Watchdog Product

This repository contains the Watchdog environmental monitoring system, composed of two primary components:

monitor/ – Raspberry Pi–based monitoring system (BLE scanning + SQLite + Streamlit dashboard)

desktop/ – Desktop launcher app for discovering and connecting to a Watchdog device on the local network

This repository is intended for development, controlled deployments, and early productization. It assumes comfort with Python, Linux, and basic system administration.

Repository Layout

watchdog-product/
├── monitor/        # Raspberry Pi monitor + dashboard
├── desktop/        # Desktop launcher application
├── CODE-FIXES.md   # Engineering notes / known fixes
├── MERGE-TASK-LIST.md
└── README.md       # (this file)

Quick Start Overview

Choose one of the following paths depending on what you are working on:

Raspberry Pi installation: see monitor/README_INSTALL_PI.md

Desktop launcher (local dev): see desktop/README.md

This top-level README intentionally does not duplicate detailed setup instructions.

Components

Monitor (Raspberry Pi)

The monitor component runs on a Raspberry Pi and is responsible for:

BLE scanning of environmental sensors

Local data persistence (SQLite)

Running a Streamlit-based web dashboard

Exposing the dashboard on port 8501

The monitor runs as two systemd services:

watchdog-monitor.service – BLE scanning + data ingestion

watchdog-dashboard.service – Web dashboard

All paths, users, and groups are resolved dynamically at install time. No hard-coded /home/pi assumptions are required.

➡ Installation instructions: monitor/README_INSTALL_PI.md

Desktop Launcher

The desktop component is a lightweight launcher application that:

Discovers Watchdog devices on the local network (mDNS + fallback methods)

Verifies connectivity via HTTP to the dashboard

Launches the dashboard UI in a desktop webview

This component is designed to be user-facing but is still under active development.

➡ Desktop setup: desktop/README.md

Development Notes

Systemd services include sanity checks for executable paths

Services are user-level (non-root) and restart automatically

HTTP-based verification is preferred over raw socket checks to avoid false positives

Build artifacts, runtime data, and generated files are excluded via .gitignore

Status

This repository reflects a merge baseline for the Watchdog product:

Core monitor and desktop codebases merged

Path- and user-independent systemd deployment

Known failure modes addressed prior to SD card re-imaging

Further hardening, packaging, and documentation will follow after validation on a fresh Raspberry Pi SD card.

License

Private / internal use only. Not open source.
