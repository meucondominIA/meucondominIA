"""Integração: a lista que o morador recebe (Fase 3 · Passo 3).

Basename próprio (não test_condominios.py) para não colidir com o unitário
homônimo — import mismatch do pytest.

Prova o que o fake não alcança: que `ativo` realmente exclui, que a ordem sai
determinística do Postgres, e que o COLLATE está fazendo efeito. Este último é
o teste que importa de verdade — o container ordena por libc e o Supabase por
ICU, e os dois discordam justamente nos nomes usados aqui. Sem o COLLATE na
query, `test_collate_fixa_a_ordem_entre_icu_e_libc` falha NESTE container, que
é o único lugar onde a CI conseguiria perceber.

Marcados 'integration' -> deselecionados no `pytest` padrão; rode com
`pytest -m integration` (precisa de Docker).
"""

import pytest

from condominios import listar_elegiveis, nome_por_id

pytestmark = pytest.mark.integration


async def _inserir(conn, slug: str, nome: str, ativo: bool = True):
    return await conn.fetchval(
        "insert into condominios (slug, nome, ativo) values ($1, $2, $3) returning id",
        slug,
        nome,
        ativo,
    )


def test_inativo_nunca_aparece_na_lista(rodar_tx):
    """O D2 virando comportamento: é esta cláusula que esconde o eval-sentinela."""

    async def body(conn):
        await _inserir(conn, "res-gabro", "Edifício Residencial Gabro")
        await _inserir(conn, "eval-sentinela", "Sentinela (eval)", ativo=False)

        lista = await listar_elegiveis(conn)

        assert [c.nome for c in lista] == ["Edifício Residencial Gabro"]

    rodar_tx(body)


def test_lista_vazia_quando_nenhum_esta_ativo(rodar_tx):
    async def body(conn):
        await _inserir(conn, "so-inativo", "Inativo", ativo=False)
        assert await listar_elegiveis(conn) == []

    rodar_tx(body)


def test_ordena_por_nome(rodar_tx):
    async def body(conn):
        await _inserir(conn, "gama", "Gama")
        await _inserir(conn, "alfa", "Alfa")
        await _inserir(conn, "beta", "Beta")

        assert [c.nome for c in await listar_elegiveis(conn)] == [
            "Alfa",
            "Beta",
            "Gama",
        ]

    rodar_tx(body)


def test_collate_fixa_a_ordem_entre_icu_e_libc(rodar_tx):
    """Os três nomes em que os dois provedores de locale discordam.

    Medido em 21/07/2026: com o COLLATE, Supabase (ICU) e este container (libc)
    devolvem esta ordem. Sem ele, o container devolve 'Ed. 25 de Março',
    'Ed. 3 Marias', 'Ed Alfa' — e um teste de posição passaria aqui descrevendo
    errado a produção.
    """

    async def body(conn):
        await _inserir(conn, "ed-3-marias", "Ed. 3 Marias")
        await _inserir(conn, "ed-alfa", "Ed Alfa")
        await _inserir(conn, "ed-25-marco", "Ed. 25 de Março")

        assert [c.nome for c in await listar_elegiveis(conn)] == [
            "Ed Alfa",
            "Ed. 25 de Março",
            "Ed. 3 Marias",
        ]

    rodar_tx(body)


def test_homonimos_saem_sempre_na_mesma_ordem(rodar_tx):
    """`nome` não é único: sem o desempate por id o empate seria arbitrário."""

    async def body(conn):
        for slug in ("primeiro", "segundo", "terceiro"):
            await _inserir(conn, slug, "Edifício Central")

        primeira = await listar_elegiveis(conn)
        segunda = await listar_elegiveis(conn)

        assert len(primeira) == 3
        assert [c.id for c in primeira] == [c.id for c in segunda]

    rodar_tx(body)


def test_nome_por_id_encontra_o_condominio(rodar_tx):
    async def body(conn):
        procurado = await _inserir(conn, "res-gabro", "Edifício Residencial Gabro")
        await _inserir(conn, "outro", "Outro Edifício")

        assert await nome_por_id(conn, procurado) == "Edifício Residencial Gabro"

    rodar_tx(body)


def test_nome_por_id_devolve_ate_o_desativado(rodar_tx):
    """Sem filtro de ativo: o candidato desativado ainda tem nome para a
    mensagem de confirmação citar. O id vem de FK — nunca fica pendurado."""

    async def body(conn):
        desativado = await _inserir(conn, "sumiu", "Sumiu", ativo=False)
        assert await nome_por_id(conn, desativado) == "Sumiu"

    rodar_tx(body)


def test_nome_por_id_inexistente_devolve_none(rodar_tx):
    async def body(conn):
        from uuid import uuid4

        assert await nome_por_id(conn, uuid4()) is None

    rodar_tx(body)
