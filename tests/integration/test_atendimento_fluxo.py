"""Integração ponta a ponta do atendimento — Teste A (Fase 3 · Passos 3 e 7).

Payload REAL do Z-PRO atravessa parse → processador → atendimento → banco real,
com o ENVIO mockado (httpx.MockTransport no zpro_client) e a GERAÇÃO mockada
(responder_duvida fake que captura os argumentos). O que se prova aqui e não
nos unitários: que a conversa progride de estado NO BANCO ao longo de uma
sessão inteira, que o índice N mapeia para o condomínio certo com N>1, e que o
tenant confirmado e o histórico lido do banco chegam à geração.

Pool real (não rodar_tx): o processador commita as próprias transações, então o
isolamento entre testes é por TRUNCATE, não por rollback.

Marca 'integration' -> rode com `pytest -m integration` (precisa de Docker).
"""

import asyncio

import asyncpg
import httpx
import pytest

import db
import processador
import zpro_client

pytestmark = pytest.mark.integration


def _payload(texto: str, *, msg_id: str, numero: str = "555592372732") -> dict:
    return {
        "method": "message",
        "msg": {
            "key": {"id": msg_id, "fromMe": False, "sender_pn": f"{numero}@s.whatsapp.net"},
            "messageTimestamp": 123,
            "pushName": "Lorenzo",
            "message": {"conversation": texto},
        },
        "ticket": {
            "id": 1,
            "isGroup": False,
            "tenantId": 8,
            "whatsappId": 45,
            "contact": {"id": 1, "number": numero, "name": "Lorenzo"},
            "whatsapp": {"id": 45, "type": "baileys"},
        },
    }


class _Ambiente:
    """Pool real + captura dos envios e das gerações. Reusa o schema do container."""

    def __init__(self, pool, enviados, geracoes):
        self.pool = pool
        self.enviados = enviados
        self.geracoes = geracoes
        self._contador = 0

    async def entregar(self, texto: str) -> str:
        """Simula uma mensagem do morador e devolve o texto que o sistema enviou."""
        self._contador += 1
        antes = len(self.enviados)
        from zpro_models import parse_zpro_webhook

        msg = parse_zpro_webhook(_payload(texto, msg_id=f"M{self._contador}"))
        await processador.processar_mensagem(msg)
        novos = self.enviados[antes:]
        assert len(novos) == 1, f"esperava 1 envio, houve {len(novos)}"
        return novos[0]

    async def estado(self) -> tuple[str, object, object]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "select estado, condominio_id, condominio_pendente from conversas "
                "where telefone = $1 and status = 'ativa'",
                "555592372732",
            )
        return row["estado"], row["condominio_id"], row["condominio_pendente"]


@pytest.fixture
def ambiente(pg_dsn, monkeypatch):
    enviados: list[str] = []
    geracoes: list[tuple] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        enviados.append(json.loads(request.content)["body"])
        return httpx.Response(
            200, json={"success": True, "data": {"message": "ok", "ticketId": 1}}
        )

    async def _gerar_fake(pergunta, condominio_id, historico):
        geracoes.append((pergunta, condominio_id, historico))
        return f"[gerada] {pergunta}"

    async def _corpo(passos):
        pool = await asyncpg.create_pool(dsn=pg_dsn, min_size=1, max_size=4, init=db._registrar_codecs)
        await zpro_client.criar_cliente(transport=httpx.MockTransport(handler))
        monkeypatch.setattr(processador, "get_pool", lambda: pool)
        monkeypatch.setattr(processador, "responder_duvida", _gerar_fake)
        import webhook

        monkeypatch.setattr(webhook, "get_pool", lambda: pool)
        try:
            async with pool.acquire() as conn:
                # cascade limpa conversas/mensagens junto; deixa o banco como
                # os testes de rollback (rodar_tx) esperam encontrá-lo: vazio.
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
    # Ordem alfabética por COLLATE "pt-BR-x-icu": Gabro(1), Solar(2), Verde(3).
    await conn.execute(
        """
        insert into condominios (slug, nome, ativo) values
          ('res-gabro', 'Edifício Gabro', true),
          ('res-solar', 'Edifício Solar', true),
          ('res-verde', 'Edifício Verde', true),
          ('eval-sentinela', 'Sentinela (eval)', false)
        """
    )


def test_conversa_completa_progride_no_banco(ambiente):
    async def passos(a: _Ambiente):
        # Primeiro contato: qualquer texto que não seja número pede a lista.
        lista = await a.entregar("oi")
        assert "1 - Edifício Gabro" in lista
        assert "2 - Edifício Solar" in lista
        assert "Sentinela" not in lista  # inativo nunca aparece
        assert await a.estado() == ("identificacao", None, None)

        # Escolhe o item 2 -> confirmação do Solar (posição resolvida na lista).
        confirma = await a.entregar("2")
        assert "Edifício Solar" in confirma
        estado, cond, pend_confirmado = await a.estado()
        assert estado == "aguardando_confirmacao"
        assert cond is None and pend_confirmado is not None

        # Confirma -> menu; agora o tenant está gravado e o pendente zerado.
        menu = await a.entregar("1")
        assert "Como posso ajudar" in menu
        estado, cond, pend = await a.estado()
        assert estado == "menu"
        assert cond == pend_confirmado
        assert pend is None

        # Menu -> dúvidas: o convite a perguntar, e o estado já muda.
        convite = await a.entregar("1")
        assert "pode perguntar" in convite.lower()
        assert (await a.estado())[0] == "duvidas"

        # Uma pergunta de verdade -> geração (mockada), fora da conexão.
        resposta = await a.entregar("Posso ter cachorro?")
        assert resposta == "[gerada] Posso ter cachorro?"
        assert (await a.estado())[0] == "duvidas"  # perguntar não muda de estado

        # O tenant confirmado e o histórico chegam à geração; sem filtro de
        # fluxo (B7 pendente de revisão), a navegação entra nas 3 trocas.
        pergunta, cond_gerado, historico = a.geracoes[-1]
        assert pergunta == "Posso ter cachorro?"
        assert cond_gerado == pend_confirmado
        assert [t.pergunta for t in historico] == ["2", "1", "1"]

        # Escape 0 -> volta ao menu.
        await a.entregar("0")
        assert (await a.estado())[0] == "menu"

        # Opção 9 -> sai do condomínio, zera os dois tenants.
        volta = await a.entregar("9")
        assert "1 - Edifício Gabro" in volta
        assert await a.estado() == ("identificacao", None, None)

    ambiente(passos)


def test_indice_mapeia_o_condominio_certo(ambiente):
    """Escolher 3 confirma o Verde, não o Gabro — o mapeamento posição→tenant."""

    async def passos(a: _Ambiente):
        await a.entregar("oi")
        confirma = await a.entregar("3")
        assert "Edifício Verde" in confirma

        _, _, pendente = await a.estado()
        async with a.pool.acquire() as conn:
            nome = await conn.fetchval(
                "select nome from condominios where id = $1", pendente
            )
        assert nome == "Edifício Verde"

    ambiente(passos)


def test_sentinela_nunca_e_escolhivel(ambiente):
    """4 itens no banco, 3 elegíveis: o número 4 não resolve para o sentinela."""

    async def passos(a: _Ambiente):
        await a.entregar("oi")
        resposta = await a.entregar("4")
        # 3 elegíveis: índice 4 está além do teto -> pede de novo, não confirma.
        assert "1 - Edifício Gabro" in resposta
        assert (await a.estado())[0] == "identificacao"

    ambiente(passos)
