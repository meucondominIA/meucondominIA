"""Integração: reingestão por substituição atômica (Fase 2 · Passo 5).

O contrato da reingestão é apagar_regras_do_documento + inserir_regras dentro
de UMA transação do chamador — nunca meio documento no ar. No Postgres real
provamos: o delete null-safe (IS NOT DISTINCT FROM enxerga o escopo 'geral',
onde condominio_id é NULL), a cirurgia por (condominio_id, documento) — outros
documentos, tenants e o escopo geral ficam intactos —, a contagem vinda do
command tag "DELETE n" e o rollback restaurando a versão antiga quando o
insert da nova versão falha no meio (transação aninhada = savepoint, doc do
asyncpg).

Marcados 'integration' -> deselecionados no `pytest` padrão; rode com
`pytest -m integration` (precisa de Docker).
"""

from uuid import UUID

import pytest

from chunker import Chunk
from regras import apagar_regras_do_documento, inserir_regras

pytestmark = pytest.mark.integration

_VETOR = [1.0] + [0.0] * 3071


def _chunk(documento: str, artigo: int, versao: str = "v1") -> Chunk:
    return Chunk(
        conteudo=f"Art. {artigo} ({versao}) do {documento}.",
        fonte=f"{documento}, Art. {artigo}",
        documento=documento,
        metadata={"artigo": str(artigo), "posicao": artigo - 1},
    )


def _lote(documento: str, artigos: int, versao: str = "v1"):
    chunks = [_chunk(documento, n, versao) for n in range(1, artigos + 1)]
    return chunks, [_VETOR] * len(chunks)


async def _novo_condominio(conn) -> UUID:
    return await conn.fetchval("insert into condominios default values returning id")


async def _semear(conn) -> tuple[UUID, UUID]:
    """Tenant A com dois documentos, tenant B e escopo geral com um cada.

    'Regimento Exemplo' existe em A E em B — mesma identidade textual, tenants
    diferentes — para provar que o delete não cruza a fronteira do tenant.
    """
    cond_a = await _novo_condominio(conn)
    cond_b = await _novo_condominio(conn)
    await inserir_regras(conn, *_lote("Regimento Exemplo", 2), condominio_id=cond_a)
    await inserir_regras(conn, *_lote("Convenção Exemplo", 1), condominio_id=cond_a)
    await inserir_regras(conn, *_lote("Regimento Exemplo", 1), condominio_id=cond_b)
    await inserir_regras(conn, *_lote("Norma Geral Exemplo", 1), condominio_id=None)
    return cond_a, cond_b


async def _fontes(conn) -> list[tuple[UUID | None, str]]:
    rows = await conn.fetch(
        "select condominio_id, fonte from regras order by condominio_id, fonte"
    )
    return [(row["condominio_id"], row["fonte"]) for row in rows]


def test_apaga_somente_o_documento_do_tenant_alvo(rodar_tx):
    async def body(conn):
        cond_a, cond_b = await _semear(conn)
        apagadas = await apagar_regras_do_documento(
            conn, condominio_id=cond_a, documento="Regimento Exemplo"
        )
        assert apagadas == 2
        restantes = await _fontes(conn)
        assert (cond_a, "Convenção Exemplo, Art. 1") in restantes
        assert (cond_b, "Regimento Exemplo, Art. 1") in restantes
        assert (None, "Norma Geral Exemplo, Art. 1") in restantes
        assert len(restantes) == 3

    rodar_tx(body)


def test_apagar_geral_e_null_safe_e_nao_cruza_escopos(rodar_tx):
    async def body(conn):
        cond_a, _ = await _semear(conn)
        errado = await apagar_regras_do_documento(
            conn, condominio_id=cond_a, documento="Norma Geral Exemplo"
        )
        assert errado == 0
        apagadas = await apagar_regras_do_documento(
            conn, condominio_id=None, documento="Norma Geral Exemplo"
        )
        assert apagadas == 1
        assert all(cond is not None for cond, _ in await _fontes(conn))

    rodar_tx(body)


def test_documento_inexistente_devolve_zero(rodar_tx):
    async def body(conn):
        cond_a, _ = await _semear(conn)
        assert (
            await apagar_regras_do_documento(
                conn, condominio_id=cond_a, documento="Documento Fantasma"
            )
            == 0
        )
        assert len(await _fontes(conn)) == 5

    rodar_tx(body)


def test_reingestao_substitui_a_versao_na_mesma_transacao(rodar_tx):
    async def body(conn):
        cond_a, _ = await _semear(conn)
        chunks_v2, vetores_v2 = _lote("Regimento Exemplo", 3, versao="v2")
        async with conn.transaction():
            await apagar_regras_do_documento(
                conn, condominio_id=cond_a, documento="Regimento Exemplo"
            )
            await inserir_regras(conn, chunks_v2, vetores_v2, condominio_id=cond_a)
        rows = await conn.fetch(
            "select conteudo from regras "
            "where condominio_id = $1 and documento = 'Regimento Exemplo'",
            cond_a,
        )
        assert len(rows) == 3
        assert all("(v2)" in row["conteudo"] for row in rows)

    rodar_tx(body)


def test_falha_no_meio_da_reingestao_preserva_a_versao_antiga(rodar_tx):
    async def body(conn):
        cond_a, _ = await _semear(conn)
        chunks_v2, vetores_v2 = _lote("Regimento Exemplo", 3, versao="v2")
        with pytest.raises(RuntimeError, match="falha simulada"):
            async with conn.transaction():
                await apagar_regras_do_documento(
                    conn, condominio_id=cond_a, documento="Regimento Exemplo"
                )
                await inserir_regras(conn, chunks_v2, vetores_v2, condominio_id=cond_a)
                raise RuntimeError("falha simulada depois do insert")
        rows = await conn.fetch(
            "select conteudo from regras "
            "where condominio_id = $1 and documento = 'Regimento Exemplo'",
            cond_a,
        )
        assert len(rows) == 2
        assert all("(v1)" in row["conteudo"] for row in rows)

    rodar_tx(body)
