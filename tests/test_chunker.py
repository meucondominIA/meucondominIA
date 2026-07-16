"""Testes do chunker de regimentos (Fase 2 · Passo 3).

Unitários puros, sem rede e sem custo: um regimento sintético pequeno cobre
as variantes controladas (marcadores, cabeçalhos, bordas, erros) e a Lei
4.591/64 real (fixture extraída do PDF via pypdf) prova o reconhecimento em
documento de verdade — 86 artigos, sequência completa e maior chunk com os
valores medidos.

A regressão strip-antes-de-min_length existe porque a doc do Pydantic não
declara essa ordem (verificado em 15/07/2026 na página de StringConstraints);
se uma versão futura inverter, o teste acusa.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from chunker import Chunk, dividir_regimento

FIXTURE_LEI = Path(__file__).parent / "fixtures" / "lei-4591-extraida.txt"

DOC = "Regimento Exemplo"
DOC_LEI = "Lei 4.591/1964"

REGIMENTO = """\
REGIMENTO INTERNO DO CONDOMÍNIO EXEMPLO
Aprovado em assembleia geral.
CAPÍTULO I
DAS DISPOSIÇÕES GERAIS
Art. 1º O presente regimento disciplina a convivência no condomínio.
Parágrafo único. Aplica-se a moradores, locatários e visitantes.
Art. 2º São deveres dos condôminos:
I - respeitar o silêncio entre 22h e 8h;
II - zelar pelas áreas comuns.
CAPÍTULO II
DO USO DAS ÁREAS COMUNS
Art. 3º A piscina funciona das 8h às 22h.
"""


def _chunk(**campos) -> Chunk:
    base = {
        "conteudo": "Art. 1º Regra.",
        "fonte": f"{DOC}, Art. 1º",
        "documento": DOC,
        "metadata": {"artigo": "1º", "posicao": 0},
    }
    base.update(campos)
    return Chunk(**base)


def _chunks_da_lei() -> list[Chunk]:
    return dividir_regimento(FIXTURE_LEI.read_text(encoding="utf-8"), DOC_LEI)


# Modelo Chunk


def test_chunk_frozen_rejeita_atribuicao():
    chunk = _chunk()
    with pytest.raises(ValidationError, match="frozen"):
        chunk.conteudo = "outro"


def test_campos_em_branco_sao_rejeitados():
    for campo in ("conteudo", "fonte", "documento"):
        with pytest.raises(ValidationError, match="string_too_short"):
            _chunk(**{campo: "   "})


def test_regressao_strip_roda_antes_do_min_length():
    with pytest.raises(ValidationError, match="string_too_short"):
        _chunk(conteudo=" \n\t ")


def test_guarda_de_tamanho_passa_justo_em_20000_bytes():
    assert _chunk(conteudo="a" * 20_000).conteudo == "a" * 20_000


def test_guarda_de_tamanho_estoura_acima_de_20000_bytes():
    with pytest.raises(ValidationError, match="20000 bytes"):
        _chunk(conteudo="a" * 20_001)


def test_guarda_de_tamanho_conta_bytes_nao_caracteres():
    assert _chunk(conteudo="§" * 10_000).conteudo == "§" * 10_000
    with pytest.raises(ValidationError, match="excede a guarda"):
        _chunk(conteudo="§" * 10_001)


# Reconhecimento de marcador e canonização da fonte


def test_variantes_tipograficas_canonizam_para_a_mesma_fonte():
    variantes = [
        "Art. 5º",
        "Art. 5°",
        "Art. 5o",
        "Art 5",
        "Art.   5",
        "Artigo 5º",
        "ART. 5",
        "ARTIGO 5",
    ]
    for marcador in variantes:
        [chunk] = dividir_regimento(f"{marcador} Fica proibido barulho.", DOC)
        assert chunk.fonte == f"{DOC}, Art. 5º", marcador
        assert chunk.metadata["artigo"] == "5º", marcador


def test_cardinal_de_10_em_diante_mesmo_se_a_entrada_grafar_ordinal():
    [chunk] = dividir_regimento("Art. 10º Regra de exemplo.", DOC)
    assert chunk.fonte == f"{DOC}, Art. 10"


def test_sufixo_maiusculo_preservado():
    [cardinal] = dividir_regimento("Art. 30-A. (Artigo revogado.)", DOC)
    assert cardinal.fonte == f"{DOC}, Art. 30-A"
    assert cardinal.metadata["artigo"] == "30-A"
    [ordinal] = dividir_regimento("Art. 5º-A Regra de exemplo.", DOC)
    assert ordinal.fonte == f"{DOC}, Art. 5º-A"


def test_referencia_cruzada_minuscula_nao_abre_chunk():
    texto = (
        "Art. 1º Nos casos previstos no\n"
        "art. 43, este direito aplica-se ao locatário, e o\n"
        "artigo 12 também se aplica.\n"
    )
    [chunk] = dividir_regimento(texto, DOC)
    assert "art. 43, este direito" in chunk.conteudo
    assert "artigo 12" in chunk.conteudo


def test_virgula_apos_o_numero_nao_e_delimitador():
    texto = "Art. 1º Nos termos do\nArt. 43, este direito é assegurado.\n"
    [chunk] = dividir_regimento(texto, DOC)
    assert "Art. 43, este direito" in chunk.conteudo


# Montagem: preâmbulo, corte e descarte de cabeçalho


def test_preambulo_vira_chunk_proprio():
    chunks = dividir_regimento(REGIMENTO, DOC)
    preambulo = chunks[0]
    assert preambulo.fonte == f"{DOC}, Preâmbulo"
    assert preambulo.metadata == {"artigo": None, "posicao": 0}
    assert preambulo.conteudo.startswith("REGIMENTO INTERNO")
    assert preambulo.conteudo.endswith("assembleia geral.")


def test_sem_preambulo_o_primeiro_chunk_e_o_artigo():
    [chunk] = dividir_regimento("Art. 1º Regra.", DOC)
    assert chunk.fonte == f"{DOC}, Art. 1º"
    assert chunk.metadata["posicao"] == 0


def test_preambulo_que_e_so_cabecalho_nao_vira_chunk():
    texto = "CAPÍTULO I\nDAS DISPOSIÇÕES GERAIS\nArt. 1º Regra.\n"
    chunks = dividir_regimento(texto, DOC)
    assert len(chunks) == 1
    assert chunks[0].fonte == f"{DOC}, Art. 1º"


def test_cabecalho_e_linha_titulo_sao_descartados():
    conteudos = "\n".join(c.conteudo for c in dividir_regimento(REGIMENTO, DOC))
    assert "CAPÍTULO" not in conteudos
    assert "DAS DISPOSIÇÕES GERAIS" not in conteudos
    assert "DO USO DAS ÁREAS COMUNS" not in conteudos


def test_cabecalho_com_numeral_arabico_e_linha_em_branco_antes_do_titulo():
    texto = "Art. 1º Regra um.\nSEÇÃO 2\n\nDA PISCINA\nArt. 2º Regra dois.\n"
    chunks = dividir_regimento(texto, DOC)
    assert [c.metadata["artigo"] for c in chunks] == ["1º", "2º"]
    conteudos = chunks[0].conteudo + chunks[1].conteudo
    assert "SEÇÃO" not in conteudos
    assert "DA PISCINA" not in conteudos


def test_artigo_logo_apos_cabecalho_nao_e_engolido_como_linha_titulo():
    texto = "Art. 1º Regra um.\nCAPÍTULO II\nArt. 2º Regra dois.\n"
    chunks = dividir_regimento(texto, DOC)
    assert [c.metadata["artigo"] for c in chunks] == ["1º", "2º"]


def test_palavra_de_cabecalho_sem_numeral_e_conteudo():
    texto = "Art. 1º A vaga de garagem é\nPARTE integrante da unidade.\n"
    [chunk] = dividir_regimento(texto, DOC)
    assert "PARTE integrante" in chunk.conteudo


def test_conteudo_integral_inclui_marcador_paragrafos_e_incisos():
    chunks = dividir_regimento(REGIMENTO, DOC)
    art1, art2 = chunks[1], chunks[2]
    assert art1.conteudo.startswith("Art. 1º O presente regimento")
    assert "Parágrafo único." in art1.conteudo
    assert art2.conteudo.startswith("Art. 2º")
    assert "I - respeitar o silêncio" in art2.conteudo
    assert "II - zelar" in art2.conteudo
    assert "piscina" not in art2.conteudo


def test_artigo_duplicado_gera_fontes_iguais_e_posicoes_distintas():
    texto = "Art. 3º Primeira versão.\nArt. 3º Segunda versão.\n"
    chunks = dividir_regimento(texto, DOC)
    assert [c.fonte for c in chunks] == [f"{DOC}, Art. 3º"] * 2
    assert [c.metadata["posicao"] for c in chunks] == [0, 1]


def test_documento_ecoado_e_stripado_em_todos_os_chunks():
    chunks = dividir_regimento(REGIMENTO, "  Regimento Exemplo  ")
    assert all(c.documento == DOC for c in chunks)
    assert all(c.fonte.startswith(f"{DOC}, ") for c in chunks)


def test_metadata_tem_exatamente_artigo_e_posicao_sequencial():
    chunks = dividir_regimento(REGIMENTO, DOC)
    assert all(set(c.metadata) == {"artigo", "posicao"} for c in chunks)
    assert [c.metadata["posicao"] for c in chunks] == list(range(len(chunks)))


def test_determinismo_mesma_entrada_mesma_saida():
    primeira = dividir_regimento(REGIMENTO, DOC)
    segunda = dividir_regimento(REGIMENTO, DOC)
    assert [c.model_dump() for c in primeira] == [c.model_dump() for c in segunda]


# Bordas de erro


def test_texto_em_branco_levanta_valueerror():
    with pytest.raises(ValueError, match="texto em branco"):
        dividir_regimento("   \n ", DOC)


def test_documento_em_branco_levanta_valueerror():
    with pytest.raises(ValueError, match="documento em branco"):
        dividir_regimento("Art. 1º Regra.", "  ")


def test_sem_artigo_reconhecivel_levanta_valueerror():
    texto = "Texto corrido sem estrutura.\nartigo 12 minúsculo não conta.\n"
    with pytest.raises(ValueError, match="nenhum artigo"):
        dividir_regimento(texto, DOC)


# Lei 4.591/64 real (fixture extraída do PDF)


def _designacoes_esperadas() -> list[str]:
    sufixos = {30: "ABCDEFG", 31: "ABCDEF", 35: "A", 43: "A", 67: "A"}
    saida: list[str] = []
    for n in range(1, 71):
        saida.append(f"{n}º" if n <= 9 else str(n))
        saida.extend(f"{n}-{s}" for s in sufixos.get(n, ""))
    return saida


def test_lei_4591_reconhece_os_86_artigos_na_ordem():
    artigos = [c for c in _chunks_da_lei() if c.metadata["artigo"] is not None]
    assert len(artigos) == 86
    assert [c.metadata["artigo"] for c in artigos] == _designacoes_esperadas()


def test_lei_4591_preambulo_e_maior_artigo_medidos():
    chunks = _chunks_da_lei()
    preambulo = chunks[0]
    assert preambulo.fonte == f"{DOC_LEI}, Preâmbulo"
    assert preambulo.conteudo.startswith("CÂMARA DOS DEPUTADOS")
    assert preambulo.conteudo.endswith("sanciono a seguinte Lei:")
    assert len(preambulo.conteudo) == 273
    maior = max(chunks, key=lambda c: len(c.conteudo.encode("utf-8")))
    assert maior.fonte == f"{DOC_LEI}, Art. 31-F"
    assert len(maior.conteudo.encode("utf-8")) == 10_112


def test_lei_4591_nenhum_cabecalho_dentro_de_chunk():
    linhas = [
        linha for chunk in _chunks_da_lei() for linha in chunk.conteudo.split("\n")
    ]
    assert not [x for x in linhas if x.startswith(("TÍTULO ", "CAPÍTULO "))]


def test_lei_4591_artigos_revogados_viram_chunks_fieis():
    revogados = [
        c for c in _chunks_da_lei() if "revogado pela Lei nº 10.931" in c.conteudo
    ]
    assert [c.metadata["artigo"] for c in revogados] == [f"30-{s}" for s in "ABCDEFG"]


def test_lei_4591_toda_saida_e_citavel_e_ecoa_o_documento():
    chunks = _chunks_da_lei()
    assert all(c.fonte.startswith(f"{DOC_LEI}, ") for c in chunks)
    assert all(c.documento == DOC_LEI for c in chunks)
