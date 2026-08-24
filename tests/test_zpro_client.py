"""Testes do adapter de saída do Z-PRO (Parte 2 · Peça 3).

Sem rede: httpx.MockTransport intercepta o request e devolve a resposta
programada. A fixture RESPOSTA_REAL é a resposta capturada num envio real
(09/07/2026). Criar/usar/fechar acontecem no MESMO event loop (asyncio.run).

Doc: https://www.python-httpx.org/advanced/transports/#mock-transports
"""

import asyncio
import json

import httpx
import pytest

import zpro_client
from config import settings
from zpro_client import OutgoingMessage

RESPOSTA_REAL = {
    "success": True,
    "data": {"message": "Message sent successfully", "ticketId": 4578},
}

MSG = OutgoingMessage(phone="555590000000", text="Eco: Oi", external_key="MSG-1")


async def _roda(handler, msg: OutgoingMessage) -> None:
    await zpro_client.criar_cliente(transport=httpx.MockTransport(handler))
    try:
        await zpro_client.enviar(msg)
    finally:
        await zpro_client.fechar_cliente()


def test_2xx_completa_sem_erro():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=RESPOSTA_REAL)

    asyncio.run(_roda(handler, MSG))


def test_nao_2xx_levanta_httpstatuserror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Token was not provided."})

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_roda(handler, MSG))


def test_2xx_com_success_false_levanta_zproenvioerror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error": "número inválido"})

    with pytest.raises(zpro_client.ZproEnvioError):
        asyncio.run(_roda(handler, MSG))


def test_2xx_sem_json_segue_como_sucesso():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    asyncio.run(_roda(handler, MSG))


def test_request_segue_contrato_verificado():
    capturados: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturados.append(request)
        return httpx.Response(200, json=RESPOSTA_REAL)

    asyncio.run(_roda(handler, MSG))

    req = capturados[0]
    assert req.method == "POST"
    assert str(req.url).rstrip("/") == settings.zpro_api_url.rstrip("/")
    assert req.headers["Authorization"] == f"Bearer {settings.zpro_api_token}"
    assert json.loads(req.content) == {
        "body": "Eco: Oi",
        "number": "555590000000",
        "externalKey": "MSG-1",
        "isClosed": False,
    }


def _handler_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=RESPOSTA_REAL)


def test_get_cliente_sem_startup_levanta_runtimeerror():
    asyncio.run(zpro_client.fechar_cliente())  # garante estado limpo (_cliente None)
    with pytest.raises(RuntimeError):
        zpro_client.get_cliente()


def test_criar_cliente_e_idempotente():
    async def _corpo() -> tuple[httpx.AsyncClient, httpx.AsyncClient]:
        await zpro_client.fechar_cliente()
        await zpro_client.criar_cliente(transport=httpx.MockTransport(_handler_ok))
        primeiro = zpro_client.get_cliente()
        await zpro_client.criar_cliente(transport=httpx.MockTransport(_handler_ok))
        segundo = zpro_client.get_cliente()
        await zpro_client.fechar_cliente()
        return primeiro, segundo

    primeiro, segundo = asyncio.run(_corpo())
    assert primeiro is segundo  # 2ª chamada não recria o cliente


def test_fechar_cliente_sem_cliente_nao_estoura():
    asyncio.run(zpro_client.fechar_cliente())  # garante None
    asyncio.run(
        zpro_client.fechar_cliente()
    )  # 2ª vez com _cliente None -> early return
