# DataBridge

## Configuration

The service requires two environment variables before the application is
imported. There is no configuration file or automatic default:

| Variable | Value |
| --- | --- |
| `DATABRIDGE_SECRET_KEY` | A Fernet-compatible key: URL-safe base64 encoding of 32 random bytes. |
| `DATABRIDGE_SQLITE_PATH` | An absolute SQLite file path, such as `/var/lib/databridge/db.sqlite`. |

Generate a key for initial setup:

```bash
export DATABRIDGE_SECRET_KEY="$(python -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"))')"
```

Keep this key private and backed up securely, and supply the **same key** on
subsequent starts. Do not regenerate it each time or commit it to the repository.
It encrypts connection settings with Fernet, not the entire SQLite file.
Names and types remain readable in the database. Changing or losing the key
prevents decryption of existing settings; key rotation is not implemented.
The encrypted field currently returns `[DECRYPTION_FAILED]` when decryption
fails.

Startup validates the key and file path, creates missing database parent
directories, and fails clearly on invalid configuration or directory-creation
errors. Existing database files are opened read-only and checked for SQLite
integrity; unreadable, invalid, or corrupt databases cause startup to fail.
After validation, the SQLModel connection store creates a missing database and
its own table. Saved configurations survive restarts; unrelated tables are left
unchanged. Configuration validation by itself does not create a database.
There is no base-directory restriction: choose a path the service can access.
Restart the service after changing configuration.

## Run locally

From the `data-bridge` directory, install the application dependencies and start the
ASGI application like follows. Be sure to update the example secret key after generating
it from the instructions above.

```bash
cd ./data-bridge

python -m pip install -r requirements.txt

DATABRIDGE_SECRET_KEY=LGV0e84Z6Dyv1zXPyrjUnW75clrMQQ8LINNNaLtvTKQ= DATABRIDGE_SQLITE_PATH=/workspaces/data-bridge/dbridge.sqlite uvicorn app:app --app-dir src --host 0.0.0.0 --port 8000
```


## Run with Docker Compose

From the repository root, with `DATABRIDGE_SECRET_KEY` exported:


```bash
docker compose up --build
```

The API container listens on port `8000`. Compose supplies the absolute database
path `/var/lib/databridge/db.sqlite` and bind-mounts its parent directory from
`.databridge/local` so saved configurations survive container
recreation.

Within Compose, the API reaches the SFTP service at `sftp:22`; port `8822` is only
the host-published port. Exposing port `8822` on the container does not change
the SSH listener. If `sftp:22` times out despite a listening server, check Docker
bridge forwarding and host firewall rules, try running `fix_codespace_iptables.sh`
for GitHub codespaces.


## API operations

The examples assume the service is at `http://localhost:8000` and use the
connection examples from `docker-compose.yml`. Add `-i` to show response
status and headers. For an interactive menu of these calls, run
`./curl-suite.sh` (set `DATABRIDGE_URL` to override the base URL).

### Connections

```bash
# Create a connection (201)
curl -X POST -H 'Content-Type: application/json' \
    -d '{"name":"b","type":"local","path":"/var/lib/databridge"}' \
    http://localhost:8000/connection

curl -X POST -H 'Content-Type: application/json' \
    -d '{"name":"a","type":"sftp","host":"sftp","port":22,"username":"testuser","password":"testpass"}' \
    http://localhost:8000/connection

# List all connections
curl http://localhost:8000/connection

# Get one connection
curl 'http://localhost:8000/connection?name=b'

# Replace a connection; the name goes in the query, not the body
curl -X PUT -H 'Content-Type: application/json' \
    -d '{"type":"local","path":"/data"}' \
    'http://localhost:8000/connection?name=b'

# Delete a connection (204)
curl -X DELETE 'http://localhost:8000/connection?name=b'
```

### Files

```bash
# List files (optionally recursive)
curl 'http://localhost:8000/connection/file?name=b&action=list&recursive=true'

# Read a file
curl -o example.txt 'http://localhost:8000/connection/file?name=b&action=read&path=example.txt'

# Head a file (defaults: lines=10, max_bytes=65536)
curl 'http://localhost:8000/connection/file?name=b&action=head&path=example.txt&lines=5&max_bytes=1024'

# Create a new file (201)
curl -X POST -H 'Content-Type: application/octet-stream' --data-binary @example.txt \
    'http://localhost:8000/connection/file?name=b&action=write&path=example.txt'

# Replace an existing file (204)
curl -X PUT -H 'Content-Type: application/octet-stream' --data-binary @example.txt \
    'http://localhost:8000/connection/file?name=b&action=write&path=example.txt'
```

URL-encode query values containing special characters, for example with
`curl -G --data-urlencode 'path=my file.txt' ...`.

### Transfers

```bash
# Start a transfer (202); the response includes the transfer ID and a Location header
curl -X POST -H 'Content-Type: application/json' \
    -d '{"source":"b","source_file":"example.txt","destination":"a","destination_file":"data/example.txt"}' \
    http://localhost:8000/transfer

# Get transfer status
curl 'http://localhost:8000/transfer?transfer_id=<transfer-id>'
```

## Run tests

```bash
cd ./data-bridge
python -m pip install -r requirements-dev.txt
pytest
```

Tests supply temporary configuration; no real key or database is needed.


---

## Implemented Features

1. Local & SFTP Connector API to create, list, get, replace, delete connectors

1. Connector Persistence with a SQLite database

1. Connector interface (`list`, `read`, `write`, `head`, plus chunked `read_chunks`/`write_chunks`) implemented by both the local filesystem and SFTP connectors

1. File API to list (optionally recursive), read, head (limited by lines and bytes), create, and replace files through any connection

1. Path safety: file paths are relative to the connection's home directory and cannot escape it via `..`, absolute paths, or symlinks

1. Data-agnostic transfers between any two connections (local ↔ SFTP, or same type), with no connector-specific logic in the transfer pipeline

1. Asynchronous transfers run on a background worker pool; `POST /transfer` returns `202` with a transfer ID and `Location` header

1. Transfer status tracking via the API: state, started/completed timestamps, bytes transferred (with periodic progress updates), and failure messages

1. Streaming I/O so files are transferred in chunks rather than fully loaded into memory

1. Graceful shutdown: running transfers are stopped and queued ones cancelled on shutdown, and transfers interrupted by a restart are marked as failed on startup

1. Encryption of stored connection settings (including credentials) at rest using Fernet

1. Startup validation of configuration and SQLite database integrity

1. Clear error responses with appropriate status codes (e.g. `404` missing connection/file, `409` duplicate connection/existing file, `422` invalid input, `403` permission denied, `502` SFTP server unreachable or bad credentials) without leaking stack traces or credentials

1. OpenAPI/Swagger UI docs served by FastAPI at `/docs`, however, these do not list the schemas of the requests and responses.

1. Unit tests for the connectors, controllers, database, configuration, and API routes

1. Docker Compose setup running the service alongside an SFTP server, plus an interactive `curl-suite.sh` for exercising the API


## Future Enhancements

Given more time, these are items that I would add to the service

1. Complete the Phase 3 implementation. Would like to add json handling using `jq` for more efficient memory usage to parse a large json file.

1. FastAPI includes OpenAPI spec docs at the `/docs` endpoint. However, these are very bare for request & response schemas. With additional time, I would detail out the request & response schemas for better documentation about using the API.

1. The project scope did not include the feature to delete files from a connection. However, during testing, it is handy to want to delete a file occasionally.

1. The service would benefit from authentication and that split into at least two layers. One level of authentication that would allow creating & managing connections. Another that can use existing connections to manage files & transfers. Without authentication the servers behind the connections are fully exposed to anyone with network access to the webservice.

1. On server shutdown & start, instead of abandonning transfers, be able to pause the transfer and then continue from that block of bytes. This can save time and avoid having users to restart transfers after a deployment/maintenance of the webservice.