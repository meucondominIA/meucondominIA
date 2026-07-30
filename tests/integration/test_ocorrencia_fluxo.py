"""Integração do wizard de ocorrência — o MARCO em forma de teste (Fase 4 · Etapa 4).

Payload REAL do Z-PRO atravessa parse → processador → roteador → motor → banco
real, com o ENVIO e o STORAGE mockados e ZERO chamada de IA. Inclusive a FOTO: o
payload de imagem é o capturado por endpoint-armadilha em 28/07/2026.

O que se prova aqui e não nos unitários: que o rascunho sobrevive no jsonb entre
mensagens, que os três CHECKs aceitam cada transição, que a solicitação cai como
'aberta' no tenant certo com a descrição verbatim, que o upload roda FORA da
janela de conexão, e que reprocessar a mesma mensagem NÃO duplica.

Pool real (não rodar_tx): o processador commita as próprias transações.

Marca 'integration' -> rode com `pytest -m integration` (precisa de Docker).
"""

import asyncio
import base64
import json
import pathlib

import asyncpg
import httpx
import pytest

import anexos
import db
import processador
import zpro_client

pytestmark = pytest.mark.integration

NUMERO = "555592372732"
TZ = "America/Sao_Paulo"

_IMAGEM_REAL = json.loads(
    (pathlib.Path(__file__).parent.parent / "fixtures" / "zpro_imagem.json").read_text(
        encoding="utf-8"
    )
)
FOTO_BYTES = base64.b64decode(_IMAGEM_REAL["msg"]["base64"])


def _payload(texto: str, *, msg_id: str) -> dict:
    return {
        "method": "message",
        "msg": {
            "key": {"id": msg_id, "fromMe": False,
                    "sender_pn": f"{NUMERO}@s.whatsapp.net"},
            "messageTimestamp": 123,
            "pushName": "Lorenzo",
            "message": {"conversation": texto},
        },
        "ticket": {
            "id": 1, "isGroup": False, "tenantId": 8, "whatsappId": 45,
            "contact": {"id": 1, "number": NUMERO, "name": "Lorenzo"},
            "whatsapp": {"id": 45, "type": "baileys"},
        },
    }


def _payload_foto(*, msg_id: str, legenda: str | None = None) -> dict:
    """A captura REAL, com só a chave e a legenda trocadas."""
    raw = json.loads(json.dumps(_IMAGEM_REAL))
    raw["msg"]["key"]["id"] = msg_id
    raw["msg"]["key"]["sender_pn"] = f"{NUMERO}@s.whatsapp.net"
    raw["ticket"] = _payload("x", msg_id=msg_id)["ticket"]
    if legenda is not None:
        raw["msg"]["message"]["imageMessage"]["caption"] = legenda
    return raw


class _Ambiente:
    def __init__(self, pool, enviados, uploads):
        self.pool = pool
        self.enviados = enviados
        self.uploads = uploads
        self._contador = 0

    async def _processar(self, raw: dict) -> None:
        from zpro_models import parse_zpro_webhook

        await processador.processar_mensagem(parse_zpro_webhook(raw))

    async def entregar(self, texto: str, *, msg_id: str | None = None) -> str:
        self._contador += 1
        antes = len(self.enviados)
        await self._processar(_payload(texto, msg_id=msg_id or f"O{self._contador}"))
        novos = self.enviados[antes:]
        assert len(novos) == 1, f"esperava 1 envio, houve {len(novos)}"
        return novos[0]

    async def entregar_foto(
        self, *, msg_id: str | None = None, legenda: str | None = None
    ) -> str:
        self._contador += 1
        antes = len(self.enviados)
        await self._processar(
            _payload_foto(msg_id=msg_id or f"F{self._contador}", legenda=legenda)
        )
        novos = self.enviados[antes:]
        assert len(novos) == 1, f"esperava 1 envio, houve {len(novos)}"
        return novos[0]

    async def reentregar(self, texto: str, *, msg_id: str) -> int:
        antes = len(self.enviados)
        await self._processar(_payload(texto, msg_id=msg_id))
        return len(self.enviados) - antes

    async def conversa(self) -> asyncpg.Record:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "select estado, condominio_id, rascunho from conversas "
                "where telefone = $1 and status = 'ativa'",
                NUMERO,
            )

    async def solicitacoes(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """
                select s.id, s.tipo, s.status, s.descricao, s.telefone, s.anexos,
                       s.morador_id, s.unidade_id, s.origem_mensagem_id, c.slug
                  from solicitacoes s
                  join condominios c on c.id = s.condominio_id
                 order by s.created_at
                """
            )

    async def avisos(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "select a.status, a.texto, a.solicitacao_id, c.sindico_telefone "
                "from avisos_sindico a "
                "join condominios c on c.id = a.condominio_id order by a.created_at"
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
    uploads: list[dict] = []

    def envio(request: httpx.Request) -> httpx.Response:
        enviados.append(json.loads(request.content)["body"])
        return httpx.Response(
            200, json={"success": True, "data": {"message": "ok", "ticketId": 1}}
        )

    def storage(request: httpx.Request) -> httpx.Response:
        uploads.append({"url": str(request.url), "bytes": len(request.content),
                        "upsert": request.headers.get("x-upsert")})
        return httpx.Response(200, json={"Key": "anexos/x", "Id": "id-1"})

    async def _gerar_fake(pergunta, condominio_id, historico):
        raise AssertionError("o wizard não pode chamar a geração")

    async def _corpo(passos):
        pool = await asyncpg.create_pool(
            dsn=pg_dsn, min_size=1, max_size=4, init=db._registrar_codecs
        )
        await zpro_client.criar_cliente(transport=httpx.MockTransport(envio))
        await anexos.criar_cliente(transport=httpx.MockTransport(storage))
        monkeypatch.setattr(processador, "get_pool", lambda: pool)
        monkeypatch.setattr(processador, "responder_duvida", _gerar_fake)
        try:
            async with pool.acquire() as conn:
                await conn.execute("truncate condominios cascade")
                await _semear(conn)
            await passos(_Ambiente(pool, enviados, uploads))
        finally:
            async with pool.acquire() as conn:
                await conn.execute("truncate condominios cascade")
            await anexos.fechar_cliente()
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


async def _tenant_id(a: _Ambiente):
    async with a.pool.acquire() as conn:
        return await conn.fetchval("select id from condominios limit 1")


async def _ate_o_menu(a: _Ambiente) -> None:
    await a.entregar("oi")
    await a.entregar("1")  # escolhe o Gabro (único da lista)
    await a.entregar("1")  # confirma


# ── o caminho do MARCO ───────────────────────────────────────────────────────


def test_marco_ocorrencia_so_texto(ambiente):
    """tipo -> descrição -> pular foto -> confirma -> linha em solicitacoes."""
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)

        tela = await a.entregar("3")
        assert "Reclamação" in tela and "Manutenção" in tela
        assert (await a.conversa())["estado"] == "ocorrencia"

        tela = await a.entregar("1")  # reclamação
        assert "Descreva" in tela

        tela = await a.entregar("Vazamento no 3º andar 💧\nDesde ontem.")
        assert "foto" in tela.lower()

        tela = await a.entregar("1")  # seguir sem foto
        assert "Confirmar" in tela and "Vazamento no 3º andar" in tela

        tela = await a.entregar("1")  # confirma
        assert "Registrei" in tela

        linhas = await a.solicitacoes()
        assert len(linhas) == 1
        (s,) = linhas
        assert s["slug"] == "res-gabro"
        assert s["status"] == "aberta"
        assert s["tipo"] == "reclamacao"
        assert s["telefone"] == NUMERO
        assert s["descricao"] == "Vazamento no 3º andar 💧\nDesde ontem."
        assert s["anexos"] == []
        assert s["morador_id"] is None and s["unidade_id"] is None

        # o aviso ao síndico nasceu na MESMA transação da solicitação (Etapa 5)
        (aviso,) = await a.avisos()
        assert aviso["solicitacao_id"] == s["id"]
        assert aviso["status"] == "pendente"
        assert aviso["texto"].startswith(f"Ocorrência #{s['id'].hex[:8]}")
        assert "Vazamento no 3º andar" in aviso["texto"]
        assert NUMERO in aviso["texto"]
        assert "Aprovar" not in aviso["texto"]

        assert (await a.conversa())["estado"] == "menu"
        assert (await a.conversa())["rascunho"] is None
        assert a.uploads == []

    ambiente(passos)


def test_marco_ocorrencia_com_foto(ambiente):
    """A foto REAL do Z-PRO sobe no passo dela e vira anexo na solicitação."""
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("3")
        await a.entregar("3")  # manutenção
        await a.entregar("Portão da garagem emperrado")

        tela = await a.entregar_foto()
        assert "Confirmar" in tela and "foto anexada" in tela

        assert len(a.uploads) == 1
        assert a.uploads[0]["bytes"] == len(FOTO_BYTES)
        assert a.uploads[0]["upsert"] == "true"

        await a.entregar("1")

        (s,) = await a.solicitacoes()
        anexos_gravados = s["anexos"]
        assert len(anexos_gravados) == 1
        assert anexos_gravados[0]["bytes"] == len(FOTO_BYTES)
        assert anexos_gravados[0]["mimetype"] == "image/jpeg"
        # O caminho é {condominio_id}/{sha256}.jpg — tenant como prefixo, digest
        # como nome: é o que torna o upload idempotente.
        tenant = await _tenant_id(a)
        assert anexos_gravados[0]["caminho"] == (
            f"{tenant}/{anexos_gravados[0]['sha256']}.jpg"
        )

    ambiente(passos)


def test_foto_com_legenda_resolve_descricao_e_anexo_de_uma_vez(ambiente):
    """O atalho que o morador tenta naturalmente: fotografa e escreve na legenda."""
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("3")
        await a.entregar("2")  # ocorrência

        tela = await a.entregar_foto(legenda="Vazou muita água na garagem")
        assert "Confirmar" in tela
        assert "Vazou muita água na garagem" in tela
        assert "foto anexada" in tela

        await a.entregar("1")
        (s,) = await a.solicitacoes()
        assert s["descricao"] == "Vazou muita água na garagem"
        assert len(s["anexos"]) == 1

    ambiente(passos)


def test_foto_sem_legenda_na_descricao_guarda_anexo_e_ainda_pede_texto(ambiente):
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("3")
        await a.entregar("1")

        tela = await a.entregar_foto()
        assert "descrição" in tela.lower()
        assert len(a.uploads) == 1

        tela = await a.entregar("Infiltração na parede")
        assert "Confirmar" in tela and "foto anexada" in tela

        await a.entregar("1")
        (s,) = await a.solicitacoes()
        assert s["descricao"] == "Infiltração na parede"
        assert len(s["anexos"]) == 1

    ambiente(passos)


# ── as garantias ─────────────────────────────────────────────────────────────


def test_reprocessar_a_confirmacao_nao_duplica_nem_responde_de_novo(ambiente):
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("3")
        await a.entregar("1")
        await a.entregar("Barulho no salão")
        await a.entregar("1")  # sem foto
        await a.entregar("1", msg_id="CONFIRMA")

        assert len(await a.solicitacoes()) == 1

        reenvios = await a.reentregar("1", msg_id="CONFIRMA")
        assert reenvios == 0, "reprocessamento não pode reenviar"
        assert len(await a.solicitacoes()) == 1
        assert all(n == 1 for n in await a.respostas_por_entrada())

    ambiente(passos)


def test_escape_zero_volta_ao_menu_sem_gravar(ambiente):
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("3")
        await a.entregar("1")
        await a.entregar("comecei a descrever")

        tela = await a.entregar("0")
        assert "não registrei" in tela.lower()
        assert (await a.conversa())["estado"] == "menu"
        assert (await a.conversa())["rascunho"] is None
        assert await a.solicitacoes() == []

    ambiente(passos)


def test_rascunho_sobrevive_no_jsonb_entre_mensagens(ambiente):
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("3")
        assert (await a.conversa())["rascunho"] == {"passo": "tipo"}

        await a.entregar("2")
        rascunho = (await a.conversa())["rascunho"]
        assert rascunho["passo"] == "descricao"
        assert rascunho["tipo"] == "ocorrencia"

        await a.entregar("Cheiro de gás no corredor")
        rascunho = (await a.conversa())["rascunho"]
        assert rascunho["passo"] == "foto"
        assert rascunho["descricao"] == "Cheiro de gás no corredor"

    ambiente(passos)


def test_falha_do_storage_mantem_o_rascunho_e_pede_de_novo(ambiente):
    """O upload é a única rede do fluxo: falhar não pode perder o que o morador
    já respondeu."""
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        await a.entregar("3")
        await a.entregar("1")
        await a.entregar("Lâmpada queimada na garagem")

        # Troca manual, e não monkeypatch: o undo dele desfaria também o get_pool
        # que a fixture instalou.
        original = anexos.guardar

        async def _falha(*args, **kwargs):
            raise anexos.AnexoIndisponivelError("storage fora do ar")

        anexos.guardar = _falha
        try:
            tela = await a.entregar_foto()
        finally:
            anexos.guardar = original
        assert "não consegui guardar" in tela.lower()

        rascunho = (await a.conversa())["rascunho"]
        assert rascunho["passo"] == "foto"
        assert rascunho["descricao"] == "Lâmpada queimada na garagem"

        tela = await a.entregar("1")  # segue sem foto
        assert "Confirmar" in tela
        await a.entregar("1")
        (s,) = await a.solicitacoes()
        assert s["descricao"] == "Lâmpada queimada na garagem"
        assert s["anexos"] == []

    ambiente(passos)


def test_foto_fora_da_ocorrencia_nao_sobe_nada(ambiente):
    """A guarda de tipo do roteador cede num estado só."""
    async def passos(a: _Ambiente):
        await _ate_o_menu(a)
        tela = await a.entregar_foto()
        assert "só entendo texto" in tela.lower()
        assert a.uploads == []
        assert (await a.conversa())["estado"] == "menu"

    ambiente(passos)
