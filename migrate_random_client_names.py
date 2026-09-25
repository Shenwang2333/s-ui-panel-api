#!/usr/bin/env python3
"""One-time migration from Discord IDs to randomized S-UI client names."""

import re
import json
from urllib.request import Request, urlopen

import receiver as api


DISCORD_ID_RE = re.compile(r"^[0-9]+$")


def bot_token() -> str:
    values = {}
    with open("/etc/discord-oauth.env", encoding="utf-8") as env_file:
        for line in env_file:
            if "=" in line and not line.startswith("#"):
                key, value = line.rstrip("\n").split("=", 1)
                values[key] = value
    return values.get("DISCORD_BOT_TOKEN", "")


def fetch_discord_tag(discord_id: str, token: str) -> str:
    request = Request(
        f"https://discord.com/api/v10/users/{discord_id}",
        headers={
            "Authorization": f"Bot {token}",
            "User-Agent": "DiscordBot (s-ui-migration, 1.0)",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read())
        return str(data.get("username") or discord_id)
    except Exception:
        return discord_id


def main() -> None:
    migrated = []
    token = bot_token()
    with api.open_api_db() as db:
        db.execute("BEGIN IMMEDIATE")
        api.ensure_identity_table(db)
        rows = db.execute(
            'SELECT name, desc FROM clients ORDER BY id'
        ).fetchall()
        for discord_id, description in rows:
            if not DISCORD_ID_RE.fullmatch(discord_id):
                continue
            tag = fetch_discord_tag(discord_id, token) if token else discord_id
            old_name, new_name = api.rotate_client_credentials(db, discord_id, tag)
            migrated.append((discord_id, old_name, new_name))
        db.commit()

    print(f"migrated={len(migrated)}")
    for discord_id, _, new_name in migrated:
        print(f"{discord_id} -> {new_name}")


if __name__ == "__main__":
    main()
