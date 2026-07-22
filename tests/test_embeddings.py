"""Testes do adapter de embeddings da OpenAI (Fase 2 · Passo 2).

Sem rede: httpx.MockTransport dentro de um httpx.AsyncClient injetado no
AsyncOpenAI. Criar/usar/fechar acontecem no MESMO event loop (asyncio.run),
espelhando test_zpro_client.py.

Doc: https://www.python-httpx.org/advanced/transports/#mock-transports
"""

import asyncio
import json

import httpx
import openai
import pytest

import embeddings
from config import settings

DIM = settings.embedding_dimensions


def _http_mock(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _handler_vazio(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={})


def _vetor(valor: float, dim: int = DIM) -> list[float]:
    return [valor] * dim


def _resposta_ok(pares: list[tuple[int, list[float]]]) -> dict:
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in pares],
        "model": settings.embedding_model,
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }


async def _roda(handler, textos: list[str], caminho: str = "busca"):
    await embeddings.criar_cliente(http_client=_http_mock(handler))
    try:
        return await embeddings.gerar_embeddings(textos, caminho=caminho)
    finally:
        await embeddings.fechar_cliente()


def test_um_input_devolve_um_vetor():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_ok([(0, _vetor(0.5))]))

    resultado = asyncio.run(_roda(handler, ["qual o horário da piscina?"]))
    assert resultado == [_vetor(0.5)]


def test_n_inputs_ordem_reconstruida_pelo_index():
    def handler(request: httpx.Request) -> httpx.Response:
        embaralhado = [(2, _vetor(2.0)), (0, _vetor(0.0)), (1, _vetor(1.0))]
        return httpx.Response(200, json=_resposta_ok(embaralhado))

    resultado = asyncio.run(_roda(handler, ["a", "b", "c"]))
    assert resultado == [_vetor(0.0), _vetor(1.0), _vetor(2.0)]


def test_request_segue_contrato():
    capturados: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturados.append(request)
        return httpx.Response(200, json=_resposta_ok([(0, _vetor(1.0))]))

    asyncio.run(_roda(handler, ["texto"], caminho="busca"))

    req = capturados[0]
    assert req.method == "POST"
    assert req.url.path == "/v1/embeddings"
    assert req.headers["Authorization"] == f"Bearer {settings.openai_api_key}"
    assert json.loads(req.content) == {
        "input": ["texto"],
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
        "encoding_format": "float",
    }
    assert req.extensions["timeout"]["read"] == settings.openai_timeout_busca_seconds


def test_caminho_ingestao_usa_timeout_de_ingestao():
    capturados: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturados.append(request)
        return httpx.Response(200, json=_resposta_ok([(0, _vetor(1.0))]))

    asyncio.run(_roda(handler, ["texto"], caminho="ingestao"))

    timeout = capturados[0].extensions["timeout"]["read"]
    assert timeout == settings.openai_timeout_ingestao_seconds


def test_contagem_divergente_levanta_erro_de_contrato():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_ok([(0, _vetor(0.0))]))

    with pytest.raises(embeddings.EmbeddingRespostaError, match="contagem"):
        asyncio.run(_roda(handler, ["a", "b"]))


def test_indice_duplicado_levanta_erro_de_contrato():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_resposta_ok([(0, _vetor(0.0)), (0, _vetor(1.0))])
        )

    with pytest.raises(embeddings.EmbeddingRespostaError, match="índices"):
        asyncio.run(_roda(handler, ["a", "b"]))


def test_dimensao_divergente_levanta_erro_de_contrato():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_resposta_ok([(0, _vetor(0.0, dim=8))]))

    with pytest.raises(embeddings.EmbeddingRespostaError, match="dimensão"):
        asyncio.run(_roda(handler, ["a"]))


def _handler_contador(chamadas: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(200, json=_resposta_ok([]))

    return handler


def test_lista_vazia_devolve_vazio_sem_chamar_api():
    chamadas: list[httpx.Request] = []

    resultado = asyncio.run(_roda(_handler_contador(chamadas), []))

    assert resultado == []
    assert chamadas == []


def test_string_vazia_levanta_valueerror_sem_chamar_api():
    chamadas: list[httpx.Request] = []

    with pytest.raises(ValueError, match="vazio"):
        asyncio.run(_roda(_handler_contador(chamadas), [""]))

    assert chamadas == []


def test_string_so_espacos_levanta_valueerror_sem_chamar_api():
    chamadas: list[httpx.Request] = []

    with pytest.raises(ValueError, match="vazio"):
        asyncio.run(_roda(_handler_contador(chamadas), ["ok", "   "]))

    assert chamadas == []


def test_acima_do_teto_levanta_valueerror_sem_chamar_api():
    chamadas: list[httpx.Request] = []

    with pytest.raises(ValueError, match="2048"):
        asyncio.run(_roda(_handler_contador(chamadas), ["x"] * 2049))

    assert chamadas == []


def test_teto_exato_2048_passa_pela_borda():
    chamadas: list[httpx.Request] = []

    with pytest.raises(embeddings.EmbeddingRespostaError):
        asyncio.run(_roda(_handler_contador(chamadas), ["x"] * 2048))

    assert len(chamadas) == 1


def test_erro_do_sdk_vira_embedding_indisponivel_com_causa():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "Incorrect API key provided",
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "invalid_api_key",
                }
            },
        )

    with pytest.raises(embeddings.EmbeddingIndisponivelError) as excinfo:
        asyncio.run(_roda(handler, ["a"]))

    assert isinstance(excinfo.value.__cause__, openai.AuthenticationError)


def _handler_500(chamadas: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        chamadas.append(request)
        return httpx.Response(
            500,
            headers={"retry-after-ms": "1"},
            json={"error": {"message": "server error", "type": "server_error"}},
        )

    return handler


def test_busca_usa_os_retries_do_caminho_busca():
    chamadas: list[httpx.Request] = []

    with pytest.raises(embeddings.EmbeddingIndisponivelError):
        asyncio.run(_roda(_handler_500(chamadas), ["a"], caminho="busca"))

    assert len(chamadas) == 1 + settings.openai_retries_busca


def test_ingestao_usa_os_retries_do_caminho_ingestao():
    chamadas: list[httpx.Request] = []

    with pytest.raises(embeddings.EmbeddingIndisponivelError):
        asyncio.run(_roda(_handler_500(chamadas), ["a"], caminho="ingestao"))

    assert len(chamadas) == 1 + settings.openai_retries_ingestao


def test_get_cliente_sem_startup_levanta_runtimeerror():
    asyncio.run(embeddings.fechar_cliente())
    with pytest.raises(RuntimeError):
        embeddings.get_cliente()


def test_criar_cliente_e_idempotente():
    async def _corpo():
        await embeddings.fechar_cliente()
        await embeddings.criar_cliente(http_client=_http_mock(_handler_vazio))
        primeiro = embeddings.get_cliente()
        await embeddings.criar_cliente(http_client=_http_mock(_handler_vazio))
        segundo = embeddings.get_cliente()
        await embeddings.fechar_cliente()
        return primeiro, segundo

    primeiro, segundo = asyncio.run(_corpo())
    assert primeiro is segundo


def test_fechar_cliente_sem_cliente_nao_estoura():
    asyncio.run(embeddings.fechar_cliente())
    asyncio.run(embeddings.fechar_cliente())
