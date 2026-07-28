"""Integração do wizard de reserva — o MARCO em forma de teste (Fase 4 · Etapa 3).

Payload REAL do Z-PRO atravessa parse → processador → roteador → motor → banco
real, com o ENVIO mockado e ZERO chamada de IA (o wizard nunca passa pela
geração). O que se prova aqui e não nos unitários: que o rascunho sobrevive no
jsonb entre mensagens, que os dois CHECKs aceitam cada transição do wizard, que
a reserva cai como 'pendente' no tenant certo com os instantes do fuso do
condomínio, e que reprocessar a mesma mensagem NÃO duplica a reserva.

Pool real (não rodar_tx): o processador commita as próprias transações.

Marca 'integration' -> rode com `pytest -m integration` (precisa de Docker).
"""

import asyncio
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest

import db
import processador
import zpro_client
from config import settings

pytestmark = pytest.mark.integration

NUMERO = "555592372732"
TZ = "America/Sao_Paulo"


def _payload(texto: str, *, msg_id: str) -> dict:
    return {
        "method": "message",
        "msg": {
            "key": {
                "id": msg_id,
                "fromMe": False,
                "sender_pn": f"{NUMERO}@s.whatsapp.net",
            },
            "messageTimestamp": 123,
            "pushName": "Lorenzo",
            "message": {"conversation": texto},
        },
        "ticket": {
            "id": 1,
            "isGroup": False,
            "tenantId": 8,
            "whatsappId": 45,
            "contact": {"id": 1, "number": NUMERO, "name": "Lorenzo"},
            "whatsapp": {"id": 45, "type": "baileys"},
        },
    }


class _Ambiente:
    def __init__(self, pool, enviados, geracoes):
        self.pool = pool
        self.enviados = enviados
        self.geracoes = geracoes
        self._contador = 0

    async def entregar(self, texto: str, *, msg_id: str | None = None) -> str:
        from zpro_models import parse_zpro_webhook

        self._contador += 1
        antes = len(self.enviados)
        msg = parse_zpro_webhook(
            _payload(texto, msg_id=msg_id or f"R{self._contador}")
        )
        await processador.processar_mensagem(msg)
        novos = self.enviados[antes:]
        assert len(novos) == 1, f"esperava 1 envio, houve {len(novos)}"
        return novos[0]

    async def reentregar(self, texto: str, *, msg_id: str) -> int:
        """Reprocessa uma mensagem já entregue; devolve quantos envios saíram."""
        from zpro_models import parse_zpro_webhook

        antes = len(self.enviados)
        await processador.processar_mensagem(
            parse_zpro_webhook(_payload(texto, msg_id=msg_id))
        )
        return len(self.enviados) - antes

    async def conversa(self) -> asyncpg.Record:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "select estado, condominio_id, rascunho from conversas "
                "where telefone = $1 and status = 'ativa'",
                NUMERO,
            )

    async def reservas(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                select r.status, r.telefone, r.origem_mensagem_id,
                       c.slug, a.nome as area,
                       r.inicio at time zone c.timezone as inicio_civil,
                       r.fim    at time zone c.timezone as fim_civil
                  from reservas r
                  join condominios c on c.id = r.condominio_id
                  join areas_comuns a on a.id = r.area_id
                 order by r.created_at
                """
            )

    async def respostas_por_entrada(self) -> list[int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "select count(*) as n from mensagens where em_resposta_a is not null "
                "group by em_resposta_a"
            )
        return [r["n"] for r in rows]


@pytest.fixture
def ambiente(pg_dsn, monkeypatch):
    enviados: list[str] = []
    geracoes: list[tuple] = []

    def handler(request: httpx.Request) -> httpx.Response:
        enviados.append(json.loads(request.content)["body"])
        return httpx.Response(
            200, json={"success": True, "data": {"message": "ok", "ticketId": 1}}
        )

    async def _gerar_fake(pergunta, condominio_id, historico):
        geracoes.append((pergunta, condominio_id, historico))
        return "[NUNCA DEVERIA SER CHAMADA NO WIZARD]"

    async def _corpo(passos):
        pool = await asyncpg.create_pool(
            dsn=pg_dsn, min_size=1, max_size=4, init=db._registrar_codecs
        )
        await zpro_client.criar_cliente(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(processador, "get_pool", lambda: pool)
        monkeypatch.setattr(processador, "responder_duvida", _gerar_fake)
        try:
            async with pool.acquire() as conn:
                await conn.execute("truncate condominios cascade")
                await _semear(conn)
            await passos(_Ambiente(pool, enviados, geracoes))
        finally:
            async with pool.acquire() as conn:
                await conn.execute("truncate condominios cascade")
            await zpro_client.fechar_cliente()
            await pool.close()

    def _rodar(passos):
        asyncio.run(_corpo(passos))

    return _rodar


async def _semear(conn: asyncpg.Connection) -> None:
    await conn.execute(
        "insert into condominios (slug, nome, ativo, timezone) "
        "values ('res-gabro', 'Edifício Gabro', true, $1)",
        TZ,
    )
    await conn.execute(
        """
        insert into areas_comuns (condominio_id, nome, reservavel)
        select id, 'Salão de Festas', true from condominios where slug = 'res-gabro'
        """
    )


async def _ate_o_menu(a: _Ambiente) -> None:
    await a.entregar("oi")
    await a.entregar("1")  # escolhe o Gabro (único da lista)
    await a.entregar("1")  # confirma


def _hoje() -> "datetime.date":
    return datetime.now(ZoneInfo(TZ)).date()


# ── o caminho do MARCO ───────────────────────────────────────────────────────


def test_reserva_ponta_a_ponta_cai_como_pendente(ambiente):
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)

        areas = await a.entregar("2")
        assert "Salão de Festas" in areas
        linha = await a.conversa()
        assert linha["estado"] == "reserva"
        assert linha["rascunho"] == {"passo": "area"}

        dias = await a.entregar("1")
        assert "escolha a data" in dias
        linha = await a.conversa()
        assert linha["rascunho"]["passo"] == "dia"
        assert len(linha["rascunho"]["opcoes"]) == 7

        escolhido = linha["rascunho"]["opcoes"][0]
        confirmar = await a.entregar("1")
        assert "Confirmar a reserva?" in confirmar
        linha = await a.conversa()
        assert linha["rascunho"]["passo"] == "confirmacao"
        assert linha["rascunho"]["dia"] == escolhido

        pronto = await a.entregar("1")
        assert "Registrei seu pedido" in pronto

        # o wizard terminou: volta ao menu e a sacola some (chk_conversas_rascunho)
        linha = await a.conversa()
        assert linha["estado"] == "menu"
        assert linha["rascunho"] is None

        (reserva,) = await a.reservas()
        assert reserva["status"] == "pendente"
        assert reserva["slug"] == "res-gabro"
        assert reserva["area"] == "Salão de Festas"
        assert reserva["telefone"] == NUMERO
        assert reserva["origem_mensagem_id"] is not None
        # dia civil inteiro, no fuso do condomínio
        dia = date.fromisoformat(escolhido)
        assert str(reserva["inicio_civil"]) == f"{dia} 00:00:00"
        assert str(reserva["fim_civil"]) == f"{dia + timedelta(days=1)} 00:00:00"

        # nenhuma resposta duplicada
        assert set(await a.respostas_por_entrada()) == {1}
        # e nenhuma chamada de IA no caminho inteiro
        assert a.geracoes == []

    ambiente(passos)


def test_reprocessar_a_confirmacao_nao_duplica_a_reserva(ambiente):
    """Simula o crash entre o INSERT da reserva e a TX2: o rascunho ainda está em
    'confirmacao' e a saída não foi gravada. O gate é uq_reservas_origem_mensagem."""

    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("2")
        await a.entregar("1")
        await a.entregar("1")

        conf = await a.conversa()
        rascunho = conf["rascunho"]
        await a.entregar("1", msg_id="CONFIRMA")

        (antes,) = await a.reservas()
        async with a.pool.acquire() as conn:
            entrada = await conn.fetchval(
                "select id from mensagens where message_id = 'CONFIRMA'"
            )
            await conn.execute("delete from mensagens where em_resposta_a = $1", entrada)
            await conn.execute(
                "update conversas set estado = 'reserva', rascunho = $2 "
                "where telefone = $1 and status = 'ativa'",
                NUMERO,
                rascunho,
            )

        envios = await a.reentregar("1", msg_id="CONFIRMA")

        depois = await a.reservas()
        assert len(depois) == 1, "reprocessamento duplicou a reserva"
        assert depois[0]["origem_mensagem_id"] == antes["origem_mensagem_id"]
        assert envios == 1  # respondeu de novo, mas gravou a MESMA reserva
        assert "Registrei seu pedido" in a.enviados[-1]

    ambiente(passos)


def test_reprocessar_mensagem_ja_respondida_nao_reenvia(ambiente):
    """Com a saída gravada, saida_ja_existe curto-circuita antes do wizard."""

    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("2", msg_id="ABRE")
        assert await a.reentregar("2", msg_id="ABRE") == 0

    ambiente(passos)


# ── as bordas, contra o banco real ───────────────────────────────────────────


def test_dia_ja_reservado_some_da_lista(ambiente):
    """D4: pendente também ocupa. A lista é o retrato; quem trava é o banco."""

    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("2")
        primeira = await a.entregar("1")
        linha = await a.conversa()
        tomado = linha["rascunho"]["opcoes"][0]

        await a.entregar("1")  # confirma o primeiro dia
        await a.entregar("1")  # grava

        await a.entregar("2")
        segunda = await a.entregar("1")
        depois = (await a.conversa())["rascunho"]["opcoes"]

        assert tomado not in depois, "dia pendente continuou ofertado"
        assert primeira != segunda, "a lista não foi refeita depois da reserva"

    ambiente(passos)


def test_escape_zero_sai_do_wizard_e_limpa_a_sacola(ambiente):
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("2")
        await a.entregar("1")

        texto = await a.entregar("0")
        assert "não agendei nada" in texto.lower()

        linha = await a.conversa()
        assert linha["estado"] == "menu" and linha["rascunho"] is None
        assert await a.reservas() == []

    ambiente(passos)


def test_recusar_na_confirmacao_nao_grava_nada(ambiente):
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("2")
        await a.entregar("1")
        await a.entregar("1")

        texto = await a.entregar("2")
        assert "não agendei nada" in texto.lower()
        assert await a.reservas() == []
        assert (await a.conversa())["rascunho"] is None

    ambiente(passos)


def test_ver_mais_pagina_e_a_janela_respeita_o_teto(ambiente):
    """A janela é de settings.reserva_janela_dias: a 2ª tela é a última."""

    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("2")
        primeira = await a.entregar("1")
        assert "9 - Ver mais datas" in primeira
        p1 = (await a.conversa())["rascunho"]["opcoes"]

        segunda = await a.entregar("9")
        p2 = (await a.conversa())["rascunho"]["opcoes"]

        assert not set(p1) & set(p2), "as páginas se sobrepuseram"
        assert len(p1) + len(p2) == settings.reserva_janela_dias
        assert "9 - Ver mais datas" not in segunda  # última página não oferece

        ultima = await a.entregar("9")
        assert "últimas datas" in ultima

    ambiente(passos)


def test_rascunho_corrompido_reinicia_em_vez_de_prender(ambiente):
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("2")
        await a.entregar("1")

        async with a.pool.acquire() as conn:
            await conn.execute(
                "update conversas set rascunho = $2 where telefone = $1 "
                "and status = 'ativa'",
                NUMERO,
                {"passo": "turno", "area_id": "nao-existe"},
            )

        texto = await a.entregar("1")
        assert "Qual área" in texto
        assert (await a.conversa())["rascunho"] == {"passo": "area"}

    ambiente(passos)


def test_condominio_sem_area_reservavel_nao_prende(ambiente):
    async def passos(a: _Ambiente):
        async with a.pool.acquire() as conn:
            await conn.execute("delete from areas_comuns")

        await _ate_o_menu(a)
        texto = await a.entregar("2")

        assert "não tem área para reservar" in texto
        linha = await a.conversa()
        assert linha["estado"] == "menu" and linha["rascunho"] is None

    ambiente(passos)
