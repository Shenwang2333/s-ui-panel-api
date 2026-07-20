# S-UI Management API

REST API for managing VPN users through the [s-ui](https://github.com/alireza0/s-ui) panel database.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/create` | Create a new VPN user |
| GET | `/api/info/<name>` | Get user info and traffic stats |
| GET | `/api/sub/<name>` | Get user subscription link |
| GET | `/api/clients` | List all enabled clients |
| GET | `/api/stats` | Online users count + total traffic |

## Configuration

- **Database path**: `/usr/local/s-ui/db/s-ui.db`
- **Default port**: `8891`
- **Inbound ID**: `6` (hysteria2)

## Usage

```bash
# Install dependencies
pip install flask

# Run
python3 api.py --host 0.0.0.0 --port 8891
```

### Create User

```bash
curl -X POST http://localhost:8891/api/create \
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
curl http://localhost:8891/api/info/discord_user_id
```

### Get Subscription Link

```bash
curl http://localhost:8891/api/sub/discord_user_id
```

### Get Stats

```bash
curl http://localhost:8891/api/stats
```

Response:
```json
{
  "online": 3,
  "total_bytes": 436591783663
}
```

## Notes

- The `/api/create` endpoint automatically restarts the s-ui service after creating a user so the sing-box core picks up the new client.
- All config/inbounds/links fields are stored as BLOBs (required by s-ui's Go backend).
- The server running this must have read/write access to the s-ui database.
test push 15:26:46
