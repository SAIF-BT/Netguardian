import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import database
from backend.app.main import app


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

    def test_multiple_parent_accounts_can_sign_in_to_shared_installation(self):
        self.sign_in()
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
        devices = self.client.get("/api/devices/", headers=second_headers).json()["devices"]
        self.assertEqual(devices[0]["device_identifier"], "existing-001")

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


if __name__ == "__main__":
    unittest.main()
