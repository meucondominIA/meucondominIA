"""Testes do adapter de chat da OpenAI (Fase 3 · Passo 1).

Só o ciclo de vida — a geração é do passo 4. Sem rede: httpx.MockTransport
dentro de um httpx.AsyncClient injetado no AsyncOpenAI, como em
test_embeddings.py. Criar/usar/fechar no MESMO event loop (asyncio.run).

Doc: https://www.python-httpx.org/advanced/transports/#mock-transports
"""

import asyncio

import httpx
import pytest

import chat
import embeddings


def _http_mock() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    )


def test_get_cliente_sem_startup_levanta_runtimeerror():
    asyncio.run(chat.fechar_cliente())
    with pytest.raises(RuntimeError):
        chat.get_cliente()


def test_criar_cliente_e_idempotente():
    async def _corpo():
        await chat.fechar_cliente()
        await chat.criar_cliente(http_client=_http_mock())
        primeiro = chat.get_cliente()
        await chat.criar_cliente(http_client=_http_mock())
        segundo = chat.get_cliente()
        await chat.fechar_cliente()
        return primeiro, segundo

    primeiro, segundo = asyncio.run(_corpo())
    assert primeiro is segundo


def test_fechar_cliente_sem_cliente_nao_estoura():
    asyncio.run(chat.fechar_cliente())
    asyncio.run(chat.fechar_cliente())


def test_fechar_cliente_permite_recriar():
    async def _corpo():
        await chat.criar_cliente(http_client=_http_mock())
        primeiro = chat.get_cliente()
        await chat.fechar_cliente()
        await chat.criar_cliente(http_client=_http_mock())
        segundo = chat.get_cliente()
        await chat.fechar_cliente()
        return primeiro, segundo

    primeiro, segundo = asyncio.run(_corpo())
    assert primeiro is not segundo


def test_cliente_de_chat_e_independente_do_de_embeddings():
    """A promessa do B1: trocar de provedor de chat não encosta em embeddings."""

    async def _corpo():
        await chat.criar_cliente(http_client=_http_mock())
        await embeddings.criar_cliente(http_client=_http_mock())
        cliente_chat = chat.get_cliente()
        cliente_emb = embeddings.get_cliente()

        await chat.fechar_cliente()
        sobreviveu = embeddings.get_cliente()

        await embeddings.fechar_cliente()
        return cliente_chat, cliente_emb, sobreviveu

    cliente_chat, cliente_emb, sobreviveu = asyncio.run(_corpo())
    assert cliente_chat is not cliente_emb
    assert sobreviveu is cliente_emb
