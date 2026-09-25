#!/usr/bin/env python3
"""Rebuild stored S-UI links using the deployed API configuration."""

import argparse
import datetime as dt
import importlib.util
import json
import os
import sqlite3
from pathlib import Path


def load_environment(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key:
            raise ValueError(f"invalid environment line for {key or 'unknown key'}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value


def load_api(path: Path):
    spec = importlib.util.spec_from_file_location("sui_management_api", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load API module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def backup_database(source: sqlite3.Connection, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = directory / f"s-ui-before-link-rebuild-{timestamp}.db"
    with sqlite3.connect(target) as backup:
        source.backup(backup)
    os.chmod(target, 0o600)
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument(
        "--backup-directory",
        type=Path,
        default=Path("/var/backups/sui-api-links"),
    )
    args = parser.parse_args()

    if args.environment_file:
        load_environment(args.environment_file)
    api = load_api(args.api)
    database = sqlite3.connect(args.database, timeout=10)
    database.execute("PRAGMA busy_timeout=10000")
    updates = []
    for client_id, name, config_blob, links_blob in database.execute(
        "SELECT id, name, config, links FROM clients ORDER BY id"
    ):
        config = json.loads(config_blob or b"{}")
        links = api.build_subscription_links(name, config)
        encoded = json.dumps(links).encode()
        current = (
            links_blob.encode()
            if isinstance(links_blob, str)
            else bytes(links_blob or b"")
        )
        if current != encoded:
            updates.append((sqlite3.Binary(encoded), client_id))

    if not updates:
        print("All subscription links are already current")
        database.close()
        return

    backup = backup_database(database, args.backup_directory)
    database.execute("BEGIN IMMEDIATE")
    database.executemany("UPDATE clients SET links=? WHERE id=?", updates)
    database.commit()
    database.close()
    print(f"Updated {len(updates)} clients; backup: {backup}")


if __name__ == "__main__":
    main()
