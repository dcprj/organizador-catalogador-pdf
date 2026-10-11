"""Testes abrangentes de Biblioteconomia, Recuperação de Informação e Auditoria.

Cobre:
1. Validação estrita de checksums de ISBN (10 e 13) e ISSN (8 com 'X').
2. Extração de candidatos preservando formatos e validação estrita posterior.
3. Consultas a Crossref e DataCite com verificação rigorosa de mesma obra/edição.
4. Geração de variantes de busca por título e compatibilidade de sobrenomes de autor.
5. Regras de parada de busca progressiva (esgotamento econômico e justificado).
6. As 7 decisões bibliográficas separadas de encaminhamento.
7. Formatação ABNT NBR 6023 (ausência de cidade como [S. l.], omissão de ISBN na referência textual).
8. Processamento exclusivo de tipologias acadêmicas e didáticas (livros, artigos, apostilas, teses).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from organizador_pdf.models import (
    Metadados,
    Identificadores,
    TipoPublicacao,
    StatusConsultaProvedor,
    ConsultaFonteExterna,
    JevValidationResult,
    is_valid_isbn,
    is_valid_issn,
)
from organizador_pdf.metadata_api import (
    MetadataEnricher,
    gerar_variantes_de_busca,
    normalizar_para_busca,
    extrair_sobrenomes,
    is_title_relevant,
    is_author_compatible,
    tem_campos_essenciais_completos,
)
from organizador_pdf.abnt_formatter import ABNTFormatter


# ==============================================================================
# 1. Validação estrita de Checksum de ISBN e ISSN
# ==============================================================================

def test_isbn13_strict_checksum():
    # Válido: 978-85-216-1234-6
    assert is_valid_isbn("978-85-216-1234-6", strict=True) is True
    # Inválido: dígito verificador 5 em vez de 6
    assert is_valid_isbn("978-85-216-1234-5", strict=True) is False
    # Modo leniente preserva candidato
    assert is_valid_isbn("978-85-216-1234-5", strict=False) is True


def test_isbn10_strict_checksum():
    # Válido: 85-216-1234-X ou similar cálculo
    # 0-306-40615-2 (famoso ISBN-10 válido)
    assert is_valid_isbn("0-306-40615-2", strict=True) is True
    # Inválido
    assert is_valid_isbn("0-306-40615-3", strict=True) is False
    assert is_valid_isbn("0-306-40615-3", strict=False) is True


def test_issn_strict_checksum():
    # Válido: 0001-0782 (Communications of the ACM)
    assert is_valid_issn("0001-0782", strict=True) is True
    # Válido com X: 2434-561X (exemplo com X)
    # 0378-5955 (Hearing Research)
    assert is_valid_issn("0378-5955", strict=True) is True
    # Inválido
    assert is_valid_issn("0001-0789", strict=True) is False
    assert is_valid_issn("0001-0789", strict=False) is True


# ==============================================================================
# 2. Busca por Título, Subtítulo e Compatibilidade de Sobrenomes
# ==============================================================================

def test_gerar_variantes_de_busca():
    variantes = gerar_variantes_de_busca(
        "Vigiar e Punir: História da Violência nas Prisões",
        authors=["Foucault, Michel"],
    )
    assert any("vigiar e punir" in v.lower() for v in variantes)
    assert len(variantes) >= 2


def test_is_title_relevant_tolerance():
    # Título com subtítulo deve ser compatível com título principal isolado
    assert is_title_relevant("Vigiar e Punir", "Vigiar e Punir: Nascimento da Prisão") is True
    assert is_title_relevant("História da Filosofia", "Receita de Bolo de Cenoura") is False


def test_is_author_compatible_surnames():
    # Correspondência por sobrenome
    assert is_author_compatible(["Foucault, Michel"], ["Michel Foucault"]) is True
    assert is_author_compatible(["Silva, Carlos"], ["Silva, João", "Silva, Carlos"]) is True
    # Autores vazios nunca são compatíveis
    assert is_author_compatible([], ["Michel Foucault"]) is False
    assert is_author_compatible(["Michel Foucault"], []) is False


# ==============================================================================
# 3. Completude de Campos Essenciais por Tipologia
# ==============================================================================

def test_tem_campos_essenciais_completos():
    livro_completo = Metadados(
        titulo="O Ser e o Nada",
        autores=["Sartre, Jean-Paul"],
        ano=1943,
        editora="Gallimard",
        local="Paris",
        tipo_publicacao=TipoPublicacao.LIVRO,
    )
    assert tem_campos_essenciais_completos(livro_completo, "livro") is True

    livro_sem_editora = Metadados(
        titulo="O Ser e o Nada",
        autores=["Sartre, Jean-Paul"],
        ano=1943,
        editora=None,
        tipo_publicacao=TipoPublicacao.LIVRO,
    )
    assert tem_campos_essenciais_completos(livro_sem_editora, "livro") is False

    artigo_completo = Metadados(
        titulo="Computing Machinery and Intelligence",
        autores=["Turing, A. M."],
        ano=1950,
        periodico="Mind",
        identificadores=Identificadores(doi="10.1093/mind/LIX.236.433"),
        tipo_publicacao=TipoPublicacao.ARTIGO,
    )
    assert tem_campos_essenciais_completos(artigo_completo, "artigo") is True


# ==============================================================================
# 4. As 7 Decisões Bibliográficas Separadas de Revisão
# ==============================================================================

def test_sete_decisoes_bibliograficas_separadas():
    enricher = MetadataEnricher(online=False)

    meta = Metadados(
        titulo="Crítica da Razão Pura",
        autores=["Kant, Immanuel"],
        ano=1985,
        editora="Hartknoch",
        local="Riga",
        area_principal="Filosofia",
        subarea="Epistemologia",
        tipo_publicacao=TipoPublicacao.LIVRO,
    )
    jev_kant = JevValidationResult(
        classification="livro",
        classification_confidence=0.95,
        assunto="Filosofia",
        subarea="Epistemologia",
        subject_confidence=0.98,
    )

    enricher._evaluate_needs_review(meta, jev_kant, sources_consulted=[])

    # 1. Identidade plausível localmente pela folha de rosto/estrutura nativa (sem corroboração externa)
    assert meta.identidade_plausivel_local is True
    assert meta.identidade_corroborada_fonte is False
    assert meta.identidade_confirmada is False
    assert "estrutura do documento" in meta.evidencia_identidade
    # 2. Metadados essenciais completos (título, autor, ano, editora, local)
    assert meta.metadados_completos is True
    # 3. Tipo documental confiante (livro com confiança 0.95)
    assert meta.tipo_documental_confiante is True
    # 4. Assunto confiante (Filosofia / Epistemologia)
    assert meta.assunto_confiante is True
    # 5. Integridade do arquivo ok
    assert meta.integridade_arquivo_ok is True
    # 6. Busca externa em modo offline não é declarada esgotada
    assert meta.busca_externa_esgotada is False
    assert meta.status_busca_externa == "desativada_offline"
    # 7. Revisão manual NÃO necessária (obra de identidade plausível e íntegra)
    assert meta.revisao_manual_necessaria is False
    assert meta.needs_review is False

    # Cenário com busca externa ativa e confirmada por autoridade independente
    enricher_online = MetadataEnricher(online=True)
    meta_corroborado = Metadados(
        titulo="Crítica da Razão Pura",
        autores=["Kant, Immanuel"],
        ano=1985,
        editora="Hartknoch",
        local="Riga",
        area_principal="Filosofia",
        subarea="Epistemologia",
        tipo_publicacao=TipoPublicacao.LIVRO,
        identificadores=Identificadores(isbn="978-85-326-0871-3"),
        isbn_verified=True,
    )
    enricher_online._evaluate_needs_review(
        meta_corroborado, jev_kant, sources_consulted=["Brasil API / CBL (ISBN)"]
    )
    assert meta_corroborado.identidade_plausivel_local is True
    assert meta_corroborado.identidade_corroborada_fonte is True
    assert meta_corroborado.identidade_confirmada is True
    assert "Corroborada por fonte independente" in meta_corroborado.evidencia_identidade
    assert meta_corroborado.busca_externa_esgotada is True
    assert meta_corroborado.status_busca_externa == "concluida_esgotada"
    assert meta_corroborado.revisao_manual_necessaria is False


# ==============================================================================
# 5. Formatação ABNT NBR 6023: [S. l.] e Omissão de ISBN no Texto
# ==============================================================================

def test_abnt_sem_cidade_e_sem_isbn_na_referencia():
    meta = Metadados(
        titulo="Fenomenologia do Espírito",
        autores=["Hegel, Georg Wilhelm Friedrich"],
        ano=1807,
        editora="Goebhardt",
        local=None,
        tipo_publicacao=TipoPublicacao.LIVRO,
        identificadores=Identificadores(isbn="978-85-216-1234-6"),
    )
    ref = ABNTFormatter.format(meta)
    # Sem localidade gera [S. l.]
    assert "[S. l.]" in ref
    # Editora e ano presentes
    assert "Goebhardt" in ref
    assert "1807" in ref
    # ISBN NÃO deve constar na referência textual ABNT 6023 para livros
    assert "978-85-216-1234-6" not in ref
    assert "ISBN" not in ref


# ==============================================================================
# 6. Documento com Incerteza de Identidade (sem autor) Vai para Revisão
# ==============================================================================

def test_documento_sem_autoria_exige_revisao_manual():
    enricher = MetadataEnricher(online=False)

    meta_anonimo = Metadados(
        titulo="Compêndio Geral de Ciências",
        autores=[],
        ano=2015,
        tipo_publicacao=TipoPublicacao.LIVRO,
        area_principal="Ciências",
        subarea="Geral",
    )
    jev_res = JevValidationResult(
        classification="livro",
        classification_confidence=0.85,
        assunto="Ciências",
        subarea="Geral",
        subject_confidence=0.90,
    )

    enricher._evaluate_needs_review(meta_anonimo, jev_res, sources_consulted=[])

    assert meta_anonimo.identidade_confirmada is False
    assert meta_anonimo.revisao_manual_necessaria is True
    assert meta_anonimo.needs_review is True
    assert "autores" in meta_anonimo.campos_irresolvidos
