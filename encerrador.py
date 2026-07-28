"""Encerramento de conversas ociosas (Fase 4).

Fecha o atendimento do NOSSO lado quando passa do TTL. Quem avisa o morador é o
Z-PRO ("Resolver atendimento sem interação"), então aqui não há rede — é um flip
de coluna, e por isso o job não esbarra na regra de ouro.

Job e não preguiça: fechar só quando a pessoa volta deixaria a conversa
abandonada `ativa` para sempre, e é justamente ela que a métrica precisa contar
como atendimento encerrado.

Encerrar tira a linha do uq_conversas_telefone_ativa (índice parcial em
status='ativa') e libera a vaga para a próxima mensagem abrir outra conversa.
Nada é apagado: a linha vira unidade de contagem e de retenção.

UPDATE simples basta sob concorrência. Em READ COMMITTED, "the search condition
of the command (the WHERE clause) is re-evaluated to see if the updated version
of the row still matches" (transaction-iso.html): o segundo job espera o lock,
reavalia, vê 'encerrada' e não casa mais. Sem SKIP LOCKED como o sweeper — lá
cada linha vira trabalho, aqui é idempotente por construção.
"""

import asyncio
import logging

from config import settings
from db import get_pool

logger = logging.getLogger(__name__)

_ENCERRAR_OCIOSAS = """
    update conversas
       set status = 'encerrada'
     where status = 'ativa'
       and ultima_interacao_em < now() - make_interval(hours => $1)
    returning id
"""


async def encerrar_ociosas() -> int:
    """Um ciclo: fecha as conversas paradas há mais que o TTL. Devolve quantas."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(_ENCERRAR_OCIOSAS, settings.sessao_ttl_horas)
    if rows:
        logger.info("encerrador: %d conversa(s) ociosa(s) encerrada(s)", len(rows))
    return len(rows)


async def rodar_encerrador() -> None:
    """Loop do encerrador: um ciclo, dorme, repete. Cancelado no shutdown."""
    while True:
        try:
            await encerrar_ociosas()
        except Exception:
            logger.exception("encerrador: ciclo abortado — tenta no próximo")
        await asyncio.sleep(settings.encerrador_interval_seconds)
