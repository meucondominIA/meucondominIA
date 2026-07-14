"""Integração: codec do tipo vector (pgvector) resolvido no schema 'extensions'.

db._registrar_codecs registra o vector com schema=settings.pgvector_schema
('extensions', como no Supabase), e o schema.sql instala a extensão nesse mesmo
schema — o roundtrip prova a resolução do tipo fora do default 'public'. Se o
schema estivesse errado, o registro falharia com 'unknown type: public.vector'
antes de qualquer INSERT.

Precisão: o vector armazena float32; os valores do teste são inteiros pequenos
(exatos em float32), por isso a igualdade estrita vale.

Marcados 'integration' -> deselecionados no `pytest` padrão; rode com
`pytest -m integration` (precisa de Docker).
"""

import asyncpg
import pytest

pytestmark = pytest.mark.integration


def test_roundtrip_vector_3072_no_schema_extensions(rodar_tx):
    async def body(conn):
        entrada = [float((i % 7) - 3) for i in range(3072)]
        devolvido = await conn.fetchval(
            "insert into regras (escopo, conteudo, fonte, documento, embedding) "
            "values ('geral', 'x', 'Lei 4591/64, Art. 1', 'Lei 4591/64', $1) "
            "returning embedding",
            entrada,
        )
        assert devolvido.dimensions() == 3072
        assert devolvido.to_list() == entrada

    rodar_tx(body)


def test_chk_regras_fonte_e_documento_obrigatorios(rodar_tx):
    async def body(conn):
        # fonte só com espaços -> CHECK (~ '\S') barra
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into regras (escopo, conteudo, fonte, documento) "
                    "values ('geral', 'x', '   ', 'Lei 4591/64')"
                )
        # sem documento -> NOT NULL barra
        with pytest.raises(asyncpg.NotNullViolationError):
            async with conn.transaction():
                await conn.execute(
                    "insert into regras (escopo, conteudo, fonte) "
                    "values ('geral', 'x', 'Lei 4591/64, Art. 1')"
                )

    rodar_tx(body)
