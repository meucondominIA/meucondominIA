"""Integração: ultimas_trocas contra Postgres real (Fase 3 · Passo 7).

created_at explícito nos inserts: dentro de rodar_tx tudo é uma transação só e
now() é o timestamp DELA (constante) — sem timestamps distintos não há ordem a
provar.
"""

from datetime import datetime, timedelta, timezone

import pytest

from mensagens import ultimas_trocas, upsert_conversa_ativa

pytestmark = pytest.mark.integration

_BASE = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


async def _troca(conn, cid, n, *, pergunta, resposta):
    quando = _BASE + timedelta(seconds=n)
    entrada = await conn.fetchval(
        "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id, created_at) "
        "values ($1, 'morador', 'text', $2, $3, $4) returning id",
        cid,
        pergunta,
        f"M-{cid}-{n}",
        quando,
    )
    await conn.execute(
        "insert into mensagens (conversa_id, papel, conteudo, em_resposta_a, created_at) "
        "values ($1, 'assistente', $2, $3, $4)",
        cid,
        resposta,
        entrada,
        quando,
    )
    return entrada


def test_devolve_as_ultimas_em_ordem_cronologica_so_da_conversa(rodar_tx):
    async def body(conn):
        cid = (await upsert_conversa_ativa(conn, "5511999990101")).id
        outra = (await upsert_conversa_ativa(conn, "5511999990102")).id
        for n, tema in enumerate(["cachorro", "gato", "festa", "piscina"], start=1):
            await _troca(conn, cid, n, pergunta=f"Pode {tema}?", resposta=f"R{n}")
        await _troca(conn, outra, 9, pergunta="Pode churrasco?", resposta="R-outra")

        trocas = await ultimas_trocas(conn, cid, limite=3)

        assert [t.pergunta for t in trocas] == [
            "Pode gato?",
            "Pode festa?",
            "Pode piscina?",
        ]
        assert [t.resposta for t in trocas] == ["R2", "R3", "R4"]

    rodar_tx(body)


def test_entrada_sem_resposta_fica_de_fora_por_construcao(rodar_tx):
    """A entrada recém-gravada ainda não tem saída na janela 1 — o par-join a
    exclui sem OFFSET nem filtro por id."""

    async def body(conn):
        cid = (await upsert_conversa_ativa(conn, "5511999990103")).id
        await _troca(conn, cid, 1, pergunta="Pode cachorro?", resposta="R1")
        await conn.execute(
            "insert into mensagens (conversa_id, papel, tipo, conteudo, message_id, created_at) "
            "values ($1, 'morador', 'text', 'E gato?', $2, $3)",
            cid,
            f"M-{cid}-atual",
            _BASE + timedelta(seconds=2),
        )

        trocas = await ultimas_trocas(conn, cid, limite=3)

        assert [t.pergunta for t in trocas] == ["Pode cachorro?"]

    rodar_tx(body)


def test_pergunta_nula_nao_vira_troca(rodar_tx):
    """O par (mídia -> só entendo texto) tem pergunta NULL: não é histórico."""

    async def body(conn):
        cid = (await upsert_conversa_ativa(conn, "5511999990104")).id
        entrada = await conn.fetchval(
            "insert into mensagens (conversa_id, papel, tipo, message_id, created_at) "
            "values ($1, 'morador', 'unsupported', $2, $3) returning id",
            cid,
            f"M-{cid}-midia",
            _BASE,
        )
        await conn.execute(
            "insert into mensagens (conversa_id, papel, conteudo, em_resposta_a, created_at) "
            "values ($1, 'assistente', 'Só entendo texto.', $2, $3)",
            cid,
            entrada,
            _BASE,
        )
        await _troca(conn, cid, 1, pergunta="Pode cachorro?", resposta="R1")

        trocas = await ultimas_trocas(conn, cid, limite=3)

        assert [t.pergunta for t in trocas] == ["Pode cachorro?"]

    rodar_tx(body)
