"""Modelos Pydantic dos metadados bibliográficos e resultados do pipeline.

Harmoniza a tipagem estrita do pacote organizador_pdf com o motor determinístico
Jev/CIP, suportando tanto os atributos em português quanto em inglês para
máxima compatibilidade.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, model_validator, field_validator


class TipoPublicacao(str, Enum):
    """Classificação estrita do tipo de publicação."""

    LIVRO = "Livro"
    ARTIGO = "Artigo"
    DISSERTACAO_TESE = "Dissertação/Tese"
    TESE = "Tese"
    APOSTILA = "Apostila"
    REVISTA = "Revista"
    CAPITULO_LIVRO = "Capítulo de Livro"
    RELATORIO = "Relatório"
    TRABALHO_EVENTO = "Trabalho em Evento"
    INSTRUMENTO_AVALIACAO = "Instrumento/Escala"
    DOCUMENTO_ADMINISTRATIVO = "Documento Administrativo"
    ARQUIVO_PARCIAL = "Arquivo Parcial"
    OUTROS = "Outros"

    @classmethod
    def normalizar(cls, valor: Union[str, TipoPublicacao]) -> TipoPublicacao:
        """Converte strings e variações para a enum oficial."""
        if isinstance(valor, TipoPublicacao):
            return valor
        if not valor:
            return cls.OUTROS
        v = str(valor).strip().lower()
        if any(term in v for term in ("instrumento", "escala", "questionário", "questionario", "inventário", "inventario", "teste psicologico", "teste psicológico")):
            return cls.INSTRUMENTO_AVALIACAO
        if any(term in v for term in ("administrativo", "prontuário", "prontuario", "laudo", "parecer individual")):
            return cls.DOCUMENTO_ADMINISTRATIVO
        if any(term in v for term in ("parcial", "incompleto", "ilegível", "ilegivel")):
            return cls.ARQUIVO_PARCIAL
        if "artigo" in v:
            return cls.ARTIGO
        if "tese" in v or "dissert" in v or "monografia" in v:
            return cls.DISSERTACAO_TESE
        if "livro" in v and "capítulo" not in v and "capitulo" not in v:
            return cls.LIVRO
        if "capítulo" in v or "capitulo" in v:
            return cls.CAPITULO_LIVRO
        if "relatorio" in v or "relatório" in v or "report" in v:
            return cls.RELATORIO
        if "evento" in v or "anais" in v or "congresso" in v or "simposio" in v or "simpósio" in v:
            return cls.TRABALHO_EVENTO
        if "apostila" in v or "curso" in v or "didático" in v or "didatico" in v:
            return cls.APOSTILA
        if "revista" in v or "periodico" in v or "periódico" in v:
            return cls.REVISTA
        return cls.OUTROS


#: Forma plural usada como nome de pasta para cada tipo.
PLURAL_POR_TIPO: dict[TipoPublicacao, str] = {
    TipoPublicacao.LIVRO: "Livros",
    TipoPublicacao.ARTIGO: "Artigos",
    TipoPublicacao.DISSERTACAO_TESE: "Dissertações e Teses",
    TipoPublicacao.TESE: "Dissertações e Teses",
    TipoPublicacao.APOSTILA: "Apostilas",
    TipoPublicacao.REVISTA: "Revistas",
    TipoPublicacao.CAPITULO_LIVRO: "Capítulos de Livro",
    TipoPublicacao.RELATORIO: "Relatórios",
    TipoPublicacao.TRABALHO_EVENTO: "Trabalhos em Eventos",
    TipoPublicacao.INSTRUMENTO_AVALIACAO: "Instrumentos e Escalas",
    TipoPublicacao.DOCUMENTO_ADMINISTRATIVO: "Documentos Administrativos",
    TipoPublicacao.ARQUIVO_PARCIAL: "Arquivos Parciais",
    TipoPublicacao.OUTROS: "Outros",
}

PublicationType = Literal[
    "artigo",
    "livro",
    "tese",
    "revista",
    "apostila",
    "capitulo_livro",
    "relatorio",
    "trabalho_evento",
    "instrumento_escala",
    "documento_administrativo",
    "arquivo_parcial",
    "outros",
    "artigo_cientifico",
    "dissertacao_tese",
]


class Identificadores(BaseModel):
    """ISBN, ISSN e DOI, quando disponíveis no documento."""

    isbn: Optional[str] = Field(
        default=None, description="ISBN do documento, se houver. Caso contrário, null."
    )
    issn: Optional[str] = Field(
        default=None, description="ISSN do periódico, se houver. Caso contrário, null."
    )
    doi: Optional[str] = Field(
        default=None, description="DOI do documento, se houver. Caso contrário, null."
    )

    @field_validator("isbn", mode="before")
    @classmethod
    def normalizar_isbn(cls, v: Any) -> Optional[str]:
        if not v:
            return None
        s = str(v).strip()
        if "OCLC" in s.upper() or "EAN" in s.upper():
            return None
        clean = re.sub(r"[^0-9Xx]", "", s)
        if len(clean) in (10, 13) and is_valid_isbn(clean, strict=False):
            return s
        return None

    def esta_vazio(self) -> bool:
        return not any((self.isbn, self.issn, self.doi))


def is_valid_isbn(isbn: Optional[str], strict: bool = True) -> bool:
    """Valida formato e opcionalmente dígito verificador (checksum) de ISBN-10 e ISBN-13."""
    if not isbn:
        return False
    clean = re.sub(r"[^0-9Xx]", "", str(isbn)).upper()
    if "OCLC" in clean or "EAN" in clean:
        return False
    if len(clean) == 13 and clean.isdigit():
        if not (clean.startswith("978") or clean.startswith("979")):
            return False
        if not strict:
            return True
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(clean))
        return total % 10 == 0
    elif len(clean) == 10:
        digits = clean[:-1]
        check = clean[-1]
        if not digits.isdigit() or (not check.isdigit() and check != "X"):
            return False
        if not strict:
            return True
        total = sum(int(c) * (10 - i) for i, c in enumerate(digits))
        check_val = 10 if check == "X" else int(check)
        total += check_val
        return total % 11 == 0
    return False


class StatusConsultaProvedor(str, Enum):
    """Estados granulares de consulta a provedores bibliográficos externos."""

    NAO_CONSULTADO = "NAO_CONSULTADO"
    FALHA_CONEXAO = "FALHA_CONEXAO"
    ERRO_HTTP_OU_LIMITE = "ERRO_HTTP_OU_LIMITE"
    NAO_ENCONTRADO = "NAO_ENCONTRADO"
    RESPOSTA_SEM_CONFIRMACAO = "RESPOSTA_SEM_CONFIRMACAO"
    DIVERGENTE = "DIVERGENTE"
    CONFIRMADO = "CONFIRMADO"
    REQUER_AUTENTICACAO_OU_CHAVE = "REQUER_AUTENTICACAO_OU_CHAVE"
    DISPENSADO_JA_CONFIRMADO = "DISPENSADO_JA_CONFIRMADO"
    NAO_SE_APLICA = "NAO_SE_APLICA"
    NAO_CONSULTADO_CONFIGURACAO = "NAO_CONSULTADO_CONFIGURACAO"
    BLOQUEIO_PRIVACIDADE = "BLOQUEIO_PRIVACIDADE"


def converter_isbn10_para_isbn13(isbn10: str) -> Optional[str]:
    """Converte um ISBN-10 válido para ISBN-13 (prefixo 978) com recálculo de checksum módulo 10."""
    clean = re.sub(r"[^0-9X]", "", str(isbn10).upper())
    if len(clean) != 10 or not is_valid_isbn(clean, strict=False):
        return None
    body = "978" + clean[:-1]
    s = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(body))
    check = (10 - (s % 10)) % 10
    return body + str(check)


def normalizar_isbn_canonico(isbn: Optional[str]) -> Optional[str]:
    """Retorna o ISBN-13 canônico normalizado (prefixos 978 ou 979).

    Converte ISBN-10 para ISBN-13 correspondente. Mantém ISBN-13 inalterado após validação.
    """
    if not isbn:
        return None
    clean = re.sub(r"[^0-9X]", "", str(isbn)).upper()
    if len(clean) == 10 and is_valid_isbn(clean, strict=False):
        return converter_isbn10_para_isbn13(clean)
    if len(clean) == 13 and is_valid_isbn(clean, strict=False):
        return clean
    return None


def is_valid_issn(issn: Optional[str], strict: bool = True) -> bool:
    """Valida formato e opcionalmente dígito verificador (checksum) de ISSN-8."""
    if not issn:
        return False
    clean = re.sub(r"[^0-9Xx]", "", str(issn)).upper()
    if len(clean) != 8:
        return False
    digits = clean[:-1]
    check = clean[-1]
    if not digits.isdigit() or (not check.isdigit() and check != "X"):
        return False
    if not strict:
        return True
    total = sum(int(c) * (8 - i) for i, c in enumerate(digits))
    check_val = 10 if check == "X" else int(check)
    total += check_val
    return total % 11 == 0


def normalizar_doi(doi: Optional[str]) -> Optional[str]:
    """Remove prefixos comuns de URL ou protocolo de um DOI e limpa espaços."""
    if not doi:
        return None
    d = str(doi).strip()
    for prefixo in ("https://doi.org/", "http://doi.org/", "http://dx.doi.org/", "doi:"):
        if d.lower().startswith(prefixo):
            d = d[len(prefixo):].strip()
    return d


# Alias de compatibilidade
Identifiers = Identificadores


class CandidatoMetadado(BaseModel):
    """Representa um candidato a metadado com seu valor, origem e confiança."""

    campo: str
    valor: Any
    origem: str
    confianca: float = 0.5
    valor_original: Any = None


class ConsultaFonteExterna(BaseModel):
    """Registro detalhado de consulta a uma fonte externa de metadados."""

    fonte: str
    consulta: str
    data_consulta: str
    status: StatusConsultaProvedor
    resultado_encontrado: bool = False
    tem_texto_integral: bool = False
    url_texto_integral: Optional[str] = None
    apenas_referencia: bool = False
    metadados_recuperados: Dict[str, Any] = Field(default_factory=dict)
    erro_ou_aviso: Optional[str] = None
    cache_hit: bool = False
    origem_resposta: str = "rede_ao_vivo"  # 'rede_ao_vivo', 'cache_disco', 'dispensada', 'nao_aplicavel'
    motivo_dispensa: Optional[str] = None


class ResultadoExtracaoTexto(BaseModel):
    """Resultado da avaliação de suficiência da extração de texto nativo."""

    caracteres_uteis: int = 0
    total_palavras: int = 0
    paginas_lidas: int = 0
    suficiente: bool = True
    requer_ocr: bool = False
    motivo_insuficiencia: Optional[str] = None

    ocr_aplicado: bool = False
    paginas_ocr: List[int] = Field(default_factory=list)
    ocr_disponivel: bool = True
    estado_ocr: str = "NAO_NECESSARIO"  # 'NAO_NECESSARIO', 'EXECUTADO', 'OCR_INDISPONIVEL'
    qualidade_ocr: Optional[float] = None


class RegistroRastreabilidade(BaseModel):
    """Registro completo de rastreabilidade para auditoria do processamento."""

    nome_arquivo: str
    identificador_arquivo: str = ""  # Hash SHA-256
    resultado_extracao_texto: Optional[ResultadoExtracaoTexto] = None
    candidatos_metadados: Dict[str, Any] = Field(default_factory=dict)
    candidatos_originais_preservados: Dict[str, Any] = Field(default_factory=dict)
    resposta_jev: Dict[str, Any] = Field(default_factory=dict)
    fontes_consultadas: List[ConsultaFonteExterna] = Field(default_factory=list)
    indicacao_texto_integral: str = "sem_resultado"  # 'texto_integral_disponivel', 'apenas_referencia', 'sem_resultado'
    url_texto_integral: Optional[str] = None
    valores_finais_selecionados: Dict[str, Any] = Field(default_factory=dict)
    conflitos: List[str] = Field(default_factory=list)
    ausencias: List[str] = Field(default_factory=list)
    erros: List[str] = Field(default_factory=list)
    avisos: List[str] = Field(default_factory=list)
    criterios_confianca_atendidos: List[str] = Field(default_factory=list)
    decisao_encaminhamento: str = "organizado"  # 'organizado' ou 'revisao_manual'
    justificativa_decisao: str = ""
    caminho_final: Optional[str] = None
    classificador_remoto_consultado: bool = False
    amostra_remota_enviada: bool = False
    tamanho_amostra_remota: int = 0
    tamanho_amostra_local: int = 0
    motivo_bloqueio_privacidade: Optional[str] = None

    # Decisões bibliográficas separadas de revisão (Passo 9)
    identidade_confirmada: bool = False
    identidade_corroborada_fonte: bool = False
    identidade_plausivel_local: bool = False
    evidencia_identidade: str = ""
    status_busca_externa: str = "nao_iniciada"
    metadados_completos: bool = False
    tipo_documental_confiante: bool = False
    assunto_confiante: bool = False
    integridade_arquivo_ok: bool = True
    busca_externa_esgotada: bool = False
    revisao_manual_necessaria: bool = False
    motivo_revisao_manual: Optional[str] = None
    fontes_pendentes: List[str] = Field(default_factory=list)
    campos_irresolvidos: List[str] = Field(default_factory=list)
    jev_chamada_nova: bool = True
    jev_status: Optional[str] = "NAO_INICIADO"


class ExtractedCandidates(BaseModel):
    """Candidatos brutos extraídos das primeiras/últimas páginas do PDF."""

    raw_title: Optional[str] = None
    raw_subtitle: Optional[str] = None
    raw_authors: List[str] = Field(default_factory=list)
    papel_autoria: Optional[str] = None
    raw_publisher: Optional[str] = None
    raw_edition: Optional[str] = None
    raw_year: Optional[int] = None
    raw_city: Optional[str] = None
    raw_area: Optional[str] = None
    doi: Optional[str] = None
    doi_source: Optional[str] = None
    isbn: Optional[str] = None
    isbn_source: Optional[str] = None
    issn: Optional[str] = None
    issn_source: Optional[str] = None
    title_source: Optional[str] = None
    author_source: Optional[str] = None
    sample_text: str = ""
    total_pages: int = 0
    cip_pages: Optional[int] = None
    cip_year: Optional[int] = None
    year_source: Optional[str] = None
    city_source: Optional[str] = None
    publisher_source: Optional[str] = None
    reimpressao: Optional[str] = None
    raw_journal: Optional[str] = None
    raw_volume: Optional[str] = None
    raw_number: Optional[str] = None
    raw_pages: Optional[str] = None
    journal_source: Optional[str] = None
    pages_source: Optional[str] = None
    candidatos_detalhados: Dict[str, CandidatoMetadado] = Field(default_factory=dict)
    candidatos_originais: Dict[str, Any] = Field(default_factory=dict)
    extracao_texto: Optional[ResultadoExtracaoTexto] = None
    fn_title: Optional[str] = None
    fn_author: Optional[str] = None
    is_isbn_strong: bool = False
    pdf_path: Optional[str] = None


class JevValidationResult(BaseModel):
    """Resultado da classificação Jev / heurística estrutural e pontuação de probabilidades."""

    classification: PublicationType = "outros"
    classification_confidence: float = 0.0
    assunto: str = "Outros"
    subarea: str = "Geral"
    justificativa: str = ""
    subject_confidence: float = 0.0
    probabilities: Dict[str, float] = Field(
        default_factory=lambda: {
            "title": 0.0,
            "authors": 0.0,
            "publisher": 0.0,
            "isbn": 0.0,
            "doi": 0.0,
            "issn": 0.0,
        }
    )
    candidates: ExtractedCandidates = Field(default_factory=ExtractedCandidates)
    provider: str = Field(
        default="deterministico_local",
        description="Motor que realizou a classificação ('typesafe' ou 'deterministico_local').",
    )
    raw_jev_data: Dict[str, Any] = Field(default_factory=dict)
    classificador_remoto_consultado: bool = False
    amostra_remota_enviada: bool = False
    motivo_bloqueio_privacidade: Optional[str] = None


class Metadados(BaseModel):
    """Metadados bibliográficos de uma publicação.

    Oferece suporte bilíngue transparente (propriedades em português e inglês):
    - titulo / title
    - subtitulo / subtitle
    - autores / authors
    - editora_ou_periodico / publisher
    - ano / year
    - local / city
    - tipo_publicacao / classification
    - identificadores / identifiers
    - referencia_abnt / abnt_reference
    """

    tipo_publicacao: TipoPublicacao = Field(
        default=TipoPublicacao.OUTROS,
        description="Tipo da publicação.",
    )
    area_principal: str = Field(
        default="Outros",
        description="Área macro do conhecimento (ex.: Psicologia, Filosofia, Tecnologia).",
    )
    subarea: str = Field(
        default="Geral",
        description="Especialidade temática dentro da área principal.",
    )
    titulo: str = Field(default="Sem Título", description="Título principal do trabalho.")
    subtitulo: Optional[str] = Field(default=None, description="Subtítulo do trabalho, se houver.")
    autores: list[str] = Field(default_factory=list, description="Lista de autores formatados.")
    autor_principal: Optional[str] = Field(default=None, description="Autor principal.")
    papel_autoria: str = Field(default="autor", description="Papel bibliográfico da responsabilidade (autor, organizador, coordenador, editor).")
    editora: Optional[str] = Field(default=None, description="Editora da publicação.")
    periodico: Optional[str] = Field(default=None, description="Nome do periódico ou revista (para artigos).")
    editora_ou_periodico: Optional[str] = Field(default=None, description="Campo consolidado para compatibilidade.")
    ano: Optional[int] = Field(default=None, description="Ano de publicação com 4 dígitos.")
    local: Optional[str] = Field(default=None, description="Cidade ou local de publicação.")
    identificadores: Identificadores = Field(default_factory=Identificadores)
    referencia_abnt: str = Field(default="", description="Referência ABNT NBR 6023 completa.")

    # Campos auxiliares para enriquecimento e Obsidian
    edition: Optional[str] = None
    journal: Optional[str] = None
    volume: Optional[str] = None
    number: Optional[str] = None
    pages: Optional[str] = None
    numero: Optional[str] = None
    paginas: Optional[str] = None
    avisos: list[str] = Field(default_factory=list)
    url: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    source_apis: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    confidence_classification: float = 1.0
    confidence_metadata: float = 1.0
    provedor_classificador: Optional[str] = None
    doi_source: Optional[str] = None
    title_source: Optional[str] = None
    author_source: Optional[str] = None
    isbn_source: Optional[str] = None
    issn_source: Optional[str] = None
    year_source: Optional[str] = None
    city_source: Optional[str] = None
    publisher_source: Optional[str] = None
    reimpressao: Optional[str] = None
    isbn_verified: bool = False
    status_por_provedor: dict[str, str] = Field(default_factory=dict)
    regra_classificacao_tematica: Optional[str] = None
    total_pages: Optional[int] = None
    cip_pages: Optional[int] = None
    cip_year: Optional[int] = None
    registro_rastreabilidade: Optional[RegistroRastreabilidade] = None
    fontes_consultadas: list[ConsultaFonteExterna] = Field(default_factory=list)
    tem_texto_integral: bool = False
    url_texto_integral: Optional[str] = None
    apenas_referencia: bool = False
    reconciliacao_campos: dict[str, dict[str, Any]] = Field(default_factory=dict)
    candidatos_originais: dict[str, Any] = Field(default_factory=dict)
    conflitos_registrados: list[str] = Field(default_factory=list)
    criterios_atendidos: list[str] = Field(default_factory=list)
    justificativa_jev: Optional[str] = None
    assunto_jev: Optional[str] = None
    subarea_jev: Optional[str] = None
    justificativa_classificacao_tematica: Optional[str] = None
    classificador_remoto_consultado: bool = False
    amostra_remota_enviada: bool = False
    tamanho_amostra_remota: int = 0
    tamanho_amostra_local: int = 0
    motivo_bloqueio_privacidade: Optional[str] = None

    # Decisões bibliográficas separadas (Passo 9)
    identidade_confirmada: bool = False
    identidade_corroborada_fonte: bool = False
    identidade_plausivel_local: bool = False
    evidencia_identidade: str = ""
    status_busca_externa: str = "nao_iniciada"
    metadados_completos: bool = False
    tipo_documental_confiante: bool = False
    assunto_confiante: bool = False
    integridade_arquivo_ok: bool = True
    busca_externa_esgotada: bool = False
    revisao_manual_necessaria: bool = False
    motivo_revisao_manual: Optional[str] = None
    edicao_confirmada: bool = False
    campos_faltantes_identificados: list[str] = Field(default_factory=list)
    campos_irresolvidos: list[str] = Field(default_factory=list)
    jev_status: Optional[str] = None
    fontes_pendentes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def harmonizar_entradas(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        reasons = list(d.get("review_reasons") or [])

        # Detecção de conflitos entre campos legados inglês/português
        # 1. Título
        if "title" in d and "titulo" in d:
            t1, t2 = str(d["title"]).strip(), str(d["titulo"]).strip()
            if t1 and t2 and t1.lower() != t2.lower():
                d["needs_review"] = True
                reasons.append(f"Conflito entre campos legados de título: '{t1}' vs '{t2}'")
            d["titulo"] = t2 or t1
            d["title"] = d["titulo"]
        elif "title" in d and "titulo" not in d:
            d["titulo"] = d["title"]
        elif "titulo" in d and "title" not in d:
            d["title"] = d["titulo"]

        # 2. Subtítulo
        if "subtitle" in d and "subtitulo" not in d:
            d["subtitulo"] = d["subtitle"]

        # 3. Autores e Papel de Autoria
        if "authors" in d and "autores" in d:
            a1, a2 = d["authors"], d["autores"]
            if a1 != a2:
                d["needs_review"] = True
                reasons.append("Conflito detectado entre listas legadas de autores")
            d["autores"] = a2 or a1
            d["authors"] = d["autores"]
        elif "authors" in d and "autores" not in d:
            d["autores"] = d["authors"]
        elif "autores" in d and "authors" not in d:
            d["authors"] = d["autores"]

        raw_auth_list = d.get("autores") or []
        cleaned_auth_list = []
        papel = d.get("papel_autoria") or "autor"
        for a in raw_auth_list:
            if not isinstance(a, str):
                cleaned_auth_list.append(a)
                continue
            a_str = a.strip()
            # Detecta papéis bibliográficos embutidos na autoria
            if re.search(r"(?i)\b(?:organiza[çc][aã]o|organizad(?:or|ores|ora|oras)|org\.?)\b", a_str):
                papel = "organizador"
            elif re.search(r"(?i)\b(?:coordena[çc][aã]o|coordenad(?:or|ores|ora|oras)|coord\.?)\b", a_str):
                papel = "coordenador"
            elif re.search(r"(?i)\b(?:edi[çc][aã]o|edit(?:or|ores|ora|oras)|ed\.?)\b", a_str):
                papel = "editor"

            # Remove prefixos de papéis da string do autor
            a_clean = re.sub(
                r"(?i)^(?:organiza[çc][aã]o|organizad(?:or|ores|ora|oras)|coordena[çc][aã]o|coordenad(?:or|ores|ora|oras)|edi[çc][aã]o|edit(?:or|ores|ora|oras)|org\.?|coord\.?|ed\.?)\s*(?:de|por)?[:\.\s]+",
                "",
                a_str,
            ).strip()
            a_clean = a_clean.strip("\"'“”«»–- ")

            # Se contiver múltiplos autores concatenados com ' e ' ou ';'
            deve_dividir = False
            if ";" in a_clean:
                deve_dividir = True
            elif " e " in a_clean and not re.search(r"(?i)\b(?:departamento|faculdade|universidade)\b", a_clean):
                sub_parts = re.split(r"\s+e\s+", a_clean)
                if papel in ("organizador", "coordenador", "editor") or all(len(sp.strip().split()) >= 2 for sp in sub_parts if sp.strip()):
                    deve_dividir = True

            if deve_dividir:
                sub_parts = re.split(r"\s+e\s+|;\s*", a_clean)
                for sp in sub_parts:
                    sp_clean = re.sub(
                        r"(?i)^(?:organiza[çc][aã]o|organizad(?:or|ores|ora|oras)|coordena[çc][aã]o|coordenad(?:or|ores|ora|oras)|org\.?|coord\.?)\s*(?:de|por)?[:\.\s]+",
                        "",
                        sp.strip(),
                    ).strip()
                    if sp_clean:
                        cleaned_auth_list.append(sp_clean)
            elif a_clean:
                cleaned_auth_list.append(a_clean)

        if cleaned_auth_list:
            d["autores"] = cleaned_auth_list
            d["authors"] = cleaned_auth_list
        d["papel_autoria"] = papel

        # Autor principal: SEMPRE sincronizado com o primeiro autor canônico
        if d.get("autores"):
            d["autor_principal"] = d["autores"][0]
        elif d.get("autor_principal"):
            d["autores"] = [d["autor_principal"]]
            d["authors"] = d["autores"]

        # 4. Periódico vs Editora (separação estruturada)
        if "journal" in d and "periodico" not in d:
            d["periodico"] = d["journal"]
        elif "periodico" in d and "journal" not in d:
            d["journal"] = d["periodico"]

        if "publisher" in d and "editora" not in d:
            d["editora"] = d["publisher"]
        elif "editora" in d and "publisher" not in d:
            d["publisher"] = d["editora"]

        if not d.get("editora_ou_periodico"):
            d["editora_ou_periodico"] = d.get("periodico") or d.get("editora") or d.get("publisher")

        # 5. Ano
        if "year" in d and "ano" in d:
            try:
                y1, y2 = int(d["year"]), int(d["ano"])
                if y1 != y2:
                    d["needs_review"] = True
                    reasons.append(f"Conflito entre campos legados de ano: '{y1}' vs '{y2}'")
                d["ano"] = y2
            except (ValueError, TypeError):
                d["ano"] = d["ano"] or d["year"]
            d["year"] = d["ano"]
        elif "year" in d and "ano" not in d:
            d["ano"] = d["year"]
        elif "ano" in d and "year" not in d:
            d["year"] = d["ano"]

        # Volume, número, páginas
        if "volume" in d and d["volume"] is not None:
            d["volume"] = str(d["volume"])

        if "numero" in d and "number" not in d:
            d["number"] = str(d["numero"]) if d["numero"] is not None else None
            d["numero"] = d["number"]
        elif "number" in d and "numero" not in d:
            d["numero"] = str(d["number"]) if d["number"] is not None else None
            d["number"] = d["numero"]
        elif "numero" in d and "number" in d:
            d["numero"] = str(d["numero"]) if d["numero"] is not None else None
            d["number"] = d["numero"]

        if "paginas" in d and "pages" not in d:
            d["pages"] = str(d["paginas"]) if d["paginas"] is not None else None
            d["paginas"] = d["pages"]
        elif "pages" in d and "paginas" not in d:
            d["paginas"] = str(d["pages"]) if d["pages"] is not None else None
            d["pages"] = d["paginas"]
        elif "paginas" in d and "pages" in d:
            d["paginas"] = str(d["paginas"]) if d["paginas"] is not None else None
            d["pages"] = d["paginas"]

        # 6. Local / Cidade
        if "city" in d and "local" not in d:
            d["local"] = d["city"]
        elif "local" in d and "city" not in d:
            d["city"] = d["local"]

        # Área
        area_val = d.get("area") or d.get("area_principal") or "Outros"
        d["area_principal"] = area_val if area_val else "Outros"
        subarea_val = d.get("subarea") or area_val or "Geral"
        d["subarea"] = subarea_val if subarea_val else "Geral"

        # Classificação / Tipo
        raw_tipo = d.get("tipo_publicacao") or d.get("classification")
        if raw_tipo:
            d["tipo_publicacao"] = TipoPublicacao.normalizar(raw_tipo)

        # Identificadores
        if "identifiers" in d and "identificadores" not in d:
            d["identificadores"] = d["identifiers"]

        # Suporte a aliases legados no nível raiz (isbn, doi, issn) sem duplicar no registro final
        if "identificadores" not in d or isinstance(d.get("identificadores"), dict):
            id_dict = dict(d.get("identificadores") or {})
            if "isbn" in d and d["isbn"] and not id_dict.get("isbn"):
                id_dict["isbn"] = d["isbn"]
            if "doi" in d and d["doi"] and not id_dict.get("doi"):
                id_dict["doi"] = d["doi"]
            if "issn" in d and d["issn"] and not id_dict.get("issn"):
                id_dict["issn"] = d["issn"]
            if id_dict:
                d["identificadores"] = id_dict

        # ABNT
        if "abnt_reference" in d and "referencia_abnt" not in d:
            d["referencia_abnt"] = d["abnt_reference"]

        if d.get("needs_review") or d.get("revisao_manual_necessaria"):
            d["needs_review"] = True
            d["revisao_manual_necessaria"] = True

        d["review_reasons"] = reasons
        return d

    # Aliases via propriedades para compatibilidade com código em inglês
    @property
    def title(self) -> str:
        return self.titulo

    @title.setter
    def title(self, value: str) -> None:
        self.titulo = value

    @property
    def subtitle(self) -> Optional[str]:
        return self.subtitulo

    @subtitle.setter
    def subtitle(self, value: Optional[str]) -> None:
        self.subtitulo = value

    @property
    def authors(self) -> list[str]:
        return self.autores

    @authors.setter
    def authors(self, value: list[str]) -> None:
        self.autores = value
        if value:
            self.autor_principal = value[0]

    @property
    def publisher(self) -> Optional[str]:
        return self.editora or self.editora_ou_periodico

    @publisher.setter
    def publisher(self, value: Optional[str]) -> None:
        self.editora = value
        self.editora_ou_periodico = value

    @property
    def year(self) -> Optional[int]:
        return self.ano

    @year.setter
    def year(self, value: Optional[int]) -> None:
        self.ano = value

    @property
    def city(self) -> Optional[str]:
        return self.local

    @city.setter
    def city(self, value: Optional[str]) -> None:
        self.local = value

    @property
    def area(self) -> Optional[str]:
        return self.area_principal

    @area.setter
    def area(self, value: Optional[str]) -> None:
        if value:
            self.area_principal = value

    @property
    def classification(self) -> str:
        # Devolve em minúsculo compatível com PublicationType
        mapping = {
            TipoPublicacao.LIVRO: "livro",
            TipoPublicacao.ARTIGO: "artigo",
            TipoPublicacao.DISSERTACAO_TESE: "tese",
            TipoPublicacao.TESE: "tese",
            TipoPublicacao.APOSTILA: "apostila",
            TipoPublicacao.REVISTA: "revista",
            TipoPublicacao.CAPITULO_LIVRO: "capitulo_livro",
            TipoPublicacao.RELATORIO: "relatorio",
            TipoPublicacao.TRABALHO_EVENTO: "trabalho_evento",
            TipoPublicacao.OUTROS: "outros",
        }
        return mapping.get(self.tipo_publicacao, "outros")

    @classification.setter
    def classification(self, value: Union[str, TipoPublicacao]) -> None:
        self.tipo_publicacao = TipoPublicacao.normalizar(value)

    @property
    def identifiers(self) -> Identificadores:
        return self.identificadores

    @property
    def isbn(self) -> Optional[str]:
        return self.identificadores.isbn

    @isbn.setter
    def isbn(self, value: Optional[str]) -> None:
        self.identificadores.isbn = value

    @property
    def doi(self) -> Optional[str]:
        return self.identificadores.doi

    @doi.setter
    def doi(self, value: Optional[str]) -> None:
        self.identificadores.doi = value

    @property
    def issn(self) -> Optional[str]:
        return self.identificadores.issn

    @issn.setter
    def issn(self, value: Optional[str]) -> None:
        self.identificadores.issn = value

    @property
    def plural_do_tipo(self) -> str:
        """Nome de pasta correspondente ao tipo (forma plural)."""
        return PLURAL_POR_TIPO.get(self.tipo_publicacao, "Outros")

    @property
    def autor_para_nome(self) -> Optional[str]:
        """Autor a ser usado na nomenclatura do arquivo."""
        if self.autores:
            return self.autores[0]
        if self.autor_principal:
            return self.autor_principal
        return None


# Alias de compatibilidade
PublicationMetadata = Metadados


class Situacao(str, Enum):
    """Situação do processamento de um arquivo."""

    SUCESSO = "sucesso"
    FALHA = "falha"
    SIMULADO = "simulado"
    PENDENTE_OCR = "pendente_ocr"


class ResultadoDoArquivo(BaseModel):
    """Resultado do processamento de um PDF no lote."""

    origem: Path
    situacao: Situacao
    metadados: Optional[Metadados] = None
    pdf_destino: Optional[Path] = None
    markdown_destino: Optional[Path] = None
    aviso: Optional[str] = None
    erro: Optional[str] = None
    etapa: Optional[str] = None
    provedor_usado: str = "deterministico_local"
    usou_fallback: bool = False

    @property
    def ok(self) -> bool:
        return self.situacao in (Situacao.SUCESSO, Situacao.SIMULADO)

    @property
    def requer_ocr(self) -> bool:
        return self.situacao is Situacao.PENDENTE_OCR or "ocr" in (self.erro or "").lower()


class PipelineResult(BaseModel):
    """Resultado do pipeline compatível com os testes e scripts locais."""

    original_pdf: str
    target_pdf: str
    output_markdown: str
    classification: PublicationType
    metadata: PublicationMetadata
    abnt_reference: str
    success: bool
    is_dry_run: bool = False
    needs_review: bool = False
    requires_ocr: bool = False
    error_message: Optional[str] = None
