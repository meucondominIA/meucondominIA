"""Integração do outbox de avisos ao síndico (Fase 4 · Etapa 5).

O que fake nenhum pega: a atomicidade pedido↔intenção numa transação real, os
dois uniques parciais deduplicando o replay, o CHECK bicondicional, o JOIN que
resolve o síndico do tenant certo, e a lease separando dois workers de verdade.

A âncora muda aqui: o síndico não manda nada, então a asserção é a linha no
banco — não "respondeu pra ele?".
"""

from datetime import date, timedelta

import asyncpg
import pytest

from avisos import (
    TipoAviso,
    enfileirar_aviso_cancelamento,
    enfileirar_aviso_ocorrencia,
    enfileirar_aviso_reserva,
    marcar_enviado,
    reservar_lote,
)
from ocorrencia import TipoSolicitacao
from reservas import confirmar_reserva
from solicitacoes import criar_solicitacao

pytestmark = pytest.mark.integration

TZ = "America/Sao_Paulo"
DIA = date.today() + timedelta(days=3)


async def _cond(conn, slug, telefone=None):
    return await conn.fetchval(
        "insert into condominios (slug, nome, sindico_telefone) values ($1,$2,$3) "
        "returning id",
        slug,
        slug.upper(),
        telefone,
    )


async def _area(conn, cond, nome="Salão"):
    return await conn.fetchval(
        "insert into areas_comuns (condominio_id, nome) values ($1,$2) returning id",
        cond,
        nome,
    )


async def _entrada(conn, mid, telefone="5511"):
    conversa = await conn.fetchval(
        "insert into conversas (telefone) values ($1) returning id", telefone
    )
    return await conn.fetchval(
        "insert into mensagens (conversa_id, papel, conteudo, message_id) "
        "values ($1,'morador','1',$2) returning id",
        conversa,
        mid,
    )


async def _reserva_com_aviso(conn, *, cond, area, origem, dia=DIA, telefone="5511"):
    """O gancho de atendimento._gravar: a TX amarra pedido e intenção."""
    async with conn.transaction():
        rid = await confirmar_reserva(
            conn,
            condominio_id=cond,
            area_id=area,
            dia=dia,
            tz=TZ,
            telefone=telefone,
            origem_mensagem_id=origem,
        )
        if rid is not None:
            await enfileirar_aviso_reserva(
                conn, condominio_id=cond, reserva_id=rid, texto=f"Reserva #{rid.hex[:8]}"
            )
        return rid


async def _ocorrencia_com_aviso(conn, *, cond, origem, telefone="5512"):
    async with conn.transaction():
        sid = await criar_solicitacao(
            conn,
            condominio_id=cond,
            tipo=TipoSolicitacao.MANUTENCAO,
            descricao="vazou",
            anexos=[],
            telefone=telefone,
            origem_mensagem_id=origem,
        )
        await enfileirar_aviso_ocorrencia(
            conn,
            condominio_id=cond,
            solicitacao_id=sid,
            texto=f"Ocorrência #{sid.hex[:8]}",
        )
        return sid


def test_pedido_e_intencao_nascem_juntos(rodar_tx):
    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        area = await _area(conn, cond)
        rid = await _reserva_com_aviso(
            conn, cond=cond, area=area, origem=await _entrada(conn, "M1")
        )
        sid = await _ocorrencia_com_aviso(
            conn, cond=cond, origem=await _entrada(conn, "M2", "5512")
        )

        assert await conn.fetchval(
            "select count(*) from avisos_sindico where reserva_id = $1", rid
        ) == 1
        assert await conn.fetchval(
            "select count(*) from avisos_sindico where solicitacao_id = $1", sid
        ) == 1

    rodar_tx(corpo)


def test_crash_na_tx_desfaz_pedido_E_intencao(rodar_tx):
    """Atômico nos dois sentidos: nem aviso órfão, nem pedido sem aviso."""

    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        area = await _area(conn, cond)
        origem = await _entrada(conn, "M1")

        with pytest.raises(RuntimeError):
            async with conn.transaction():
                rid = await confirmar_reserva(
                    conn, condominio_id=cond, area_id=area, dia=DIA, tz=TZ,
                    telefone="5511", origem_mensagem_id=origem,
                )
                await enfileirar_aviso_reserva(
                    conn, condominio_id=cond, reserva_id=rid, texto="x"
                )
                raise RuntimeError("crash antes do commit")

        assert await conn.fetchval("select count(*) from reservas") == 0
        assert await conn.fetchval("select count(*) from avisos_sindico") == 0

    rodar_tx(corpo)


def test_replay_da_mesma_mensagem_nao_cria_segundo_aviso(rodar_tx):
    """A janela real: a reentrega do sweeper reprocessa a MESMA entrada."""

    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        area = await _area(conn, cond)
        origem = await _entrada(conn, "M1")

        primeiro = await _reserva_com_aviso(conn, cond=cond, area=area, origem=origem)
        segundo = await _reserva_com_aviso(conn, cond=cond, area=area, origem=origem)

        assert primeiro == segundo
        assert await conn.fetchval("select count(*) from avisos_sindico") == 1

    rodar_tx(corpo)


def test_replay_depois_do_aviso_enviado_nao_ressuscita_a_fila(rodar_tx):
    """O pior caso: o síndico já foi avisado e a mensagem volta a ser processada."""

    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        area = await _area(conn, cond)
        origem = await _entrada(conn, "M1")

        await _reserva_com_aviso(conn, cond=cond, area=area, origem=origem)
        await conn.execute(
            "update avisos_sindico set status='enviado', enviado_em=now()"
        )
        await _reserva_com_aviso(conn, cond=cond, area=area, origem=origem)

        assert await conn.fetchval("select count(*) from avisos_sindico") == 1
        assert await conn.fetchval(
            "select count(*) from avisos_sindico where status='pendente'"
        ) == 0

    rodar_tx(corpo)


def test_o_replay_preserva_o_texto_do_primeiro_aviso(rodar_tx):
    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        area = await _area(conn, cond)
        rid = await _reserva_com_aviso(
            conn, cond=cond, area=area, origem=await _entrada(conn, "M1")
        )
        await enfileirar_aviso_reserva(
            conn, condominio_id=cond, reserva_id=rid, texto="TEXTO NOVO"
        )
        assert await conn.fetchval(
            "select texto from avisos_sindico where reserva_id = $1", rid
        ) == f"Reserva #{rid.hex[:8]}"

    rodar_tx(corpo)


def test_dia_tomado_nao_enfileira_aviso(rodar_tx):
    """confirmar_reserva devolve None: não há pedido a avisar."""

    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        area = await _area(conn, cond)
        await _reserva_com_aviso(
            conn, cond=cond, area=area, origem=await _entrada(conn, "M1")
        )
        segundo = await _reserva_com_aviso(
            conn, cond=cond, area=area, origem=await _entrada(conn, "M2", "5599")
        )

        assert segundo is None
        assert await conn.fetchval("select count(*) from avisos_sindico") == 1

    rodar_tx(corpo)


@pytest.mark.parametrize(
    "rotulo, aceitas",
    [
        ("nenhum pedido", {"chk_avisos_sindico_um_pedido",
                           "chk_avisos_sindico_tipo_coerente"}),
        ("os dois pedidos", {"chk_avisos_sindico_um_pedido"}),
    ],
)
def test_check_recusa_linha_sem_pedido_ou_com_dois(rodar_tx, rotulo, aceitas):
    """Sem nenhum id as DUAS CHECKs são violadas e qual delas o Postgres reporta
    não é garantido; com os dois preenchidos só a um_pedido morde."""

    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        area = await _area(conn, cond)
        rid = await _reserva_com_aviso(
            conn, cond=cond, area=area, origem=await _entrada(conn, "M1")
        )
        sid = await _ocorrencia_com_aviso(
            conn, cond=cond, origem=await _entrada(conn, "M2", "5512")
        )
        alvo = (None, None) if rotulo == "nenhum pedido" else (rid, sid)

        with pytest.raises(asyncpg.CheckViolationError) as erro:
            await conn.execute(
                "insert into avisos_sindico (condominio_id, reserva_id, "
                "solicitacao_id, tipo, texto) values ($1,$2,$3,'reserva_criada','x')",
                cond, *alvo,
            )
        assert erro.value.constraint_name in aceitas

    rodar_tx(corpo)


@pytest.mark.parametrize(
    "tipo", [TipoAviso.OCORRENCIA_ABERTA.value, TipoAviso.RESERVA_CRIADA.value]
)
def test_check_recusa_tipo_que_nao_fala_do_id_preenchido(rodar_tx, tipo):
    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        area = await _area(conn, cond)
        rid = await _reserva_com_aviso(
            conn, cond=cond, area=area, origem=await _entrada(conn, "M1")
        )
        sid = await _ocorrencia_com_aviso(
            conn, cond=cond, origem=await _entrada(conn, "M2", "5512")
        )
        alvo = (
            ("reserva_id", rid)
            if tipo == TipoAviso.OCORRENCIA_ABERTA.value
            else ("solicitacao_id", sid)
        )

        with pytest.raises(asyncpg.CheckViolationError) as erro:
            await conn.execute(
                f"insert into avisos_sindico (condominio_id, {alvo[0]}, tipo, texto) "
                "values ($1,$2,$3,'x')",
                cond, alvo[1], tipo,
            )
        assert erro.value.constraint_name == "chk_avisos_sindico_tipo_coerente"

    rodar_tx(corpo)


def test_a_mesma_reserva_aceita_os_dois_tipos_e_recusa_o_terceiro(rodar_tx):
    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        area = await _area(conn, cond)
        rid = await _reserva_com_aviso(
            conn, cond=cond, area=area, origem=await _entrada(conn, "M1")
        )
        await enfileirar_aviso_cancelamento(
            conn, condominio_id=cond, reserva_id=rid, texto="Reserva cancelada"
        )

        tipos = [
            r["tipo"]
            for r in await conn.fetch(
                "select tipo from avisos_sindico where reserva_id = $1 order by tipo",
                rid,
            )
        ]
        assert tipos == ["reserva_cancelada", "reserva_criada"]

        await enfileirar_aviso_cancelamento(
            conn, condominio_id=cond, reserva_id=rid, texto="outro texto"
        )
        assert await conn.fetchval(
            "select count(*) from avisos_sindico where reserva_id = $1", rid
        ) == 2

        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "insert into avisos_sindico (condominio_id, reserva_id, tipo, texto) "
                "values ($1,$2,'reserva_cancelada','sem on conflict')",
                cond, rid,
            )

    rodar_tx(corpo)


def test_o_lote_sai_na_ordem_em_que_foi_enfileirado(rodar_tx):
    """O RETURNING de um UPDATE não tem ordem (queries-order.html): sem o SELECT
    externo o síndico lê o cancelamento antes da reserva que o gerou."""

    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        area = await _area(conn, cond)
        for n in range(8):
            rid = await _reserva_com_aviso(
                conn,
                cond=cond,
                area=area,
                origem=await _entrada(conn, f"M{n}", f"55{n}"),
                dia=DIA + timedelta(days=n),
            )
            await conn.execute(
                "update avisos_sindico set texto = $2, "
                "created_at = now() + make_interval(secs => $3) where reserva_id = $1",
                rid, f"MSG-{n}", float(n),
            )

        lote = await reservar_lote(conn, lease_segundos=60.0, limite=20)
        assert [a.texto for a in lote] == [f"MSG-{n}" for n in range(8)]

    rodar_tx(corpo)


# ── o lote do worker ─────────────────────────────────────────────────────────


def test_o_lote_resolve_o_sindico_do_tenant_certo(rodar_tx):
    """Isolamento: o destino sai do JOIN por PK, nunca de outro tenant."""

    async def corpo(conn):
        um = await _cond(conn, "gabro", "5555992372732")
        outro = await _cond(conn, "outro", "5511888887777")
        await _reserva_com_aviso(
            conn, cond=um, area=await _area(conn, um),
            origem=await _entrada(conn, "M1"),
        )
        await _reserva_com_aviso(
            conn, cond=outro, area=await _area(conn, outro),
            origem=await _entrada(conn, "M2", "5599"),
        )

        lote = await reservar_lote(conn, lease_segundos=60.0, limite=10)
        assert {a.telefone for a in lote} == {"5555992372732", "5511888887777"}

    rodar_tx(corpo)


def test_tenant_sem_sindico_represa_e_o_cadastro_libera(rodar_tx):
    """Aviso sem destino não vira erro em loop: fica esperando o número."""

    async def corpo(conn):
        cond = await _cond(conn, "semsindico", None)
        await _reserva_com_aviso(
            conn, cond=cond, area=await _area(conn, cond),
            origem=await _entrada(conn, "M1"),
        )

        assert await reservar_lote(conn, lease_segundos=60.0, limite=10) == []
        assert await conn.fetchval(
            "select count(*) from avisos_sindico where status='pendente'"
        ) == 1

        await conn.execute(
            "update condominios set sindico_telefone='5521999998888' where id=$1", cond
        )
        lote = await reservar_lote(conn, lease_segundos=60.0, limite=10)
        assert [a.telefone for a in lote] == ["5521999998888"]

    rodar_tx(corpo)


def test_marcar_enviado_tira_do_lote(rodar_tx):
    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        await _reserva_com_aviso(
            conn, cond=cond, area=await _area(conn, cond),
            origem=await _entrada(conn, "M1"),
        )
        [aviso] = await reservar_lote(conn, lease_segundos=60.0, limite=10)
        await marcar_enviado(conn, aviso.id)

        await conn.execute("update avisos_sindico set reservado_ate = null")
        assert await reservar_lote(conn, lease_segundos=60.0, limite=10) == []

    rodar_tx(corpo)


def test_lease_esconde_o_aviso_e_a_expiracao_o_devolve(rodar_tx):
    """A janela residual em forma de teste: enviou, caiu antes de marcar, e o
    aviso volta — at-least-once, nunca exactly once."""

    async def corpo(conn):
        cond = await _cond(conn, "gabro", "5555992372732")
        await _reserva_com_aviso(
            conn, cond=cond, area=await _area(conn, cond),
            origem=await _entrada(conn, "M1"),
        )

        assert len(await reservar_lote(conn, lease_segundos=60.0, limite=10)) == 1
        assert await reservar_lote(conn, lease_segundos=60.0, limite=10) == []

        await conn.execute(
            "update avisos_sindico set reservado_ate = now() - interval '1 second'"
        )
        devolvidos = await reservar_lote(conn, lease_segundos=60.0, limite=10)
        assert len(devolvidos) == 1
        # 2 e não 3: o ciclo do meio nem pegou a linha, então não conta tentativa.
        assert await conn.fetchval("select tentativas from avisos_sindico") == 2

    rodar_tx(corpo)


def test_dois_workers_enviam_cada_aviso_uma_vez(rodar_concorrente):
    """A prova da lease sob concorrência REAL. Medido em 29/07/2026: sem ela,
    cada aviso sai 2x — e isso não é at-least-once, é rotina de duplicata."""

    async def corpo(abrir):
        preparo = await abrir()
        cond = await _cond(preparo, "gabro", "5555992372732")
        await _reserva_com_aviso(
            preparo, cond=cond, area=await _area(preparo, cond),
            origem=await _entrada(preparo, "M1"),
        )
        await _ocorrencia_com_aviso(
            preparo, cond=cond, origem=await _entrada(preparo, "M2", "5512")
        )

        import asyncio

        async def worker():
            conn = await abrir()
            async with conn.transaction():
                return await reservar_lote(conn, lease_segundos=60.0, limite=10)

        lotes = await asyncio.gather(worker(), worker())
        pegos = [aviso.id for lote in lotes for aviso in lote]

        assert len(pegos) == 2
        assert len(set(pegos)) == 2

    rodar_concorrente(corpo)
