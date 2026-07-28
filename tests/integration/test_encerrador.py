"""Encerramento de conversas ociosas contra o Postgres real (Fase 4).

O que só o banco de verdade prova: que encerrar tira a linha do índice parcial e
libera a vaga; que a conversa reaberta nasce com o condomínio LEMBRADO; e que dois
jobs simultâneos fecham a mesma linha uma vez só — o argumento do READ COMMITTED
medido em vez de argumentado.

Marca 'integration' -> rode com `pytest -m integration` (precisa de Docker).
"""

import asyncio

import asyncpg
import pytest

import db
import encerrador
from config import settings
from mensagens import conversa_ativa

pytestmark = pytest.mark.integration

TEL = "5511999990300"


async def _semear(conn: asyncpg.Connection):
    return await conn.fetchval(
        "insert into condominios (slug, nome) values ('res-gabro', 'Gabro') "
        "returning id"
    )


async def _envelhecer(conn: asyncpg.Connection, telefone: str, horas: int):
    """Recua o relógio da conversa — mais barato que esperar 24h."""
    await conn.execute(
        "update conversas set ultima_interacao_em = now() - make_interval(hours => $2)"
        " where telefone = $1 and status = 'ativa'",
        telefone,
        horas,
    )


@pytest.fixture
def com_pool(pg_dsn, monkeypatch):
    """Pool real: o encerrador pega a própria conexão, então não cabe em rodar_tx."""

    def _rodar(passos):
        async def _corpo():
            pool = await asyncpg.create_pool(
                dsn=pg_dsn, min_size=1, max_size=4, init=db._registrar_codecs
            )
            monkeypatch.setattr(encerrador, "get_pool", lambda: pool)
            try:
                async with pool.acquire() as conn:
                    await conn.execute("truncate condominios cascade")
                    await conn.execute("truncate conversas cascade")
                await passos(pool)
            finally:
                async with pool.acquire() as conn:
                    await conn.execute("truncate condominios cascade")
                    await conn.execute("truncate conversas cascade")
                await pool.close()

        asyncio.run(_corpo())

    return _rodar


def test_ociosa_e_encerrada_e_recente_nao(com_pool):
    async def passos(pool):
        async with pool.acquire() as conn:
            await _semear(conn)
            await conversa_ativa(conn, TEL)
            await conversa_ativa(conn, "5511999990301")
            await _envelhecer(conn, TEL, settings.sessao_ttl_horas + 1)

        assert await encerrador.encerrar_ociosas() == 1

        async with pool.acquire() as conn:
            rows = await conn.fetch("select telefone, status from conversas")
        estados = {r["telefone"]: r["status"] for r in rows}

        assert estados[TEL] == "encerrada"
        assert estados["5511999990301"] == "ativa"

    com_pool(passos)


def test_encerrar_libera_a_vaga_do_indice_parcial(com_pool):
    """uq_conversas_telefone_ativa é parcial: encerrada sai do índice."""

    async def passos(pool):
        async with pool.acquire() as conn:
            await _semear(conn)
            primeira, nova = await conversa_ativa(conn, TEL)
            assert nova is True
            await _envelhecer(conn, TEL, settings.sessao_ttl_horas + 1)

        await encerrador.encerrar_ociosas()

        async with pool.acquire() as conn:
            segunda, nova = await conversa_ativa(conn, TEL)

        assert nova is True, "a vaga não foi liberada"
        assert segunda.id != primeira.id

    com_pool(passos)


def test_conversa_reaberta_lembra_o_condominio(com_pool):
    """O ganho combinado: quem volta ouve "É o Gabro?" em vez de refazer a lista."""

    async def passos(pool):
        async with pool.acquire() as conn:
            gabro = await _semear(conn)
            primeira, _ = await conversa_ativa(conn, TEL)
            await conn.execute(
                "update conversas set estado = 'menu', condominio_id = $2 "
                "where id = $1",
                primeira.id,
                gabro,
            )
            await _envelhecer(conn, TEL, settings.sessao_ttl_horas + 1)

        await encerrador.encerrar_ociosas()

        async with pool.acquire() as conn:
            segunda, nova = await conversa_ativa(conn, TEL)

        assert nova is True
        assert segunda.estado.value == "aguardando_confirmacao"
        assert segunda.condominio_pendente == gabro
        assert segunda.condominio_id is None, "tenant só volta depois do 1"

    com_pool(passos)


def test_telefone_sem_historico_ainda_cai_na_lista(com_pool):
    """Sem condomínio lembrado o CASE escolhe 'identificacao' — comportamento
    da Fase 1 preservado para quem nunca confirmou nada."""

    async def passos(pool):
        async with pool.acquire() as conn:
            await _semear(conn)
            conversa, nova = await conversa_ativa(conn, "5511999990399")

        assert nova is True
        assert conversa.estado.value == "identificacao"
        assert conversa.condominio_pendente is None

    com_pool(passos)


def test_dois_jobs_simultaneos_encerram_uma_vez_so(com_pool):
    """READ COMMITTED: o segundo reavalia o WHERE, vê 'encerrada' e não casa mais
    (transaction-iso.html). É o que dispensa o SKIP LOCKED do sweeper."""

    async def passos(pool):
        async with pool.acquire() as conn:
            await _semear(conn)
            for sufixo in range(5):
                await conversa_ativa(conn, f"55119999905{sufixo:02d}")
            await conn.execute(
                "update conversas set ultima_interacao_em = "
                "now() - make_interval(hours => $1)",
                settings.sessao_ttl_horas + 1,
            )

        fechadas = await asyncio.gather(
            encerrador.encerrar_ociosas(), encerrador.encerrar_ociosas()
        )

        assert sum(fechadas) == 5, f"soma errada: {fechadas}"
        async with pool.acquire() as conn:
            ativas = await conn.fetchval(
                "select count(*) from conversas where status = 'ativa'"
            )
        assert ativas == 0

    com_pool(passos)


def test_ciclo_sem_ociosa_nao_faz_nada(com_pool):
    async def passos(pool):
        async with pool.acquire() as conn:
            await _semear(conn)
            await conversa_ativa(conn, TEL)

        assert await encerrador.encerrar_ociosas() == 0

    com_pool(passos)
