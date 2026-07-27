"""Integração: a EXCLUDE sob transações concorrentes (Fase 4 · Etapa 2).

O manual do PostgreSQL documenta a espera/recheck para índices ÚNICOS
(index-unique-checks), não para exclusion constraints — então o comportamento
aqui é MEDIDO, não citado. O que se mede: a segunda transação bloqueia em
Lock/transactionid, e o desfecho dela depende do commit ou rollback da primeira.

Precisa de conexões independentes (rodar_concorrente): dentro do rodar_tx tudo
seria a mesma transação, e não existiria corrida nenhuma.
"""

import asyncio
from datetime import date

import asyncpg
import pytest

from reservas import criar_reserva_pendente

pytestmark = pytest.mark.integration

TZ = "America/Sao_Paulo"
DIA = date(2026, 8, 15)


async def _cenario(conn, slug):
    cond = await conn.fetchval(
        "insert into condominios (slug, nome) values ($1, $2) returning id",
        slug,
        slug.upper(),
    )
    area = await conn.fetchval(
        "insert into areas_comuns (condominio_id, nome) values ($1, 'Salão') "
        "returning id",
        cond,
    )
    return cond, area


async def _mensagem(conn, telefone):
    conversa = await conn.fetchval(
        "insert into conversas (telefone) values ($1) returning id", telefone
    )
    return await conn.fetchval(
        "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id) "
        "values ($1, 'morador', 'text', '1', $2) returning id",
        conversa,
        f"M-{telefone}",
    )


async def _aprovada(conn, cond, area):
    await conn.execute(
        "insert into reservas (condominio_id, area_id, inicio, fim, status) values "
        "($1, $2, ($3::date)::timestamp at time zone $4, "
        "(($3::date) + 1)::timestamp at time zone $4, 'aprovada')",
        cond,
        area,
        DIA,
        TZ,
    )


async def _espera_bloqueio(observador, pid):
    """Deixa a segunda transação chegar ao lock e devolve o wait event dela."""
    await asyncio.sleep(1.5)
    return await observador.fetchrow(
        "select state, wait_event_type, wait_event from pg_stat_activity "
        "where pid = $1",
        pid,
    )


def test_a_segunda_aprovada_espera_e_leva_23p01_quando_a_primeira_commita(
    rodar_concorrente,
):
    async def body(abrir):
        observador = await abrir()
        cond, area = await _cenario(observador, "corrida-commit")

        primeira, segunda = await abrir(), await abrir()
        pid_segunda = await segunda.fetchval("select pg_backend_pid()")
        tx_primeira, tx_segunda = primeira.transaction(), segunda.transaction()
        await tx_primeira.start()
        await tx_segunda.start()

        await _aprovada(primeira, cond, area)
        tentativa = asyncio.create_task(_aprovada(segunda, cond, area))
        espera = await _espera_bloqueio(observador, pid_segunda)

        assert not tentativa.done()
        assert espera["state"] == "active"
        assert (espera["wait_event_type"], espera["wait_event"]) == (
            "Lock",
            "transactionid",
        )

        await tx_primeira.commit()
        with pytest.raises(asyncpg.ExclusionViolationError) as erro:
            await tentativa
        assert erro.value.sqlstate == "23P01"

    rodar_concorrente(body)


def test_a_segunda_aprovada_vence_quando_a_primeira_faz_rollback(rodar_concorrente):
    """Bloquear não é perder: sem conflito real, a que esperou grava."""

    async def body(abrir):
        observador = await abrir()
        cond, area = await _cenario(observador, "corrida-rollback")

        primeira, segunda = await abrir(), await abrir()
        tx_primeira, tx_segunda = primeira.transaction(), segunda.transaction()
        await tx_primeira.start()
        await tx_segunda.start()

        await _aprovada(primeira, cond, area)
        tentativa = asyncio.create_task(_aprovada(segunda, cond, area))
        await asyncio.sleep(1.5)
        assert not tentativa.done()

        await tx_primeira.rollback()
        await tentativa
        await tx_segunda.commit()

        aprovadas = await observador.fetchval(
            "select count(*) from reservas where status = 'aprovada'"
        )
        assert aprovadas == 1

    rodar_concorrente(body)


def test_dois_pendentes_concorrentes_passam_os_dois(rodar_concorrente):
    """O LIMITE do NOT EXISTS: sob READ COMMITTED cada transação não enxerga a
    linha não-commitada da outra. Cortesia contra o morador lento, não contra o
    clique simultâneo — é a camada 3 (a EXCLUDE) que garante, na aprovação."""

    async def body(abrir):
        observador = await abrir()
        cond, area = await _cenario(observador, "corrida-pendentes")
        msg_a = await _mensagem(observador, "5555990015001")
        msg_b = await _mensagem(observador, "5555990015002")

        primeira, segunda = await abrir(), await abrir()
        tx_primeira, tx_segunda = primeira.transaction(), segunda.transaction()
        await tx_primeira.start()
        await tx_segunda.start()

        na_primeira = await criar_reserva_pendente(
            primeira,
            condominio_id=cond,
            area_id=area,
            dia=DIA,
            tz=TZ,
            telefone="5555990015001",
            origem_mensagem_id=msg_a,
        )
        na_segunda = await criar_reserva_pendente(
            segunda,
            condominio_id=cond,
            area_id=area,
            dia=DIA,
            tz=TZ,
            telefone="5555990015002",
            origem_mensagem_id=msg_b,
        )
        await tx_primeira.commit()
        await tx_segunda.commit()

        pendentes = await observador.fetchval(
            "select count(*) from reservas where status = 'pendente'"
        )
        assert na_primeira is not None and na_segunda is not None
        assert pendentes == 2

    rodar_concorrente(body)
