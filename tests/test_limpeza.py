"""Testes da limpeza de OCR (Fase 2 · Passo 5).

Unitários puros com fixtures sintéticas: os documentos reais do piloto contêm
dados pessoais (LGPD) e são gitignored — não podem virar fixture versionada.
Cada padrão de ruído observado nos OCR reais tem aqui uma miniatura: página de
assinaturas, página de desenho técnico, linha de cartório, cabeçalho caixa-alta
órfão. A validação contra os documentos reais é o --dry-run da ingestão.
"""

import pytest

from chunker import dividir_regimento
from limpeza import limpar_ocr

DOC = "Convenção Exemplo"

PAGINA_LEGAL = """\
===== Página 1 =====

CAPÍTULO I - DO OBJETIVO

Art. 1º - Primeira regra do condomínio exemplo.

[[CARTÓRIO: número de página "1" e rubrica no rodapé]]
"""

PAGINA_ASSINATURAS = """\
===== Página 2 =====

[Página composta por blocos de formulário de assinatura dos condôminos:]

Apartamento: [manuscrito: 802] Box:
Nome: [manuscrito: FULANA DE TAL EXEMPLO] [manuscrito: assinatura]
"""

PAGINA_DESENHO = """\
===== Página 3 =====

[desenho técnico: elevação de fachada]
305 -1005
REPRESENTAÇÃO DE COMO DEVE SER FIXADO O EXEMPLO
[rótulo no desenho: CAIXA EXTERNA EXEMPLO]
"""


# Derrubada de página inteira (LGPD primeiro)


@pytest.mark.parametrize(
    "marca",
    [
        "[assinatura]",
        "[Página composta por blocos de formulário de assinatura dos condôminos:]",
        "[Continuação dos blocos de assinatura:]",
    ],
)
def test_pagina_de_assinaturas_cai_inteira(marca):
    texto = PAGINA_LEGAL + f"===== Página 2 =====\n{marca}\nNome: BELTRANO SIGILOSO\n"
    resultado = limpar_ocr(texto)
    assert "BELTRANO SIGILOSO" not in resultado.texto
    assert resultado.paginas_assinatura == 1
    assert "Art. 1º" in resultado.texto


def test_nome_de_morador_nao_sobrevive_nem_no_relatorio():
    resultado = limpar_ocr(PAGINA_LEGAL + PAGINA_ASSINATURAS)
    despejo = resultado.model_dump_json()
    assert "FULANA" not in despejo


def test_pagina_de_desenho_cai_inteira_com_legendas_soltas():
    resultado = limpar_ocr(PAGINA_LEGAL + PAGINA_DESENHO)
    assert resultado.paginas_desenho == 1
    assert "REPRESENTAÇÃO" not in resultado.texto
    assert "305 -1005" not in resultado.texto


# Filtros de linha nas páginas mantidas


def test_linha_cartorio_removida_em_pagina_legal():
    resultado = limpar_ocr(PAGINA_LEGAL)
    assert "CARTÓRIO" not in resultado.texto
    assert resultado.linhas_cartorio == 1
    assert "Art. 1º" in resultado.texto


def test_cabecalho_caixa_alta_orfao_removido_e_reportado():
    texto = PAGINA_LEGAL + "DAS PENALIDADES\n\nArt. 2º - Multa simples.\n"
    resultado = limpar_ocr(texto)
    assert "DAS PENALIDADES" not in resultado.texto
    assert "DAS PENALIDADES" in resultado.cabecalhos_removidos
    assert "Art. 2º" in resultado.texto


def test_cabecalho_estrutural_fica_para_o_chunker():
    resultado = limpar_ocr(PAGINA_LEGAL)
    assert "CAPÍTULO I - DO OBJETIVO" in resultado.texto


def test_caixa_alta_com_digito_ou_palavra_unica_fica():
    texto = "RUA EXEMPLO, Nº 2220 – CIDADE/UF\nCONVENÇÃO\nArt. 1º - Regra.\n"
    resultado = limpar_ocr(texto)
    assert "RUA EXEMPLO, Nº 2220 – CIDADE/UF" in resultado.texto
    assert "CONVENÇÃO" in resultado.texto
    assert resultado.cabecalhos_removidos == ()


def test_linha_com_colchete_nao_classificada_e_mantida_como_suspeita():
    texto = "Art. 1º - Regra com ressalva [ilegível] no meio.\n"
    resultado = limpar_ocr(texto)
    assert "[ilegível]" in resultado.texto
    assert resultado.linhas_suspeitas == (
        "Art. 1º - Regra com ressalva [ilegível] no meio.",
    )


# Estrutura de páginas


def test_texto_sem_marcadores_passa_intacto():
    texto = "Art. 1º - Regra.\nParágrafo único. Detalhe."
    resultado = limpar_ocr(texto)
    assert resultado.texto == texto
    assert resultado.paginas_total == 1
    assert resultado.paginas_assinatura == 0
    assert resultado.linhas_cartorio == 0


def test_artigo_atravessa_quebra_de_pagina():
    texto = (
        "===== Página 1 =====\nArt. 1º - Regra que continua\n"
        "===== Página 2 =====\nna página seguinte.\n"
    )
    resultado = limpar_ocr(texto)
    assert resultado.paginas_total == 2
    chunks = dividir_regimento(resultado.texto, DOC)
    assert len(chunks) == 1
    assert "continua\nna página seguinte." in chunks[0].conteudo


def test_limpeza_e_idempotente():
    primeira = limpar_ocr(PAGINA_LEGAL + PAGINA_ASSINATURAS + PAGINA_DESENHO)
    segunda = limpar_ocr(primeira.texto)
    assert segunda.texto == primeira.texto
    assert segunda.paginas_assinatura == 0
    assert segunda.paginas_desenho == 0
    assert segunda.linhas_cartorio == 0
    assert segunda.cabecalhos_removidos == ()


# Fim a fim com o chunker


def test_fim_a_fim_ruido_nao_chega_aos_chunks():
    texto = (
        PAGINA_LEGAL
        + PAGINA_DESENHO
        + "===== Página 4 =====\n\nREGULAMENTO DE OBRAS\n\n"
        + "Art. 2º - Obras só em horário comercial.\n\n"
        + "[[CARTÓRIO: rubrica na margem direita]]\n"
        + PAGINA_ASSINATURAS
    )
    resultado = limpar_ocr(texto)
    chunks = dividir_regimento(resultado.texto, DOC)
    assert [c.fonte for c in chunks] == [f"{DOC}, Art. 1º", f"{DOC}, Art. 2º"]
    tudo = "\n".join(c.conteudo for c in chunks)
    for ruido in ("CARTÓRIO", "FULANA", "REPRESENTAÇÃO", "REGULAMENTO DE OBRAS"):
        assert ruido not in tudo
