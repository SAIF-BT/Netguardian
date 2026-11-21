import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import database
from backend.app.main import app
from hub.main import HubEngine


class NetGuardianApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "netguardian.db"
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """CREATE TABLE devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_name TEXT NOT NULL,
                    device_type TEXT NOT NULL,
                    device_identifier TEXT NOT NULL UNIQUE,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                """INSERT INTO devices
                   (device_name, device_type, device_identifier, is_active)
                   VALUES ('Existing laptop', 'Laptop', 'existing-001', 1)"""
            )
            connection.execute(
                """CREATE TABLE website_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('allow', 'block')),
                    category TEXT NOT NULL,
                    mode TEXT,
                    device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    UNIQUE(domain, action, category, mode, device_id)
                )"""
            )
            connection.execute(
                """INSERT INTO website_rules
                   (domain, action, category, created_at)
                   VALUES ('legacy.example', 'block', 'other', '2025-01-01T00:00:00Z')"""
            )
            connection.execute(
                """CREATE TABLE app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                )"""
            )
            connection.execute(
                "INSERT INTO app_settings (setting_key, setting_value) VALUES ('active_mode', 'study')"
            )
            connection.commit()
        finally:
            connection.close()

        self.database_patch = patch.object(database, "DATABASE_PATH", self.database_path)
        self.database_patch.start()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.headers = {}

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.database_patch.stop()
        self.temporary_directory.cleanup()

    def sign_in(self):
        registration = self.client.post(
            "/api/auth/register",
            json={"email": "parent@example.com", "password": "secure-pass-123"},
        )
        self.assertEqual(registration.status_code, 201, registration.text)
        login = self.client.post(
            "/api/auth/login",
            json={"email": "parent@example.com", "password": "secure-pass-123"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    def test_frontend_and_health_are_served(self):
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("NetGuardian", page.text)
        self.assertEqual(self.client.get("/js/app.js").status_code, 200)

    def test_protected_routes_require_login(self):
        response = self.client.get("/api/devices/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get("/api/rules").status_code, 401)

    def test_existing_database_and_device_crud_are_preserved(self):
        self.sign_in()
        devices = self.client.get("/api/devices/", headers=self.headers).json()["devices"]
        self.assertEqual(devices[0]["device_identifier"], "existing-001")

        response = self.client.post(
            "/api/devices/",
            headers=self.headers,
            json={"device_name": "Study tablet", "device_type": "Tablet"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        device_id = response.json()["device_id"]
        device = self.client.get(
            f"/api/devices/{device_id}", headers=self.headers
        ).json()["device"]
        self.assertEqual(device["device_type"], "Tablet")
        self.assertTrue(device["device_identifier"].startswith("device-"))

    def test_rules_schedules_and_temporary_access_flow(self):
        self.sign_in()
        device_response = self.client.post(
            "/api/devices/",
            headers=self.headers,
            json={"device_name": "Study tablet", "device_type": "Tablet"},
        )
        device_id = device_response.json()["device_id"]

        rule = self.client.post(
            "/api/rules",
            headers=self.headers,
            json={
                "domain": "HTTPS://example.com/path" ,
                "action": "block",
                "category": "gaming",
                "mode": "study",
                "device_id": device_id,
            },
        )
        self.assertEqual(rule.status_code, 422)
        rule = self.client.post(
            "/api/rules",
            headers=self.headers,
            json={
                "domain": "example.com",
                "action": "block",
                "category": "gaming",
                "mode": "study",
                "device_id": device_id,
            },
        )
        self.assertEqual(rule.status_code, 201, rule.text)
        self.assertEqual(
            self.client.get("/api/rules", headers=self.headers).json()["rules"][0]["domain"],
            "example.com",
        )

        schedule = self.client.post(
            "/api/schedules",
            headers=self.headers,
            json={
                "device_id": device_id,
                "mode": "study",
                "start_time": "14:00",
                "end_time": "17:00",
                "days": ["Mon", "Tue"],
            },
        )
        self.assertEqual(schedule.status_code, 201, schedule.text)
        hub_schedules = self.client.get(
            "/api/hub/config", headers={"X-API-Key": "dev-hub-key-123"}
        ).json()["schedules"]
        self.assertEqual(hub_schedules[0]["days"], ["Mon", "Tue"])
        self.assertEqual(
            self.client.get("/api/schedules", headers=self.headers).json()["schedules"][0]["days"],
            ["Mon", "Tue"],
        )

        access_request = self.client.post(
            "/api/access-requests",
            headers=self.headers,
            json={"device_id": device_id, "domain": "lecture.example", "reason": "Classwork"},
        )
        self.assertEqual(access_request.status_code, 201, access_request.text)
        request_id = access_request.json()["request_id"]
        decision = self.client.post(
            f"/api/access-requests/{request_id}/decision",
            headers=self.headers,
            json={"decision": "temporary", "duration_minutes": 30},
        )
        self.assertEqual(decision.status_code, 200, decision.text)
        permission = self.client.get("/api/permissions", headers=self.headers).json()["permissions"][0]
        self.assertTrue(permission["active"])
        self.assertEqual(permission["domain"], "lecture.example")

    def test_registered_device_can_submit_access_request_for_parent_approval(self):
        self.sign_in()
        client_ip = "192.168.100.55"
        device = self.client.post(
            "/api/devices/",
            headers=self.headers,
            json={
                "device_name": "Child laptop",
                "device_type": "Laptop",
                "device_identifier": client_ip,
            },
        )
        self.assertEqual(device.status_code, 201, device.text)

        with TestClient(app, client=(client_ip, 43100)) as child_client:
            page = child_client.get("/request.html")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Request a website", page.text)
            response = child_client.post(
                "/api/child/access-requests",
                json={"domain": "lesson.example", "reason": "School assignment"},
            )
        self.assertEqual(response.status_code, 201, response.text)

        requests = self.client.get(
            "/api/access-requests", headers=self.headers
        ).json()["requests"]
        self.assertEqual(requests[0]["domain"], "lesson.example")
        self.assertEqual(requests[0]["device_name"], "Child laptop")

        decision = self.client.post(
            f"/api/access-requests/{response.json()['request_id']}/decision",
            headers=self.headers,
            json={"decision": "temporary", "duration_minutes": 30},
        )
        self.assertEqual(decision.status_code, 200, decision.text)

        hub_config = self.client.get(
            "/api/hub/config", headers={"X-API-Key": "dev-hub-key-123"}
        ).json()
        self.assertEqual(hub_config["temporary_permissions"][0]["domain"], "lesson.example")

    def test_unregistered_device_cannot_submit_access_request(self):
        with TestClient(app, client=("192.168.100.99", 43100)) as child_client:
            response = child_client.post(
                "/api/child/access-requests",
                json={"domain": "lesson.example", "reason": "School assignment"},
            )
        self.assertEqual(response.status_code, 403)

    def test_repeated_blocked_events_create_threshold_alert(self):
        self.sign_in()
        self.assertEqual(
            self.client.put(
                "/api/settings",
                headers=self.headers,
                json={"blocked_attempt_threshold": 2},
            ).status_code,
            200,
        )
        for _ in range(2):
            response = self.client.post(
                "/api/simulator/activity",
                headers=self.headers,
                json={"domain": "game.example", "category": "gaming", "decision": "blocked"},
            )
            self.assertEqual(response.status_code, 201, response.text)
        events = self.client.get("/api/activity", headers=self.headers).json()["events"]
        self.assertIn("repeated_attempt", [event["event_type"] for event in events])

    def test_live_hub_dns_logs_are_included_in_activity_totals(self):
        self.sign_in()
        device_response = self.client.post(
            "/api/devices/",
            headers=self.headers,
            json={"device_name": "Hub client", "device_type": "Laptop"},
        )
        device_id = device_response.json()["device_id"]
        response = self.client.post(
            "/api/hub/logs",
            headers={"X-API-Key": "dev-hub-key-123"},
            json={
                "logs": [
                    {"device_id": device_id, "domain": "allowed.example", "decision": "allow"},
                    {"device_id": device_id, "domain": "blocked.example", "decision": "block"},
                ]
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        activity = self.client.get("/api/activity", headers=self.headers).json()
        self.assertEqual(activity["totals"], {"allowed": 1, "blocked": 1})

    def test_hub_status_expires_when_heartbeat_stops(self):
        self.sign_in()
        with database.get_connection() as connection:
            connection.execute(
                """UPDATE service_status SET is_online = 1, status_message = 'Connected',
                   updated_at = '2000-01-01T00:00:00+00:00' WHERE service = 'hub'"""
            )

        status = self.client.get("/api/status", headers=self.headers).json()
        self.assertFalse(status["hub"]["online"])
        self.assertEqual(status["hub"]["message"], "Heartbeat expired or not connected")

    def test_parent_accounts_have_separate_data_and_legacy_data_is_preserved(self):
        self.sign_in()
        first_devices = self.client.get("/api/devices/", headers=self.headers).json()["devices"]
        self.assertEqual(first_devices[0]["device_identifier"], "existing-001")
        self.assertEqual(
            self.client.get("/api/rules", headers=self.headers).json()["rules"][0]["domain"],
            "legacy.example",
        )
        self.assertEqual(
            self.client.get("/api/modes", headers=self.headers).json()["active_mode"],
            "study",
        )

        first_device_id = first_devices[0]["id"]
        first_rule = self.client.post(
            "/api/rules",
            headers=self.headers,
            json={"domain": "private.example", "action": "block"},
        )
        self.assertEqual(first_rule.status_code, 201, first_rule.text)
        first_only_rule = self.client.post(
            "/api/rules",
            headers=self.headers,
            json={"domain": "first-only.example", "action": "block"},
        )
        self.assertEqual(first_only_rule.status_code, 201, first_only_rule.text)
        first_schedule = self.client.post(
            "/api/schedules",
            headers=self.headers,
            json={
                "device_id": first_device_id,
                "mode": "study",
                "start_time": "14:00",
                "end_time": "17:00",
                "days": ["Mon"],
            },
        )
        self.assertEqual(first_schedule.status_code, 201, first_schedule.text)
        first_request = self.client.post(
            "/api/access-requests",
            headers=self.headers,
            json={"device_id": first_device_id, "domain": "request.example"},
        )
        self.assertEqual(first_request.status_code, 201, first_request.text)
        self.client.post(
            "/api/simulator/activity",
            headers=self.headers,
            json={"domain": "private.example", "decision": "blocked"},
        )

        registration = self.client.post(
            "/api/auth/register",
            json={"email": "another@example.com", "password": "secure-pass-123"},
        )
        self.assertEqual(registration.status_code, 201, registration.text)
        login = self.client.post(
            "/api/auth/login",
            json={"email": "another@example.com", "password": "secure-pass-123"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        second_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        self.assertEqual(self.client.get("/api/devices/", headers=second_headers).json()["devices"], [])
        self.assertEqual(self.client.get("/api/rules", headers=second_headers).json()["rules"], [])
        self.assertEqual(self.client.get("/api/schedules", headers=second_headers).json()["schedules"], [])
        self.assertEqual(
            self.client.get("/api/access-requests", headers=second_headers).json()["requests"],
            [],
        )
        self.assertEqual(self.client.get("/api/activity", headers=second_headers).json()["events"], [])
        self.assertEqual(
            self.client.get("/api/settings", headers=second_headers).json()["blocked_attempt_threshold"],
            5,
        )
        self.assertEqual(
            self.client.get("/api/modes", headers=second_headers).json()["active_mode"],
            "normal",
        )

        same_rule = self.client.post(
            "/api/rules",
            headers=second_headers,
            json={"domain": "private.example", "action": "block"},
        )
        self.assertEqual(same_rule.status_code, 201, same_rule.text)
        second_device = self.client.post(
            "/api/devices/",
            headers=second_headers,
            json={
                "device_name": "Second account laptop",
                "device_type": "Laptop",
                "device_identifier": "second-device",
            },
        )
        self.assertEqual(second_device.status_code, 201, second_device.text)

        hub_config = self.client.get(
            "/api/hub/config", headers={"X-API-Key": "dev-hub-key-123"}
        ).json()
        hub_engine = HubEngine()
        hub_engine.devices = {
            device["device_identifier"]: device for device in hub_config["devices"]
        }
        hub_engine.rules = hub_config["rules"]
        self.assertEqual(
            hub_engine.evaluate_query("existing-001", "first-only.example")[0],
            "block",
        )
        self.assertEqual(
            hub_engine.evaluate_query("second-device", "first-only.example")[0],
            "allow",
        )
        self.assertEqual(
            self.client.get(
                f"/api/devices/{first_device_id}", headers=second_headers
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete(
                f"/api/rules/{first_rule.json()['rule_id']}", headers=second_headers
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/api/schedules",
                headers=second_headers,
                json={
                    "device_id": first_device_id,
                    "mode": "study",
                    "start_time": "18:00",
                    "end_time": "19:00",
                    "days": ["Mon"],
                },
            ).status_code,
            404,
        )

        duplicate_email = self.client.post(
            "/api/auth/register",
            json={"email": "another@example.com", "password": "different-pass-123"},
        )
        self.assertEqual(duplicate_email.status_code, 409)

    def test_hardware_endpoints(self):
        # Missing header should fail
        response = self.client.post("/api/hardware/heartbeat", json={"esp32_id": "esp32-01"})
        self.assertEqual(response.status_code, 422)

        # Invalid header should fail
        response = self.client.post(
            "/api/hardware/heartbeat",
            headers={"X-API-Key": "wrong-key"},
            json={"esp32_id": "esp32-01"}
        )
        self.assertEqual(response.status_code, 401)

        # Valid heartbeat
        response = self.client.post(
            "/api/hardware/heartbeat",
            headers={"X-API-Key": "dev-secret-key-123"},
            json={"esp32_id": "esp32-01"}
        )
        self.assertEqual(response.status_code, 200)

        # Valid tamper
        response = self.client.post(
            "/api/hardware/tamper",
            headers={"X-API-Key": "dev-secret-key-123"},
            json={"esp32_id": "esp32-01", "message": "Lid opened"}
        )
        self.assertEqual(response.status_code, 200)

        # Valid tamper cleared
        response = self.client.post(
            "/api/hardware/tamper-cleared",
            headers={"X-API-Key": "dev-secret-key-123"}
        )
        self.assertEqual(response.status_code, 200)

        # Verify status endpoint returns updated hardware info
        self.sign_in()
        status_res = self.client.get("/api/status", headers=self.headers).json()
        self.assertTrue(status_res["esp32"]["online"])
        self.assertEqual(status_res["esp32"]["message"], "Enclosure secure")

    def test_esp32_events_are_private_to_the_primary_parent_account(self):
        self.sign_in()
        headers = {"X-API-Key": "dev-secret-key-123"}
        self.assertEqual(
            self.client.post(
                "/api/hardware/tamper",
                headers=headers,
                json={"esp32_id": "esp32-01", "message": "Enclosure opened"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/hardware/tamper-cleared", headers=headers
            ).status_code,
            200,
        )
        first_events = self.client.get(
            "/api/activity", headers=self.headers
        ).json()["events"]
        self.assertIn("tamper", [event["event_type"] for event in first_events])

        self.client.post(
            "/api/auth/register",
            json={"email": "second-parent@example.com", "password": "secure-pass-123"},
        )
        login = self.client.post(
            "/api/auth/login",
            json={"email": "second-parent@example.com", "password": "secure-pass-123"},
        )
        second_headers = {
            "Authorization": f"Bearer {login.json()['access_token']}"
        }
        second_events = self.client.get(
            "/api/activity", headers=second_headers
        ).json()["events"]
        self.assertNotIn("tamper", [event["event_type"] for event in second_events])


if __name__ == "__main__":
    unittest.main()
