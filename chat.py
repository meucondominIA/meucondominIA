"""Adapter de chat da OpenAI (3º serviço externo; espelho de embeddings.py).

Cliente PRÓPRIO, não o de embeddings.py, de propósito: a Fase 3 promete que
trocar de provedor de LLM custa um arquivo. Se o chat pedisse o cliente ao
adapter de embeddings, trocar o provedor de chat obrigaria a editar
embeddings.py — e a promessa deixaria de valer por construção.

O preço são dois pools HTTP para o mesmo host: o chat não reaproveita a conexão
que o embedding acabou de abrir, ~170ms de handshake por dúvida (medida ruidosa,
n=4, 21/07/2026; keepalive_expiry do SDK é 5s e as duas chamadas distam ~1s).
Compartilhar exigiria o main.py virar dono do httpx.AsyncClient e os adapters
pararem de fechar cliente injetado — mudança de responsabilidade, não injeção
inocente. Fica para o passo 8 decidir com o eval na mão.

Passo 1 entrega só o ciclo de vida (criar no startup, fechar no shutdown). A
geração — Responses API, store=False, modelo/esforço/verbosidade/timeout por
Settings — é do passo 4, e nasce aqui.
"""

import httpx
from openai import AsyncOpenAI

from config import settings

_cliente: AsyncOpenAI | None = None


async def criar_cliente(http_client: httpx.AsyncClient | None = None) -> None:
    """Abre o cliente OpenAI de chat. Chamado UMA vez, no startup da aplicação."""
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
            "Cliente de chat não inicializado — chame criar_cliente() no startup."
        )
    return _cliente
