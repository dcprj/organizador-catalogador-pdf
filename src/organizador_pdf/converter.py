"""Extração leve de páginas de PDFs e montagem de Markdown de Acompanhamento (Companion).

Regras de negócio:
1. NÃO converte o miolo inteiro do PDF em Markdown (execução instantânea e sem gasto desnecessário).
2. Para a análise e catalogação dos metadados, extrai texto nativo das 10 primeiras e 10 últimas páginas.
3. O Markdown gerado é um arquivo de acompanhamento contendo apenas Frontmatter YAML padronizado
   e a citação ABNT NBR 6023 completa formatada.
4. Gera nomes de arquivos padronizados e legíveis para visualização clara no gerenciador de arquivos.
"""

from __future__ import annotations

import io
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

from .models import Metadados, PublicationMetadata

logger = logging.getLogger(__name__)


class ErroDeConversao(RuntimeError):
    """Falha ao ler ou processar o PDF."""


@dataclass
class DocumentoConvertido:
    """Resultado da leitura das páginas de análise de um PDF."""

    caminho: Path
    markdown_inicial: str
    total_paginas: int
    metadados_embutidos: dict[str, str] = field(default_factory=dict)
    markdown_completo: str = ""

    @property
    def tem_texto(self) -> bool:
        return bool(self.markdown_inicial.strip())


def sanitize_filename(name: str) -> str:
    """Sanitiza uma string para uso seguro como nome de arquivo no macOS, Linux e Windows."""
    # Substitui caracteres ilegais por hífen
    cleaned = re.sub(r'[\/\\:\*\?"<>\|]', " - ", name)
    # Colapsa hifens e espaços múltiplos
    cleaned = re.sub(r"(?:\s*-\s*)+", " - ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-_")
    return cleaned


def generate_standardized_filename(
    metadata: PublicationMetadata,
    fallback_name: Optional[str] = None,
) -> str:
    """Gera um nome de arquivo padronizado para fácil identificação visual em pastas.

    Convenção:
      - Com autor e ano:     SOBRENOME, Nome - Título (Ano)
      - Múltiplos autores:   SOBRENOME, Nome et al. - Título (Ano)
      - Com subtítulo curto: SOBRENOME, Nome - Título - Subtítulo (Ano)
      - Sem autor:           Título (Ano)
      - Sem ano:             SOBRENOME, Nome - Título
    """
    from .abnt_formatter import format_single_author_abnt

    # 1. Componente de autor
    author_part = ""
    authors = metadata.authors or metadata.autores
    if authors:
        clean_authors = [
            a.strip() for a in authors
            if a.strip() and not re.search(r"^(?:et\s+al\.?|\[et\s+al\.?\]|\.\.\.|organizad|coord)", a.strip(), re.I)
        ]
        has_et_al = (
            len(authors) > len(clean_authors)
            or len(clean_authors) > 1
            or any(re.search(r"\bet\s+al\b", a, re.I) for a in authors)
        )
        if clean_authors:
            first_author_abnt = format_single_author_abnt(clean_authors[0])
            first_author_abnt = re.sub(r"\s+et\s+al\.?$", "", first_author_abnt, flags=re.I).strip()
            if has_et_al:
                author_part = f"{first_author_abnt} et al."
            else:
                author_part = first_author_abnt

    # 2. Componente de título e subtítulo (com desduplicação)
    title_raw = metadata.title or metadata.titulo or "Documento"
    title_part = title_raw.strip()
    subtitle_raw = metadata.subtitle or metadata.subtitulo
    if subtitle_raw:
        sub = subtitle_raw.strip()
        sub_norm = "".join(c for c in unicodedata.normalize("NFKD", sub) if not unicodedata.combining(c)).lower()
        sub_norm = re.sub(r"[^\w\s]", "", sub_norm).strip()
        title_norm = "".join(c for c in unicodedata.normalize("NFKD", title_part) if not unicodedata.combining(c)).lower()
        title_norm = re.sub(r"[^\w\s]", "", title_norm).strip()
        if sub_norm and sub_norm not in title_norm and len(title_part) + len(sub) <= 120:
            title_part = f"{title_part} - {sub}"

    # 3. Componente de ano
    year_val = metadata.year or metadata.ano
    year_part = f"({year_val})" if year_val else ""

    # Monta os componentes
    components = []
    if author_part:
        components.append(author_part)
    components.append(title_part)
    if year_part:
        components.append(year_part)

    stem = " - ".join(components) if len(components) > 1 else components[0]
    stem = sanitize_filename(stem)

    # Fallback se vazio ou curto
    if not stem or len(stem) < 3:
        stem = Path(fallback_name).stem if fallback_name else "documento_classificado"

    # Trunca se ultrapassar 180 caracteres para segurança máxima no sistema de arquivos
    if len(stem) > 180:
        stem = stem[:177].rstrip(" .-_") + "..."

    return stem


# Alias em português
gerar_nome_padronizado = generate_standardized_filename
sanitizar_nome_arquivo = sanitize_filename


def converter_pdf(
    caminho: Path,
    *,
    paginas_inicio: int = 10,
    paginas_fim: int = 10,
    max_caracteres_analise: int = 30_000,
    **kwargs: Any,
) -> DocumentoConvertido:
    """Extrai texto das 10 primeiras e 10 últimas páginas do PDF para catalogação.

    Não converte o livro ou artigo inteiro para Markdown.
    """
    import pymupdf

    pymupdf.set_messages(stream=io.StringIO())

    try:
        doc = pymupdf.open(caminho)
    except Exception as exc:
        raise ErroDeConversao(f"Não foi possível abrir o PDF: {exc}") from exc

    try:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ErroDeConversao("PDF protegido por senha")

        total_paginas = doc.page_count
        if total_paginas == 0:
            raise ErroDeConversao("PDF sem páginas")

        metadados_embutidos = _metadados_uteis(doc.metadata or {})

        # Seleciona as 10 primeiras páginas e as 10 últimas páginas
        indices_inicio = list(range(min(paginas_inicio, total_paginas)))
        if total_paginas > paginas_inicio:
            fim_start = max(paginas_inicio, total_paginas - paginas_fim)
            indices_fim = list(range(fim_start, total_paginas))
        else:
            indices_fim = []

        todos_indices = sorted(set(indices_inicio + indices_fim))

        textos_paginas = []
        for idx in todos_indices:
            try:
                page_text = doc[idx].get_text("text")
                if page_text and page_text.strip():
                    textos_paginas.append(f"--- [Página {idx + 1}] ---\n{page_text}")
            except Exception as e:
                logger.debug("Página %d ilegível em %s: %s", idx, caminho.name, e)

        texto_amostra = "\n\n".join(textos_paginas)
    finally:
        doc.close()

    if not texto_amostra.strip():
        raise ErroDeConversao(
            "Nenhum texto nativo extraível — o PDF provavelmente é digitalizado/escaneado. "
            "Dica: use uma ferramenta de OCR (ex.: ocrmypdf) para adicionar camada de texto antes de catalogar."
        )

    return DocumentoConvertido(
        caminho=caminho,
        markdown_inicial=texto_amostra[:max_caracteres_analise],
        total_paginas=total_paginas,
        metadados_embutidos=metadados_embutidos,
        markdown_completo="",  # Miolo não é convertido por especificação do usuário
    )


def _metadados_uteis(brutos: dict) -> dict[str, str]:
    interessantes = ("title", "author", "subject", "keywords", "creator", "producer")
    return {
        k: str(v).strip()
        for k, v in brutos.items()
        if k in interessantes and v and str(v).strip()
    }


class MarkdownConverter:
    """Gerador do Markdown de acompanhamento com Frontmatter YAML e ABNT."""

    @staticmethod
    def build_yaml_frontmatter(metadata: PublicationMetadata) -> str:
        frontmatter_dict: Dict[str, Any] = {
            "title": metadata.title or metadata.titulo,
        }
        sub = metadata.subtitle or metadata.subtitulo
        if sub:
            frontmatter_dict["subtitle"] = sub

        frontmatter_dict["authors"] = metadata.authors or metadata.autores or []
        frontmatter_dict["publisher"] = metadata.publisher or metadata.editora_ou_periodico or ""

        city = metadata.city or metadata.local
        if city:
            frontmatter_dict["city"] = city

        if metadata.edition:
            frontmatter_dict["edition"] = metadata.edition

        frontmatter_dict["identifiers"] = {
            "isbn": metadata.identificadores.isbn or "",
            "doi": metadata.identificadores.doi or "",
            "issn": metadata.identificadores.issn or "",
        }
        frontmatter_dict["classification"] = metadata.classification

        yr = metadata.year or metadata.ano
        if yr:
            frontmatter_dict["year"] = yr

        ar = metadata.area or metadata.area_principal
        if ar and ar != "Outros":
            frontmatter_dict["area"] = ar

        if metadata.journal:
            frontmatter_dict["journal"] = metadata.journal
        if metadata.source_apis:
            frontmatter_dict["enriched_by"] = metadata.source_apis

        # Gera tags para Obsidian / Logseq
        tags = list(metadata.tags)
        if metadata.classification:
            tag_cls = f"tipo/{metadata.classification}"
            if tag_cls not in tags:
                tags.append(tag_cls)
        if yr:
            tag_yr = f"ano/{yr}"
            if tag_yr not in tags:
                tags.append(tag_yr)
        if ar and ar != "Outros":
            area_slug = re.sub(r"[^\w]+", "-", ar.lower()).strip("-")
            if area_slug:
                tag_area = f"area/{area_slug}"
                if tag_area not in tags:
                    tags.append(tag_area)
        if metadata.needs_review:
            if "status/revisao" not in tags:
                tags.append("status/revisao")
            frontmatter_dict["needs_review"] = True
            if metadata.review_reasons:
                frontmatter_dict["review_reasons"] = metadata.review_reasons

        if tags:
            frontmatter_dict["tags"] = tags

        yaml_content = yaml.safe_dump(
            frontmatter_dict,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ).strip()

        return f"---\n{yaml_content}\n---\n\n"

    @classmethod
    def assemble_markdown(
        cls,
        metadata: PublicationMetadata,
        abnt_reference: str,
        pdf_path: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Monta o arquivo Markdown de acompanhamento contendo Frontmatter e Referência ABNT."""
        frontmatter = cls.build_yaml_frontmatter(metadata)
        title_str = metadata.title or metadata.titulo or "Documento"
        subtitle_str = metadata.subtitle or metadata.subtitulo
        subtitle_part = f": {subtitle_str}" if subtitle_str else ""

        markdown_doc = (
            f"{frontmatter}"
            f"# {title_str}{subtitle_part}\n\n"
            f"## Referência Bibliográfica\n\n"
            f"{abnt_reference}\n"
        )
        return markdown_doc


def gerar_markdown(
    metadados: Metadados,
    referencia_abnt: str = "",
    *,
    arquivo_origem: Optional[Path] = None,
    total_paginas: Optional[int] = None,
    **kwargs: Any,
) -> str:
    """Função de conveniência para gerar o Markdown de acompanhamento."""
    ref = referencia_abnt or metadados.referencia_abnt
    return MarkdownConverter.assemble_markdown(
        metadata=metadados,
        abnt_reference=ref,
        pdf_path=str(arquivo_origem) if arquivo_origem else None,
    )


def listar_pdfs(origem: Path, *, recursivo: bool = True) -> list[Path]:
    """Lista os PDFs da pasta de origem, ordenados alfabeticamente."""
    if not origem.exists():
        raise ErroDeConversao(f"Diretório de origem não encontrado: {origem}")
    if not origem.is_dir():
        raise ErroDeConversao(f"A origem não é um diretório: {origem}")

    padrao = "**/*" if recursivo else "*"
    encontrados = [
        caminho
        for caminho in origem.glob(padrao)
        if caminho.is_file()
        and caminho.suffix.lower() == ".pdf"
        and not caminho.name.startswith(".")
    ]
    return sorted(encontrados)
