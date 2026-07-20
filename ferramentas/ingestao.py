"""CLI offline de ingestão: OCR bruto → regras citáveis no banco (Fase 2 · Passo 5).

Não roda dentro do FastAPI: faz o próprio bootstrap (pool + cliente OpenAI),
espelhando o lifespan do main.py, e fecha tudo no finally. A ordem do pipeline
é a regra de ouro do projeto: limpeza (LGPD primeiro) → chunker → [--dry-run
para aqui] → resolve o slug (conexão curta, devolvida antes da rede) →
embeddings (rede, NENHUMA conn do pool adquirida) → transação única
delete+insert (substituição atômica: commit no sucesso, rollback em exceção).
A conexão da escrita só é adquirida com todos os vetores já em memória.

--dry-run é a auditoria humana pré-ingestão: imprime o relatório da limpeza
(páginas derrubadas, cabeçalhos, linhas suspeitas) e dos chunks, sem tocar
OpenAI nem banco — nem bootstrap acontece. "substituídos: 0" numa reingestão
é o alarme de typo no --documento.

Saída: 0 sucesso · 1 falha de operação (mensagem limpa no stderr, via
sys.exit) · 2 erro de uso (argparse). Falha inesperada sobe crua — traceback
é informação e o processo já termina não-zero.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

import embeddings
from chunker import Chunk, dividir_regimento
from condominios import buscar_id_por_slug
from db import criar_pool, fechar_pool, get_pool
from limpeza import ResultadoLimpeza, limpar_ocr
from regras import apagar_regras_do_documento, inserir_regras


class ErroDeOperacao(Exception):
    """Falha esperada de operação: vira mensagem limpa no stderr e exit 1."""


def _montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingestao",
        description="Ingere um documento de regras (texto de OCR) na tabela regras.",
    )
    parser.add_argument("arquivo", type=Path, help="caminho do .txt do OCR")
    parser.add_argument(
        "--documento",
        required=True,
        help="nome canônico citável (vira a fonte) e chave de reingestão",
    )
    alvo = parser.add_mutually_exclusive_group(required=True)
    alvo.add_argument("--condominio", metavar="SLUG", help="slug do condomínio dono")
    alvo.add_argument(
        "--geral", action="store_true", help="escopo geral (sem condomínio)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="só limpeza + chunks + relatório; sem OpenAI e sem banco",
    )
    return parser


def _relatorio_limpeza(resultado: ResultadoLimpeza) -> None:
    print(
        f"limpeza: {resultado.paginas_total} página(s) | derrubadas: "
        f"{resultado.paginas_assinatura} de assinaturas, "
        f"{resultado.paginas_desenho} de desenho técnico | "
        f"linhas de cartório removidas: {resultado.linhas_cartorio}"
    )
    for cabecalho in resultado.cabecalhos_removidos:
        print(f"  cabeçalho de seção removido: {cabecalho}")
    for linha in resultado.linhas_suspeitas:
        print(f"  AVISO — linha com colchete mantida (auditar): {linha}")


def _relatorio_chunks(chunks: list[Chunk]) -> None:
    artigos = sum(1 for c in chunks if c.metadata["artigo"] is not None)
    preambulo = ", 1 preâmbulo" if len(chunks) > artigos else ""
    maior = max(chunks, key=lambda c: len(c.conteudo.encode("utf-8")))
    print(f"chunks: {len(chunks)} ({artigos} artigo(s){preambulo})")
    print(f"  primeiro: {chunks[0].fonte} | último: {chunks[-1].fonte}")
    print(f"  maior: {maior.fonte} — {len(maior.conteudo.encode('utf-8'))} bytes")


async def _ingerir(chunks: list[Chunk], *, documento: str, slug: str | None) -> None:
    await criar_pool()
    await embeddings.criar_cliente()
    try:
        condominio_id: UUID | None = None
        if slug is not None:
            async with get_pool().acquire() as conn:
                condominio_id = await buscar_id_por_slug(conn, slug)
            if condominio_id is None:
                raise ErroDeOperacao(
                    f"condomínio {slug!r} não encontrado — confira o slug na "
                    "tabela condominios"
                )

        vetores = await embeddings.gerar_embeddings(
            [chunk.conteudo for chunk in chunks], caminho="ingestao"
        )

        async with get_pool().acquire() as conn, conn.transaction():
            substituidas = await apagar_regras_do_documento(
                conn, condominio_id=condominio_id, documento=documento
            )
            await inserir_regras(conn, chunks, vetores, condominio_id=condominio_id)

        escopo = "escopo geral" if slug is None else f"condomínio {slug!r}"
        print(
            f"ingestão concluída ({escopo}): {len(chunks)} chunks gravados, "
            f"{substituidas} antigos substituídos."
        )
    finally:
        await embeddings.fechar_cliente()
        await fechar_pool()


def main(argv: list[str] | None = None) -> None:
    args = _montar_parser().parse_args(argv)

    try:
        texto = args.arquivo.read_text(encoding="utf-8")
    except OSError as exc:
        sys.exit(f"erro ao ler {args.arquivo}: {exc}")

    resultado = limpar_ocr(texto)
    _relatorio_limpeza(resultado)
    try:
        chunks = dividir_regimento(resultado.texto, args.documento)
    except ValueError as exc:
        sys.exit(f"erro no chunking: {exc}")
    _relatorio_chunks(chunks)

    if args.dry_run:
        print("dry-run: nada foi enviado à OpenAI nem gravado no banco.")
        return

    try:
        asyncio.run(_ingerir(chunks, documento=args.documento, slug=args.condominio))
    except ErroDeOperacao as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
