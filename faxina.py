"""Varredura de anexos órfãos no Storage (Fase 4 · Etapa 4).

A foto sobe ANTES da confirmação, porque o processamento é sem memória entre
mensagens: os bytes chegam numa requisição e o rascunho da seguinte só consegue
carregar a coordenada. Quem cancela no "2" — ou simplesmente abandona — deixa o
arquivo sem dono. Medido na primeira sessão real: 1 órfã em 3 uploads.

Job, e não limpeza no cancelamento, porque abandono não gera evento nenhum: a
conversa só expira. Só uma varredura pega os três casos (cancelou, abandonou,
sessão expirou).

Duas fontes de "tem dono", e a segunda é a que evita o desastre: além de
solicitacoes.anexos, o rascunho de uma conversa VIVA também referencia o arquivo.
Sem essa checagem a varredura apagaria a foto de um wizard em andamento.

Regra de ouro: a busca usa conexão, a remoção é rede — e elas não se sobrepõem.
"""

import asyncio
import logging

import anexos
from config import settings
from db import get_pool

logger = logging.getLogger(__name__)

_ORFAOS = """
    select o.name
      from storage.objects o
     where o.bucket_id = $1
       and o.created_at < now() - make_interval(hours => $2)
       and not exists (
             select 1
               from solicitacoes s
               cross join lateral jsonb_array_elements(s.anexos) a
              where a->>'caminho' = o.name
           )
       and not exists (
             select 1
               from conversas c
               cross join lateral jsonb_array_elements(
                     case when jsonb_typeof(c.rascunho->'anexos') = 'array'
                          then c.rascunho->'anexos' else '[]'::jsonb end) a
              where a->>'caminho' = o.name
           )
     order by o.created_at
     limit $3
"""


async def _listar_orfaos() -> list[str]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            _ORFAOS,
            settings.anexos_bucket,
            settings.anexo_orfao_horas,
            settings.faxina_batch_size,
        )
    return [r["name"] for r in rows]


async def limpar_orfaos() -> int:
    """Um ciclo: acha os anexos sem dono e apaga. Devolve quantos.

    A conexão é devolvida ANTES da chamada de rede — por isso as duas etapas são
    funções separadas, e não um `async with` em volta de tudo.
    """
    orfaos = await _listar_orfaos()
    if not orfaos:
        return 0

    await anexos.apagar(orfaos)
    logger.info("faxina: %d anexo(s) órfão(s) removido(s)", len(orfaos))
    return len(orfaos)


async def rodar_faxina() -> None:
    """Loop da faxina: um ciclo, dorme, repete. Cancelado no shutdown."""
    while True:
        try:
            await limpar_orfaos()
        except Exception:
            logger.exception("faxina: ciclo abortado — tenta no próximo")
        await asyncio.sleep(settings.faxina_interval_seconds)
