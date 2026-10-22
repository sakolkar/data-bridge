# DataBridge

## Run locally

From the `data-bridge` directory, install the application dependencies and start the
ASGI application:

```bash
python -m pip install -r requirements.txt
uvicorn app:app --app-dir src --host 0.0.0.0 --port 8000
```

## Run tests

```bash
python -m pip install -r requirements-dev.txt
cd ./data-bridge
pytest
```

## Run with Docker Compose

From the repository root:

```bash
docker compose up --build
```

The API container listens on port `8000`.