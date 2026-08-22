# S-UI Management API

REST API for managing VPN users through the [s-ui](https://github.com/alireza0/s-ui) panel database.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/create` | Create a new VPN user in the `user` group |
| GET | `/api/info/<name>` | Get user info and traffic stats |
| GET | `/api/traffic/<name>?hours=168` | Get hourly traffic for up to seven days |
| GET | `/api/sub/<name>` | Get user subscription link |
| GET | `/api/clients` | List all enabled clients |
| GET | `/api/stats` | Online users count + total traffic + registered users count |
| POST | `/api/ban/<name>` | Disable a user and assign the `banned` group |
| POST | `/api/restore/<name>` | Restore state after a paired Discord ban fails |

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
- `SUI_ADMIN_TOKEN`: secret Bearer token accepted only by the ban and restore endpoints.
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

```bash
curl -X POST "http://${SUI_API_HOST}:${SUI_API_PORT}/api/create" \
  -H "Content-Type: application/json" \
  -d '{"name": "discord_user_id"}'
```

Response:
```json
{
  "id": 42,
  "name": "discord_user_id",
  "links": "hysteria2://password@server:443?..."
}
```

### Get User Info

```bash
curl "http://${SUI_API_HOST}:${SUI_API_PORT}/api/info/discord_user_id"
```

### Get Subscription Link

```bash
curl "http://${SUI_API_HOST}:${SUI_API_PORT}/api/sub/discord_user_id"
```

### Get Traffic History

```bash
curl "http://${SUI_API_HOST}:${SUI_API_PORT}/api/traffic/discord_user_id?hours=168"
```

### Get Stats

```bash
curl "http://${SUI_API_HOST}:${SUI_API_PORT}/api/stats"
```

Response:
```json
{
  "online": 3,
  "total_bytes": 436591783663,
  "registrations": 18
}
```

### Ban User

This endpoint keeps the client and its traffic history, sets `enable=0`, assigns
the S-UI group `banned`, and restarts S-UI so sing-box drops the client.

```bash
curl -X POST \
  "http://${SUI_API_HOST}:${SUI_API_PORT}/api/ban/discord_user_id" \
  -H "Authorization: Bearer ${SUI_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"reason":"Subscription sharing"}'
```

The response includes `previous_enabled` and `previous_group`. A caller that
pairs this operation with a Discord ban can pass those values to the protected
restore endpoint if the Discord operation fails:

```bash
curl -X POST \
  "http://${SUI_API_HOST}:${SUI_API_PORT}/api/restore/discord_user_id" \
  -H "Authorization: Bearer ${SUI_ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"group":"regular"}'
```

## Notes

- The `/api/create` endpoint automatically restarts the s-ui service after creating a user so the sing-box core picks up the new client.
- All config/inbounds/links fields are stored as BLOBs (required by s-ui's Go backend).
- `/api/sub/<name>` repairs a missing or malformed `links` field from the stored Hysteria 2 password and writes it back as a BLOB. This prevents new accounts from returning a subscription error while s-ui is reloading.
- `/api/sub/<name>` also repairs stored SOCKS links that still point at an old or proxied hostname, using `SUI_SOCKS_SERVER` and `SUI_SOCKS_PORT`.
- New users are assigned to the S-UI `user` group, receive Hysteria 2, SOCKS, and VMess links, and are assigned to all three configured inbounds.
- SOCKS links use the broadly supported `socks5://username:password@host:port` URI format; the SOCKS endpoint must resolve directly to the server and cannot use a Cloudflare-proxied hostname.
- `/api/ban` and `/api/restore` require the `SUI_ADMIN_TOKEN`; never expose this token to clients or commit it.
- Online-user statistics use S-UI's live in-memory `onlines.user` list rather than a traffic time window.
- `registrations` is the total row count in the `clients` table, including disabled clients.
- The server running this must have read/write access to the s-ui database.
