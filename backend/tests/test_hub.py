import unittest
import os
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.database import initialize_database
from hub.main import HubEngine

class NetGuardianHubTests(unittest.TestCase):
    def setUp(self):
        os.environ["NETGUARDIAN_DATABASE_PATH"] = ":memory:"
        initialize_database()
        self.client = TestClient(app)

    def test_hub_heartbeat_and_config(self):
        headers = {"X-API-Key": "dev-hub-key-123"}
        response = self.client.post("/api/hub/heartbeat", json={"hub_id": "test-hub"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

        response = self.client.get("/api/hub/config", headers=headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("devices", data)
        self.assertIn("rules", data)
        self.assertIn("active_mode", data)

    def test_hub_rule_engine_priorities(self):
        engine = HubEngine()
        engine.devices = {"192.168.100.50": {"id": 1, "device_identifier": "192.168.100.50"}}
        engine.active_mode = "study" # Study allows only Education

        # Testing category restriction in Study mode (Gaming is blocked)
        action, cat, reason = engine.evaluate_query("192.168.100.50", "steampowered.com")
        self.assertEqual(action, "block")
        self.assertEqual(cat, "Gaming")

        # Testing allowed category in Study mode (Education is allowed)
        action, cat, reason = engine.evaluate_query("192.168.100.50", "khanacademy.org")
        self.assertEqual(action, "allow")
        self.assertEqual(cat, "Education")

        # Testing temporary permission override
        future_iso = "2099-01-01T00:00:00Z"
        engine.temporary_permissions = [{"domain": "steampowered.com", "device_id": 1, "expires_at": future_iso}]
        action, cat, reason = engine.evaluate_query("192.168.100.50", "steampowered.com")
        self.assertEqual(action, "allow")
        self.assertEqual(reason, "Temporary Permission")

    def test_hub_mode_scoped_rules_and_paused_devices(self):
        engine = HubEngine()
        engine.devices = {"192.168.100.50": {"id": 1, "is_active": 1}}
        engine.rules = [
            {"domain": "steampowered.com", "action": "block", "mode": "study", "device_id": None}
        ]

        engine.active_mode = "normal"
        action, _, _ = engine.evaluate_query("192.168.100.50", "steampowered.com")
        self.assertEqual(action, "allow")

        engine.active_mode = "study"
        action, _, _ = engine.evaluate_query("192.168.100.50", "steampowered.com")
        self.assertEqual(action, "block")

        engine.devices["192.168.100.50"]["is_active"] = 0
        action, _, reason = engine.evaluate_query("192.168.100.50", "steampowered.com")
        self.assertEqual(action, "allow")
        self.assertEqual(reason, "Device is paused")

    def test_hub_ignores_pre_account_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "hub-cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "devices": [
                            {"id": 1, "device_identifier": "192.168.100.50"}
                        ],
                        "rules": [{"domain": "example.com", "action": "block"}],
                        "schedules": [],
                        "temporary_permissions": [],
                        "active_mode": "normal",
                    }
                )
            )
            engine = HubEngine()
            with patch("hub.main.CACHE_FILE", cache_path):
                engine.offline_fallback()

            self.assertEqual(engine.devices, {})
            self.assertEqual(engine.rules, [])

    def test_schedules_switch_modes_and_handle_overnight_windows(self):
        engine = HubEngine()
        engine.devices = {
            "192.168.100.50": {
                "id": 1,
                "parent_id": 7,
                "is_active": 1,
                "active_mode": "normal",
            }
        }
        engine.schedules = [
            {
                "id": 1,
                "parent_id": 7,
                "device_id": None,
                "mode": "study",
                "start_time": "09:00",
                "end_time": "17:00",
                "days": ["Mon"],
                "enabled": True,
            },
            {
                "id": 2,
                "parent_id": 7,
                "device_id": 1,
                "mode": "bedtime",
                "start_time": "10:00",
                "end_time": "12:00",
                "days": ["Mon"],
                "enabled": True,
            },
            {
                "id": 3,
                "parent_id": 7,
                "device_id": 1,
                "mode": "bedtime",
                "start_time": "22:00",
                "end_time": "02:00",
                "days": ["Mon"],
                "enabled": True,
            },
            {
                "id": 4,
                "parent_id": 8,
                "device_id": None,
                "mode": "bedtime",
                "start_time": "09:00",
                "end_time": "17:00",
                "days": ["Mon"],
                "enabled": True,
            },
        ]

        monday_bedtime = datetime(2026, 11, 23, 10, 30, tzinfo=timezone.utc)
        action, _, _ = engine.evaluate_query(
            "192.168.100.50", "khanacademy.org", monday_bedtime
        )
        self.assertEqual(action, "block")

        monday_study = datetime(2026, 11, 23, 13, 0, tzinfo=timezone.utc)
        action, _, _ = engine.evaluate_query(
            "192.168.100.50", "khanacademy.org", monday_study
        )
        self.assertEqual(action, "allow")

        tuesday_overnight = datetime(2026, 11, 24, 1, 0, tzinfo=timezone.utc)
        action, _, _ = engine.evaluate_query(
            "192.168.100.50", "khanacademy.org", tuesday_overnight
        )
        self.assertEqual(action, "block")

if __name__ == "__main__":
    unittest.main()
