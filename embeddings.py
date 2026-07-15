"""Adapter de embeddings da OpenAI (2º serviço externo; espelho do zpro_client.py).

O core só conhece gerar_embeddings(textos, caminho=...) -> vetores; modelo,
dimensões, timeout e retry vêm da config e vivem aqui. O timeout do SDK é POR
TENTATIVA e o retry é do próprio SDK (conexão/408/409/429/>=500, backoff
exponencial): busca 3s × 2 tentativas (~7s pior caso), ingestão 60s × 3
tentativas (~3min pior caso).

O 3s da busca é corte de cauda, não margem de segurança: a latência real é
bimodal (p50 ~590ms, p95 ~780ms, e ~2% travam em 5-6s). Cortar em 3s e retentar
chega antes de esperar a chamada travada — medido em 500 chamadas, 15/07/2026.

Erros do SDK (openai.*) sobem crus, como o HTTPStatusError no zpro_client;
EmbeddingRespostaError é nossa: resposta 2xx que viola o contrato (contagem,
índices ou dimensão). Precisão float32 é do codec pgvector (db.py), não daqui.

Ciclo de vida idêntico a db.py/zpro_client.py: criar no startup, fechar no
shutdown.
"""

from typing import Literal

import httpx
from openai import AsyncOpenAI

from config import settings

_cliente: AsyncOpenAI | None = None

Caminho = Literal["busca", "ingestao"]

_MAX_ITENS_POR_CHAMADA = 2048


async def criar_cliente(http_client: httpx.AsyncClient | None = None) -> None:
    """Abre o cliente OpenAI. Chamado UMA vez, no startup da aplicação."""
    global _cliente
    if _cliente is None:
        _cliente = AsyncOpenAI(
            api_key=settings.openai_api_key,
            http_client=http_client,
        )


async def fechar_cliente() -> None:
    """Fecha o cliente. Chamado no shutdown da aplicação."""
    global _cliente
    if _cliente is None:
        return
    await _cliente.close()
    _cliente = None


def get_cliente() -> AsyncOpenAI:
    """Devolve o cliente já aberto. Erro claro se chamado antes do startup."""
    if _cliente is None:
        raise RuntimeError(
            "Cliente OpenAI não inicializado — chame criar_cliente() no startup."
        )
    return _cliente


class EmbeddingRespostaError(Exception):
    """Resposta 2xx que viola o contrato: contagem, índices ou dimensão errados."""


def _opcoes(caminho: Caminho) -> tuple[float, int]:
    if caminho == "busca":
        return settings.openai_timeout_busca_seconds, settings.openai_retries_busca
    return settings.openai_timeout_ingestao_seconds, settings.openai_retries_ingestao


async def gerar_embeddings(textos: list[str], *, caminho: Caminho) -> list[list[float]]:
    """Vetoriza os textos na MESMA ordem da entrada (remontada pelo index)."""
    if not textos:
        return []
    if len(textos) > _MAX_ITENS_POR_CHAMADA:
        raise ValueError(
            f"no máximo {_MAX_ITENS_POR_CHAMADA} textos por chamada; "
            f"recebi {len(textos)}"
        )
    if any(not texto.strip() for texto in textos):
        raise ValueError("texto vazio ou só espaços não pode virar embedding")

    timeout, retries = _opcoes(caminho)
    resposta = await (
        get_cliente()
        .with_options(timeout=timeout, max_retries=retries)
        .embeddings.create(
            input=textos,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            encoding_format="float",
        )
    )
    if len(resposta.data) != len(textos):
        raise EmbeddingRespostaError(
            f"contagem: enviei {len(textos)} textos, vieram {len(resposta.data)} embeddings"
        )

    por_indice = {item.index: item.embedding for item in resposta.data}
    if sorted(por_indice) != list(range(len(textos))):
        raise EmbeddingRespostaError(
            f"índices da resposta não cobrem 0..{len(textos) - 1}"
        )

    for indice, vetor in por_indice.items():
        if len(vetor) != settings.embedding_dimensions:
            raise EmbeddingRespostaError(
                f"dimensão no índice {indice}: {len(vetor)} != "
                f"{settings.embedding_dimensions}"
            )

    return [por_indice[i] for i in range(len(textos))]
