"""Limpeza de OCR: transcrição bruta de cartório → texto estrutural p/ chunker.

Filtro de SEGURANÇA antes de qualidade: roda a montante do chunker E de
qualquer rede — dado pessoal (LGPD) não pode nem chegar à OpenAI. A unidade
de decisão é a PÁGINA (delimitador "===== Página N ====="): página com marca
de assinatura (nomes de moradores) ou de desenho técnico (legendas soltas,
não texto legal) é derrubada inteira, e essa checagem vem antes de todas as
outras. Nas páginas mantidas caem as linhas [[CARTÓRIO: ...]] (carregam nomes
de tabeliães) e os cabeçalhos de seção em caixa-alta sem palavra-chave
estrutural ("DAS PENALIDADES"), que o chunker não reconhece e vazariam para o
fim do artigo anterior.

Cabeçalho estrutural (CAPÍTULO I...) NÃO é removido aqui: o chunker o
reconhece e descarta junto a linha-título seguinte; removê-lo na limpeza
deixaria essa linha-título órfã vazar. Por isso a exclusão reusa o regex do
próprio chunker — fonte única, sem deriva entre os dois módulos.

Linha mantida que ainda contém colchete não é erro: vira linha_suspeita no
relatório, tripwire para auditoria humana no --dry-run da ingestão. Regras
validadas nos dois documentos reais do piloto (17/07/2026): 56+88 artigos
exatos, zero nome vazado, zero suspeita residual. Função pura, sem I/O.
"""

import re

from pydantic import BaseModel, ConfigDict

from chunker import CABECALHO_ESTRUTURAL

_PAGINA = re.compile(r"^=====\s*Página\s+\d+\s*=====$")
_CARTORIO = re.compile(r"^\[\[CARTÓRIO:.*\]\]$")
_ASSINATURA = re.compile(
    r"\[assinatura\]|blocos de (?:formulário de )?assinatura", re.IGNORECASE
)
_DESENHO = re.compile(r"\[desenho técnico:", re.IGNORECASE)
_CAIXA_ALTA = re.compile(r"^[A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇ][A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇ \-–—]*$")


class ResultadoLimpeza(BaseModel):
    model_config = ConfigDict(frozen=True)

    texto: str
    paginas_total: int
    paginas_assinatura: int
    paginas_desenho: int
    linhas_cartorio: int
    cabecalhos_removidos: tuple[str, ...]
    linhas_suspeitas: tuple[str, ...]


def _paginas(texto: str) -> list[list[str]]:
    paginas: list[list[str]] = [[]]
    for linha in texto.split("\n"):
        if _PAGINA.match(linha.strip()):
            paginas.append([])
        else:
            paginas[-1].append(linha)
    return paginas


def _cabecalho_orfao(linha: str) -> bool:
    return (
        bool(_CAIXA_ALTA.match(linha))
        and len(linha.split()) >= 2
        and not CABECALHO_ESTRUTURAL.match(linha)
    )


def limpar_ocr(texto: str) -> ResultadoLimpeza:
    """Remove andaime de OCR e dados pessoais; devolve texto limpo + relatório."""
    paginas = _paginas(texto)
    mantidas: list[str] = []
    assinatura = desenho = cartorio = 0
    cabecalhos: list[str] = []
    suspeitas: list[str] = []
    for pagina in paginas:
        corpo = "\n".join(pagina)
        if _ASSINATURA.search(corpo):
            assinatura += 1
            continue
        if _DESENHO.search(corpo):
            desenho += 1
            continue
        for linha in pagina:
            limpa = linha.strip()
            if _CARTORIO.match(limpa):
                cartorio += 1
                continue
            if _cabecalho_orfao(limpa):
                cabecalhos.append(limpa)
                continue
            if "[" in linha or "]" in linha:
                suspeitas.append(limpa)
            mantidas.append(linha)
    return ResultadoLimpeza(
        texto="\n".join(mantidas),
        paginas_total=max(1, len(paginas) - 1),
        paginas_assinatura=assinatura,
        paginas_desenho=desenho,
        linhas_cartorio=cartorio,
        cabecalhos_removidos=tuple(cabecalhos),
        linhas_suspeitas=tuple(suspeitas),
    )
