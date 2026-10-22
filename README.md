# DataBridge

## Configuration

The service reads two required environment variables once at startup. There is no
configuration file or automatic default:

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
It is reserved for future encryption of sensitive database fields, not encryption
of the entire SQLite file. Encryption and database persistence are not implemented yet.

Startup validates the key and file path, creates missing database parent
directories, and fails clearly on invalid configuration or directory-creation
errors. Existing database files are opened read-only and checked for SQLite
integrity; unreadable, invalid, or corrupt databases cause startup to fail.
Missing database files are not created, and existing database contents are left unchanged.
There is no base-directory restriction: choose a path the service can access.
Restart the service after changing configuration.

## Run locally

From the `data-bridge` directory, install the application dependencies and start the
ASGI application after exporting the key above:

```bash
python -m pip install -r requirements.txt
export DATABRIDGE_SQLITE_PATH="$PWD/.databridge/db.sqlite"
uvicorn app:app --app-dir src --host 0.0.0.0 --port 8000
```

## Run tests

```bash
cd ./data-bridge
python -m pip install -r requirements-dev.txt
pytest
```

Tests supply temporary configuration; no real key or database is needed.

## Run with Docker Compose

From the repository root, with `DATABRIDGE_SECRET_KEY` exported:

```bash
docker compose up --build
```

The API container listens on port `8000`. Compose supplies the absolute database
path `/var/lib/databridge/db.sqlite` and mounts its parent directory in the
`databridge-data` named volume so future database contents survive container
recreation. The key is passed at runtime, never baked into the image. Missing keys
cause Compose to fail before starting containers.