"""Organizador e Catalogador Determinístico de PDFs."""

__version__ = "0.4.0"

from .abnt_formatter import ABNTFormatter, format_abnt_reference
from .classifier_jev import JevClassifier
from .converter import (
    DocumentoConvertido,
    ErroDeConversao,
    ErroPdfEscaneado,
    MarkdownConverter,
    converter_pdf,
    generate_standardized_filename,
    gerar_markdown,
    gerar_nome_padronizado,
    listar_pdfs,
)
from .estado import EstadoDeExecucao, EstadoManager
from .metadata_api import MetadataEnricher
from .models import (
    Identificadores,
    Identifiers,
    Metadados,
    PipelineResult,
    PublicationMetadata,
    PublicationType,
    ResultadoDoArquivo,
    Situacao,
    TipoPublicacao,
)
from .organizer import PipelineOrganizer, ResultadoDaOrganizacao, organizar
from .pipeline import OpcoesDoPipeline, Pipeline

__all__ = [
    "__version__",
    "ABNTFormatter",
    "format_abnt_reference",
    "JevClassifier",
    "DocumentoConvertido",
    "ErroDeConversao",
    "ErroPdfEscaneado",
    "MarkdownConverter",
    "converter_pdf",
    "generate_standardized_filename",
    "gerar_markdown",
    "gerar_nome_padronizado",
    "listar_pdfs",
    "EstadoDeExecucao",
    "EstadoManager",
    "MetadataEnricher",
    "Identificadores",
    "Identifiers",
    "Metadados",
    "PublicationMetadata",
    "PublicationType",
    "TipoPublicacao",
    "ResultadoDoArquivo",
    "PipelineResult",
    "Situacao",
    "ResultadoDaOrganizacao",
    "organizar",
    "PipelineOrganizer",
    "OpcoesDoPipeline",
    "Pipeline",
]
