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

from .models import Metadados, PublicationMetadata, ResultadoExtracaoTexto

logger = logging.getLogger(__name__)


class ErroDeConversao(RuntimeError):
    """Falha ao ler ou processar o PDF."""


class ErroPdfEscaneado(ErroDeConversao):
    """Erro específico para PDFs escaneados ou imagens sem camada de texto nativa (requer OCR)."""


@dataclass
class DocumentoConvertido:
    """Resultado da leitura das páginas de análise de um PDF."""

    caminho: Path
    markdown_inicial: str
    total_paginas: int
    metadados_embutidos: dict[str, str] = field(default_factory=dict)
    markdown_completo: str = ""
    extracao_texto: Optional[ResultadoExtracaoTexto] = None

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


def is_ocr_available() -> bool:
    """Verifica se o motor local de OCR (rapidocr_onnxruntime) está instalado e disponível."""
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        return True
    except ImportError:
        return False


def executar_ocr_seletivo(
    caminho: Path,
    paginas_inicio: int = 10,
    paginas_fim: int = 10,
    indices_alvo: Optional[list[int]] = None,
) -> tuple[str, list[int]]:
    """Executa OCR seletivo apenas quando necessário e restrito até as primeiras e últimas 10 páginas.

    Utiliza RapidOCR (ONNX Runtime) com PyMuPDF em memória, 100% offline, rápido e determinístico.
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        logger.warning("rapidocr_onnxruntime não está instalado; OCR indisponível.")
        return "", []

    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf

    try:
        ocr = RapidOCR()
    except Exception as exc:
        logger.warning("Falha ao inicializar RapidOCR: %s", exc)
        return "", []

    try:
        doc = pymupdf.open(str(caminho))
    except Exception as exc:
        logger.warning("Falha ao abrir PDF para OCR em %s: %s", caminho.name, exc)
        return "", []

    try:
        total_p = doc.page_count
        if total_p == 0:
            return "", []

        if indices_alvo is not None:
            todos_indices = sorted(set(idx for idx in indices_alvo if 0 <= idx < total_p))
        else:
            indices_inicio = list(range(min(paginas_inicio, total_p)))
            if total_p > paginas_inicio:
                fim_start = max(paginas_inicio, total_p - paginas_fim)
                indices_fim = list(range(fim_start, total_p))
            else:
                indices_fim = []
            todos_indices = sorted(set(indices_inicio + indices_fim))
        textos_ocr = []
        paginas_processadas = []

        for idx in todos_indices:
            try:
                page = doc[idx]
                pix = page.get_pixmap(dpi=150)
                img_bytes = pix.tobytes("png")
                result, _ = ocr(img_bytes)
                if result:
                    linhas = [line[1].strip() for line in result if line and len(line) > 1 and line[1].strip()]
                    if linhas:
                        page_txt = "\n".join(linhas)
                        textos_ocr.append(f"--- [Página {idx + 1} - OCR] ---\n{page_txt}")
                        paginas_processadas.append(idx + 1)
            except Exception as e:
                logger.debug("Falha de OCR na página %d de %s: %s", idx, caminho.name, e)

        return "\n\n".join(textos_ocr), paginas_processadas
    finally:
        doc.close()


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

    max_mb_str = os.getenv("ORGPDF_MAX_FILE_SIZE_MB", "500")
    try:
        max_bytes = int(max_mb_str) * 1024 * 1024
    except ValueError:
        max_bytes = 500 * 1024 * 1024

    try:
        file_size = caminho.stat().st_size
        if file_size > max_bytes:
            raise ErroDeConversao(
                f"Arquivo excede o tamanho máximo permitido de {max_bytes // (1024 * 1024)} MB "
                f"({file_size / (1024 * 1024):.1f} MB)."
            )
    except OSError:
        pass

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

        paginas_texto: dict[int, str] = {}
        for idx in todos_indices:
            try:
                page_text = doc[idx].get_text("text")
                if page_text and page_text.strip():
                    paginas_texto[idx] = page_text.strip()
            except Exception as e:
                logger.debug("Página %d ilegível em %s: %s", idx, caminho.name, e)

        texto_amostra = "\n\n".join(f"--- [Página {idx + 1}] ---\n{paginas_texto[idx]}" for idx in sorted(paginas_texto))
    finally:
        doc.close()

    caracteres_uteis = len(re.findall(r"[\w]", texto_amostra))
    palavras_validas = re.findall(r"[a-zA-ZÀ-ÿ]{3,}", texto_amostra.lower())
    total_palavras = len(palavras_validas)
    palavras_distintas = len(set(palavras_validas))
    paginas_lidas = len(todos_indices)
    ocr_aplicado = False
    paginas_ocr: list[int] = []

    # Aciona OCR seletivo por etapas apenas quando necessário (texto nativo ausente ou insuficiente)
    if not texto_amostra.strip() or caracteres_uteis < 45 or palavras_distintas < 4:
        logger.info(
            "[%s] Texto nativo insuficiente (%d caracteres, %d palavras distintas). Iniciando OCR seletivo por etapas...",
            caminho.name,
            caracteres_uteis,
            palavras_distintas,
        )

        # Definição das 4 etapas conforme diretriz do usuário:
        # Etapa 1: 5 primeiras páginas (1 a 5)
        # Etapa 2: 5 páginas seguintes (6 a 10)
        # Etapa 3: 5 últimas páginas
        # Etapa 4: 5 penúltimas páginas
        etapas = []
        e1 = [i for i in range(min(5, total_paginas))]
        if e1:
            etapas.append(("Etapa 1/4 (5 primeiras páginas)", e1))
        if total_paginas > 5:
            e2 = [i for i in range(5, min(10, total_paginas))]
            if e2:
                etapas.append(("Etapa 2/4 (5 páginas seguintes)", e2))
        if total_paginas > 10:
            e3_start = max(10, total_paginas - 5)
            e3 = [i for i in range(e3_start, total_paginas)]
            if e3:
                etapas.append(("Etapa 3/4 (5 últimas páginas)", e3))
        if total_paginas > 15:
            e4_start = max(10, total_paginas - 10)
            e4_end = max(10, total_paginas - 5)
            e4 = [i for i in range(e4_start, e4_end)]
            if e4:
                etapas.append(("Etapa 4/4 (5 penúltimas páginas)", e4))

        for nome_etapa, indices_etapa in etapas:
            alvos = [idx for idx in indices_etapa if len(paginas_texto.get(idx, "")) < 30]
            if not alvos:
                continue

            faixa_str = f"páginas {min(alvos) + 1} a {max(alvos) + 1}"
            logger.info(
                "[%s] OCR %s: Processando %s (%d páginas)...",
                caminho.name,
                nome_etapa,
                faixa_str,
                len(alvos),
            )

            texto_ocr_etapa, pags_ocr_etapa = executar_ocr_seletivo(
                caminho,
                indices_alvo=alvos,
            )

            if texto_ocr_etapa.strip():
                for bloco in texto_ocr_etapa.split("--- [Página "):
                    if not bloco.strip():
                        continue
                    m_idx = re.match(r"^(\d+)", bloco)
                    if m_idx:
                        p_num = int(m_idx.group(1))
                        idx_0 = p_num - 1
                        corpo = re.sub(r"^\d+\s*-\s*OCR\]\s*---\s*", "", bloco).strip()
                        if len(corpo) > len(paginas_texto.get(idx_0, "")):
                            paginas_texto[idx_0] = corpo
                ocr_aplicado = True
                paginas_ocr.extend(pags_ocr_etapa)

            texto_amostra = "\n\n".join(f"--- [Página {idx + 1}] ---\n{paginas_texto[idx]}" for idx in sorted(paginas_texto))
            caracteres_uteis = len(re.findall(r"[\w]", texto_amostra))
            palavras_validas = re.findall(r"[a-zA-ZÀ-ÿ]{3,}", texto_amostra.lower())
            total_palavras = len(palavras_validas)
            palavras_distintas = len(set(palavras_validas))

            if caracteres_uteis >= 45 and palavras_distintas >= 4:
                logger.info(
                    "[%s] OCR %s bem-sucedido: texto suficiente obtido (%d caracteres, %d palavras distintas). Interrompendo OCR.",
                    caminho.name,
                    nome_etapa,
                    caracteres_uteis,
                    palavras_distintas,
                )
                break
            else:
                logger.info(
                    "[%s] OCR %s concluído, mas texto ainda insuficiente (%d caracteres, %d palavras distintas). Tentando próxima etapa...",
                    caminho.name,
                    nome_etapa,
                    caracteres_uteis,
                    palavras_distintas,
                )

        paginas_ocr = sorted(set(paginas_ocr))

    if not texto_amostra.strip() or caracteres_uteis < 20:
        raise ErroPdfEscaneado(
            "Nenhum texto nativo extraível — o PDF provavelmente é digitalizado/escaneado "
            "(inclusive após tentativa de OCR seletivo). "
            "Dica: verifique a integridade do arquivo PDF ou a resolução das páginas digitalizadas."
        )

    # Avaliação de suficiência para identificar a obra (Passo 1 do fluxo)
    suficiente = True
    motivo_insuficiencia = None
    tem_ocr = is_ocr_available()
    if caracteres_uteis < 45 or palavras_distintas < 4:
        suficiente = False
        if not tem_ocr and not ocr_aplicado:
            estado_ocr = "OCR_INDISPONIVEL"
            motivo_insuficiencia = (
                f"Texto nativo insuficiente ({caracteres_uteis} caracteres úteis) e OCR local indisponível "
                f"(rapidocr_onnxruntime não instalado)."
            )
        elif ocr_aplicado:
            estado_ocr = "EXECUTADO"
            motivo_insuficiencia = (
                f"Texto insuficiente para identificação segura da obra mesmo após OCR seletivo "
                f"({caracteres_uteis} caracteres úteis e {palavras_distintas} palavras distintas)."
            )
        else:
            estado_ocr = "OCR_INDISPONIVEL"
            motivo_insuficiencia = (
                f"Texto nativo insuficiente ({caracteres_uteis} caracteres úteis) para identificação segura da obra."
            )
    else:
        estado_ocr = "EXECUTADO" if ocr_aplicado else "NAO_NECESSARIO"

    extracao_texto = ResultadoExtracaoTexto(
        caracteres_uteis=caracteres_uteis,
        total_palavras=total_palavras,
        paginas_lidas=paginas_lidas,
        suficiente=suficiente,
        requer_ocr=not suficiente,
        motivo_insuficiencia=motivo_insuficiencia,
        ocr_aplicado=ocr_aplicado,
        paginas_ocr=paginas_ocr,
        ocr_disponivel=tem_ocr,
        estado_ocr=estado_ocr,
    )

    return DocumentoConvertido(
        caminho=caminho,
        markdown_inicial=texto_amostra[:max_caracteres_analise],
        total_paginas=total_paginas,
        metadados_embutidos=metadados_embutidos,
        markdown_completo="",  # Miolo não é convertido por especificação do usuário
        extracao_texto=extracao_texto,
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
            "versao_esquema": "2.0",
            "tipo_documento": metadata.classification or getattr(metadata, "tipo_publicacao", TipoPublicacao.OUTROS).value,
            "titulo": metadata.title or metadata.titulo or "Sem Título",
        }
        sub = metadata.subtitle or metadata.subtitulo
        if sub:
            frontmatter_dict["subtitulo"] = sub

        autores = metadata.authors or metadata.autores or []
        frontmatter_dict["autores"] = autores
        if autores:
            frontmatter_dict["autor_principal"] = autores[0]

        # Separar periódico de editora conforme ABNT NBR 6023
        journal_val = getattr(metadata, "periodico", None) or getattr(metadata, "journal", None)
        if journal_val:
            frontmatter_dict["periodico"] = journal_val

        is_article_or_journal = (metadata.classification or "").lower() in ("artigo", "artigo_cientifico", "revista")
        pub_val = metadata.publisher or getattr(metadata, "editora", None)
        if not pub_val and not is_article_or_journal:
            pub_val = getattr(metadata, "editora_ou_periodico", None)

        if pub_val and pub_val != journal_val:
            frontmatter_dict["editora"] = pub_val

        vol_val = getattr(metadata, "volume", None)
        if vol_val:
            frontmatter_dict["volume"] = str(vol_val)
        num_val = getattr(metadata, "numero", None) or getattr(metadata, "number", None)
        if num_val:
            frontmatter_dict["numero"] = str(num_val)
        pag_val = getattr(metadata, "paginas", None) or getattr(metadata, "pages", None)
        if pag_val:
            frontmatter_dict["paginas"] = str(pag_val)

        city = metadata.city or metadata.local
        if city:
            frontmatter_dict["local"] = city

        if metadata.edition:
            frontmatter_dict["edicao"] = metadata.edition

        yr = metadata.year or metadata.ano
        if yr:
            frontmatter_dict["ano"] = yr

        ar = metadata.area or metadata.area_principal
        if ar and ar != "Outros":
            frontmatter_dict["area_principal"] = ar
        sub_ar = getattr(metadata, "subarea", None)
        if sub_ar and sub_ar not in ("Geral", "Outros", ar):
            frontmatter_dict["subarea"] = sub_ar

        frontmatter_dict["identificadores"] = {
            "isbn": metadata.identificadores.isbn or "",
            "doi": metadata.identificadores.doi or "",
            "issn": metadata.identificadores.issn or "",
        }

        if getattr(metadata, "referencia_abnt", None):
            frontmatter_dict["referencia_abnt"] = metadata.referencia_abnt

        if metadata.source_apis:
            frontmatter_dict["fontes_enriquecimento"] = metadata.source_apis

        # Gera tags para Obsidian / Logseq
        tags = list(metadata.tags)
        cls_name = metadata.classification or getattr(metadata, "tipo_publicacao", TipoPublicacao.OUTROS).value
        if cls_name:
            tag_cls = f"tipo/{cls_name}"
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

        frontmatter_dict["confidence"] = metadata.confidence
        frontmatter_dict["confidence_classification"] = getattr(metadata, "confidence_classification", metadata.confidence)
        frontmatter_dict["confidence_metadata"] = getattr(metadata, "confidence_metadata", metadata.confidence)
        if getattr(metadata, "title_source", None):
            frontmatter_dict["title_source"] = metadata.title_source
        if getattr(metadata, "author_source", None):
            frontmatter_dict["author_source"] = metadata.author_source
        if getattr(metadata, "year_source", None):
            frontmatter_dict["year_source"] = metadata.year_source
        if getattr(metadata, "city_source", None):
            frontmatter_dict["city_source"] = metadata.city_source
        if getattr(metadata, "publisher_source", None):
            frontmatter_dict["publisher_source"] = metadata.publisher_source
        if getattr(metadata, "reimpressao", None):
            frontmatter_dict["reimpressao"] = metadata.reimpressao
        if getattr(metadata, "isbn_source", None):
            frontmatter_dict["isbn_source"] = metadata.isbn_source
        if getattr(metadata, "isbn_verified", False):
            frontmatter_dict["isbn_verified"] = metadata.isbn_verified
        if getattr(metadata, "doi_source", None):
            frontmatter_dict["doi_source"] = metadata.doi_source
        if getattr(metadata, "issn_source", None):
            frontmatter_dict["issn_source"] = metadata.issn_source
        if getattr(metadata, "provedor_classificador", None):
            frontmatter_dict["provedor_classificador"] = metadata.provedor_classificador
        if getattr(metadata, "status_por_provedor", None):
            frontmatter_dict["status_por_provedor"] = metadata.status_por_provedor
        if getattr(metadata, "regra_classificacao_tematica", None):
            frontmatter_dict["regra_classificacao_tematica"] = metadata.regra_classificacao_tematica
        if getattr(metadata, "justificativa_jev", None):
            frontmatter_dict["justificativa_classificacao"] = metadata.justificativa_jev
        if getattr(metadata, "justificativa_classificacao_tematica", None):
            frontmatter_dict["justificativa_classificacao_tematica"] = metadata.justificativa_classificacao_tematica
        if getattr(metadata, "tem_texto_integral", False):
            frontmatter_dict["texto_integral_disponivel"] = True
            if getattr(metadata, "url_texto_integral", None):
                frontmatter_dict["url_texto_integral"] = metadata.url_texto_integral
        elif getattr(metadata, "apenas_referencia", False):
            frontmatter_dict["texto_integral_disponivel"] = False
            frontmatter_dict["apenas_referencia"] = True
        if getattr(metadata, "criterios_atendidos", None):
            frontmatter_dict["criterios_confianca_atendidos"] = metadata.criterios_atendidos

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


def migrar_markdown_frontmatter(texto_markdown: str) -> tuple[str, bool, list[str]]:
    """Migra frontmatter YAML de Markdown legados para o esquema canônico 2.0.

    - Detecta conflitos entre pares legados (title/titulo, year/ano, authors/autores, publisher/editora).
    - Remove aliases redundantes em inglês.
    - Anota conflitos e sinaliza needs_review = True quando detectados.
    - Versiona o esquema com versao_esquema: "2.0".

    Retorna:
        (texto_migrado, migrado, lista_de_conflitos)
    """
    if not texto_markdown.startswith("---"):
        return texto_markdown, False, []

    partes = texto_markdown.split("---", 2)
    if len(partes) < 3:
        return texto_markdown, False, []

    yaml_str = partes[1]
    corpo = partes[2]
    try:
        dados = yaml.safe_load(yaml_str) or {}
    except Exception:
        return texto_markdown, False, []

    if not isinstance(dados, dict):
        return texto_markdown, False, []

    conflitos: list[str] = []
    alterado = False

    # Conflitos de Título
    if "title" in dados and "titulo" in dados:
        t_en = str(dados["title"]).strip()
        t_pt = str(dados["titulo"]).strip()
        if t_en and t_pt and t_en.lower() != t_pt.lower():
            conflitos.append(f"Conflito entre 'title' ('{t_en}') e 'titulo' ('{t_pt}')")
        del dados["title"]
        alterado = True
    elif "title" in dados:
        dados["titulo"] = dados.pop("title")
        alterado = True

    # Subtítulo
    if "subtitle" in dados:
        sub = dados.pop("subtitle")
        if "subtitulo" not in dados:
            dados["subtitulo"] = sub
        alterado = True

    # Conflitos de Ano
    if "year" in dados and "ano" in dados:
        try:
            if int(dados["year"]) != int(dados["ano"]):
                conflitos.append(f"Conflito entre 'year' ({dados['year']}) e 'ano' ({dados['ano']})")
        except (ValueError, TypeError):
            if str(dados["year"]) != str(dados["ano"]):
                conflitos.append(f"Conflito entre 'year' ({dados['year']}) e 'ano' ({dados['ano']})")
        del dados["year"]
        alterado = True
    elif "year" in dados:
        dados["ano"] = dados.pop("year")
        alterado = True

    # Conflitos de Autores
    if "authors" in dados and "autores" in dados:
        a_en = dados["authors"]
        a_pt = dados["autores"]
        if a_en != a_pt:
            conflitos.append(f"Conflito entre 'authors' ({a_en}) e 'autores' ({a_pt})")
        del dados["authors"]
        alterado = True
    elif "authors" in dados:
        dados["autores"] = dados.pop("authors")
        alterado = True

    # Conflitos de Editora / Publisher
    if "publisher" in dados:
        pub = dados.pop("publisher")
        if "editora" in dados and dados["editora"] != pub:
            conflitos.append(f"Conflito entre 'publisher' ('{pub}') e 'editora' ('{dados['editora']}')")
        elif "editora_ou_periodico" in dados and dados["editora_ou_periodico"] != pub:
            conflitos.append(f"Conflito entre 'publisher' ('{pub}') e 'editora_ou_periodico' ('{dados['editora_ou_periodico']}')")
        if "editora" not in dados:
            dados["editora"] = pub
        alterado = True

    # City -> Local
    if "city" in dados:
        c = dados.pop("city")
        if "local" in dados and dados["local"] != c:
            conflitos.append(f"Conflito entre 'city' ('{c}') e 'local' ('{dados['local']}')")
        elif "local" not in dados:
            dados["local"] = c
        alterado = True

    # Classification / Tipo
    if "classification" in dados:
        cls_val = dados.pop("classification")
        if "tipo_documento" not in dados:
            dados["tipo_documento"] = cls_val
        alterado = True

    # Se houve conflitos, marca needs_review
    if conflitos:
        dados["needs_review"] = True
        reasons = list(dados.get("motivos_revisao") or dados.get("review_reasons") or [])
        for c in conflitos:
            if c not in reasons:
                reasons.append(c)
        dados["motivos_revisao"] = reasons
        if "review_reasons" in dados:
            del dados["review_reasons"]

    # Identificadores: consolidar em 'identificadores' canônico e eliminar chaves de topo duplicadas
    if "identificadores" not in dados or not isinstance(dados["identificadores"], dict):
        dados["identificadores"] = {}
    for id_key in ("isbn", "doi", "issn"):
        if id_key in dados:
            id_val = dados.pop(id_key)
            if id_val and not dados["identificadores"].get(id_key):
                dados["identificadores"][id_key] = id_val
            alterado = True

    dados["versao_esquema"] = "2.0"

    novo_yaml = yaml.safe_dump(
        dados, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).strip()
    return f"---\n{novo_yaml}\n---{corpo}", bool(conflitos), conflitos
