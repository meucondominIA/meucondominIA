"""Bootstrap do serviço (Fase 2.0 · Peça C).

App FastAPI com lifespan: abre o pool asyncpg no startup e fecha no shutdown.
Doc: https://fastapi.tiangolo.com/advanced/events/ (lifespan substitui on_event).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import criar_pool, fechar_pool
from webhook import router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await criar_pool()  # startup
    yield
    await fechar_pool()  # shutdown


app = FastAPI(title="Assistente Condomínios", lifespan=lifespan)
app.include_router(router)
