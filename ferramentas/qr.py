"""CLI offline do QR de entrada: slug do condomínio → arquivo para a arte (Etapa 7).

Não roda dentro do FastAPI: faz o próprio bootstrap de pool, espelhando o
lifespan do main.py, e fecha no finally. Só lê o banco.

A ordem importa e é a garantia da etapa: resolve o slug (filtrando `ativo`),
monta a frase a partir do `nome`, e ANTES de gravar qualquer arquivo passa essa
frase pelo resolvedor de PRODUÇÃO. QR que não resolve não chega a existir — é o
que impede um prefixo divergente virar cartaz impresso que ninguém consegue usar.

O nível de correção de erro é fixado em M (15%) e o Micro QR é proibido: o
default do segno é L (7%), pensado para tela, e ele pode emitir Micro QR, que
nem todo leitor de celular lê. Papel na parede se suja, amassa e é lido de
longe (https://segno.readthedocs.io/en/latest/api.html).

Uso: venv/bin/python -m ferramentas.qr --condominio res-gabro
Saída: 0 sucesso · 1 falha de operação · 2 erro de uso (argparse).
"""

import argparse
import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

import segno

from condominios import (
    FRASE_QR,
    CondominioElegivel,
    buscar_elegivel_por_slug,
    resolver_por_frase,
)
from config import settings
from db import criar_pool, fechar_pool, get_pool


class ErroDeOperacao(Exception):
    """Falha esperada de operação: vira mensagem limpa no stderr e exit 1."""


def _slug_condominio(valor: str) -> str:
    slug = valor.strip()
    if not slug:
        raise argparse.ArgumentTypeError("slug em branco: não identifica condomínio")
    return slug


def _montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qr",
        description="Gera o QR de entrada de um condomínio, para a arte do cartaz.",
    )
    parser.add_argument(
        "--condominio",
        metavar="SLUG",
        required=True,
        type=_slug_condominio,
        help="slug do condomínio",
    )
    parser.add_argument(
        "--formato",
        choices=("svg", "png"),
        default="svg",
        help="svg (vetor, para a arte) ou png (default: svg)",
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=None,
        metavar="ARQUIVO",
        help="caminho do arquivo (default: qr-<slug>.<formato>)",
    )
    return parser


def montar_link(frase: str, numero: str) -> str:
    """O click-to-chat do WhatsApp (faq.whatsapp.com/5913398998672934).

    A frase vai url-encoded; o acento vira %C3%A1 e volta acento no aparelho.
    """
    return f"https://wa.me/{numero}?text={quote(frase)}"


async def _preparar(slug: str) -> tuple[CondominioElegivel, str]:
    """Resolve o condomínio e PROVA que a frase gerada resolve de volta nele."""
    await criar_pool()
    try:
        async with get_pool().acquire() as conn:
            alvo = await buscar_elegivel_por_slug(conn, slug)
            if alvo is None:
                raise ErroDeOperacao(
                    f"condomínio {slug!r} não encontrado ou inativo — confira o "
                    "slug e a coluna `ativo` na tabela condominios"
                )
            frase = FRASE_QR + alvo.nome
            volta = await resolver_por_frase(conn, frase)
    finally:
        await fechar_pool()

    if volta is None or volta.id != alvo.id:
        raise ErroDeOperacao(
            f"a frase gerada para {alvo.nome!r} não resolve de volta para ele — "
            "nenhum arquivo foi gravado. Nome ambíguo (homônimo) ou FRASE_QR "
            "divergente do que o atendimento espera."
        )
    return alvo, frase


def main(argv: list[str] | None = None) -> None:
    args = _montar_parser().parse_args(argv)

    numero = (settings.whatsapp_numero or "").strip()
    if not numero:
        sys.exit(
            "WHATSAPP_NUMERO não configurado no .env — sem ele o QR não tem "
            "para onde apontar (veja .env.example)"
        )

    try:
        alvo, frase = asyncio.run(_preparar(args.condominio))
    except ErroDeOperacao as exc:
        sys.exit(str(exc))

    link = montar_link(frase, numero)
    qr = segno.make(link, error="M", micro=False)
    saida = args.saida or Path(f"qr-{args.condominio}.{args.formato}")
    qr.save(saida, scale=8, border=4)

    print(f"condomínio  {alvo.nome}")
    print(f"frase       {frase}")
    print(f"link        {link}")
    print(f"gravado     {saida}  (versão {qr.version}, correção M)")
    print("\n✓ ida e volta conferida: esta frase resolve para este condomínio")
    print("⚠ escaneie este QR antes de mandar para a arte — o número do canal")
    print("  é o único dado aqui que o sistema não tem como validar sozinho")


if __name__ == "__main__":
    main()
