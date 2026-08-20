#!/usr/bin/env python3
import base64
import json
import os
import secrets
import sqlite3
import subprocess
import threading
import time
import uuid
from urllib.parse import quote

from flask import Flask, jsonify, request


app = Flask(__name__)


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

TRAFFIC_HISTORY_HOURS = 24 * 7
ACTIVE_USER_WINDOW_SECONDS = 300


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
    socks_credentials = base64.b64encode(
        f"{socks['username']}:{socks['password']}".encode()
    ).decode()
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
                f"socks://{socks_credentials}@{SUI_SOCKS_SERVER}:"
                f"{SUI_SOCKS_PORT}#{quote(SUI_SOCKS_REMARK)}"
            ),
        },
        {
            "remark": SUI_VMESS_REMARK,
            "type": "local",
            "uri": vmess_uri,
        },
    ]


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
    """Return active users, enabled traffic, and total registrations."""
    with sqlite3.connect(SUI_DB_PATH) as db:
        cutoff = int(time.time()) - ACTIVE_USER_WINDOW_SECONDS
        online = db.execute(
            """
            SELECT COUNT(DISTINCT stats.tag)
            FROM stats
            JOIN clients ON clients.name = stats.tag
            WHERE stats.resource='user' AND stats.date_time > ?
              AND stats.traffic > 0 AND clients.enable=1
            """,
            (cutoff,),
        ).fetchone()[0]
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
        "(enable, name, config, inbounds, links, volume, expiry, down, up) "
        "VALUES (1, ?, ?, ?, ?, 0, 0, 0, 0)",
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
