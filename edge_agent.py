import argparse
import http.client
import json
import logging
import os
import re
import shutil
import ssl
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("sui-edge-agent")

NODE_API_URL = os.environ["SUI_NODE_API_URL"]
NODE_ID = os.environ["SUI_NODE_ID"]
NODE_TOKEN_FILE = Path(os.environ["SUI_NODE_TOKEN_FILE"])
SING_BOX_BINARY = os.environ.get("SING_BOX_BINARY", "/usr/local/bin/sing-box")
SING_BOX_CONFIG_GROUP = os.environ.get("SING_BOX_CONFIG_GROUP", "sing-box")
SING_BOX_CONFIG = Path(
    os.environ.get("SING_BOX_CONFIG", "/etc/sing-box/config.json")
)
LEGACY_USERS_FILE = Path(
    os.environ.get(
        "SING_BOX_LEGACY_USERS_FILE",
        "/etc/sing-box/legacy-hysteria2-users.json",
    )
)
HYSTERIA2_LISTEN_PORT = int(os.environ.get("HYSTERIA2_LISTEN_PORT", "443"))
HYSTERIA2_SERVER_NAME = os.environ["HYSTERIA2_SERVER_NAME"]
HYSTERIA2_CERTIFICATE = os.environ["HYSTERIA2_CERTIFICATE"]
HYSTERIA2_PRIVATE_KEY = os.environ["HYSTERIA2_PRIVATE_KEY"]
VMESS_LISTEN_PORT = int(os.environ.get("VMESS_LISTEN_PORT", "10444"))
VMESS_PATH = os.environ.get("VMESS_PATH", "/swcc")
POLL_INTERVAL = max(int(os.environ.get("SUI_NODE_POLL_INTERVAL", "10")), 5)
REQUEST_TIMEOUT = max(int(os.environ.get("SUI_NODE_REQUEST_TIMEOUT", "15")), 5)

CLIENT_NAME_PATTERN = re.compile(r"^[a-z0-9]{16}$")
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def prefer_ipv4_create_connection(
    address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None
):
    """Connect with IPv4 first while retaining IPv6 as a fallback."""
    host, port = address
    candidates = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    candidates.sort(key=lambda item: item[0] != socket.AF_INET)
    last_error = None
    for family, socktype, protocol, _, socket_address in candidates:
        connection = None
        try:
            connection = socket.socket(family, socktype, protocol)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(socket_address)
            return connection
        except OSError as error:
            last_error = error
            if connection is not None:
                connection.close()
    if last_error is not None:
        raise last_error
    raise OSError("getaddrinfo returned no addresses")


class PreferIPv4HTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        original = self._create_connection
        self._create_connection = prefer_ipv4_create_connection
        try:
            super().connect()
        finally:
            self._create_connection = original


class PreferIPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(
            PreferIPv4HTTPSConnection,
            request,
            context=self._context,
            check_hostname=self._check_hostname,
        )


def read_json(path):
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)


def fetch_desired_state():
    token = NODE_TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("node token is empty")
    request = urllib.request.Request(
        NODE_API_URL,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "User-Agent": "sui-edge-agent/1",
        },
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        PreferIPv4HTTPSHandler(context=ssl.create_default_context()),
    )
    with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
        if response.status != 200:
            raise RuntimeError("node API returned HTTP " + str(response.status))
        payload = json.load(response)
    if payload.get("node_id") != NODE_ID:
        raise ValueError("node ID mismatch")
    if not isinstance(payload.get("version"), str):
        raise ValueError("node configuration version is missing")
    if not isinstance(payload.get("clients"), list):
        raise ValueError("node client list is invalid")
    return payload


def validate_clients(clients):
    validated = []
    names = set()
    for client in clients:
        if not isinstance(client, dict):
            raise ValueError("node client entry is invalid")
        name = str(client.get("name", ""))
        password = str(client.get("hysteria2_password", ""))
        user_uuid = str(client.get("vmess_uuid", ""))
        if not CLIENT_NAME_PATTERN.fullmatch(name):
            raise ValueError("node client name is invalid")
        if name in names:
            raise ValueError("node client name is duplicated")
        if not 16 <= len(password) <= 256:
            raise ValueError("Hysteria2 password is invalid")
        if not UUID_PATTERN.fullmatch(user_uuid):
            raise ValueError("VMess UUID is invalid")
        names.add(name)
        validated.append(
            {
                "name": name,
                "hysteria2_password": password,
                "vmess_uuid": user_uuid,
            }
        )
    return validated


def load_legacy_users():
    if not LEGACY_USERS_FILE.exists():
        return []
    users = read_json(LEGACY_USERS_FILE)
    if not isinstance(users, list):
        raise ValueError("legacy Hysteria2 users file must contain a list")
    validated = []
    for user in users:
        if not isinstance(user, dict):
            raise ValueError("legacy Hysteria2 user is invalid")
        name = str(user.get("name", ""))
        password = str(user.get("password", ""))
        if not name or not password:
            raise ValueError("legacy Hysteria2 user is incomplete")
        validated.append({"name": name, "password": password})
    return validated


def build_sing_box_config(clients):
    hysteria2_users = load_legacy_users()
    hysteria2_users.extend(
        {
            "name": client["name"],
            "password": client["hysteria2_password"],
        }
        for client in clients
    )
    vmess_users = [
        {
            "name": client["name"],
            "uuid": client["vmess_uuid"],
            "alterId": 0,
        }
        for client in clients
    ]
    return {
        "log": {"level": "info", "timestamp": True},
        "inbounds": [
            {
                "type": "hysteria2",
                "tag": "hysteria2-in",
                "listen": "::",
                "listen_port": HYSTERIA2_LISTEN_PORT,
                "users": hysteria2_users,
                "tls": {
                    "enabled": True,
                    "server_name": HYSTERIA2_SERVER_NAME,
                    "certificate_path": HYSTERIA2_CERTIFICATE,
                    "key_path": HYSTERIA2_PRIVATE_KEY,
                    "alpn": ["h3"],
                },
            },
            {
                "type": "vmess",
                "tag": "vmess-ws-in",
                "listen": "127.0.0.1",
                "listen_port": VMESS_LISTEN_PORT,
                "users": vmess_users,
                "transport": {"type": "ws", "path": VMESS_PATH},
            },
        ],
        "outbounds": [{"type": "direct", "tag": "direct"}],
        "route": {"final": "direct", "auto_detect_interface": True},
    }


def serialized_config(config):
    return (json.dumps(config, indent=2, sort_keys=True) + "\n").encode()


def check_config(config):
    SING_BOX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix="config.check.", suffix=".json", dir=SING_BOX_CONFIG.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as target:
            target.write(serialized_config(config))
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary_path, 0o600)
        subprocess.run(
            [SING_BOX_BINARY, "check", "-c", str(temporary_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        temporary_path.unlink(missing_ok=True)


def install_config(config):
    content = serialized_config(config)
    if SING_BOX_CONFIG.exists() and SING_BOX_CONFIG.read_bytes() == content:
        return False

    SING_BOX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix="config.", suffix=".json", dir=SING_BOX_CONFIG.parent
    )
    temporary_path = Path(temporary_name)
    backup_path = SING_BOX_CONFIG.with_suffix(".json.previous")
    try:
        with os.fdopen(file_descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary_path, 0o640)
        shutil.chown(temporary_path, user="root", group=SING_BOX_CONFIG_GROUP)
        subprocess.run(
            [SING_BOX_BINARY, "check", "-c", str(temporary_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        if SING_BOX_CONFIG.exists():
            shutil.copy2(SING_BOX_CONFIG, backup_path)
        os.replace(temporary_path, SING_BOX_CONFIG)
        subprocess.run(
            ["systemctl", "restart", "sing-box.service"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["systemctl", "is-active", "--quiet", "sing-box.service"],
            check=True,
        )
    except Exception:
        if backup_path.exists():
            shutil.copy2(backup_path, SING_BOX_CONFIG)
            subprocess.run(
                ["systemctl", "restart", "sing-box.service"],
                check=False,
                capture_output=True,
            )
        raise
    finally:
        temporary_path.unlink(missing_ok=True)
    return True


def synchronize():
    payload = fetch_desired_state()
    clients = validate_clients(payload["clients"])
    changed = install_config(build_sing_box_config(clients))
    if changed:
        logger.info(
            "applied node configuration %s with %d clients",
            payload["version"][:12],
            len(clients),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fetch and validate configuration without installing it",
    )
    args = parser.parse_args()
    if args.check:
        payload = fetch_desired_state()
        clients = validate_clients(payload["clients"])
        print(f"Fetched and validated {len(clients)} clients", flush=True)
        config = build_sing_box_config(clients)
        print("Built sing-box configuration", flush=True)
        check_config(config)
        print(f"Configuration is valid for {len(clients)} clients")
        return

    while True:
        try:
            synchronize()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError,
                urllib.error.URLError) as error:
            logger.error("node synchronization failed: %s", error)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
