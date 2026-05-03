# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

IntelliScale is a Django 5.2 web application for industrial weighing operations. It reads weights from physical scales via serial (COM) ports, records weighing data, manages delivery notes, and syncs with an Odoo ERP system. Designed to run on Linux devices connected to scales (typically Raspberry Pi or similar).

## Common Commands

```bash
# Development server (starts Django + Celery worker + Celery beat)
./start_app.sh

# Run Django dev server only
python manage.py runserver 0.0.0.0:8000

# Run migrations
python manage.py migrate

# Start satellite weight polling service (reads scales via serial)
python manage.py run_satellite
python manage.py run_satellite --interval 0.5

# Celery worker and beat (requires Redis on localhost:6379)
celery -A core worker --loglevel=info
celery -A core beat --loglevel=info

# Run tests
python manage.py test
python manage.py test scale
python manage.py test users
```

## Architecture

### Django Apps

- **`core/`** - Django project config (settings, URLs, WSGI/ASGI, Celery app). The Celery app is configured in `core/celery.py`.
- **`scale/`** - Main app. All weighing, delivery note, printing, and scale management logic.
- **`users/`** - Custom user model (`CustomUser`) with role-based access: `admin`, `manager`, `operator`. Each role has a different dashboard.

### Key Data Flow

1. **Scale Reading**: Physical scales connect via serial ports. The satellite service (`scale/satellite_service.py`) polls active scales in a loop, writes weights to a JSON cache file (`.satellite_cache.json`). Web views read from this cache to avoid SQLite contention with Gunicorn workers.
2. **Weighing Station** (`scale/views/weighing_station.py`): Operator scans barcodes, gets weight from scale, saves `WeighingRecord` linked to a `DeliveryNote` and `WeighingProcess`.
3. **Odoo Sync**: Celery beat task `sync_odoo_delivery_notes` polls Odoo API every 60s to fetch delivery notes. `check_completed_delivery_notes` is defined but currently disabled. Delivery notes store full Odoo response in a `JSONField` (`odoo_data`).
4. **Printing Station** (`scale/views/printing.py`): Separate workflow for printing labels with weight/moisture data.

### Models (`scale/models/`)

- `Scale` - Physical scale config (COM port, baud rate, serial params, Mettler Toledo flag)
- `ScaleIdHistory` - Tracks historical scale ID assignments
- `WeighingProcess` - Configurable weighing workflow (fields, validation rules, process type)
- `WeighingRecord` - Individual weight measurement (gross/tare/net, barcode, custom JSON data)
- `DeliveryNote` - Groups weighing records, tracks scanned barcodes, syncs with Odoo. Has QR code generation and bale tracking logic.
- `CompanySettings` - Singleton-style config (API URL, API key, satellite mode toggle)
- `Product`, `Driver`, `Truck`, `Trailer` - Reference data
- `PrintingNote`, `PrintingRecord` - Printing workflow records
- `ErpSystem` - ERP system configuration

### Views (`scale/views/`)

Views are split into modules by domain: `scale.py`, `weighing_station.py`, `weighing_process.py`, `weighing_record.py`, `delivery_note.py`, `printing.py`, `settings.py`, `product.py`, `ajax.py`, `weight_stream.py`. All re-exported through `__init__.py`.

### Background Services

- **Celery tasks** (`scale/tasks.py`): `sync_odoo_delivery_notes` (every 60s) is scheduled via Celery beat. `check_completed_delivery_notes` is defined but currently commented out in settings. Uses Redis as broker (`redis://localhost:6379/0`).
- **Satellite service** (`scale/satellite_service.py`): Standalone management command (`run_satellite`) that polls scales via serial and writes to `.satellite_cache.json`. Must be enabled in `CompanySettings.satellite`. Supports Generic, Mettler Toledo, and CAS Stream protocols.

### Signals

`scale/signals.py` auto-generates unique `scale_id` (format `SCL-XXXXXX`) on new Scale creation.

## Tech Stack

- Django 5.2.1 with SQLite (WAL mode enabled; PostgreSQL config commented out in settings)
- Celery 5.4.0 + Redis (`redis://localhost:6379/0`) for async tasks
- pyserial 3.5 for scale communication
- openpyxl 3.1.5 for Excel export, qrcode 8.0 + Pillow 11.2.1 for QR generation
- reportlab 4.4.10 for PDF exports
- Production: Gunicorn + Nginx (setup via external install script)
- Templates use Django template engine with Bootstrap (server-rendered HTML)

## Configuration

- `CompanySettings` model stores API URL, API key, ERP credentials, satellite toggle, and target endpoint
- `.app_sequence.config` contains an Eraser.io sequence diagram of the full application flow
- Config import/export available via admin dashboard (`scale/views/settings.py`)
- Time zone set to `Africa/Harare` in settings
- Startup scripts: `start_app.sh` (full stack with logging), `start_simple.sh`, `start_tmux.sh` (tmux panes), `celery.sh` (service control)
- Linux auto-boot services configured via external setup scripts (celery, nginx, gunicorn, intelliscale-satellite)
