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
from pydantic import BaseModel, Field, model_validator


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
    OUTROS = "Outros"

    @classmethod
    def normalizar(cls, valor: Union[str, TipoPublicacao]) -> TipoPublicacao:
        """Converte strings e variações para a enum oficial."""
        if isinstance(valor, TipoPublicacao):
            return valor
        if not valor:
            return cls.OUTROS
        v = str(valor).strip().lower()
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

    def esta_vazio(self) -> bool:
        return not any((self.isbn, self.issn, self.doi))


def is_valid_isbn(isbn: Optional[str], strict: bool = True) -> bool:
    """Valida formato e opcionalmente dígito verificador (checksum) de ISBN-10 e ISBN-13."""
    if not isbn:
        return False
    clean = re.sub(r"[\s-]", "", str(isbn)).upper()
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


def is_valid_issn(issn: Optional[str], strict: bool = True) -> bool:
    """Valida formato e opcionalmente dígito verificador (checksum) de ISSN-8."""
    if not issn:
        return False
    clean = re.sub(r"[\s-]", "", str(issn)).upper()
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


class ExtractedCandidates(BaseModel):
    """Candidatos brutos extraídos das primeiras/últimas páginas do PDF."""

    raw_title: Optional[str] = None
    raw_subtitle: Optional[str] = None
    raw_authors: List[str] = Field(default_factory=list)
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


class JevValidationResult(BaseModel):
    """Resultado da classificação Jev / heurística estrutural e pontuação de probabilidades."""

    classification: PublicationType = "outros"
    classification_confidence: float = 0.0
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

        # 3. Autores
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

        # Autor principal
        if not d.get("autor_principal") and d.get("autores"):
            d["autor_principal"] = d["autores"][0]

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

        # ABNT
        if "abnt_reference" in d and "referencia_abnt" not in d:
            d["referencia_abnt"] = d["abnt_reference"]

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
    def plural_do_tipo(self) -> str:
        """Nome de pasta correspondente ao tipo (forma plural)."""
        return PLURAL_POR_TIPO.get(self.tipo_publicacao, "Outros")

    @property
    def autor_para_nome(self) -> Optional[str]:
        """Autor a ser usado na nomenclatura do arquivo."""
        if self.autor_principal:
            return self.autor_principal
        return self.autores[0] if self.autores else None


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
