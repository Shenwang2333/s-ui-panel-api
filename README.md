# S-UI Management API

REST API for managing VPN users through the [s-ui](https://github.com/alireza0/s-ui)
panel database.

The public identity used by callers is the user's Discord ID. The API maps that
ID to a random 16-character lowercase alphanumeric S-UI client name. Discord
IDs are therefore not exposed in subscription URLs, and callers do not need to
store or know the randomized client name.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/create` | Create a mapped VPN user with a random S-UI name in the `user` group |
| GET | `/api/info/<discord_id>` | Get mapped user info and traffic totals |
| GET | `/api/traffic/<discord_id>?hours=168` | Get hourly traffic for up to seven days |
| GET | `/api/sub/<discord_id>` | Get the mapped user's subscription link |
| GET | `/api/clients` | List all enabled clients |
| GET | `/api/stats` | Online users count + total traffic + registered users count |
| POST | `/api/reset/<discord_id>` | Rotate the random name and all protocol credentials |
| POST | `/api/ban/<discord_id>` | Disable a user and assign the `banned` group |
| POST | `/api/restore/<discord_id>` | Restore state after a paired Discord ban fails |
| POST | `/api/reload` | Restart S-UI asynchronously; internal use only |

`/api/reset`, `/api/ban`, and `/api/restore` require the management Bearer
token. Other management routes should remain on a trusted network or behind a
separately authenticated reverse proxy.

## Configuration

### Node settings

Node-specific values are read from environment variables. Do not put a server
address, SNI, inbound ID, or node remark directly in `api.py`.

Create an environment file (for example `/etc/sui-api.env`):

```dotenv
SUI_SERVER_ADDRESS=your-server.example.com
SUI_HYSTERIA2_PORT=your-port
SUI_HYSTERIA2_QUERY="security=tls&insecure=1&sni=your-sni.example.com&alpn=h3,h2&fastopen=0"
SUI_HYSTERIA2_INBOUND_ID=your-inbound-id
SUI_SUBSCRIPTION_REMARK="your-hysteria-node-name"
SUI_SOCKS_INBOUND_ID=your-socks-inbound-id
SUI_SOCKS_SERVER=your-socks-server.example.com
SUI_SOCKS_PORT=your-socks-port
SUI_SOCKS_REMARK="your-socks-node-name"
SUI_VMESS_INBOUND_ID=your-vmess-inbound-id
SUI_VMESS_SERVER=your-vmess-server.example.com
SUI_VMESS_PORT=443
SUI_VMESS_PATH=/your-websocket-path
SUI_VMESS_REMARK="your-vmess-node-name"
SUI_DB_PATH=/path/to/s-ui.db
SUI_API_HOST=127.0.0.1
SUI_API_PORT=your-api-port
SUI_ADMIN_TOKEN=generate-a-random-secret
SUI_PANEL_API_URL=https://your-panel.example.com:your-panel-port/your-panel-path/apiv2/onlines
SUI_PANEL_API_TOKEN=generate-an-s-ui-api-token
SUI_PANEL_CONNECT_HOST=127.0.0.1
SUI_PANEL_CA_FILE=/path/to/panel-origin-ca.pem
```

The variables are used as follows:

- `SUI_SERVER_ADDRESS`: server hostname or address placed in generated links.
- `SUI_HYSTERIA2_PORT`: Hysteria 2 listener port.
- `SUI_HYSTERIA2_QUERY`: complete query string after `?` in the generated URI.
- `SUI_HYSTERIA2_INBOUND_ID`: S-UI inbound ID inserted for newly created users.
- `SUI_SUBSCRIPTION_REMARK`: node name shown in subscription clients.
- `SUI_SOCKS_INBOUND_ID`: SOCKS inbound ID inserted for newly created users; defaults to `8`.
- `SUI_SOCKS_SERVER` / `SUI_SOCKS_PORT`: public SOCKS endpoint.
- `SUI_SOCKS_REMARK`: SOCKS node name; defaults to `SOCKS`.
- `SUI_VMESS_INBOUND_ID`: VMess inbound ID inserted for newly created users; defaults to `9`.
- `SUI_VMESS_SERVER` / `SUI_VMESS_PORT` / `SUI_VMESS_PATH`: public VMess WebSocket endpoint.
- `SUI_VMESS_REMARK`: VMess node name; defaults to `VMess`.
- `SUI_DB_PATH`: path to the S-UI SQLite database.
- `SUI_API_HOST`: address on which this API listens.
- `SUI_API_PORT`: port on which this API listens.
- `SUI_ADMIN_TOKEN`: secret Bearer token accepted by the reset, ban, and restore endpoints.
- `SUI_PANEL_API_URL`: S-UI API v2 `onlines` endpoint, including its TLS hostname.
- `SUI_PANEL_API_TOKEN`: S-UI API token sent only to the local panel endpoint.
- `SUI_PANEL_CONNECT_HOST`: local address used for the panel connection while preserving TLS SNI.
- `SUI_PANEL_CA_FILE`: CA certificate used to verify the panel's TLS certificate.

Generate the management token on the server and keep the environment file restricted:

```bash
openssl rand -hex 32
chmod 600 /etc/sui-api.env
```

Quote environment-file values that contain spaces or shell metacharacters such as `&`.

Load the file from the systemd unit before restarting the service:

```ini
[Service]
EnvironmentFile=/etc/sui-api.env
```

Then apply the change:

```bash
systemctl daemon-reload
systemctl restart sui-api-receiver
```

## Usage

```bash
# Install dependencies
pip install flask

# Run with the environment file
set -a
. /etc/sui-api.env
set +a
python3 api.py
```

### Create User

`discord_id` must contain only decimal digits. `description` is required and
should be the current Discord username/tag displayed in the S-UI description
column. For compatibility, `name` is still accepted as an alias for
`discord_id`, but new callers should use `discord_id`.

```bash
curl -X POST "http://${SUI_API_HOST}:${SUI_API_PORT}/api/create" \
  -H "Content-Type: application/json" \
  -d '{"discord_id":"1096824314973663273","description":"discord_username"}'
```

Response:
```json
{
  "id": 42,
  "discord_id": "1096824314973663273",
  "name": "k7v2m9a4q1x8c6bz",
  "links": "hysteria2://password@server:443?..."
}
```

Creation is idempotent by Discord ID. If the mapping already exists, the API
returns the existing random name without creating another client:

```json
{
  "name": "k7v2m9a4q1x8c6bz",
  "existing": true
}
```

### Get User Info

```bash
curl "http://${SUI_API_HOST}:${SUI_API_PORT}/api/info/1096824314973663273"
```

The response includes the internal random `name`, the external `discord_id`,
the S-UI description as `desc`, the current `group`, enabled state, quota and
traffic totals.

### Get Subscription Link

```bash
curl "http://${SUI_API_HOST}:${SUI_API_PORT}/api/sub/1096824314973663273"
```

Response:

```json
{
  "uri": "hysteria2://password@server:443?...",
  "name": "k7v2m9a4q1x8c6bz"
}
```

The returned `name` is the internal S-UI identity. Do not use it as a Discord
user identifier and do not expose it separately from the subscription link.

### Get Traffic History

```bash
curl "http://${SUI_API_HOST}:${SUI_API_PORT}/api/traffic/1096824314973663273?hours=168"
```

### Get Stats

```bash
curl "http://${SUI_API_HOST}:${SUI_API_PORT}/api/stats"
```

Response:
```json
{
  "online": 3,
  "total_bytes": 1353235005103,
  "registrations": 19
}
```

### Reset Subscription Identity

This operation assigns a new random 16-character S-UI name, regenerates every
protocol credential, rebuilds all subscription links, moves historical traffic
tags to the new name, and restarts S-UI after the response is sent. The old
subscription URL and all old Hysteria 2, SOCKS, VMess, and other generated
credentials become invalid.

The optional `description` updates the S-UI description, normally to the
user's current Discord username/tag. If omitted, the existing description is
preserved.

```bash
curl -X POST \
  "http://${SUI_API_HOST}:${SUI_API_PORT}/api/reset/1096824314973663273" \
  -H "Authorization: Bearer ${SUI_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"description":"discord_username"}'
```

Response:

```json
{
  "discord_id": "1096824314973663273",
  "old_name": "k7v2m9a4q1x8c6bz",
  "name": "p3n8r5w1d9f6h2jt"
}
```

### Ban User

This endpoint keeps the client and its traffic history, sets `enable=0`, assigns
the S-UI group `banned`, and restarts S-UI so sing-box drops the client.

```bash
curl -X POST \
  "http://${SUI_API_HOST}:${SUI_API_PORT}/api/ban/1096824314973663273" \
  -H "Authorization: Bearer ${SUI_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"reason":"Subscription sharing"}'
```

The reason is stored in `discord_users.ban_reason`. The response includes
`previous_enabled`, `previous_group`, and `previous_desc`. A caller that pairs
this operation with a Discord ban should pass those values to the protected
restore endpoint if the Discord operation fails:

```bash
curl -X POST \
  "http://${SUI_API_HOST}:${SUI_API_PORT}/api/restore/1096824314973663273" \
  -H "Authorization: Bearer ${SUI_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"group":"user","desc":"discord_username"}'
```

Restore clears the stored ban reason and restarts S-UI after returning the
response.

## Identity Mapping and Migration

The API maintains the following mapping table in the same S-UI SQLite
database:

```sql
CREATE TABLE discord_users (
    discord_id TEXT PRIMARY KEY,
    client_name TEXT NOT NULL UNIQUE,
    ban_reason TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT 0
);
```

- `discord_id` is the stable identifier accepted by API callers.
- `client_name` is the current random 16-character S-UI identity.
- `clients.desc` stores the user's current Discord username/tag.
- `ban_reason` stores the most recent administrative ban reason.

`api.py` creates or upgrades this table automatically. Existing installations
whose `clients.name` values are Discord IDs must run the one-time
`migrate_random_client_names.py` migration. On the production layout,
`api.py` is deployed as `/root/receiver.py`, which is the module imported by
the migration script.

Before running the migration, back up the database and the deployed API:

```bash
cp /usr/local/s-ui/db/s-ui.db \
  /usr/local/s-ui/db/s-ui.db.bak-before-random-client-names
cp /root/receiver.py /root/receiver.py.bak-before-random-client-names
```

The migration optionally reads `DISCORD_BOT_TOKEN` from
`/etc/discord-oauth.env` to replace each S-UI description with the current
Discord username. It rotates the random name and all protocol credentials,
rebuilds links, migrates matching historical `stats.tag` values, and skips
clients whose names are already non-numeric.

```bash
cd /root
python3 migrate_random_client_names.py
systemctl restart s-ui sui-api-receiver
```

After migration, verify there are no numeric S-UI client names, every mapping
has a 16-character lowercase alphanumeric `client_name`, old subscription URLs
fail, and a mapped `/api/sub/<discord_id>` request succeeds.

## Notes

- The `/api/create` endpoint automatically restarts the s-ui service after creating a user so the sing-box core picks up the new client.
- `/api/create` retries transient SQLite write locks caused by S-UI traffic updates and returns HTTP 503 if the database remains busy.
- `/api/create` is idempotent by Discord ID and stores a random 16-character lowercase alphanumeric value in `clients.name`.
- All config/inbounds/links fields are stored as BLOBs (required by s-ui's Go backend).
- `/api/sub/<discord_id>` repairs a missing or malformed `links` field from stored credentials and writes it back as a BLOB. This prevents new accounts from returning a subscription error while S-UI is reloading.
- `/api/sub/<discord_id>` also repairs stored SOCKS links that still point at an old or proxied hostname, using `SUI_SOCKS_SERVER` and `SUI_SOCKS_PORT`.
- `/api/reset/<discord_id>` rotates the internal name and every generated protocol credential; callers must discard all previously returned links.
- New users are assigned to the S-UI `user` group, receive Hysteria 2, SOCKS, and VMess links, and are assigned to all three configured inbounds.
- SOCKS links use the broadly supported `socks5://username:password@host:port` URI format; the SOCKS endpoint must resolve directly to the server and cannot use a Cloudflare-proxied hostname.
- `/api/reset`, `/api/ban`, and `/api/restore` require the `SUI_ADMIN_TOKEN`; never expose this token to clients or commit it.
- Online-user statistics use S-UI's live in-memory `onlines.user` list rather than a traffic time window.
- `registrations` is the total row count in the `clients` table, including disabled clients.
- The server running this must have read/write access to the s-ui database.
