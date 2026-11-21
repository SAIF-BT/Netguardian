import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = Path(
    os.environ.get("NETGUARDIAN_DATABASE_PATH", PROJECT_ROOT / "netguardian.db")
)


@contextmanager
def get_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_name TEXT NOT NULL,
                device_type TEXT NOT NULL,
                device_identifier TEXT NOT NULL UNIQUE,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS parents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                parent_id INTEGER NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS website_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('allow', 'block')),
                category TEXT NOT NULL,
                mode TEXT,
                device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                UNIQUE(domain, action, category, mode, device_id)
            );

            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
                mode TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                days TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS access_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER REFERENCES devices(id) ON DELETE SET NULL,
                domain TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                decided_at TEXT
            );

            CREATE TABLE IF NOT EXISTS temporary_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL REFERENCES access_requests(id) ON DELETE CASCADE,
                device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
                domain TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                device_id INTEGER REFERENCES devices(id) ON DELETE SET NULL,
                domain TEXT,
                category TEXT,
                decision TEXT,
                message TEXT NOT NULL,
                simulated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS service_status (
                service TEXT PRIMARY KEY,
                is_online INTEGER NOT NULL DEFAULT 0,
                status_message TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_created_at
                ON activity_events(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_schedules_device
                ON schedules(device_id);
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO app_settings (setting_key, setting_value) VALUES ('active_mode', 'normal')"
        )
        now = "1970-01-01T00:00:00+00:00"
        connection.executemany(
            """INSERT OR IGNORE INTO service_status
               (service, is_online, status_message, updated_at)
               VALUES (?, 0, 'Not connected', ?)""",
            (("hub", now), ("esp32", now)),
        )
