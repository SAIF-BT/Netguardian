# NetGuardian

A minimal parent-control software prototype with a FastAPI/SQLite backend and a responsive static web interface.

## Project layout

```text
backend/
  app/
    routers/       API routes
    database.py    SQLite schema and connection handling
frontend/
  css/
  js/
  *.html
netguardian.db     Local development database (created on first run)
```

The backend serves the frontend on the same origin. The database path is anchored to the repository, so starting the server from another working directory does not create a second database. Set `NETGUARDIAN_DATABASE_PATH` to use a different local database file.

## Run locally (Windows PowerShell)

From the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://127.0.0.1:8000`. Create a parent account from the sign-in page; additional unique email accounts can also register and share the same installation's devices, rules, schedules, and activity. API documentation is at `http://127.0.0.1:8000/docs`; health check: `http://127.0.0.1:8000/api/health`.

## Prototype features

- Parent account sign-up/sign-in with salted password hashing and expiring bearer sessions.
- Device registration and management, domain/category allow/block rules, modes, and schedules.
- Demo access requests and time-limited permission records.
- Activity totals, category counts, a configurable repeated-block threshold, and Hub/ESP32 status records.
- Demo controls for activity, connectivity, and enclosure tamper events.

All protected API routes use the bearer session issued by `/api/auth/login`. The web UI stores that session in browser local storage and sends it to the API.

## Hardware boundary

This repository does **not** implement DNS filtering, a NetGuardian Hub client, or ESP32 firmware. Rule and schedule records are configuration only; simulated activity/status/tamper endpoints do not contact hardware or control network traffic. The proposal leaves the Hub model and communication protocol open, so those integrations need a selected target and protocol before they can be built or verified. The prototype is not production security infrastructure; use it only in a trusted local development environment.

The initial ESP32 DevKit V1 wiring reference (without OLED; buzzer left disconnected pending its rating) is [docs/esp32-wiring-reference.svg](docs/esp32-wiring-reference.svg).

## Verify

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s backend\tests -v
```
