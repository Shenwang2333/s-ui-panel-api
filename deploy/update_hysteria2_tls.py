#!/usr/bin/env python3
"""Install a renewed Hysteria2 certificate into one S-UI TLS record."""

import argparse
import datetime as dt
import json
import os
import sqlite3
from pathlib import Path


def pem_lines(path: Path, marker: str) -> list[str]:
    text = path.read_text(encoding="ascii").strip()
    if marker not in text:
        raise ValueError(f"{path} is not a valid PEM file")
    return text.splitlines()


def backup_database(source: sqlite3.Connection, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"s-ui-before-hysteria2-tls-{timestamp}.db"
    with sqlite3.connect(target) as backup:
        source.backup(backup)
    os.chmod(target, 0o600)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--server-name", required=True)
    parser.add_argument("--tls-id", type=int, default=4)
    parser.add_argument(
        "--backup-directory",
        type=Path,
        default=Path("/var/backups/s-ui-cert-renewal"),
    )
    args = parser.parse_args()

    certificate = pem_lines(args.certificate, "BEGIN CERTIFICATE")
    private_key = pem_lines(args.private_key, "PRIVATE KEY")

    database = sqlite3.connect(args.database, timeout=10)
    database.execute("PRAGMA busy_timeout=10000")
    row = database.execute(
        "SELECT server, client FROM tls WHERE id=?", (args.tls_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError(f"S-UI TLS record {args.tls_id} does not exist")

    server = json.loads(row[0] or b"{}")
    client = json.loads(row[1] or b"{}")
    server.update(
        {
            "enabled": True,
            "server_name": args.server_name,
            "certificate": certificate,
            "key": private_key,
        }
    )
    client["insecure"] = False
    client.pop("certificate_public_key_sha256", None)

    new_server = json.dumps(server, separators=(",", ":")).encode()
    new_client = json.dumps(client, separators=(",", ":")).encode()
    current_server = row[0].encode() if isinstance(row[0], str) else row[0]
    current_client = row[1].encode() if isinstance(row[1], str) else row[1]
    if (
        isinstance(row[0], bytes)
        and isinstance(row[1], bytes)
        and (current_server, current_client) == (new_server, new_client)
    ):
        print(f"TLS record {args.tls_id} is already current")
        database.close()
        return

    backup = backup_database(database, args.backup_directory)
    database.execute("BEGIN IMMEDIATE")
    database.execute(
        "UPDATE tls SET server=?, client=? WHERE id=?",
        (sqlite3.Binary(new_server), sqlite3.Binary(new_client), args.tls_id),
    )
    database.commit()
    database.close()
    print(f"Updated TLS record {args.tls_id}; backup: {backup}")


if __name__ == "__main__":
    main()
