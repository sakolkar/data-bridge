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
ASGI application after exporting the key above:

```bash
python -m pip install -r requirements.txt
export DATABRIDGE_SQLITE_PATH="$PWD/.databridge/db.sqlite"
uvicorn app:app --app-dir src --host 0.0.0.0 --port 8000

DATABRIDGE_SECRET_KEY=LGV0e84Z6Dyv1zXPyrjUnW75clrMQQ8LINNNaLtvTKQ= DATABRIDGE_SQLITE_PATH=/workspaces/data-bridge/dbridge.sqlite uvicorn app:app --app-dir src --host 0.
0.0.0 --port 8000
```

## Run tests

```bash
cd ./data-bridge
python -m pip install -r requirements-dev.txt
pytest
```

Tests supply temporary configuration; no real key or database is needed.

## Connector contract

`data-bridge/src/connector/__init__.py` defines the transport-independent `Connector`
abstract class:

| Operation | Contract |
| --- | --- |
| `list(recursive=False)` | Return objects with relative `path`, byte `size`, and `type` (`directory`, `file`, or `symlink`); recursive listings omit directory entries. |
| `read(path)` | Return the complete file as bytes. |
| `write(path, data, mode="create")` | Create a file exclusively, or use `mode="replace"` to replace an existing file. |
| `head(path, lines=10, max_bytes=65536)` | Return a bounded prefix, limited by LF-delimited lines and bytes. |

Paths are logical names relative to a connector, not host-specific absolute
paths. Both head limits must be positive; the byte limit may truncate a line.
Implementations must read only a bounded prefix for head, not the entire file.
Connector errors distinguish missing files, existing-file conflicts, invalid arguments, and operational
failures without depending on HTTP.
All application-defined exceptions live in `data-bridge/src/exceptions.py` and
derive from `DatabridgeException`.

`LocalConnector(path=...)` in `data-bridge/src/connector/local.py` uses an existing
directory as its home. Listing returns entry objects sorted by relative path.
Nonrecursive listings include directories; recursive listings include files and
symlinks from all subdirectories, but not directory entries. Symbolic links are
listed without following them, including broken links. Sizes are filesystem
entry sizes in bytes (`lstat`), not target sizes or recursive directory totals.
Reads, writes, and head operations reject absolute paths,
parent traversal, and symbolic links that resolve outside the home. Writes
create or replace files; parent directories must already exist.

SFTP connectors, transfers, and schema inference are not implemented yet.

## Connection configuration API

Connection routes are defined in `data-bridge/src/routes/connection.py`.

Run `bash ./curl-suite.sh` from the repository root for a basic menu of curl
requests to `http://localhost:8000` (override with `DATABRIDGE_URL`). It covers
connection CRUD and file list/read/head/create/replace; enter values when
prompted, and choose `0` to quit. File writes upload a local source file as raw
bytes; reads can print to the terminal or save to a local file.

Use the singular `/connection` endpoint. POST requires `name` in the JSON body;
GET, PUT, and DELETE use query parameters, never URL path segments.
Clients must URL-encode query values exactly once;
for example, `demo + data` becomes `demo%20%2B%20data`.

| Request | Result |
| --- | --- |
| `POST /connection` | Create with name in the body; returns 201 with name and type. |
| `GET /connection` | List connections with decrypted settings, ordered by case-sensitive name. |
| `GET /connection?name=...` | Retrieve metadata for the exact name. |
| `PUT /connection?name=...` | Replace all type/settings fields; returns 200. |
| `DELETE /connection?name=...` | Delete; returns 204 without a body. |

Names are nonblank, case-sensitive, and at most 128 characters. Spaces and
Unicode are preserved; control characters are rejected. POST/PUT accept only
implemented connector types: `local` and `sftp`. Local connections require a
nonblank `path` with no null characters; the home directory is not accessed or
checked when saving configuration. SFTP connections require `host`, `port`
(1-65535), `username`, and `password`. Extra body fields are rejected.
PUT cannot rename a connection and rejects `name` in the body.

Settings are encrypted in SQLite and returned decrypted in the connection list.
Each list entry contains `name`, `type`, and `settings` as a JSON object;
single-connection detail and create/replace responses contain only `name` and
`type`. To replace settings, supply the complete new object; no merge endpoint
is provided. Local paths are serialized as `{"path":"..."}` in encrypted
settings. SFTP credentials are encrypted at rest. Validation responses do not
echo submitted values.

Duplicate names return 409; absent connections return 404; invalid input returns
422. Storage failures return a generic 500 and are logged without credentials.
An explicitly blank name is invalid, not a request to list all connections.

```bash
# Configure a local connector without performing file operations.
curl -X POST 'http://localhost:8000/connection' \
  -H 'Content-Type: application/json' \
  -d '{"name":"local-files","type":"local","path":"/data"}'
# {"name":"local-files","type":"local"}

curl 'http://localhost:8000/connection'
curl --get 'http://localhost:8000/connection' --data-urlencode 'name=local-files'

curl -X PUT 'http://localhost:8000/connection?name=local-files' \
  -H 'Content-Type: application/json' \
  -d '{"type":"local","path":"/replacement"}'

curl -X DELETE 'http://localhost:8000/connection?name=local-files'

# Configure an SFTP connector. Its host key must already be trusted in the
# service user's SSH known_hosts file.
curl -X POST 'http://localhost:8000/connection' \
  -H 'Content-Type: application/json' \
  -d '{"name":"sftp-files","type":"sftp","host":"localhost","port":8822,"username":"testuser","password":"<password>"}'

curl --get 'http://localhost:8000/connection/file' \
  --data-urlencode 'name=sftp-files' --data-urlencode 'action=list'
```

Connector configuration and construction are registered in
`src/connector/registry.py`; each connector type provides its own validated
settings model and factory.
`ConnectionRecord` in `src/db/models.py` is the SQLModel model used for database
storage and internal decrypted configurations. POST validates the body directly;
PUT combines the query name with the body before validating the record.
API responses are plain dictionaries containing only name and type;
validation and storage errors use application-level exception handlers.
Database table models live in `src/db/models.py`. The model-independent
`Database` class in `src/db/db.py` owns the engine,
schema initialization, transactional SQLModel sessions, and cleanup. The app
keeps the database on `app.state.database` and manages its startup and shutdown;
the connection store receives only its session factory. The connection store
handles connection queries and storage error translation, retaining SQLAlchemy's
SQLite conflict handling. The model uses `EncryptedString` from `src/db/fields.py`
to transparently encrypt and decrypt settings as strings. New values are stored
as text Fernet tokens; older binary tokens need conversion to text before they
can be read by this field.
Saving connection configuration does not verify connectivity. Authentication and
authorization are not implemented; do not expose this service to untrusted clients.

## File I/O API

`/connection/file` resolves a saved connection using the required `name` query
parameter. The required `action` selects the operation:

| Request | Result |
| --- | --- |
| `GET /connection/file?name=...&action=list` | JSON array of entry objects; `recursive=false` by default. |
| `GET /connection/file?name=...&action=read&path=...` | Complete file as raw bytes. |
| `GET /connection/file?name=...&action=head&path=...` | Raw prefix; defaults to `lines=10` and `max_bytes=65536`, both positive. |
| `POST /connection/file?name=...&action=write&path=...` | Create a file; returns 201 with no body, or 409 if the entry exists. |
| `PUT /connection/file?name=...&action=write&path=...` | Replace an existing file; returns 204, or 404 if it is missing. |

File paths are relative to the connection home and must be URL-encoded exactly
once. Listing starts at the home directory and does not accept `path`.
Read and head responses use `application/octet-stream`. POST and PUT write the
raw request body as-is, regardless of Content-Type; JSON, text, and binary data
are not parsed or transformed. Empty bodies create or truncate to an empty file.
Parent directories must already exist. Reads and writes currently buffer
contents in memory rather than streaming.

Missing connections or files return 404; invalid actions, paths, and limits
return 422. Operational failures return a generic 500 and are logged without
file contents or configuration values.

```bash
curl --get 'http://localhost:8000/connection/file' \
  --data-urlencode 'name=local-files' --data-urlencode 'action=list' \
  --data-urlencode 'recursive=true'

curl -X POST 'http://localhost:8000/connection/file?name=local-files&action=write&path=example.bin' \
  -H 'Content-Type: application/octet-stream' --data-binary @example.bin

curl --get 'http://localhost:8000/connection/file' \
  --data-urlencode 'name=local-files' --data-urlencode 'action=read' \
  --data-urlencode 'path=example.bin' --output downloaded.bin
```

## Run with Docker Compose

From the repository root, with `DATABRIDGE_SECRET_KEY` exported:

```bash
docker compose up --build
```

The API container listens on port `8000`. Compose supplies the absolute database
path `/var/lib/databridge/db.sqlite` and mounts its parent directory in the
`databridge-data` named volume so saved configurations survive container
recreation. The key is passed at runtime, never baked into the image. Missing keys
cause Compose to fail before starting containers.