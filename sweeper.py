"""Sweeper do inbox (passo 2): recupera mensagens 'pendente' órfãs.

Uma linha fica órfã quando o processo morre entre o 200 (INSERT commitado) e a
marcação do desfecho — a BackgroundTask vive só na memória do worker. Cada ciclo
abre UMA transação: tranca um lote de 'pendente' antigas com FOR UPDATE SKIP
LOCKED, reprocessa e marca o desfecho na MESMA transação. Crash no meio =>
rollback => as linhas seguem 'pendente' para o próximo ciclo (at-least-once).

- SKIP LOCKED: consumidores concorrentes pulam linhas já trancadas em vez de
  esperar (padrão de fila da doc do SELECT).
- Carência (grace): só pega linhas mais velhas que N segundos, para não
  disputar com BackgroundTasks ainda em voo.
- O status vai como LITERAL no SQL: índice parcial não casa com parâmetro
  ("parameterized query clauses do not work with a partial index").
"""

import asyncio
import logging

from config import settings
from db import get_pool
from dedup import StatusEvento, marcar_status
from webhook import processar_mensagem
from zpro_models import parse_zpro_webhook

logger = logging.getLogger(__name__)

_SQL_LOTE_ORFAS = f"""
    select message_id, payload
      from webhook_events
     where status = '{StatusEvento.PENDENTE.value}'
       and recebido_em < now() - make_interval(secs => $1)
     order by recebido_em
     limit $2
       for update skip locked
"""


async def _reprocessar(message_id: str, payload: dict) -> StatusEvento:
    """Reparseia o payload cru e roda o processamento; devolve o desfecho."""
    try:
        msg = parse_zpro_webhook(payload)
        await processar_mensagem(msg)
    except Exception:
        logger.exception("sweeper: reprocessamento falhou: message_id=%s", message_id)
        return StatusEvento.FALHOU
    return StatusEvento.PROCESSADO


async def varrer_pendentes() -> int:
    """Um ciclo do sweeper. Devolve quantas órfãs foram tratadas."""
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                _SQL_LOTE_ORFAS,
                float(settings.sweeper_grace_seconds),
                settings.sweeper_batch_size,
            )
            for row in rows:
                status = await _reprocessar(row["message_id"], row["payload"])
                await marcar_status(conn, row["message_id"], status)
    if rows:
        logger.info("sweeper: %d órfã(s) tratada(s)", len(rows))
    return len(rows)


async def rodar_sweeper() -> None:
    """Loop do sweeper: um ciclo, dorme, repete. Cancelado no shutdown."""
    while True:
        try:
            await varrer_pendentes()
        except Exception:
            logger.exception("sweeper: ciclo abortado — tenta no próximo")
        await asyncio.sleep(settings.sweeper_interval_seconds)
