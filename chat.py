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

A geração usa a Responses API num subconjunto simples: store=False (a resposta
NÃO fica armazenada na OpenAI — 404 no retrieve; invariante LGPD), sem tool
calling e sem previous_response_id. Modelo, esforço, verbosidade, timeout e
retry vêm de Settings. Anti-corrupção estrita: nenhum tipo do SDK atravessa a
fronteira — nem na entrada (MensagemChat nossa), nem na saída (str), nem no erro
(openai.APIError vira ChatIndisponivelError). É mais rígido que embeddings.py,
que deixa openai.* subir cru — dívida registrada; alinhar embeddings é à parte.

A guarda de leitura checa `status == "completed"` PRIMEIRO: uma resposta cortada
(incomplete) devolve output_text PARCIAL não-vazio — mandá-lo ao morador seria
uma frase truncada no meio da palavra (medido, 22/07/2026). output_text vazio
(recusa / só-raciocínio) é a guarda secundária.
"""

from enum import Enum

import httpx
import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from config import settings

_cliente: AsyncOpenAI | None = None


class PapelChat(str, Enum):
    USUARIO = "user"
    ASSISTENTE = "assistant"
    SISTEMA = "developer"


class MensagemChat(BaseModel):
    model_config = ConfigDict(frozen=True)

    papel: PapelChat
    conteudo: str


class ChatRespostaError(Exception):
    """Resposta 2xx que viola o contrato: status != completed ou sem texto."""


class ChatIndisponivelError(Exception):
    """Falha de transporte/status do SDK (timeout, conexão, não-2xx), embrulhada."""


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


async def gerar_resposta(mensagens: list[MensagemChat]) -> str:
    """A resposta do modelo às mensagens, na mesma ordem. Só rede, nenhum banco:
    o chamador invoca fora de qualquer conexão/transação (regra de ouro)."""
    if not mensagens:
        raise ValueError("lista de mensagens vazia")
    if any(not m.conteudo.strip() for m in mensagens):
        raise ValueError("mensagem com conteúdo vazio ou só espaços")

    try:
        resposta = await (
            get_cliente()
            .with_options(
                timeout=settings.openai_timeout_chat_seconds,
                max_retries=settings.openai_retries_chat,
            )
            .responses.create(
                model=settings.openai_chat_model,
                input=[{"role": m.papel.value, "content": m.conteudo} for m in mensagens],
                reasoning={"effort": settings.openai_chat_effort},
                text={"verbosity": settings.openai_chat_verbosity},
                store=False,
            )
        )
    except openai.APIError as e:
        raise ChatIndisponivelError("chat indisponível") from e

    if resposta.status != "completed":
        raise ChatRespostaError(
            f"status {resposta.status!r} (não 'completed'): {resposta.incomplete_details}"
        )
    texto = resposta.output_text
    if not texto.strip():
        raise ChatRespostaError("resposta 2xx sem texto (output_text vazio)")
    return texto
