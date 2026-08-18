"""Modelos Pydantic dos metadados bibliográficos extraídos pelo LLM."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TipoPublicacao(str, Enum):
    """Classificação estrita do tipo de publicação."""

    LIVRO = "Livro"
    ARTIGO = "Artigo"
    DISSERTACAO_TESE = "Dissertação/Tese"
    APOSTILA = "Apostila"
    REVISTA = "Revista"
    CAPITULO_LIVRO = "Capítulo de Livro"
    OUTROS = "Outros"


#: Forma plural usada como nome de pasta para cada tipo.
PLURAL_POR_TIPO: dict[TipoPublicacao, str] = {
    TipoPublicacao.LIVRO: "Livros",
    TipoPublicacao.ARTIGO: "Artigos",
    TipoPublicacao.DISSERTACAO_TESE: "Dissertações e Teses",
    TipoPublicacao.APOSTILA: "Apostilas",
    TipoPublicacao.REVISTA: "Revistas",
    TipoPublicacao.CAPITULO_LIVRO: "Capítulos de Livro",
    TipoPublicacao.OUTROS: "Outros",
}


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


class Metadados(BaseModel):
    """Metadados bibliográficos de uma publicação."""

    tipo_publicacao: TipoPublicacao = Field(
        description=(
            "Tipo da publicação. Escolha exatamente um dos valores permitidos. "
            "Use 'Outros' apenas quando nenhum dos demais se aplicar."
        )
    )
    area_principal: str = Field(
        description=(
            "Área macro do conhecimento, em português, no singular e capitalizada. "
            "Ex.: Psicologia, Filosofia, Tecnologia, Direito, Saúde, Educação."
        )
    )
    subarea: str = Field(
        description=(
            "Especialidade temática dentro da área principal, em português. "
            "Ex.: Psicologia Organizacional, Logoterapia, Machine Learning. "
            "Se não for possível especificar, repita a área principal."
        )
    )
    titulo: str = Field(description="Título principal do trabalho, sem o subtítulo.")
    subtitulo: Optional[str] = Field(
        default=None,
        description="Subtítulo do trabalho, se houver. Caso contrário, null.",
    )
    autores: list[str] = Field(
        default_factory=list,
        description=(
            "Todos os autores, na ordem em que aparecem na obra, no formato "
            "bibliográfico 'SOBRENOME, Nome' (ex.: 'Frankl, Viktor E.'), o mesmo "
            "usado dentro de referencia_abnt. Lista vazia se a obra for anônima "
            "ou institucional."
        ),
    )
    autor_principal: Optional[str] = Field(
        default=None,
        description=(
            "Autor principal (primeiro autor ou organizador). Deve ser um dos itens "
            "de 'autores' quando a lista não estiver vazia."
        ),
    )
    editora_ou_periodico: Optional[str] = Field(
        default=None,
        description=(
            "Nome da editora, universidade ou revista acadêmica responsável pela "
            "publicação. Null se não for identificável."
        ),
    )
    ano: Optional[int] = Field(
        default=None,
        description="Ano de publicação com 4 dígitos. Null se não for identificável.",
    )
    local: Optional[str] = Field(
        default=None,
        description="Cidade/local de publicação. Null se não for identificável.",
    )
    identificadores: Identificadores = Field(
        default_factory=Identificadores,
        description="ISBN, ISSN e/ou DOI encontrados no documento.",
    )
    referencia_abnt: str = Field(
        description=(
            "Referência bibliográfica completa formatada segundo a ABNT NBR 6023, "
            "em uma única linha. Use 's.l.' para local desconhecido, 's.n.' para "
            "editora desconhecida e 's.d.' para data desconhecida."
        )
    )

    @property
    def plural_do_tipo(self) -> str:
        """Nome de pasta correspondente ao tipo (forma plural)."""
        return PLURAL_POR_TIPO[self.tipo_publicacao]

    @property
    def autor_para_nome(self) -> Optional[str]:
        """Autor a ser usado na nomenclatura do arquivo."""
        if self.autor_principal:
            return self.autor_principal
        return self.autores[0] if self.autores else None
