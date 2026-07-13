"""Bootstrap do serviço.

App FastAPI com lifespan: abre o pool asyncpg e liga o sweeper no startup;
no shutdown cancela o sweeper ANTES de fechar o pool (ele usa o pool).
Doc: https://fastapi.tiangolo.com/advanced/events/ (lifespan substitui on_event).
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from db import criar_pool, fechar_pool
from sweeper import rodar_sweeper
from webhook import router
from zpro_client import criar_cliente, fechar_cliente

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await criar_pool()
    await criar_cliente()
    sweeper = asyncio.create_task(rodar_sweeper(), name="sweeper")
    yield
    sweeper.cancel()
    with suppress(asyncio.CancelledError):
        await sweeper
    await fechar_cliente()
    await fechar_pool()


app = FastAPI(title="Assistente Condomínios", lifespan=lifespan)
app.include_router(router)
