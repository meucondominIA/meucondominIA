"""Integração: prova que as constraints da Fase 1 barram de verdade no Postgres.

Cada teste roda numa transação revertida no fim (isolamento). A violação
esperada é envolta num savepoint (async with conn.transaction()) para não
abortar a transação externa. As conexões registram o mesmo codec jsonb do
db.py, então os repositórios reais rodam como em produção.

Marcados 'integration' -> deselecionados no `pytest` padrão; rode com
`pytest -m integration` (precisa de Docker).
"""

import asyncio

import asyncpg
import pytest

import db
from dedup import registrar_mensagem
from mensagens import registrar_saida, upsert_conversa_ativa

pytestmark = pytest.mark.integration


def _run(dsn, body):
    async def _corpo():
        conn = await asyncpg.connect(dsn)
        await db._registrar_codecs(conn)
        try:
            tr = conn.transaction()
            await tr.start()
            try:
                await body(conn)
            finally:
                await tr.rollback()
        finally:
            await conn.close()

    asyncio.run(_corpo())


def test_uq_mensagens_message_id_bloqueia_duplicata(pg_dsn):
    async def body(conn):
        cid = await upsert_conversa_ativa(conn, "5511999990001")
        await conn.execute(
            "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id) "
            "values ($1, 'morador', 'text', 'oi', 'M-DUP')",
            cid,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id) "
                    "values ($1, 'morador', 'text', 'de novo', 'M-DUP')",
                    cid,
                )
        # message_id NULL fica FORA do índice parcial -> pode repetir à vontade
        await conn.execute(
            "insert into mensagens (conversa_id, papel, conteudo) values ($1, 'assistente', 'a')",
            cid,
        )
        await conn.execute(
            "insert into mensagens (conversa_id, papel, conteudo) values ($1, 'assistente', 'b')",
            cid,
        )

    _run(pg_dsn, body)


def test_uq_mensagens_em_resposta_a_bloqueia_segunda_saida(pg_dsn):
    async def body(conn):
        cid = await upsert_conversa_ativa(conn, "5511999990002")
        entrada = await conn.fetchval(
            "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id) "
            "values ($1, 'morador', 'text', 'oi', 'M2') returning id",
            cid,
        )
        await registrar_saida(conn, cid, "Eco: oi", entrada)

        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into mensagens (conversa_id, papel, conteudo, em_resposta_a) "
                    "values ($1, 'assistente', 'Eco: oi 2', $2)",
                    cid,
                    entrada,
                )

        # registrar_saida de novo (ON CONFLICT DO NOTHING): não estoura nem duplica
        await registrar_saida(conn, cid, "Eco: oi 3", entrada)
        n = await conn.fetchval(
            "select count(*) from mensagens where em_resposta_a = $1", entrada
        )
        assert n == 1

    _run(pg_dsn, body)


def test_uq_conversas_telefone_ativa_uma_por_vez(pg_dsn):
    async def body(conn):
        id1 = await upsert_conversa_ativa(conn, "5511999990003")
        id2 = await upsert_conversa_ativa(conn, "5511999990003")
        assert id1 == id2  # ON CONFLICT devolveu a MESMA conversa ativa

        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into conversas (telefone, status) values ($1, 'ativa')",
                    "5511999990003",
                )

        # 'encerrada' fica FORA do índice parcial (where status='ativa') -> coexiste
        await conn.execute(
            "insert into conversas (telefone, status) values ($1, 'encerrada')",
            "5511999990003",
        )
        await conn.execute(
            "insert into conversas (telefone, status) values ($1, 'encerrada')",
            "5511999990003",
        )

    _run(pg_dsn, body)


def test_chk_mensagens_assistente_exige_conteudo(pg_dsn):
    async def body(conn):
        cid = await upsert_conversa_ativa(conn, "5511999990004")

        # assistente SEM conteúdo -> CHECK barra
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into mensagens (conversa_id, papel, conteudo) "
                    "values ($1, 'assistente', null)",
                    cid,
                )

        # morador SEM conteúdo -> OK (mídia 'unsupported' chega sem texto)
        await conn.execute(
            "insert into mensagens (conversa_id, papel, tipo, conteudo) "
            "values ($1, 'morador', 'unsupported', null)",
            cid,
        )

    _run(pg_dsn, body)


def test_webhook_events_dedup_atomico(pg_dsn):
    async def body(conn):
        assert await registrar_mensagem(conn, "W-1", {"a": 1}) is True
        assert await registrar_mensagem(conn, "W-1", {"a": 2}) is False  # ON CONFLICT

        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into webhook_events (message_id, payload) values ('W-1', '{}')"
                )

        # o payload volta como dict (codec jsonb), não como texto
        payload = await conn.fetchval(
            "select payload from webhook_events where message_id = 'W-1'"
        )
        assert payload == {"a": 1}

    _run(pg_dsn, body)
