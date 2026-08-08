#!/usr/bin/env python3
import json, os, secrets, sqlite3, uuid, time
from urllib.parse import quote
from flask import Flask, request, jsonify

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


def fmt_bytes(n: int) -> str:
    if n >= 1073741824:
        return f"{n / 1073741824:.1f}GB"
    if n >= 1048576:
        return f"{n / 1048576:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n}B"


@app.route("/api/info/<name>")
def api_info(name):
    """get client info by name"""
    db = sqlite3.connect(SUI_DB_PATH)
    cur = db.cursor()
    cur.execute(
        "SELECT id, name, volume, expiry, down, up, total_up, total_down, enable "
        "FROM clients WHERE name=?",
        (name,),
    )
    r = cur.fetchone()
    db.close()
    if not r:
        return jsonify({"error": "not found"}), 404
    return jsonify(
        {
            "id": r[0],
            "name": r[1],
            "volume": r[2],
            "expiry": r[3],
            "down": r[4],
            "up": r[5],
            "total_up": r[6],
            "total_down": r[7],
            "enabled": bool(r[8]),
            "down_str": fmt_bytes(r[4]),
            "up_str": fmt_bytes(r[5]),
        }
    )


@app.route("/api/sub/<name>")
def api_sub(name):
    """get subscription link"""
    db = sqlite3.connect(SUI_DB_PATH)
    cur = db.cursor()
    cur.execute("SELECT links, config FROM clients WHERE name=?", (name,))
    row = cur.fetchone()
    if not row:
        db.close()
        return jsonify({"error": "not found"}), 404

    links_blob, config_blob = row
    try:
        links = json.loads(links_blob or b"[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        links = []

    if isinstance(links, list) and links and isinstance(links[0], dict) and links[0].get("uri"):
        uri = links[0]["uri"]
    else:
        try:
            config = json.loads(config_blob or b"{}")
            password = config.get("hysteria2", {}).get("password")
        except (TypeError, ValueError, json.JSONDecodeError):
            password = None
        if not password:
            db.close()
            return jsonify({"error": "subscription is not ready"}), 503
        uri = build_subscription_uri(password)
        repaired = json.dumps([{
            "remark": SUI_SUBSCRIPTION_REMARK,
            "type": "local",
            "uri": uri,
        }]).encode()
        cur.execute("UPDATE clients SET links=? WHERE name=?", (sqlite3.Binary(repaired), name))
        db.commit()

    db.close()
    return jsonify({"uri": uri})


def build_subscription_uri(password):
    return (
        f"hysteria2://{password}@{SUI_SERVER_ADDRESS}:{SUI_HYSTERIA2_PORT}?"
        f"{SUI_HYSTERIA2_QUERY}#{quote(SUI_SUBSCRIPTION_REMARK)}"
    )


@app.route("/api/clients")
def api_clients():
    """list all enabled client"""
    db = sqlite3.connect(SUI_DB_PATH)
    cur = db.cursor()
    cur.execute("SELECT name FROM clients WHERE enable=1 AND name GLOB '[0-9]*'")
    names = [r[0] for r in cur.fetchall()]
    db.close()
    return jsonify(names)


@app.route("/api/stats")
def api_stats():
    """online users | total traffic volume | registered user count"""
    db = sqlite3.connect(SUI_DB_PATH)
    cur = db.cursor()
    cutoff = int(time.time()) - 300
    cur.execute(
        "SELECT COUNT(*) FROM clients WHERE enable=1 AND online_at > ?", (cutoff,)
    )
    online = cur.fetchone()[0]
    cur.execute("SELECT SUM(down), SUM(up) FROM clients WHERE enable=1")
    r = cur.fetchone()
    total_bytes = (r[0] or 0) + (r[1] or 0)
    cur.execute("SELECT COUNT(*) FROM clients")
    registrations = cur.fetchone()[0]
    db.close()
    return jsonify({
        "online": online,
        "total_bytes": total_bytes,
        "registrations": registrations,
    })


@app.route("/api/create", methods=["POST"])
def api_create():
    """create a new user(client)"""
    data = request.json
    name = data.get("name", "")
    if not name:
        return jsonify({"error": "name required"}), 400

    db = sqlite3.connect(SUI_DB_PATH)
    cur = db.cursor()
    cur.execute("SELECT id FROM clients WHERE name=?", (name,))
    if cur.fetchone():
        db.close()
        return jsonify({"error": "already exists"}), 409

    pw = secrets.token_hex(16)
    uid = str(uuid.uuid4())
    import base64 as b64

    sspw = b64.b64encode(pw.encode()).decode()
    sspw16 = b64.b64encode(pw[:16].encode()).decode()

    cfg = json.dumps(
        {
            "mixed": {"username": name, "password": pw},
            "socks": {"username": name, "password": pw},
            "http": {"username": name, "password": pw},
            "shadowsocks": {"name": name, "password": sspw},
            "shadowsocks16": {"name": name, "password": sspw16},
            "shadowtls": {"name": name, "password": sspw},
            "vmess": {"name": name, "uuid": uid, "alterId": 0},
            "vless": {"name": name, "uuid": uid, "flow": "xtls-rprx-vision"},
            "anytls": {"name": name, "password": pw},
            "trojan": {"name": name, "password": pw},
            "naive": {"username": name, "password": pw},
            "hysteria": {"name": name, "auth_str": pw},
            "tuic": {"name": name, "uuid": uid, "password": pw},
            "hysteria2": {"name": name, "password": pw},
        }
    )

    uri = build_subscription_uri(pw)
    lnk = json.dumps(
        [{"remark": SUI_SUBSCRIPTION_REMARK, "type": "local", "uri": uri}]
    )

    cur.execute(
        "INSERT INTO clients (enable, name, config, inbounds, links, volume, expiry, down, up) "
        "VALUES (1, ?, ?, ?, ?, 0, 0, 0, 0)",
        (
            name,
            sqlite3.Binary(cfg.encode()),
            sqlite3.Binary(json.dumps([SUI_HYSTERIA2_INBOUND_ID]).encode()),
            sqlite3.Binary(lnk.encode()),
        ),
    )
    db.commit()
    cid = cur.lastrowid
    db.close()

    import threading, subprocess as _sp

    threading.Thread(
        target=lambda: _sp.run(
            ["systemctl", "restart", "s-ui"], capture_output=True
        ),
        daemon=True,
    ).start()

    return jsonify({"id": cid, "name": name, "links": uri})


if __name__ == "__main__":
    app.run(host=SUI_API_HOST, port=SUI_API_PORT)
