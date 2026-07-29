"""Guarda de anexos no Supabase Storage (espelho do zpro_client.py: mesmo ciclo
de vida, mesma forma de adapter de saída).

Não cabe no db.py: aquele é pool asyncpg (protocolo Postgres, senha do
DATABASE_URL); aqui é HTTP com a chave secreta. E não dá para subir bytes por
SQL — storage.objects é só metadado.

O único módulo novo com rede, e por isso nunca é chamado com conexão do pool na
mão: quem o invoca é o processador, entre as janelas de conexão.

Caminho = `{condominio_id}/{sha256}.{ext}`:
- o tenant vira PREFIXO, então policy de RLS por condomínio (Fase 5) entra sem
  remexer em dado gravado;
- o sha256 torna o upload idempotente com `x-upsert`.

A doc desaconselha sobrescrever (o CDN propaga devagar e serve conteúdo velho).
Não se aplica aqui: o caminho É o digest do conteúdo, então sobrescrever grava
bytes idênticos. Medido — dois uploads no mesmo caminho devolvem o MESMO Id.

Sem solicitacao_id no caminho de propósito: o upload acontece no passo da foto, e
a solicitação só nasce na confirmação.
"""

import logging

import httpx

from config import settings
from ocorrencia import Anexo
from zpro_models import MidiaRecebida

logger = logging.getLogger(__name__)

_cliente: httpx.AsyncClient | None = None

_EXTENSOES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# O Storage responde HTTP 400 para tudo e põe o código REAL no corpo (medido:
# teto -> 413, mime -> 415, duplicata -> 409). Estes dois são culpa do arquivo,
# não da rede: retentar não muda o desfecho, e o morador precisa saber.
_RECUSA_DO_ARQUIVO = {"413", "415"}


class AnexoRecusadoError(Exception):
    """A mídia não é guardável. Retentar não adianta."""


class AnexoIndisponivelError(Exception):
    """Rede, credencial ou Storage fora do ar — retentável."""


async def criar_cliente(transport: httpx.AsyncBaseTransport | None = None) -> None:
    """Abre o cliente HTTP. Chamado UMA vez, no startup da aplicação."""
    global _cliente
    if _cliente is None:
        chave = settings.supabase_secret_key
        _cliente = httpx.AsyncClient(
            base_url=f"{settings.supabase_url.rstrip('/')}/storage/v1",
            headers={"apikey": chave, "Authorization": f"Bearer {chave}"},
            timeout=httpx.Timeout(
                settings.storage_timeout_seconds,
                connect=settings.storage_connect_timeout_seconds,
            ),
            transport=transport or httpx.AsyncHTTPTransport(retries=2),
        )


async def fechar_cliente() -> None:
    """Fecha o cliente. Chamado no shutdown da aplicação."""
    global _cliente
    if _cliente is None:
        return
    await _cliente.aclose()
    _cliente = None


def get_cliente() -> httpx.AsyncClient:
    if _cliente is None:
        raise RuntimeError(
            "Cliente de Storage não inicializado — chame criar_cliente() no startup."
        )
    return _cliente


def caminho_de(midia: MidiaRecebida, *, condominio_id) -> str:
    """Puro: testável sem rede, e é aqui que o tipo é aceito ou recusado."""
    extensao = _EXTENSOES.get(midia.mimetype)
    if extensao is None:
        raise AnexoRecusadoError(f"tipo não aceito: {midia.mimetype}")
    return f"{condominio_id}/{midia.sha256}.{extensao}"


def _classificar(resp: httpx.Response, caminho: str) -> Exception:
    """Traduz a resposta de erro: quem manda é o corpo, não o status HTTP."""
    try:
        corpo = resp.json()
    except ValueError:
        corpo = {}
    codigo = str(corpo.get("statusCode", resp.status_code))
    detalhe = corpo.get("message") or resp.text[:200]

    if codigo in _RECUSA_DO_ARQUIVO:
        return AnexoRecusadoError(f"{caminho}: {detalhe}")
    return AnexoIndisponivelError(f"{caminho}: [{codigo}] {detalhe}")


async def guardar(midia: MidiaRecebida, *, condominio_id) -> Anexo:
    """Sobe a mídia e devolve a COORDENADA — nunca a URL, que expiraria.

    Upload padrão (não resumable): a doc o indica até 6 MB, e o teto do bucket é
    5 MB. Tamanho e tipo são conferidos ANTES de subir — recusar aqui poupa 5 MB
    de rede para ouvir o mesmo não do outro lado.
    """
    if len(midia.conteudo) > settings.anexo_max_bytes:
        raise AnexoRecusadoError(
            f"{len(midia.conteudo)} bytes acima do teto de {settings.anexo_max_bytes}"
        )

    caminho = caminho_de(midia, condominio_id=condominio_id)
    try:
        resp = await get_cliente().post(
            f"/object/{settings.anexos_bucket}/{caminho}",
            headers={"Content-Type": midia.mimetype, "x-upsert": "true"},
            content=midia.conteudo,
        )
    except httpx.HTTPError as exc:
        raise AnexoIndisponivelError(f"{caminho}: {exc}") from exc

    if resp.is_error:
        raise _classificar(resp, caminho)

    logger.info("anexo guardado: %s (%d bytes)", caminho, len(midia.conteudo))
    return Anexo(
        bucket=settings.anexos_bucket,
        caminho=caminho,
        mimetype=midia.mimetype,
        bytes=len(midia.conteudo),
        sha256=midia.sha256,
    )


async def apagar(caminhos: list[str]) -> None:
    """Remove objetos do bucket. Só a API apaga o arquivo — deletar a linha de
    storage.objects deixaria o arquivo no S3."""
    if not caminhos:
        return
    try:
        resp = await get_cliente().request(
            "DELETE",
            f"/object/{settings.anexos_bucket}",
            json={"prefixes": caminhos},
        )
    except httpx.HTTPError as exc:
        raise AnexoIndisponivelError(f"falha ao apagar {len(caminhos)}: {exc}") from exc

    if resp.is_error:
        raise _classificar(resp, f"{len(caminhos)} objeto(s)")
    logger.info("anexos apagados: %d", len(caminhos))
