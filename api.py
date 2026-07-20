#!/usr/bin/env python3
"""
S-UI Management API
Provides REST endpoints for VPN user management via s-ui panel database.
"""

import json, secrets, sqlite3, uuid, time
from flask import Flask, request, jsonify

app = Flask(__name__)
DB = "/usr/local/s-ui/db/s-ui.db"


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
    """Get client info by name (Discord user ID)."""
    db = sqlite3.connect(DB)
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
    """Get subscription link for a client."""
    db = sqlite3.connect(DB)
    cur = db.cursor()
    cur.execute("SELECT links FROM clients WHERE name=?", (name,))
    r = cur.fetchone()
    db.close()
    if not r or not r[0]:
        return jsonify({"error": "not found"}), 404
    links = json.loads(r[0])
    return jsonify({"uri": links[0]["uri"]})


@app.route("/api/clients")
def api_clients():
    """List all enabled client names."""
    db = sqlite3.connect(DB)
    cur = db.cursor()
    cur.execute("SELECT name FROM clients WHERE enable=1 AND name GLOB '[0-9]*'")
    names = [r[0] for r in cur.fetchall()]
    db.close()
    return jsonify(names)


@app.route("/api/stats")
def api_stats():
    """Get online user count and total traffic volume."""
    db = sqlite3.connect(DB)
    cur = db.cursor()
    cutoff = int(time.time()) - 300
    cur.execute(
        "SELECT COUNT(*) FROM clients WHERE enable=1 AND online_at > ?", (cutoff,)
    )
    online = cur.fetchone()[0]
    cur.execute("SELECT SUM(down), SUM(up) FROM clients WHERE enable=1")
    r = cur.fetchone()
    total_bytes = (r[0] or 0) + (r[1] or 0)
    db.close()
    return jsonify({"online": online, "total_bytes": total_bytes})


@app.route("/api/create", methods=["POST"])
def api_create():
    """Create a new VPN client. Auto-restarts s-ui after creation."""
    data = request.json
    name = data.get("name", "")
    if not name:
        return jsonify({"error": "name required"}), 400

    db = sqlite3.connect(DB)
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

    uri = (
        f"hysteria2://{pw}@131.143.215.72:443"
        f"?security=tls&insecure=1&sni=bing.com"
        f"&alpn=h3,h2,http/1.1&fastopen=0"
        f"#sw.cc%20network%20%7C%20hysteria2"
    )
    lnk = json.dumps(
        [{"remark": "sw.cc network | hysteria2", "type": "local", "uri": uri}]
    )

    cur.execute(
        "INSERT INTO clients (enable, name, config, inbounds, links, volume, expiry, down, up) "
        "VALUES (1, ?, ?, ?, ?, 0, 0, 0, 0)",
        (
            name,
            sqlite3.Binary(cfg.encode()),
            sqlite3.Binary(json.dumps([6]).encode()),
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
    app.run(host="0.0.0.0", port=8891)
