"""Testes do adapter de chat da OpenAI (Fase 3 · Passos 1 e 4).

Sem rede: httpx.MockTransport dentro de um httpx.AsyncClient injetado no
AsyncOpenAI, como em test_embeddings.py. Criar/usar/fechar no MESMO event loop
(asyncio.run). Os corpos-mock seguem a forma real da Responses API (validada
contra o SDK 2.45.0), então o próprio parse do SDK é exercitado.

Doc: https://www.python-httpx.org/advanced/transports/#mock-transports
"""

import asyncio
import json

import httpx
import openai
import pytest

import chat
import embeddings
from chat import MensagemChat, PapelChat
from config import settings


def _http_mock(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _handler_vazio(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={})


def _corpo(texto: str, *, status: str = "completed", incomplete=None, com_output=True) -> dict:
    body = {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "model": settings.openai_chat_model,
        "status": status,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "output": [],
    }
    if incomplete is not None:
        body["incomplete_details"] = {"reason": incomplete}
    if com_output:
        body["output"] = [
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": texto, "annotations": []}],
            }
        ]
    return body


def _handler_ok(texto: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_corpo(texto))

    return handler


async def _roda(handler, mensagens: list[MensagemChat]) -> str:
    await chat.criar_cliente(http_client=_http_mock(handler))
    try:
        return await chat.gerar_resposta(mensagens)
    finally:
        await chat.fechar_cliente()


_UMA = [MensagemChat(papel=PapelChat.USUARIO, conteudo="posso ter cachorro?")]


# --- geração (Passo 4) ---


def test_happy_path_devolve_o_texto():
    resultado = asyncio.run(_roda(_handler_ok("Sim, pode ter animal."), _UMA))
    assert resultado == "Sim, pode ter animal."


def test_request_segue_contrato():
    capturados: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturados.append(request)
        return httpx.Response(200, json=_corpo("ok"))

    asyncio.run(_roda(handler, _UMA))

    req = capturados[0]
    assert req.method == "POST"
    assert req.url.path == "/v1/responses"
    assert req.headers["Authorization"] == f"Bearer {settings.openai_api_key}"
    corpo = json.loads(req.content)
    assert corpo["model"] == settings.openai_chat_model
    assert corpo["store"] is False
    assert corpo["reasoning"] == {"effort": settings.openai_chat_effort}
    assert corpo["text"] == {"verbosity": settings.openai_chat_verbosity}
    assert corpo["input"] == [{"role": "user", "content": "posso ter cachorro?"}]
    assert req.extensions["timeout"]["read"] == settings.openai_timeout_chat_seconds


def test_papeis_mapeiam_para_a_wire_string_da_api():
    capturados: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturados.append(request)
        return httpx.Response(200, json=_corpo("ok"))

    mensagens = [
        MensagemChat(papel=PapelChat.SISTEMA, conteudo="você é o assistente"),
        MensagemChat(papel=PapelChat.USUARIO, conteudo="oi"),
        MensagemChat(papel=PapelChat.ASSISTENTE, conteudo="olá"),
        MensagemChat(papel=PapelChat.USUARIO, conteudo="posso ter cachorro?"),
    ]
    asyncio.run(_roda(handler, mensagens))

    papeis = [item["role"] for item in json.loads(capturados[0].content)["input"]]
    assert papeis == ["developer", "user", "assistant", "user"]


def test_status_incomplete_levanta_resposta_error_e_nao_devolve_o_parcial():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_corpo("Sim, em geral é permitido, desde que não cause incô",
                             status="incomplete", incomplete="max_output_tokens")
        )

    with pytest.raises(chat.ChatRespostaError, match="incomplete"):
        asyncio.run(_roda(handler, _UMA))


def test_output_text_vazio_levanta_resposta_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_corpo("", com_output=False))

    with pytest.raises(chat.ChatRespostaError, match="vazio"):
        asyncio.run(_roda(handler, _UMA))


def _handler_500(chamadas: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(
            500,
            headers={"retry-after-ms": "1"},
            json={"error": {"message": "server error", "type": "server_error"}},
        )

    return handler


def test_erro_do_sdk_vira_chat_indisponivel_com_causa_e_usa_os_retries():
    chamadas: list[httpx.Request] = []

    with pytest.raises(chat.ChatIndisponivelError) as excinfo:
        asyncio.run(_roda(_handler_500(chamadas), _UMA))

    assert isinstance(excinfo.value.__cause__, openai.APIError)
    assert len(chamadas) == 1 + settings.openai_retries_chat


def test_lista_vazia_levanta_valueerror_sem_chamar_api():
    chamadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(200, json=_corpo("ok"))

    with pytest.raises(ValueError, match="vazia"):
        asyncio.run(_roda(handler, []))

    assert chamadas == []


def test_conteudo_vazio_levanta_valueerror_sem_chamar_api():
    chamadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(200, json=_corpo("ok"))

    mensagens = [MensagemChat(papel=PapelChat.USUARIO, conteudo="")]
    with pytest.raises(ValueError, match="vazio"):
        asyncio.run(_roda(handler, mensagens))

    assert chamadas == []


def test_conteudo_so_espacos_levanta_valueerror_sem_chamar_api():
    chamadas: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(200, json=_corpo("ok"))

    mensagens = [MensagemChat(papel=PapelChat.USUARIO, conteudo="   ")]
    with pytest.raises(ValueError, match="vazio"):
        asyncio.run(_roda(handler, mensagens))

    assert chamadas == []


# --- ciclo de vida (Passo 1) ---


def test_get_cliente_sem_startup_levanta_runtimeerror():
    asyncio.run(chat.fechar_cliente())
    with pytest.raises(RuntimeError):
        chat.get_cliente()


def test_criar_cliente_e_idempotente():
    async def _corpo():
        await chat.fechar_cliente()
        await chat.criar_cliente(http_client=_http_mock(_handler_vazio))
        primeiro = chat.get_cliente()
        await chat.criar_cliente(http_client=_http_mock(_handler_vazio))
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
        await chat.criar_cliente(http_client=_http_mock(_handler_vazio))
        primeiro = chat.get_cliente()
        await chat.fechar_cliente()
        await chat.criar_cliente(http_client=_http_mock(_handler_vazio))
        segundo = chat.get_cliente()
        await chat.fechar_cliente()
        return primeiro, segundo

    primeiro, segundo = asyncio.run(_corpo())
    assert primeiro is not segundo


def test_cliente_de_chat_e_independente_do_de_embeddings():
    """A promessa do B1: trocar de provedor de chat não encosta em embeddings."""

    async def _corpo():
        await chat.criar_cliente(http_client=_http_mock(_handler_vazio))
        await embeddings.criar_cliente(http_client=_http_mock(_handler_vazio))
        cliente_chat = chat.get_cliente()
        cliente_emb = embeddings.get_cliente()

        await chat.fechar_cliente()
        sobreviveu = embeddings.get_cliente()

        await embeddings.fechar_cliente()
        return cliente_chat, cliente_emb, sobreviveu

    cliente_chat, cliente_emb, sobreviveu = asyncio.run(_corpo())
    assert cliente_chat is not cliente_emb
    assert sobreviveu is cliente_emb
