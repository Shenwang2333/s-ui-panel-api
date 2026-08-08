# S-UI Management API

REST API for managing VPN users through the [s-ui](https://github.com/alireza0/s-ui) panel database.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/create` | Create a new VPN user |
| GET | `/api/info/<name>` | Get user info and traffic stats |
| GET | `/api/sub/<name>` | Get user subscription link |
| GET | `/api/clients` | List all enabled clients |
| GET | `/api/stats` | Online users count + total traffic + registered users count |

## Configuration

### Node settings

Node-specific values are read from environment variables. Do not put a server
address, SNI, inbound ID, or node remark directly in `api.py`.

Create an environment file (for example `/etc/sui-api.env`):

```dotenv
SUI_SERVER_ADDRESS=your-server.example.com
SUI_HYSTERIA2_PORT=your-port
SUI_HYSTERIA2_QUERY=security=tls&insecure=1&sni=your-sni.example.com&alpn=h3,h2&fastopen=0
SUI_HYSTERIA2_INBOUND_ID=your-inbound-id
SUI_SUBSCRIPTION_REMARK=your-node-name
SUI_DB_PATH=/path/to/s-ui.db
SUI_API_HOST=127.0.0.1
SUI_API_PORT=your-api-port
```

The variables are used as follows:

- `SUI_SERVER_ADDRESS`: server hostname or address placed in generated links.
- `SUI_HYSTERIA2_PORT`: Hysteria 2 listener port.
- `SUI_HYSTERIA2_QUERY`: complete query string after `?` in the generated URI.
- `SUI_HYSTERIA2_INBOUND_ID`: S-UI inbound ID inserted for newly created users.
- `SUI_SUBSCRIPTION_REMARK`: node name shown in subscription clients.
- `SUI_DB_PATH`: path to the S-UI SQLite database.
- `SUI_API_HOST`: address on which this API listens.
- `SUI_API_PORT`: port on which this API listens.

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

## Notes

- The `/api/create` endpoint automatically restarts the s-ui service after creating a user so the sing-box core picks up the new client.
- All config/inbounds/links fields are stored as BLOBs (required by s-ui's Go backend).
- `/api/sub/<name>` repairs a missing or malformed `links` field from the stored Hysteria 2 password and writes it back as a BLOB. This prevents new accounts from returning a subscription error while s-ui is reloading.
- `registrations` is the total row count in the `clients` table, including disabled clients.
- The server running this must have read/write access to the s-ui database.
