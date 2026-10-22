from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import load_settings, prepare_storage


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = load_settings()
    prepare_storage(settings)
    app.state.settings = settings
    yield


app = FastAPI(title="DataBridge", lifespan=lifespan)
