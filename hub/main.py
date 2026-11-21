import os
import sys
import json
import time
import socket
import logging
import threading
import requests
from datetime import datetime, timezone
from pathlib import Path
from dnslib import DNSRecord, RR, QTYPE, A, TXT, DNSHeader

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hub")

HUB_PORT = int(os.environ.get("HUB_DNS_PORT", 53))
UPSTREAM_DNS = os.environ.get("HUB_UPSTREAM_DNS", "8.8.8.8")
BACKEND_URL = os.environ.get("NETGUARDIAN_BACKEND_URL", "http://127.0.0.1:8000")
HUB_API_KEY = os.environ.get("NETGUARDIAN_HUB_API_KEY", "dev-hub-key-123")
CACHE_FILE = Path("hub_rules_cache.json")

# Seed Categories (at least 20 domains across 4 categories)
SEED_CATEGORIES = {
    "Education": [
        "khanacademy.org", "coursera.org", "edx.org", "duolingo.com",
        "quizlet.com", "britannica.com", "mit.edu", "stanford.edu"
    ],
    "Gaming": [
        "steampowered.com", "epicgames.com", "roblox.com", "minecraft.net",
        "twitch.tv", "ea.com", "riotgames.com"
    ],
    "Social Media": [
        "instagram.com", "tiktok.com", "snapchat.com", "facebook.com",
        "twitter.com", "reddit.com"
    ],
    "Streaming": [
        "netflix.com", "youtube.com", "disneyplus.com", "twitch.tv",
        "hulu.com", "spotify.com"
    ]
}

# Default allowed categories per mode
MODE_PERMISSIONS = {
    "study": ["Education"],
    "entertainment": ["Education", "Gaming", "Streaming", "Social Media"],
    "bedtime": [],
    "normal": ["Education", "Gaming", "Streaming", "Social Media"]
}

class HubEngine:
    def __init__(self):
        self.devices = {} # ip -> device dict
        self.rules = []
        self.schedules = []
        self.temporary_permissions = []
        self.active_mode = "normal"
        self.blocked_counts = {} # (device_id, domain) -> count
        self.alert_threshold = 5
        self.log_queue = []
        self.lock = threading.Lock()
        self.online = False

    def domain_matches(self, query_domain: str, rule_domain: str) -> bool:
        q = query_domain.lower().strip(".")
        r = rule_domain.lower().strip(".")
        return q == r or q.endswith("." + r)

    def get_domain_category(self, domain: str) -> str:
        for cat, domains in SEED_CATEGORIES.items():
            for d in domains:
                if self.domain_matches(domain, d):
                    return cat
        return "Uncategorized"

    def scheduled_mode(
        self, device: dict | None, now: datetime | None = None
    ) -> str | None:
        if not device:
            return None

        current_time = now or datetime.now().astimezone()
        weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        current_day = current_time.weekday()
        minute_of_day = current_time.hour * 60 + current_time.minute
        matching_schedules = []

        for schedule in self.schedules:
            if schedule.get("parent_id") != device.get("parent_id"):
                continue
            if schedule.get("device_id") not in (None, device.get("id")):
                continue
            if not schedule.get("enabled", True):
                continue

            try:
                days = schedule.get("days", [])
                if isinstance(days, str):
                    days = json.loads(days)
                selected_days = {weekdays.index(day) for day in days}
                start_hour, start_minute = map(
                    int, schedule["start_time"].split(":")[:2]
                )
                end_hour, end_minute = map(
                    int, schedule["end_time"].split(":")[:2]
                )
            except (IndexError, KeyError, TypeError, ValueError):
                logger.error("Skipping invalid schedule %s", schedule.get("id"))
                continue

            start = start_hour * 60 + start_minute
            end = end_hour * 60 + end_minute
            if start == end:
                logger.error("Skipping zero-length schedule %s", schedule.get("id"))
                continue
            if start < end:
                active = current_day in selected_days and start <= minute_of_day < end
            elif minute_of_day >= start:
                active = current_day in selected_days
            else:
                active = (
                    minute_of_day < end
                    and (current_day - 1) % len(weekdays) in selected_days
                )

            if active:
                is_device_specific = schedule.get("device_id") is not None
                matching_schedules.append(
                    (
                        is_device_specific,
                        schedule.get("id", 0),
                        schedule["mode"],
                    )
                )

        if not matching_schedules:
            return None
        return max(matching_schedules, key=lambda match: (match[0], match[1]))[2]

    def evaluate_query(
        self, client_ip: str, domain: str, now: datetime | None = None
    ) -> tuple[str, str, str]:
        with self.lock:
            device = self.devices.get(client_ip)
            device_id = device["id"] if device else None
            parent_id = device.get("parent_id") if device else None
            matched_category = self.get_domain_category(domain)
            scheduled_mode = self.scheduled_mode(device, now)
            current_mode = (
                scheduled_mode
                or (device.get("active_mode", self.active_mode) if device else self.active_mode)
            ).lower()

            if device and not device.get("is_active", True):
                return "allow", matched_category, "Device is paused"

            current_time = now or datetime.now().astimezone()
            now_iso = current_time.astimezone(timezone.utc).isoformat()
            for tp in self.temporary_permissions:
                if tp.get("parent_id") != parent_id:
                    continue
                if tp.get("domain") and self.domain_matches(domain, tp["domain"]):
                    if tp.get("device_id") is None or tp.get("device_id") == device_id:
                        if tp.get("expires_at", "") > now_iso:
                            return "allow", matched_category, "Temporary Permission"

            for rule in self.rules:
                if rule.get("parent_id") != parent_id:
                    continue
                if self.domain_matches(domain, rule["domain"]):
                    if rule.get("mode") and rule["mode"].lower() != current_mode:
                        continue
                    if rule.get("device_id") is None or rule.get("device_id") == device_id:
                        return rule["action"], matched_category, f"Rule: {rule['domain']} -> {rule['action']}"

            allowed_cats = MODE_PERMISSIONS.get(current_mode, ["Education", "Gaming", "Streaming", "Social Media"])

            if matched_category != "Uncategorized" and matched_category not in allowed_cats:
                return "block", matched_category, f"Mode '{current_mode}' restricts category '{matched_category}'"

            return "allow", matched_category, "Default Rule (Allowed)"

    def log_lookup(self, device_id, domain, category, decision):
        with self.lock:
            self.log_queue.append({
                "device_id": device_id,
                "domain": domain,
                "category": category,
                "decision": decision,
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            })
            if decision == "block":
                key = (device_id, domain)
                self.blocked_counts[key] = self.blocked_counts.get(key, 0) + 1
                if self.blocked_counts[key] >= self.alert_threshold:
                    self.send_alert(device_id, domain, self.blocked_counts[key])
                    self.blocked_counts[key] = 0

    def send_alert(self, device_id, domain, count):
        try:
            requests.post(
                f"{BACKEND_URL}/api/hub/alerts",
                headers={"X-API-Key": HUB_API_KEY},
                json={
                    "device_id": device_id,
                    "domain": domain,
                    "count": count,
                    "message": f"Repeated blocked attempts ({count}x) for {domain}"
                },
                timeout=3
            )
        except Exception as e:
            logger.error(f"Failed to send alert: {e}")

    def sync_loop(self):
        while True:
            try:
                resp = requests.get(
                    f"{BACKEND_URL}/api/hub/config",
                    headers={"X-API-Key": HUB_API_KEY},
                    timeout=3
                )
                if resp.status_code == 200:
                    data = resp.json()
                    with self.lock:
                        self.devices = {}
                        for d in data.get("devices", []):
                            self.devices[d.get("device_identifier")] = d
                        self.rules = data.get("rules", [])
                        self.schedules = data.get("schedules", [])
                        self.temporary_permissions = data.get("temporary_permissions", [])
                        self.active_mode = data.get("active_mode", "normal")
                        self.online = True
                        CACHE_FILE.write_text(json.dumps(data))

                    requests.post(
                        f"{BACKEND_URL}/api/hub/heartbeat",
                        headers={"X-API-Key": HUB_API_KEY},
                        json={"hub_id": "NetGuardian-Hub-1"},
                        timeout=3
                    )

                    with self.lock:
                        logs_to_send = list(self.log_queue)
                        self.log_queue = []

                    if logs_to_send:
                        requests.post(
                            f"{BACKEND_URL}/api/hub/logs",
                            headers={"X-API-Key": HUB_API_KEY},
                            json={"logs": logs_to_send},
                            timeout=3
                        )
                else:
                    self.offline_fallback()
            except Exception as e:
                logger.warning(f"Backend unreachable ({e}), operating offline from cache.")
                self.offline_fallback()

            time.sleep(10)

    def offline_fallback(self):
        with self.lock:
            self.online = False
            if CACHE_FILE.exists():
                try:
                    data = json.loads(CACHE_FILE.read_text())
                    scoped_records = (
                        ("devices", "active_mode"),
                        ("rules", "parent_id"),
                        ("schedules", "parent_id"),
                        ("temporary_permissions", "parent_id"),
                    )
                    if any(
                        not isinstance(record, dict) or field not in record
                        for records, field in scoped_records
                        for record in data.get(records, [])
                    ):
                        logger.error(
                            "Ignoring Hub cache without per-account ownership data."
                        )
                        return
                    self.devices = {d.get("device_identifier"): d for d in data.get("devices", [])}
                    self.rules = data.get("rules", [])
                    self.schedules = data.get("schedules", [])
                    self.temporary_permissions = data.get("temporary_permissions", [])
                    self.active_mode = data.get("active_mode", "normal")
                except Exception:
                    pass

engine = HubEngine()

def forward_dns(query_data: bytes) -> bytes:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3.0)
        sock.sendto(query_data, (UPSTREAM_DNS, 53))
        response, _ = sock.recvfrom(512)
        sock.close()
        return response
    except Exception:
        return None

def handle_dns_query(data: bytes, client_ip: str) -> bytes:
    try:
        query = DNSRecord.parse(data)
        qname = str(query.q.qname).strip(".")

        with engine.lock:
            # For testing convenience, also filter 127.0.0.1 / ::1 if requested or if any device is registered
            is_registered = client_ip in engine.devices or client_ip in ["127.0.0.1", "::1"]

        device = engine.devices.get(client_ip)
        device_id = device["id"] if device else None

        decision = "allow"
        category = "Uncategorized"

        if is_registered:
            decision, category, reason = engine.evaluate_query(client_ip, qname)
            engine.log_lookup(device_id, qname, category, decision)

        if decision == "block":
            reply = query.reply()
            reply.header.rcode = 3 # NXDOMAIN
            return reply.pack()
        else:
            upstream_resp = forward_dns(data)
            if upstream_resp:
                return upstream_resp
            else:
                reply = query.reply()
                reply.header.rcode = 2 # SERVFAIL
                return reply.pack()
    except Exception as e:
        logger.error(f"Error handling DNS packet: {e}")
        return None

def start_dns_server():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        server_socket.bind(("0.0.0.0", HUB_PORT))
        logger.info(f"NetGuardian Hub DNS Server listening on UDP port {HUB_PORT}")
    except Exception as e:
        logger.error(f"Failed to bind port {HUB_PORT}: {e}")
        sys.exit(1)

    while True:
        try:
            data, addr = server_socket.recvfrom(512)
            threading.Thread(target=process_client_query, args=(server_socket, data, addr)).start()
        except Exception as e:
            logger.error(f"Server socket error: {e}")

def process_client_query(sock, data, addr):
    resp = handle_dns_query(data, addr[0])
    if resp:
        try:
            sock.sendto(resp, addr)
        except Exception:
            pass

if __name__ == "__main__":
    logger.info("Starting NetGuardian Hub Service...")
    sync_thread = threading.Thread(target=engine.sync_loop, daemon=True)
    sync_thread.start()
    start_dns_server()
