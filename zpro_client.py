"""Adapter de saída do Z-PRO (espelho do zpro_models.py, sentido inverso).

O core só conhece OutgoingMessage; o formato do body da API externa v2 vive
aqui. A API endereça POR NÚMERO e abre/reutiliza ticket sozinha. A resposta
não traz id de mensagem do WhatsApp — só ticketId, que se repete por conversa:
vai para log, nunca para mensagens.message_id. externalKey NÃO deduplica no
provedor (verificado 09/07/2026); é só correlação/rastreio.

Ciclo de vida idêntico a db.py: criar no startup, fechar no shutdown.
O transport retenta SÓ falha de conexão (request ainda não saiu — não duplica
eco); não-2xx vira HTTPStatusError e sobe para o chamador marcar 'falhou'.
"""

import logging

import httpx
from pydantic import BaseModel, ConfigDict

from config import settings

logger = logging.getLogger(__name__)

_cliente: httpx.AsyncClient | None = None


class OutgoingMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    phone: str  # só dígitos com DDI: "555592372732"
    text: str
    external_key: str  # message_id da entrada — correlação, não idempotência


async def criar_cliente(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Abre o cliente HTTP. Chamado UMA vez, no startup da aplicação."""
    global _cliente
    if _cliente is None:
        _cliente = httpx.AsyncClient(
            base_url=settings.zpro_api_url,
            headers={"Authorization": f"Bearer {settings.zpro_api_token}"},
            timeout=httpx.Timeout(10.0, connect=5.0),
            transport=transport or httpx.AsyncHTTPTransport(retries=2),
        )


async def fechar_cliente() -> None:
    """Fecha o cliente. Chamado no shutdown da aplicação."""
    global _cliente
    if _cliente is None:
        return
    await _cliente.aclose()
    _cliente = None


def get_cliente() -> httpx.AsyncClient:
    """Devolve o cliente já aberto. Erro claro se chamado antes do startup."""
    if _cliente is None:
        raise RuntimeError(
            "Cliente HTTP não inicializado — chame criar_cliente() no startup."
        )
    return _cliente


class ZproEnvioError(Exception):
    """Resposta 2xx cujo body declara falha (success=false) — envio NÃO aconteceu."""


async def enviar(msg: OutgoingMessage) -> None:
    """POST {ZPRO_API_URL}/ — HTTPStatusError em não-2xx; ZproEnvioError em success=false.

    Body sem JSON ou sem o campo success segue como sucesso (não inventamos
    falha onde o contrato verificado não declara uma).
    """
    resp = await get_cliente().post(
        "/",
        json={
            "body": msg.text,
            "number": msg.phone,
            "externalKey": msg.external_key,
            "isClosed": False,
        },
    )
    resp.raise_for_status()

    try:
        corpo = resp.json()
    except ValueError:
        corpo = None
        logger.warning("zpro: resposta 2xx sem JSON — seguindo como sucesso")

    if isinstance(corpo, dict) and corpo.get("success") is False:
        raise ZproEnvioError(f"Z-PRO respondeu 2xx com success=false: {corpo}")

    data = corpo.get("data") if isinstance(corpo, dict) else None
    ticket_id = data.get("ticketId") if isinstance(data, dict) else None
    logger.info(
        "zpro: enviado external_key=%s ticket_id=%s", msg.external_key, ticket_id
    )
