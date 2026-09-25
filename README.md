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
| GET | `/api/nodes/<node_id>/config` | Return enabled credentials to one authenticated edge node |
| POST | `/api/reload` | Restart S-UI asynchronously; internal use only |

Every `/api/...` endpoint requires a Bearer token. Management endpoints use
`SUI_ADMIN_TOKEN`; `/api/nodes/<node_id>/config` uses the separate
`SUI_EDGE_NODE_TOKEN`. The edge token is intentionally restricted to the node
configuration endpoint and cannot read users, subscriptions, statistics, or
perform management actions. Requests without the correct token return HTTP
401. The production API is exposed at `https://api.swangnetwork.asia`.

## Architecture

The S-UI database on the control-plane server is the only source of truth.
Edge nodes do not mount, copy, or directly open that SQLite database. Instead,
`edge_agent.py` polls the authenticated node configuration endpoint, validates
the returned client names and credentials, builds a complete sing-box config,
runs `sing-box check`, and atomically replaces the active config only when it
changes.

Each subscription contains five links in this order:

1. Control-plane Hysteria2 over direct DNS with a public TLS certificate.
2. Control-plane SOCKS over a direct address.
3. Control-plane VMess WebSocket through its CDN hostname.
4. Edge-node Hysteria2 over direct DNS with its own public TLS certificate.
5. Edge-node VMess WebSocket through the edge CDN hostname.

Hysteria2 hostnames must be DNS-only and must not be proxied by a CDN. VMess
uses a separate CDN-proxied hostname and WebSocket path. A reset rotates the
credentials used by both servers; a ban removes the user from the edge payload.
The edge applies either change on its next poll without sharing SQLite files.

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
SUI_EDGE_NODE_ID=your-edge-node-id
SUI_EDGE_NODE_TOKEN=generate-a-separate-random-secret
SUI_EDGE_HYSTERIA2_SERVER=your-edge-hysteria-host.example.com
SUI_EDGE_HYSTERIA2_PORT=8443
SUI_EDGE_HYSTERIA2_QUERY="security=tls&insecure=0&sni=your-edge-hysteria-host.example.com&alpn=h3&fastopen=0"
SUI_EDGE_HYSTERIA2_REMARK="your-edge-hysteria-node-name"
SUI_EDGE_VMESS_SERVER=your-edge-cdn-host.example.com
SUI_EDGE_VMESS_PORT=8443
SUI_EDGE_VMESS_PATH=/your-websocket-path
SUI_EDGE_VMESS_REMARK="your-edge-vmess-node-name"
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
- `SUI_ADMIN_TOKEN`: secret Bearer token required by every management API endpoint.
- `SUI_PANEL_API_URL`: S-UI API v2 `onlines` endpoint, including its TLS hostname.
- `SUI_PANEL_API_TOKEN`: S-UI API token sent only to the local panel endpoint.
- `SUI_PANEL_CONNECT_HOST`: local address used for the panel connection while preserving TLS SNI.
- `SUI_PANEL_CA_FILE`: CA certificate used to verify the panel's TLS certificate.
- `SUI_EDGE_NODE_ID`: identifier accepted in the edge-node API path.
- `SUI_EDGE_NODE_TOKEN`: independent Bearer token used only by the edge agent.
- `SUI_EDGE_HYSTERIA2_*`: direct edge Hysteria2 endpoint, TLS query, and remark.
- `SUI_EDGE_VMESS_*`: CDN edge VMess hostname, port, WebSocket path, and remark.

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

`deploy/sui-api.env.example` contains the complete environment template. Keep
the production copy outside the repository with mode `0600`.

## Edge Deployment

Install sing-box and copy the deployment files to the edge node:

```bash
install -d -m 755 /usr/local/lib/sui-edge-agent
install -d -m 750 -o root -g sing-box /etc/sing-box /etc/sing-box/certs
install -m 755 edge_agent.py /usr/local/lib/sui-edge-agent/edge_agent.py
install -m 644 deploy/sing-box.service /etc/systemd/system/sing-box.service
install -m 644 deploy/sui-edge-agent.service /etc/systemd/system/sui-edge-agent.service
install -m 600 deploy/sui-edge-agent.env.example /etc/sui-edge-agent.env
```

Create a separate random node token on the control plane, place the same value
in `SUI_EDGE_NODE_TOKEN`, and write it to the edge token file with mode `0600`.
Do not reuse the management or Discord token. The optional legacy users file
can temporarily retain an existing Hysteria2-only service account during a
migration; it is runtime state and must not be committed.

Before switching the production port, validate the fetched configuration on a
temporary listener:

```bash
set -a
. /etc/sui-edge-agent.env
set +a
HYSTERIA2_LISTEN_PORT=4443 \
  python3 /usr/local/lib/sui-edge-agent/edge_agent.py --check
```

The Agent prefers IPv4 when the API hostname has both A and AAAA records, then
falls back to IPv6. This avoids synchronization delays on hosts with published
but unusable IPv6 connectivity. Enable both services only after `--check`
succeeds.

```bash
systemctl daemon-reload
systemctl enable sing-box sui-edge-agent
systemctl start sui-edge-agent
```

The edge Nginx virtual host should terminate CDN HTTPS and proxy only the
configured WebSocket path to the loopback VMess listener. Its TCP 443 listener
can coexist with Hysteria2 on UDP 443.

## Certificates and Stored Links

Use independent publicly trusted certificates for each direct Hysteria2
hostname. Customize the certificate-name placeholders in both deploy hooks
before installing them under `/etc/letsencrypt/renewal-hooks/deploy/`.

- `certbot-sing-box-hook` copies the renewed edge certificate with restricted
  ownership and restarts sing-box only if it is already running.
- `certbot-sui-hysteria2-hook` updates only the configured S-UI Hysteria2 TLS
  record, stores JSON as SQLite BLOBs, creates a backup under
  `/var/backups/s-ui-cert-renewal`, and restarts S-UI.

After adding or changing a node, rebuild every existing client's stored links:

```bash
python3 deploy/rebuild_subscription_links.py \
  --environment-file /etc/sui-api.env \
  --api /root/receiver.py \
  --database /usr/local/s-ui/db/s-ui.db
```

The rebuild command creates a SQLite backup under
`/var/backups/sui-api-links` before writing changes. It reads the systemd
EnvironmentFile directly, so remarks containing spaces do not need to be
shell-compatible.

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
  -H "Authorization: Bearer ${SUI_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"discord_id":"123456789012345678","description":"discord_username"}'
```

Response:
```json
{
  "id": 42,
  "discord_id": "123456789012345678",
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
curl "https://api.swangnetwork.asia/api/info/123456789012345678" \
  -H "Authorization: Bearer ${SUI_ADMIN_TOKEN}"
```

The response includes the internal random `name`, the external `discord_id`,
the S-UI description as `desc`, the current `group`, enabled state, quota and
traffic totals.

### Get Subscription Link

```bash
curl "https://api.swangnetwork.asia/api/sub/123456789012345678" \
  -H "Authorization: Bearer ${SUI_ADMIN_TOKEN}"
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
curl "https://api.swangnetwork.asia/api/traffic/123456789012345678?hours=168" \
  -H "Authorization: Bearer ${SUI_ADMIN_TOKEN}"
```

### Get Stats

```bash
curl "https://api.swangnetwork.asia/api/stats" \
  -H "Authorization: Bearer ${SUI_ADMIN_TOKEN}"
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
  "http://${SUI_API_HOST}:${SUI_API_PORT}/api/reset/123456789012345678" \
  -H "Authorization: Bearer ${SUI_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"description":"discord_username"}'
```

Response:

```json
{
  "discord_id": "123456789012345678",
  "old_name": "k7v2m9a4q1x8c6bz",
  "name": "p3n8r5w1d9f6h2jt"
}
```

### Ban User

This endpoint keeps the client and its traffic history, sets `enable=0`, assigns
the S-UI group `banned`, and restarts S-UI so sing-box drops the client.

```bash
curl -X POST \
  "http://${SUI_API_HOST}:${SUI_API_PORT}/api/ban/123456789012345678" \
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
  "http://${SUI_API_HOST}:${SUI_API_PORT}/api/restore/123456789012345678" \
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
- `/api/create` replaces a stale Discord mapping when its referenced S-UI
  client row no longer exists. Valid bans keep their client row and therefore
  remain protected by the normal idempotency check.
- All config/inbounds/links fields are stored as BLOBs (required by s-ui's Go backend).
- `/api/sub/<discord_id>` repairs a missing or malformed `links` field from stored credentials and writes it back as a BLOB. This prevents new accounts from returning a subscription error while S-UI is reloading.
- `/api/sub/<discord_id>` also repairs stored SOCKS links that still point at an old or proxied hostname, using `SUI_SOCKS_SERVER` and `SUI_SOCKS_PORT`.
- `/api/reset/<discord_id>` rotates the internal name and every generated protocol credential; callers must discard all previously returned links.
- New users are assigned to the S-UI `user` group, receive five aggregated links, and are assigned to the three configured control-plane inbounds.
- SOCKS links use the broadly supported `socks5://username:password@host:port` URI format; the SOCKS endpoint must resolve directly to the server and cannot use a Cloudflare-proxied hostname.
- Every `/api/...` endpoint requires the `SUI_ADMIN_TOKEN`, except
  `/api/nodes/<node_id>/config`, which requires only the separate
  `SUI_EDGE_NODE_TOKEN`; never expose either token to clients or commit them.
- Online-user statistics use S-UI's live in-memory `onlines.user` list rather than a traffic time window.
- `registrations` is the total row count in the `clients` table, including disabled clients.
- The server running this must have read/write access to the s-ui database.
