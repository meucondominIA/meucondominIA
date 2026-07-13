"""Integração de concorrência: o FOR UPDATE SKIP LOCKED do sweeper.

Precisa de 2 conexões simultâneas: A insere uma órfã 'pendente', a TRANCA e
segura; B roda a query REAL do sweeper (_SQL_LOTE_ORFAS) e prova que PULA a
linha travada — e volta a enxergá-la quando A solta o lock (prova que o pulo
foi por causa do lock, não do filtro de carência).

Marcado 'integration' -> deselecionado no `pytest` padrão; rode com
`pytest -m integration` (precisa de Docker).
"""

import asyncio

import asyncpg
import pytest

import db
import sweeper

pytestmark = pytest.mark.integration


def test_skip_locked_pula_linha_travada(pg_dsn):
    async def corpo():
        a = await asyncpg.connect(pg_dsn)
        b = await asyncpg.connect(pg_dsn)
        await db._registrar_codecs(a)
        await db._registrar_codecs(b)
        try:
            # órfã 'pendente' antiga o bastante p/ passar a carência, COMMITADA (autocommit)
            await a.execute(
                "insert into webhook_events (message_id, payload, status, recebido_em) "
                "values ('SKIP-1', '{}', 'pendente', now() - interval '1 hour')"
            )

            # A tranca a linha e SEGURA (transação aberta)
            tr = a.transaction()
            await tr.start()
            travadas = await a.fetch(
                "select message_id from webhook_events "
                "where message_id = 'SKIP-1' for update"
            )
            assert len(travadas) == 1

            # B roda a query real do sweeper (grace=0, batch=10) -> deve PULAR SKIP-1
            com_lock = await b.fetch(sweeper._SQL_LOTE_ORFAS, 0.0, 10)
            assert all(row["message_id"] != "SKIP-1" for row in com_lock)

            # A solta o lock
            await tr.rollback()

            # agora B enxerga a linha -> o pulo anterior foi por causa do lock
            sem_lock = await b.fetch(sweeper._SQL_LOTE_ORFAS, 0.0, 10)
            assert any(row["message_id"] == "SKIP-1" for row in sem_lock)
        finally:
            await a.execute("delete from webhook_events where message_id = 'SKIP-1'")
            await a.close()
            await b.close()

    asyncio.run(corpo())
