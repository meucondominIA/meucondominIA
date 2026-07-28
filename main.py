"""Bootstrap do serviço.

App FastAPI com lifespan: abre pool, clientes externos e o sweeper no startup;
desfaz tudo no shutdown.

O desfazer é um AsyncExitStack, não uma fila de awaits: cada recurso registra
seu fechamento LOGO APÓS ser criado com sucesso, e a pilha desenrola em ordem
inversa "as if multiple nested with statements had been used"
(https://docs.python.org/3/library/contextlib.html).

O que a fila não dava. No shutdown, um fechar_ que estoura não impede os demais
de fecharem — aqui o processo segue vivo e o vazamento seria real. E a ordem
deixa de depender de memória: o sweeper usa o pool, então registrá-lo por último
já o cancela primeiro.

Falha ao CRIAR também fecha os anteriores (OPENAI_API_KEY vazia passa pela
config, que valida tipo e não conteúdo, e estoura em AsyncOpenAI). Aí o ganho é
pequeno — o processo morre e o SO recolhe os sockets —, mas vem junto de graça e
vale nos processos que sobrevivem à falha, como a suíte de testes. O erro
continua propagando em todos os casos.

Doc do lifespan: https://fastapi.tiangolo.com/advanced/events/
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress

from fastapi import FastAPI

import chat
import embeddings
from db import criar_pool, fechar_pool
from encerrador import rodar_encerrador
from sweeper import rodar_sweeper
from webhook import router
from zpro_client import criar_cliente, fechar_cliente

logging.basicConfig(level=logging.INFO)


async def _cancelar(tarefa: asyncio.Task) -> None:
    tarefa.cancel()
    with suppress(asyncio.CancelledError):
        await tarefa


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with AsyncExitStack() as recursos:
        await criar_pool()
        recursos.push_async_callback(fechar_pool)

        await criar_cliente()
        recursos.push_async_callback(fechar_cliente)

        await embeddings.criar_cliente()
        recursos.push_async_callback(embeddings.fechar_cliente)

        await chat.criar_cliente()
        recursos.push_async_callback(chat.fechar_cliente)

        sweeper = asyncio.create_task(rodar_sweeper(), name="sweeper")
        recursos.push_async_callback(_cancelar, sweeper)

        encerrador = asyncio.create_task(rodar_encerrador(), name="encerrador")
        recursos.push_async_callback(_cancelar, encerrador)

        yield


app = FastAPI(title="Assistente Condomínios", lifespan=lifespan)
app.include_router(router)
