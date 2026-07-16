"""Integração: isolamento multi-tenant da busca por similaridade (Forma B).

O teste é ADVERSARIAL: o trecho do condomínio B é plantado como o vizinho mais
próximo GLOBAL da pergunta (distância 0). Se o WHERE falhasse, B seria o
PRIMEIRO resultado — o teste só passa se o filtro eliminar B antes da
ordenação. Isso prova o filtro, não a geometria. A Forma B fica travada nas
asserções: escopo 'geral' é gravado normalmente, mas nunca retornado.

Vetores sintéticos determinísticos (sem OpenAI): unitários no plano das duas
primeiras dimensões, distância de cosseno até vetor(0°) = 1 − cos(θ). O vector
armazena float32, então as distâncias são conferidas com pytest.approx.

Marcados 'integration' -> deselecionados no `pytest` padrão; rode com
`pytest -m integration` (precisa de Docker).
"""

import math
from uuid import UUID

import pytest

from chunker import Chunk
from regras import buscar_por_similaridade, inserir_regras

pytestmark = pytest.mark.integration

_DIMS = 3072


def vetor(angulo_graus: float) -> list[float]:
    v = [0.0] * _DIMS
    v[0] = math.cos(math.radians(angulo_graus))
    v[1] = math.sin(math.radians(angulo_graus))
    return v


PERGUNTA = vetor(0)


def distancia_esperada(angulo_graus: float) -> float:
    return 1.0 - math.cos(math.radians(angulo_graus))


def _chunk(fonte_curta: str, documento: str) -> Chunk:
    return Chunk(
        conteudo=f"Conteúdo de {fonte_curta} do {documento}.",
        fonte=f"{documento}, {fonte_curta}",
        documento=documento,
        metadata={},
    )


async def _novo_condominio(conn) -> UUID:
    return await conn.fetchval("insert into condominios default values returning id")


async def _semear(conn) -> tuple[UUID, UUID]:
    """Condomínio A (30°/60°/90°), B adversarial (0°) e uma regra 'geral' (45°).

    Devolve (cond_a, cond_b). O chunk de B está a distância ZERO da pergunta —
    mais próximo que qualquer chunk do próprio A.
    """
    cond_a = await _novo_condominio(conn)
    cond_b = await _novo_condominio(conn)
    await inserir_regras(
        conn,
        [_chunk(f"Art. {i}º", "Regimento A") for i in (1, 2, 3)],
        [vetor(30), vetor(60), vetor(90)],
        condominio_id=cond_a,
    )
    await inserir_regras(
        conn,
        [_chunk("Art. 1º", "Regimento B")],
        [vetor(0)],
        condominio_id=cond_b,
    )
    await inserir_regras(
        conn,
        [_chunk("Art. 63", "Lei 4591/64")],
        [vetor(45)],
        condominio_id=None,
    )
    return cond_a, cond_b


def test_adversarial_vizinho_mais_proximo_de_outro_tenant_nunca_volta(rodar_tx):
    async def body(conn):
        cond_a, _ = await _semear(conn)
        resultado = await buscar_por_similaridade(conn, PERGUNTA, cond_a, limite=10)
        fontes = [regra.fonte for regra in resultado]
        assert "Regimento B, Art. 1º" not in fontes
        assert fontes == [
            "Regimento A, Art. 1º",
            "Regimento A, Art. 2º",
            "Regimento A, Art. 3º",
        ]

    rodar_tx(body)


def test_forma_b_escopo_geral_gravado_mas_nunca_retornado(rodar_tx):
    async def body(conn):
        cond_a, _ = await _semear(conn)
        assert (
            await conn.fetchval("select count(*) from regras where escopo = 'geral'")
            == 1
        )
        resultado = await buscar_por_similaridade(conn, PERGUNTA, cond_a, limite=10)
        assert all("Lei 4591/64" not in regra.fonte for regra in resultado)

    rodar_tx(body)


def test_ordenacao_por_distancia_crescente_com_valores_previstos(rodar_tx):
    async def body(conn):
        cond_a, _ = await _semear(conn)
        resultado = await buscar_por_similaridade(conn, PERGUNTA, cond_a, limite=10)
        distancias = [regra.distancia for regra in resultado]
        assert distancias == sorted(distancias)
        assert distancias == pytest.approx(
            [distancia_esperada(30), distancia_esperada(60), distancia_esperada(90)],
            abs=1e-5,
        )

    rodar_tx(body)


def test_simetria_b_tambem_nao_ve_a_nem_geral(rodar_tx):
    async def body(conn):
        _, cond_b = await _semear(conn)
        resultado = await buscar_por_similaridade(conn, PERGUNTA, cond_b, limite=10)
        assert [regra.fonte for regra in resultado] == ["Regimento B, Art. 1º"]
        assert resultado[0].distancia == pytest.approx(0.0, abs=1e-6)

    rodar_tx(body)


def test_tenant_sem_regras_devolve_vazio_sem_fallback(rodar_tx):
    async def body(conn):
        await _semear(conn)
        cond_c = await _novo_condominio(conn)
        assert await buscar_por_similaridade(conn, PERGUNTA, cond_c, limite=10) == []

    rodar_tx(body)


def test_limite_respeitado(rodar_tx):
    async def body(conn):
        cond_a, _ = await _semear(conn)
        resultado = await buscar_por_similaridade(conn, PERGUNTA, cond_a, limite=2)
        assert [regra.fonte for regra in resultado] == [
            "Regimento A, Art. 1º",
            "Regimento A, Art. 2º",
        ]

    rodar_tx(body)


def test_inserir_roundtrip_completo(rodar_tx):
    async def body(conn):
        cond_a = await _novo_condominio(conn)
        chunk = Chunk(
            conteudo="Art. 5º É proibido som alto após as 22h.",
            fonte="Regimento A, Art. 5º",
            documento="Regimento A",
            metadata={"artigo": "5º", "posicao": 0},
        )
        await inserir_regras(conn, [chunk], [vetor(30)], condominio_id=cond_a)
        row = await conn.fetchrow(
            "select condominio_id, escopo, conteudo, fonte, documento, metadata, "
            "embedding from regras where condominio_id = $1",
            cond_a,
        )
        assert row["escopo"] == "especifico"
        assert row["conteudo"] == chunk.conteudo
        assert row["fonte"] == chunk.fonte
        assert row["documento"] == chunk.documento
        assert row["metadata"] == chunk.metadata
        assert row["embedding"].to_list() == pytest.approx(vetor(30), abs=1e-6)

    rodar_tx(body)
