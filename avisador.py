"""Worker do outbox de avisos ao síndico (Fase 4 · Etapa 5).

Três fases que nunca se sobrepõem, como a faxina — e ao contrário do sweeper,
que segura conexão e transação abertas enquanto chama a rede:

  1. reserva o lote   (conexão, transação curta)
  2. envia            (SEM conexão)
  3. marca enviado    (conexão)

Marcação POR AVISO, e não uma só no fim: mantém a janela de duplicata do tamanho
de um aviso em vez do lote inteiro. Falha de envio não aborta o lote — o aviso
segue pendente e a lease o devolve ao próximo ciclo.

Entrega at-least-once com UMA janela: crash entre a fase 2 e a 3 faz a lease
expirar e reenviar. Não fecha — o Z-PRO não honra idempotency-key (verificado
por envio real). "Exactly once" seria mentira.
"""

import asyncio
import logging
from uuid import UUID

from avisos import AvisoPendente, marcar_enviado, reservar_lote
from config import settings
from db import get_pool
from zpro_client import OutgoingMessage, enviar

logger = logging.getLogger(__name__)


async def _reservar() -> list[AvisoPendente]:
    """Fase 1: tranca, marca a lease e devolve o que enviar."""
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            return await reservar_lote(
                conn,
                lease_segundos=float(settings.aviso_lease_seconds),
                limite=settings.aviso_batch_size,
            )


async def _marcar(aviso_id: UUID) -> None:
    """Fase 3, numa conexão própria — a fase 2 não pode ter nenhuma na mão."""
    async with get_pool().acquire() as conn:
        await marcar_enviado(conn, aviso_id)


async def enviar_pendentes() -> int:
    """Um ciclo: reserva, envia, marca. Devolve quantos avisos saíram.

    `external_key` leva o id do AVISO — correlação no rastro do Z-PRO, não
    idempotência; essa mora no outbox.
    """
    lote = await _reservar()
    if not lote:
        return 0

    enviados = 0
    for aviso in lote:
        try:
            await enviar(
                OutgoingMessage(
                    phone=aviso.telefone,
                    text=aviso.texto,
                    external_key=str(aviso.id),
                )
            )
        except Exception:
            logger.exception("avisador: envio falhou — aviso=%s", aviso.id)
            continue
        await _marcar(aviso.id)
        enviados += 1

    logger.info("avisador: %d de %d aviso(s) enviado(s)", enviados, len(lote))
    return enviados


async def rodar_avisador() -> None:
    """Loop do avisador: um ciclo, dorme, repete. Cancelado no shutdown."""
    while True:
        try:
            await enviar_pendentes()
        except Exception:
            logger.exception("avisador: ciclo abortado — tenta no próximo")
        await asyncio.sleep(settings.aviso_interval_seconds)
