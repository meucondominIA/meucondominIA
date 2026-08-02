"""Integração: resolução slug → id no Postgres real (Fase 2 · Passo 5).

Basename único (não test_condominios.py) para não colidir com o módulo de
teste unitário homônimo — import mismatch do pytest. Provamos o roundtrip do
lookup, o None de slug inexistente e as garantias do banco que o contrato
"no máximo um id por slug" assume (UNIQUE e CHECK do formato, espelhados no
stub do schema.sql e verificados no Supabase real em 17/07/2026).

Marcados 'integration' -> deselecionados no `pytest` padrão; rode com
`pytest -m integration` (precisa de Docker).
"""

import asyncpg
import pytest

from condominios import buscar_id_por_slug

pytestmark = pytest.mark.integration


def test_slug_resolve_para_o_id_certo_entre_varios(rodar_tx):
    async def body(conn):
        esperado = await conn.fetchval(
            "insert into condominios (slug, nome) values ('res-gabro', 'Gabro') returning id"
        )
        await conn.execute(
            "insert into condominios (slug, nome) values ('outro-cond', 'Outro')"
        )
        assert await buscar_id_por_slug(conn, "res-gabro") == esperado

    rodar_tx(body)


def test_slug_inexistente_devolve_none(rodar_tx):
    async def body(conn):
        await conn.execute(
            "insert into condominios (slug, nome) values ('res-gabro', 'Gabro')"
        )
        assert await buscar_id_por_slug(conn, "res-gabr") is None

    rodar_tx(body)


def test_unique_barra_slug_duplicado(rodar_tx):
    async def body(conn):
        await conn.execute(
            "insert into condominios (slug, nome) values ('res-gabro', 'Gabro')"
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into condominios (slug, nome) values ('res-gabro', 'Gabro')"
                )

    rodar_tx(body)


def test_check_barra_formato_invalido(rodar_tx):
    async def body(conn):
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into condominios (slug, nome) values ('Res Gabro', 'Gabro')"
                )

    rodar_tx(body)
