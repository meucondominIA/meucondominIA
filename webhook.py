"""Endpoint de entrada do webhook do Z-PRO — padrão inbox durável (Fase 1).

Estratégia ACK-after-durable: a rota valida o segredo, parseia o corpo e PERSISTE
o payload cru em webhook_events ANTES de responder 200. O 200 só sai quando a
linha está commitada (durável) — um crash logo após o ACK não perde a mensagem.
O trabalho pesado (processar_mensagem) roda depois, numa BackgroundTask.

Caminhos de resposta:
- segredo errado           -> 404
- não é mensagem real      -> 200 (eco/grupo/JSON inválido/fora do formato; nada a guardar)
- falha ao persistir       -> 503 (não confirma o que não gravou; provedor pode reenviar)
- mensagem nova persistida -> 200 + agenda processamento
- reentrega (duplicata)    -> 200 sem reprocessar (gate pelo RETURNING)
"""

import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import ValidationError

from config import settings
from db import get_pool
from dedup import StatusEvento, marcar_status, registrar_mensagem
from processador import processar_mensagem
from zpro_models import IgnoreMessage, IncomingMessage, parse_zpro_webhook

logger = logging.getLogger(__name__)

router = APIRouter()


async def _processar_e_marcar(msg: IncomingMessage) -> None:
    """Roda o processamento e grava o desfecho na linha (roda pós-ACK, no background)."""
    try:
        await processar_mensagem(msg)
        status = StatusEvento.PROCESSADO
    except Exception:
        logger.exception("processamento falhou: message_id=%s", msg.message_id)
        status = StatusEvento.FALHOU

    try:
        async with get_pool().acquire() as conn:
            await marcar_status(conn, msg.message_id, status)
    except Exception:
        logger.exception("falha ao marcar status: message_id=%s", msg.message_id)


def _extrair_mensagem(raw_body: bytes) -> tuple[IncomingMessage, dict] | None:
    """Parseia o corpo cru; devolve (msg, raw) ou None quando não é mensagem real."""
    try:
        raw = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("webhook: corpo não é JSON válido — descartado")
        return None

    try:
        msg = parse_zpro_webhook(raw)
    except IgnoreMessage as exc:
        logger.info("webhook ignorado: %s", exc)
        return None
    except ValidationError as exc:
        logger.warning("webhook suspeito (payload fora do formato Z-PRO): %s", exc)
        return None

    return msg, raw


@router.post("/webhook/{secret}")
async def receber_webhook(
    secret: str, request: Request, background_tasks: BackgroundTasks
) -> dict[str, str]:
    # URL secreta: comparação em tempo constante. Segredo errado -> 404 (sem revelar o motivo).
    if not hmac.compare_digest(
        secret.encode("utf-8"), settings.webhook_secret.encode("utf-8")
    ):
        raise HTTPException(status_code=404)

    raw_body = await request.body()

    extraido = _extrair_mensagem(raw_body)
    if extraido is None:
        return {"status": "ignored"}
    msg, raw = extraido

    try:
        async with get_pool().acquire() as conn:
            novo = await registrar_mensagem(conn, msg.message_id, raw)
    except Exception:
        logger.exception("webhook: falha ao persistir message_id=%s", msg.message_id)
        raise HTTPException(status_code=503)

    if novo:
        background_tasks.add_task(_processar_e_marcar, msg)
    else:
        logger.info("webhook duplicado — não reprocessa: message_id=%s", msg.message_id)

    return {"status": "ok"}
