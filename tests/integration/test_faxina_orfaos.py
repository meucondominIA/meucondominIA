"""Integração: a query dos anexos órfãos (Fase 4 · Etapa 4).

Só o Postgres real prova isto — a query cruza storage.objects com um jsonb array
dentro de solicitacoes E com o rascunho de conversas vivas. O caso que mais
importa é o negativo: a foto de um wizard EM ANDAMENTO ainda não está em
solicitacoes, e apagá-la quebraria a conversa do morador.
"""

import pytest

from config import settings
from faxina import _ORFAOS

pytestmark = pytest.mark.integration

BUCKET = "anexos"


def _anexo(sha: str, cond: str = "cond-1") -> dict:
    return {
        "bucket": BUCKET,
        "caminho": f"{cond}/{sha}.jpg",
        "mimetype": "image/jpeg",
        "bytes": 1000,
        "sha256": sha,
    }


async def _objeto(conn, sha, *, horas: int, cond: str = "cond-1"):
    await conn.execute(
        "insert into storage.objects (bucket_id, name, created_at) "
        "values ($1, $2, now() - make_interval(hours => $3))",
        BUCKET,
        f"{cond}/{sha}.jpg",
        horas,
    )


async def _orfaos(conn, horas=None) -> list[str]:
    rows = await conn.fetch(
        _ORFAOS, BUCKET, horas or settings.anexo_orfao_horas,
        settings.faxina_batch_size,
    )
    return [r["name"] for r in rows]


async def _cond(conn, slug="gabro"):
    return await conn.fetchval(
        "insert into condominios (slug, nome) values ($1, $2) returning id",
        slug, slug.upper(),
    )


def test_anexo_sem_dono_e_velho_e_orfao(rodar_tx):
    async def corpo(conn):
        await _objeto(conn, "aaa", horas=48)
        assert await _orfaos(conn) == ["cond-1/aaa.jpg"]

    rodar_tx(corpo)


def test_anexo_referenciado_por_solicitacao_nunca_e_orfao(rodar_tx):
    async def corpo(conn):
        cond = await _cond(conn)
        await _objeto(conn, "bbb", horas=48)
        await conn.execute(
            "insert into solicitacoes (condominio_id, tipo, anexos) "
            "values ($1, 'reclamacao', $2::jsonb)",
            cond,
            [_anexo("bbb")],
        )
        assert await _orfaos(conn) == []

    rodar_tx(corpo)


def test_anexo_de_wizard_EM_ANDAMENTO_nao_e_orfao(rodar_tx):
    """O caso que quebraria a conversa do morador: a foto já subiu, está no
    rascunho, e a solicitação só nasce na confirmação."""
    async def corpo(conn):
        cond = await _cond(conn)
        await _objeto(conn, "ccc", horas=48)
        await conn.execute(
            "insert into conversas (telefone, estado, condominio_id, rascunho) "
            "values ('5555', 'ocorrencia', $1, $2::jsonb)",
            cond,
            {"passo": "descricao", "tipo": "reclamacao", "anexos": [_anexo("ccc")]},
        )
        assert await _orfaos(conn) == []

    rodar_tx(corpo)


def test_anexo_novo_demais_espera_a_carencia(rodar_tx):
    """A carência é a segunda rede: mesmo sem referência, foto recém-subida não
    é tocada."""
    async def corpo(conn):
        await _objeto(conn, "ddd", horas=1)
        assert await _orfaos(conn) == []

    rodar_tx(corpo)


def test_rascunho_sem_anexos_nao_quebra_a_query(rodar_tx):
    """RascunhoTipo e RascunhoFoto não têm a chave 'anexos'; rascunho pode ser
    NULL. Nenhum dos dois pode fazer jsonb_array_elements estourar."""
    async def corpo(conn):
        cond = await _cond(conn)
        await _objeto(conn, "eee", horas=48)
        await conn.execute(
            "insert into conversas (telefone, estado, condominio_id, rascunho) "
            "values ('1','ocorrencia',$1,'{\"passo\":\"tipo\"}'::jsonb)", cond
        )
        await conn.execute(
            "insert into conversas (telefone, estado) values ('2','identificacao')"
        )
        assert await _orfaos(conn) == ["cond-1/eee.jpg"]

    rodar_tx(corpo)


def test_outro_bucket_nao_e_tocado(rodar_tx):
    async def corpo(conn):
        await conn.execute(
            "insert into storage.objects (bucket_id, name, created_at) "
            "values ('outro','x/y.jpg', now() - interval '48 hours')"
        )
        assert await _orfaos(conn) == []

    rodar_tx(corpo)


def test_lote_limita_quantos_saem_por_ciclo(rodar_tx):
    async def corpo(conn):
        for i in range(5):
            await _objeto(conn, f"s{i}", horas=48)
        rows = await conn.fetch(_ORFAOS, BUCKET, settings.anexo_orfao_horas, 3)
        assert len(rows) == 3

    rodar_tx(corpo)


def test_mistura_real_separa_certo(rodar_tx):
    """O cenário da primeira sessão real: 3 fotos, 2 com dono, 1 órfã."""
    async def corpo(conn):
        cond = await _cond(conn)
        for sha in ("com-dono-1", "com-dono-2", "orfa"):
            await _objeto(conn, sha, horas=48)
        for sha in ("com-dono-1", "com-dono-2"):
            await conn.execute(
                "insert into solicitacoes (condominio_id, tipo, anexos) "
                "values ($1, 'ocorrencia', $2::jsonb)",
                cond, [_anexo(sha)],
            )
        assert await _orfaos(conn) == ["cond-1/orfa.jpg"]

    rodar_tx(corpo)
