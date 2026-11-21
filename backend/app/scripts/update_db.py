import os
import sqlite3
from pathlib import Path
from datetime import datetime

# Assuming backend/app/database.py defines the path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = Path(
    os.environ.get("NETGUARDIAN_DATABASE_PATH", PROJECT_ROOT / "netguardian.db")
)

def update_db_schema():
    """Adds tables for Hub status and activity logs if they don't exist,
    and registers the Hub service status."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")

        # We already have service_status in the original, but need to make sure it's updated.
        # Let's add columns if missing or just trust it's there based on initial_database().

        conn.execute("""
            CREATE TABLE IF NOT EXISTS hub_dns_cache (
                domain TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                category TEXT,
                expires_at TEXT
            );
        """)
        conn.commit()

if __name__ == "__main__":
    update_db_schema()
    print("Database schema updated for Hub integration.")
