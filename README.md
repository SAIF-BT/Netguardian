# NetGuardian

A university parent-control system prototype with a FastAPI/SQLite backend, responsive static web interface, ESP32 hardware tamper integration, and the NetGuardian Hub DNS filtering service.

Parent accounts have separate devices, rules, schedules, activity, and settings. Existing unowned records and tamper events from the single shared ESP32 are assigned to the earliest-created parent account; the Hub combines device configurations but applies each account's rules only to that account's registered devices.

## Project layout

```text
backend/
  app/
    routers/       API routes (auth, configuration, devices, monitoring, hardware, hub)
    database.py    SQLite schema and connection handling
hub/
  main.py          NetGuardian Hub DNS filter, rule engine, sync, and offline cache
frontend/
  css/
  js/
  *.html
netguardian.db     Local development database
```

## Run locally (Windows PowerShell)

From the repository root:

1. **Start Backend**:
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt -r requirements-dev.txt
   python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

2. **Start NetGuardian Hub (DNS Filter)**:
   Open an Administrator PowerShell window (DNS port 53 may require elevated privileges):
   ```powershell
   .\.venv\Scripts\Activate.ps1
   $env:NETGUARDIAN_BACKEND_URL="http://127.0.0.1:8000"
   python -m hub.main
   ```
   The Hub listens on standard DNS port 53. If it runs on another computer, set
   `NETGUARDIAN_BACKEND_URL` to the backend computer's LAN address instead.

3. **Connect the frontend and client devices**:
   Open the frontend at `http://<backend-host>:8000` so it uses the backend API;
   do not open the HTML files directly. Give the Hub computer a fixed LAN IP,
   configure the router/client DNS server to that IP, and register each device
   using its reserved LAN IP. A generated device identifier cannot match DNS
   client traffic.
   The child can submit an access request at `http://<backend-host>:8000/request.html`
   from its registered device; the backend associates requests using that device's
   source IP. The Hub applies schedules using its local clock, with device-specific
   schedules taking priority over all-device schedules.

## Windows Firewall Rules (Administrator PowerShell)
```powershell
New-NetFirewallRule -DisplayName "NetGuardian Hub DNS" -Direction Inbound -Protocol UDP -LocalPort 53 -Action Allow
New-NetFirewallRule -DisplayName "NetGuardian Backend" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

## Verify Tests
```powershell
python -m unittest discover -s backend\tests -v
```
