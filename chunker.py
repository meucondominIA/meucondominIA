"""Chunker de regimentos: texto jurídico → chunks citáveis por artigo (função pura).

O artigo é a unidade de chunk — LC 95/1998, art. 10, I: "unidade básica de
articulação" (https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp95.htm). §§,
incisos e alíneas ficam DENTRO do chunk do artigo; cabeçalhos estruturais
(TÍTULO/CAPÍTULO/SEÇÃO/SUBSEÇÃO/LIVRO/PARTE + numeral) e a linha-título que
os segue são descartados — único descarte permitido (prefixar capítulo não
ajudou o retrieval e piorou distâncias, M9 §13 da fase). Preâmbulo (texto
antes do primeiro artigo) vira chunk próprio se não-em-branco.

Marcador de artigo só em início de linha e case-sensitive no A inicial:
"art."/"artigo" minúsculos são referência cruzada em linha quebrada de PDF,
não abertura de artigo (validado na Lei 4.591 real: 86/86 artigos, zero falso
positivo). A vírgula NÃO é delimitador final — impede "Art. 43, este
direito..." de virar chunk fantasma.

fonte canônica e determinística: "{documento}, Art. N" — ordinal de 1º a 9º,
cardinal de 10 em diante, sufixo maiúsculo preservado (LC 95, art. 10, I e
art. 12, III, "b"). A entrada pode grafar "Artigo 5°"/"ART. 5"; a saída é
sempre a canônica, porque o eval do Passo 7 compara fontes.

Guarda de tamanho: ≤ 20.000 bytes UTF-8 por conteudo — heurística anti-lixo
(2x o maior artigo real medido, Art. 31-F = 10.112 bytes; o max_length do
Pydantic conta caracteres, por isso o validator mede bytes). O enforcement
exato dos 8192 tokens/input é da própria API de embeddings, que conta como o
tiktoken (verificado com chamadas reais, fronteira 8192/8193; sem tiktoken em
runtime — D5). Fiel ao texto: artigo duplicado, fora de ordem ou revogado não
é erro — posicao distingue; curadoria é do Passo 5.
"""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

_MAX_BYTES_CONTEUDO = 20_000

TextoNaoEmBranco = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    conteudo: TextoNaoEmBranco
    fonte: TextoNaoEmBranco
    documento: TextoNaoEmBranco
    metadata: dict

    @field_validator("conteudo")
    @classmethod
    def _guarda_de_tamanho(cls, v: str) -> str:
        tamanho = len(v.encode("utf-8"))
        if tamanho > _MAX_BYTES_CONTEUDO:
            raise ValueError(
                f"conteudo com {tamanho} bytes UTF-8 excede a guarda anti-lixo "
                f"de {_MAX_BYTES_CONTEUDO} bytes"
            )
        return v


_MARCADOR_ARTIGO = re.compile(
    r"^(?:Art|ART)(?:igo|IGO)?(?:\.\s*|\s+)(\d+)[º°o]?(?:-([A-Z]+))?(?=[\s.:–—-]|$)"
)

CABECALHO_ESTRUTURAL = re.compile(
    r"^(?:T[ÍI]TULO|CAP[ÍI]TULO|SE[ÇC][ÃA]O|SUBSE[ÇC][ÃA]O|LIVRO|PARTE)"
    r"\s+(?:[IVXLCDM]+|\d+)(?:-[A-Z]+)?\b"
)


def _designacao_canonica(marcador: re.Match[str]) -> str:
    numero = int(marcador.group(1))
    sufixo = marcador.group(2)
    base = f"{numero}º" if numero <= 9 else str(numero)
    return f"{base}-{sufixo}" if sufixo else base


def dividir_regimento(texto: str, documento: str) -> list[Chunk]:
    """Divide o texto de um regimento em chunks citáveis, um por artigo."""
    if not texto.strip():
        raise ValueError("texto em branco: não há regimento para dividir")
    if not documento.strip():
        raise ValueError("documento em branco: chunk sem identidade não é citável")
    documento = documento.strip()

    blocos: list[tuple[str | None, list[str]]] = [(None, [])]
    aguardando_linha_titulo = False
    for linha in texto.split("\n"):
        marcador = _MARCADOR_ARTIGO.match(linha)
        if marcador:
            blocos.append((_designacao_canonica(marcador), [linha]))
            aguardando_linha_titulo = False
            continue
        if CABECALHO_ESTRUTURAL.match(linha):
            aguardando_linha_titulo = True
            continue
        if aguardando_linha_titulo:
            if linha.strip():
                aguardando_linha_titulo = False
            continue
        blocos[-1][1].append(linha)

    if len(blocos) == 1:
        raise ValueError(
            f"nenhum artigo reconhecível em {documento!r}: esperava marcador "
            "'Art./ART./Artigo + número' em início de linha — confira a "
            "extração do texto"
        )

    chunks: list[Chunk] = []
    for artigo, linhas in blocos:
        conteudo = "\n".join(linhas).strip()
        if not conteudo:
            continue
        citacao = f"Art. {artigo}" if artigo is not None else "Preâmbulo"
        chunks.append(
            Chunk(
                conteudo=conteudo,
                fonte=f"{documento}, {citacao}",
                documento=documento,
                metadata={"artigo": artigo, "posicao": len(chunks)},
            )
        )
    return chunks
