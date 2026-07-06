"""Endpoint de entrada do webhook do Z-PRO (Fase 2.0 · Peça C).

Estratégia ACK-first: a rota valida o segredo da URL, lê o corpo cru, responde
200 na hora e empurra TODO o processamento para uma BackgroundTask — o Z-PRO
espera resposta rápida. Falhas de parsing/processamento são tratadas e logadas
no background, sem virar 500.

##1. Ter uma porta - uma rota que o ZPRO chama e e que responde "ok"
##2. Ler a mensagem que chegou nessa porta
##3. Responder rapido e deixar ot rabalho pesado pra depois
##4. PProteger a porta com um segredo
##5. Não processar a mesma mensagem duas vezes.
##6. Fazer algo com a mensagem
"""

import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import ValidationError

from config import settings
from db import get_pool
from dedup import registrar_mensagem
from zpro_models import IgnoreMessage, IncomingMessage, parse_zpro_webhook

logger = logging.getLogger(__name__)

router = APIRouter()


async def processar_mensagem(msg: IncomingMessage) -> None:

    logger.info(
        "processar_mensagem (stub): message_id=%s phone=%s",
        msg.message_id,
        msg.phone,
    )


async def _consumir(raw_body: bytes) -> None:
    """Processa o webhook FORA do ciclo da resposta (roda como BackgroundTask)."""
    try:
        raw = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("webhook: corpo não é JSON válido — descartado")
        return

    try:
        msg = parse_zpro_webhook(raw)
    except IgnoreMessage as exc:
        logger.info("webhook ignorado: %s", exc)
        return
    except ValidationError as exc:
        logger.warning("webhook suspeito (payload fora do formato Z-PRO): %s", exc)
        return

    logger.info("webhook recebido: message_id=%s phone=%s", msg.message_id, msg.phone)

    try:
        async with get_pool().acquire() as conn:
            novo = await registrar_mensagem(conn, msg.message_id)

        if not novo:
            logger.info("webhook duplicado — ignorado: message_id=%s", msg.message_id)
            return

        await processar_mensagem(msg)
    except Exception:
        logger.exception("webhook: falha ao processar message_id=%s", msg.message_id)
        return

    logger.info("webhook processado: message_id=%s", msg.message_id)


##Porta
@router.post("/webhook/{secret}")
async def receber_webhook(
    secret: str, request: Request, background_tasks: BackgroundTasks
) -> dict[str, str]:
    # URL secreta: comparação em tempo constante. Segredo errado -> 404 (sem revelar o motivo).
    if not hmac.compare_digest(
        secret.encode("utf-8"), settings.webhook_secret.encode("utf-8")
    ):
        raise HTTPException(status_code=404)

    # Lê o corpo cru AGORA: depois da resposta o stream pode não estar mais disponível.
    raw_body = await request.body()
    background_tasks.add_task(_consumir, raw_body)
    return {"status": "ok"}
