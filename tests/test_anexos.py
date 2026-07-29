"""Testes da guarda de anexos no Storage (Fase 4 · Etapa 4).

Sem rede: httpx.MockTransport intercepta o request. As respostas de erro são as
CAPTURADAS do Supabase real em 28/07/2026 — e o que elas ensinam é que o status
HTTP é 400 para tudo, com o código real dentro do corpo.

Doc: https://www.python-httpx.org/advanced/transports/#mock-transports
"""

import asyncio
from uuid import uuid4

import httpx
import pytest

import anexos
from anexos import AnexoIndisponivelError, AnexoRecusadoError, caminho_de, guardar
from config import settings
from zpro_models import MidiaRecebida

CONDOMINIO = uuid4()
SHA = "c7a24f3c1e962bfc42f471aa00cb3b30a8ce93dd63febd602b641f12f52609b9"
FOTO = MidiaRecebida(conteudo=b"\xff\xd8\xff" + b"x" * 500, mimetype="image/jpeg", sha256=SHA)

# Respostas REAIS do Supabase: HTTP 400 em todas, código verdadeiro no corpo.
TETO = (400, {"statusCode": "413", "error": "Payload too large",
              "message": "The object exceeded the maximum allowed size"})
MIME = (400, {"statusCode": "415", "error": "invalid_mime_type",
              "message": "mime type text/plain is not supported"})
DUPLICATA = (400, {"statusCode": "409", "error": "Duplicate",
                   "message": "The resource already exists"})
SEM_PERMISSAO = (400, {"statusCode": "403", "error": "Unauthorized",
                       "message": "new row violates row-level security policy"})
OK = (200, {"Key": "anexos/x/y.jpg", "Id": "f346ddcb-1e94-467d-ba67-3fddb23a89cb"})


async def _guardar(handler, midia=FOTO):
    await anexos.criar_cliente(transport=httpx.MockTransport(handler))
    try:
        return await guardar(midia, condominio_id=CONDOMINIO)
    finally:
        await anexos.fechar_cliente()


def _responde(par):
    status, corpo = par
    return lambda request: httpx.Response(status, json=corpo)


# ── o caminho, que é puro ────────────────────────────────────────────────────


def test_caminho_comeca_pelo_tenant():
    """O isolamento vira PREFIXO: é o que deixa uma policy por condomínio entrar
    na Fase 5 sem remexer em dado gravado."""
    assert caminho_de(FOTO, condominio_id=CONDOMINIO).startswith(f"{CONDOMINIO}/")


def test_caminho_e_o_digest_do_conteudo():
    """Content-addressed: reenviar a mesma foto converge no mesmo objeto, e é por
    isso que o x-upsert não corre risco de servir conteúdo velho."""
    assert caminho_de(FOTO, condominio_id=CONDOMINIO) == f"{CONDOMINIO}/{SHA}.jpg"


@pytest.mark.parametrize(
    "mimetype,extensao",
    [("image/jpeg", "jpg"), ("image/png", "png"), ("image/webp", "webp")],
)
def test_extensao_sai_do_mimetype(mimetype, extensao):
    midia = FOTO.model_copy(update={"mimetype": mimetype})
    assert caminho_de(midia, condominio_id=CONDOMINIO).endswith(f".{extensao}")


@pytest.mark.parametrize("mimetype", ["application/pdf", "video/mp4", "text/plain"])
def test_tipo_fora_do_bucket_e_recusado_antes_de_subir(mimetype):
    midia = FOTO.model_copy(update={"mimetype": mimetype})
    with pytest.raises(AnexoRecusadoError, match="tipo não aceito"):
        caminho_de(midia, condominio_id=CONDOMINIO)


# ── o upload ─────────────────────────────────────────────────────────────────


def test_upload_devolve_a_coordenada_e_nunca_uma_url():
    anexo = asyncio.run(_guardar(_responde(OK)))
    assert anexo.bucket == settings.anexos_bucket
    assert anexo.caminho == f"{CONDOMINIO}/{SHA}.jpg"
    assert anexo.sha256 == SHA
    assert anexo.bytes == len(FOTO.conteudo)
    assert "http" not in anexo.model_dump_json()


def test_manda_upsert_e_o_content_type_da_midia():
    visto = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto.update(request.headers)
        visto["url"] = str(request.url)
        visto["corpo"] = request.content
        return httpx.Response(200, json=OK[1])

    asyncio.run(_guardar(handler))
    assert visto["x-upsert"] == "true"
    assert visto["content-type"] == "image/jpeg"
    assert visto["corpo"] == FOTO.conteudo
    assert f"/storage/v1/object/{settings.anexos_bucket}/{CONDOMINIO}/" in visto["url"]


def test_manda_as_duas_credenciais():
    """O Storage exige apikey E Authorization; só uma delas dá 401."""
    visto = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto.update(request.headers)
        return httpx.Response(200, json=OK[1])

    asyncio.run(_guardar(handler))
    assert visto["apikey"] == settings.supabase_secret_key
    assert visto["authorization"] == f"Bearer {settings.supabase_secret_key}"


def test_grande_demais_e_recusado_sem_gastar_rede():
    """Conferir o tamanho antes poupa subir 5 MB para ouvir o mesmo não."""
    def handler(request):
        raise AssertionError("não deveria ter subido nada")

    gorda = FOTO.model_copy(
        update={"conteudo": b"x" * (settings.anexo_max_bytes + 1)}
    )
    with pytest.raises(AnexoRecusadoError, match="acima do teto"):
        asyncio.run(_guardar(handler, gorda))


# ── a tradução dos erros: o corpo manda, não o status ────────────────────────


@pytest.mark.parametrize("resposta", [TETO, MIME], ids=["413_teto", "415_mime"])
def test_recusa_do_arquivo_nao_e_retentavel(resposta):
    """413 e 415 são culpa do arquivo: retentar não muda o desfecho."""
    with pytest.raises(AnexoRecusadoError):
        asyncio.run(_guardar(_responde(resposta)))


@pytest.mark.parametrize(
    "resposta", [DUPLICATA, SEM_PERMISSAO], ids=["409_duplicata", "403_rls"]
)
def test_o_resto_e_indisponibilidade(resposta):
    with pytest.raises(AnexoIndisponivelError):
        asyncio.run(_guardar(_responde(resposta)))


def test_o_codigo_do_corpo_vence_o_status_http():
    """O Storage devolve HTTP 400 para tudo. Se a gente olhasse só o status, o
    teto do bucket viraria 'indisponível' e seria retentado para sempre."""
    with pytest.raises(AnexoRecusadoError) as erro:
        asyncio.run(_guardar(_responde(TETO)))
    assert "exceeded the maximum allowed size" in str(erro.value)


def test_erro_sem_json_no_corpo_nao_quebra():
    def handler(request):
        return httpx.Response(502, text="<html>bad gateway</html>")

    with pytest.raises(AnexoIndisponivelError, match="502"):
        asyncio.run(_guardar(handler))


def test_falha_de_rede_vira_indisponivel():
    def handler(request):
        raise httpx.ConnectError("sem rota para o host")

    with pytest.raises(AnexoIndisponivelError):
        asyncio.run(_guardar(handler))


def test_usar_sem_startup_falha_claro():
    with pytest.raises(RuntimeError, match="startup"):
        asyncio.run(guardar(FOTO, condominio_id=CONDOMINIO))
