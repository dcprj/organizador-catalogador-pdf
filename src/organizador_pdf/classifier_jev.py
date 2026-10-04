"""Jev (System One) PDF Classifier and Metadata Validator.

Reads initial pages of a PDF extracting selectable native text (no OCR)
and uses TypeSafe AI's Jev model primitives (Choice and Noul) to classify
the publication type and assess probabilities of candidate metadata.

Enhanced with:
- Strict identifier extraction (preventing phone numbers from being treated as ISSNs).
- Page-by-page AACR2 / ISBD Ficha Catalográfica (CIP) parser.
- Academic Thesis and Dissertation (monograph) parser.
- E-book boilerplate, disclaimer, and publisher header filtering.
- Extended filename metadata parsing.
- Title and author author-overlap disambiguation.
- Short candidate title replacement with clean filename title.
- Accurate classification between Books, Articles, Courseware, Magazines, and Others.
"""

import os
import re
import logging
import unicodedata
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import pymupdf
from dotenv import load_dotenv

from .models import (
    PublicationType,
    ExtractedCandidates,
    JevValidationResult,
    is_valid_isbn,
    is_valid_issn,
    normalizar_doi,
)

load_dotenv()

logger = logging.getLogger(__name__)


class ErroDeClassificacaoRemota(RuntimeError):
    """Falha irrecuperável na chamada à API remota de classificação TypeSafe AI."""

# Regular expressions for candidate extraction
DOI_REGEX = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b", re.IGNORECASE)
ISBN_REGEX = re.compile(
    r"\b(?:ISBN(?:-1[03])?[:\s]+([0-9Xx -]{10,17})|(?:97[89][- ]?[0-9]{1,5}[- ]?[0-9]+[- ]?[0-9]+[- ]?[0-9X]))\b",
    re.IGNORECASE,
)
# Strict ISSN: Must be preceded by ISSN to prevent matching Brazilian phone numbers or historical year spans
ISSN_REGEX = re.compile(
    r"\bISSN(?:\s*\(?[^)\n\r]*\)?)?[:\s]+(\d{4}-\d{3}[\dX])\b",
    re.IGNORECASE,
)

# Ignored lines that pollute title/author candidate extraction
IGNORED_PATTERNS = [
    r"^\[page break\]",
    r"^-{3,}\s*\[page break\]\s*-{3,}",
    r"^-{3,}\s*\[p[aá]gina\s*\d+\]\s*-{3,}.*",
    r"^-{3,}\s*\[page\s*\d+\]\s*-{3,}.*",
    r"^\[p[aá]gina\s*\d+\]",
    r"^\[page\s*\d+\]",
    r"^-{3,}.*-{3,}$",
    r"^[\d\s\.\-_/]+$",
    r"^(?:artigo\s+original|original\s+article|artigo\s+de\s+revis[ãa]o|review\s+article|editorial|comunica[çc][ãa]o\s+breve|relato\s+de\s+caso)\b",
    r"^(?:revista\s+|journal\s+of\s+|cadernos\s+de\s+|boletim\s+|anais\s+do|mem[óo]rias\s+do)\b.*?\b(?:v(?:ol)?\.?\s*\d+|n[ºoú]\.?\s*\d+|\d{4})",
    r"^p[aá]gina\s+\d+",
    r"^p\.?\s*\d+$",
    r"^\d+$",
    r"dados de odinright",
    r"sobre a obra:",
    r"a presente obra [eé] disponibilizada",
    r"equipe elivros",
    r"elivros",
    r"seus diversos parceiros",
    r"com o objetivo de oferecer",
    r"conte[uú]do para uso parcial",
    r"[eé] expressamente proibida",
    r"sobre n[oó]s",
    r"como posso contribuir",
    r"pesquisas e estudos acad[êe]micos",
    r"simples teste da qualidade",
    r"obra, com o f[ií]m exclusivo",
    r"compra futura",
    r"repudi[aá]vel",
    r"conhecimento e a educa[çc][ãa]o",
    r"converted by epubtopdf",
    r"envie um livro",
    r"fa[çc]a uma doa[çc][ãa]o",
    r"quando o mundo estiver unido",
    r"venda,\s*aluguel",
    r"aluguel,?\s+ou\s+quaisquer",
    r"uso\s+comercial\s+do\s+presente",
    r"[eé]\s+expressamente\s+proibida",
    r"repudi[aá]vel\s+a\s+venda",
    r"distribui[çc][ãa]o\s+gratuita",
    r"licen[çc]a\s+de\s+uso",
    r"creative\s+commons",
    r"todos\s+os\s+direitos\s+reservados",
    r"reprodu[çc][ãa]o\s+proibida",
    r"^copyright",
    r"compre agora e leia",
    r"compre\s+agora",
    r"leia\s+tamb[eé]m",
    r"outras\s+obras",
    r"do\s+mesmo\s+autor",
    r"table of contents",
    r"^sum[aá]rio",
    r"nenhuma parte desta publica[çc][ãa]o pode ser reproduzida",
    r"elaborada pelo sistema de bibliotecas",
    r"sindicato nacional dos editores",
    r"c[âa]mara brasileira do livro",
    r"biblioteca comunit[áa]ria",
    r"processos t[ée]cnicos",
    r"dom[íi]nio\s+p[úu]blico",
    r"propriedade\s+intelectual",
    r"direitos?\s+autorais?",
    r"direitos?\s+reservados?",
    r"termo(?:s)?\s+de\s+uso",
    r"^https?://",
    r"^doi[:\s]",
    r"^issn[:\s]",
    r"^isbn[:\s]",
    r"^(?:issn|isbn|doi)[:\s0-9Xx\-./]+$",
]

# Disclaimers and legal terms that should never be treated as titles
DISCLAIMER_TITLE_PATTERN = re.compile(
    r"\b(?:dom[íi]nio\s+p[úu]blico|propriedade\s+intelectual|direitos?\s+autorais?|"
    r"direitos?\s+reservados?|termo(?:s)?\s+de\s+uso|licen[çc]a\s+de\s+uso|creative\s+commons|"
    r"reprodu[çc][ãa]o\s+proibida|distribui[çc][ãa]o\s+gratuita|elivros|odinright|"
    r"compra\s+futura|fim\s+exclusivo|presente\s+obra|disponibilizada\s+pela|"
    r"totalmente\s+gratuita|acreditar\s+que\s+o\s+conhecimento|conhecimento\s+e\s+a\s+educa|"
    r"dados\s+internacionais\s+de\s+cataloga[çc][ãa]o|ficha\s+catalogr[áa]fica)\b",
    re.I,
)

# Identifiers, protocols, or URLs mistaken for titles
IDENTIFIER_AS_TITLE_PATTERN = re.compile(
    r"^(?:ISSN|ISBN|DOI|HTTPS?://|DX\.DOI\.ORG|DOI\.ORG|FTP://|WWW\.)[:\s0-9X\-_./]+$",
    re.I,
)

# Editorial and translation credit dumps at the end of colophon/title blocks
CREDIT_SPLIT_PATTERN = re.compile(
    r"\s+(?:Tradu[çc][ãa]o(?:\s+de|\s+por)?|Revis[ãa]o(?:\s+t[ée]cnica)?|"
    r"Capa(?:\s*:)?|Projeto\s+gr[áa]fico|Coordena[çc][ãa]o\s+editorial|"
    r"Conselho\s+editorial|Editora\s+respons[áa]vel|Ilustra[çc][õo]es(?:\s+de)?|"
    r"Diagrama[çc][ãa]o(?:\s*:)?|Edi[çc][ãa]o\s+de\s+texto|Editora\s+[A-Z])[:\s]",
    re.I,
)

GENERIC_SINGLE_WORD_TITLES = {
    "criar", "artigo", "livro", "capítulo", "capitulo", "introdução", "introducao",
    "conclusão", "conclusao", "resumo", "abstract", "prefácio", "prefacio",
    "sumário", "sumario", "editorial", "texto", "ensaios", "ensaio", "anexo",
}


def sanitize_title_candidate(title: Optional[str]) -> Optional[str]:
    """Limpa cabeçalhos, marcadores de página, códigos de catalogação e créditos editoriais do título."""
    if not title:
        return None
    t = title.strip()

    # 1. Remove marcadores de quebra e página, com ou sem colchete e traços
    t = re.sub(r"^-{3,}\s*\[.*?\]\s*-{3,}\s*", "", t)
    t = re.sub(r"^\[.*?\]\s*", "", t)
    t = re.sub(r"^(?:\[?p[áa]gina\s*\d+\]?|p\.\s*\d+\]?)\s*", "", t, flags=re.I)
    t = re.sub(r"^(?:\[?page\s*\d+\]?)\s*", "", t, flags=re.I)

    # 2. Remove códigos de catalogação bibliográfica (CDD, CDU, Cutter) no início do título
    # Exemplo: '150.195 - Neuropsicologia...', 'CDD 150 - ...', 'CDU 159.9: ...', 'N494 ...'
    t = re.sub(r"^(?:CDD|CDU)\s*[:\-–\.]?\s*(?:\d{1,4}(?:\.\d+)?\s*[-–:]?\s*)?", "", t, flags=re.I)
    t = re.sub(r"^\d{1,4}(?:\.\d+)?\s*[-–:]\s*", "", t)
    t = re.sub(r"^[A-Z]\d{2,4}[a-z]?\s+[-–:]?\s*", "", t)

    # 3. Trunca antes de blocos maciços de créditos editoriais/ficha
    m_credit = CREDIT_SPLIT_PATTERN.search(t)
    if m_credit and m_credit.start() >= 4:
        t = t[:m_credit.start()].strip()

    # 4. Limpa pontuações periféricas residuais
    t = t.strip(" -—–:\t\r\n\"'“”«»[]")
    return t if len(t) >= 2 else None


def is_invalid_or_disclaimer_title(title: Optional[str]) -> bool:
    """Verifica se o candidato a título é claramente inválido, disclaimer ou identificador."""
    if not title:
        return True
    t = title.strip()
    if len(t) < 3:
        return True
    if t.lower() in GENERIC_SINGLE_WORD_TITLES:
        return True
    if IDENTIFIER_AS_TITLE_PATTERN.match(t):
        return True
    if DISCLAIMER_TITLE_PATTERN.search(t):
        return True
    # Nomes de periódicos/revistas não devem ser tratados como título do artigo/obra
    if re.match(r"^(?:revista\s+|journal\s+of\s+|cadernos\s+de\s+|boletim\s+|acta\s+|anais\s+d[oa]s?)\b", t, re.I):
        return True
    # Identificador ISSN/ISBN com ou sem prefixo
    clean_digits = re.sub(r"[\s-]", "", t).upper()
    if is_valid_isbn(clean_digits, strict=False) or is_valid_issn(clean_digits, strict=False):
        return True
    if t.lower().startswith("10.") and "/" in t:
        return True
    return False


def strip_author_prefixes(author: str) -> str:
    """Remove honorary, academic, and organizational prefixes from author names."""
    cleaned = author.strip()
    cleaned = re.sub(
        r"^(?:prof(?:essor|a)?|dr[a-z]?|ph\.?d\.?|msc\.?|coord(?:enador)?|orientador[a-z]?|organizad(?:or|ores)[a-z]?)[:\.\s]+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    # Strip quotes and leading/trailing dashes or punctuation
    cleaned = cleaned.strip("\"'“”«»–- ")
    return cleaned


def extract_native_sample_text_with_count(
    pdf_path: str,
    head_pages: int = 10,
    tail_pages: Optional[int] = None,
    max_pages: Optional[int] = None,
) -> Tuple[str, List[str], int]:
    """Extract selectable native text from the first and last pages of a PDF and return total pages.

    Strictly native extraction without OCR. Captures front matter (title, authors, CIP)
    and back matter (references, colophon, publication details).
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    max_mb_str = os.getenv("ORGPDF_MAX_FILE_SIZE_MB", "500")
    try:
        max_bytes = int(max_mb_str) * 1024 * 1024
    except ValueError:
        max_bytes = 500 * 1024 * 1024

    try:
        file_size = os.path.getsize(pdf_path)
        if file_size > max_bytes:
            raise RuntimeError(
                f"Arquivo excede o tamanho máximo permitido de {max_bytes // (1024 * 1024)} MB "
                f"({file_size / (1024 * 1024):.1f} MB)."
            )
    except OSError:
        pass

    if max_pages is not None:
        head_pages = max_pages
        if tail_pages is None:
            tail_pages = 0
    elif tail_pages is None:
        tail_pages = head_pages

    pages_text: List[str] = []
    with pymupdf.open(pdf_path) as doc:
        total = len(doc)
        effective_tail = tail_pages or 0
        if total <= (head_pages + effective_tail):
            page_indices = list(range(total))
        else:
            page_indices = list(range(head_pages)) + (
                list(range(total - effective_tail, total)) if effective_tail > 0 else []
            )

        # Preserve ordering without duplicates
        seen = set()
        ordered_indices = []
        for idx in page_indices:
            if idx not in seen and 0 <= idx < total:
                seen.add(idx)
                ordered_indices.append(idx)

        for page_num in ordered_indices:
            page = doc[page_num]
            text = (page.get_text("text") or "").strip()
            pages_text.append(text)

    non_empty = [p for p in pages_text if p]
    combined_text = "\n\n".join(non_empty).strip()
    if not combined_text:
        from .converter import ErroPdfEscaneado
        raise ErroPdfEscaneado(
            "Nenhum texto nativo extraível — o PDF provavelmente é digitalizado/escaneado. "
            "Dica: use uma ferramenta de OCR (ex.: ocrmypdf) para adicionar camada de texto antes de catalogar."
        )
    return combined_text, pages_text, total


def extract_native_sample_text(
    pdf_path: str,
    head_pages: int = 10,
    tail_pages: Optional[int] = None,
    max_pages: Optional[int] = None,
) -> Tuple[str, List[str]]:
    """Extract selectable native text from the first and last pages of a PDF."""
    combined_text, pages_text, _ = extract_native_sample_text_with_count(
        pdf_path, head_pages=head_pages, tail_pages=tail_pages, max_pages=max_pages
    )
    return combined_text, pages_text


def parse_filename_metadata(pdf_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Infer candidate title and author from clean filename patterns."""
    ext = parse_filename_extended(pdf_path)
    return ext.get("title"), ext.get("author")


def parse_filename_extended(pdf_path: str) -> Dict[str, Any]:
    """Extract structured candidate metadata from filename."""
    stem = Path(pdf_path).stem.strip()
    result: Dict[str, Any] = {
        "title": None,
        "author": None,
        "year": None,
        "publisher": None,
    }

    # Remove generic classification prefix like 'Livro - ', 'Artigo - ', etc.
    cleaned_stem = re.sub(r"^(?:livro|artigo|apostila|revista)\s*-\s*", "", stem, flags=re.IGNORECASE).strip()

    # 1. Calibre / multi-part format separated by ' - '
    parts = [p.strip() for p in cleaned_stem.split(" - ") if p.strip()]
    if len(parts) >= 4 and any(re.match(r"^(19|20)\d{2}$", p) for p in parts):
        y_idx = next(i for i, p in enumerate(parts) if re.match(r"^(19|20)\d{2}$", p))
        result["title"] = parts[0]
        auth = parts[1]
        if ";" in auth:
            auth = auth.split(";")[0].strip()
        result["author"] = strip_author_prefixes(auth)
        result["year"] = int(parts[y_idx])
        if y_idx + 1 < len(parts):
            result["publisher"] = parts[y_idx + 1]
        return result

    # 2. Pattern: SOBRENOME, Iniciais. Titulo (e.g. 'ANTONIO, M.C.A. A ética do desejo')
    m_inits = re.match(r"^([A-ZÁÉÍÓÚÂÊÔÃÕÇ-]+,\s*[A-Z\.\s]+)\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ“\"].+)$", cleaned_stem)
    if m_inits:
        author_raw, title_raw = m_inits.group(1).strip(), m_inits.group(2).strip()
        result["title"] = title_raw
        result["author"] = strip_author_prefixes(author_raw)
        return result

    # 3. Pattern: AUTOR1 e AUTOR2-TITULO (e.g. 'BEER e FRANCO-DA_INDISSOCIABILIDADE...')
    m_auths = re.match(r"^([A-ZÁÉÍÓÚÂÊÔÃÕÇ\s]+ e [A-ZÁÉÍÓÚÂÊÔÃÕÇ\s]+)-([A-Z0-9_]+)$", cleaned_stem)
    if m_auths:
        result["author"] = strip_author_prefixes(m_auths.group(1).strip())
        result["title"] = m_auths.group(2).replace("_", " ").strip().capitalize()
        return result

    # 4. Standard: Author - Title (e.g. 'BENTO, Cida - O Pacto da Branquitude')
    if len(parts) >= 2:
        author_part = parts[0].strip()
        title_part = parts[1].strip()

        if "," in author_part:
            sp = author_part.split(",", 1)
            author_formatted = f"{sp[1].strip()} {sp[0].strip()}"
        else:
            author_formatted = author_part

        result["title"] = title_part.rstrip(".")
        result["author"] = strip_author_prefixes(author_formatted)
        return result

    result["title"] = cleaned_stem
    return result


def parse_page_cip(page_text: str) -> Optional[Dict[str, Any]]:
    """Parse AACR2 / ISBD Brazilian Cataloging-in-Publication (CIP) card from a single page."""
    # Check if this page contains catalog card indicators
    norm_low = page_text.lower()
    if not any(k in norm_low for k in ["ficha catalogr", "cip-brasil", "catalogação na publicação", "cdu", "cdd", "crb-"]):
        return None

    # Slice from catalog card header if present to avoid masthead/board text pollution
    m_header = re.search(r"(?:ficha catalogr[aá]fica|cip-brasil|dados internacionais de cataloga[çc][aã]o)", page_text, re.I)
    if m_header:
        page_text = page_text[m_header.start():]

    lines = [l.strip() for l in page_text.splitlines() if l.strip()]

    # 1. Locate primary author line: 'Surname, Given Name.'
    auth_idx = -1
    auth_name = None
    for idx, l in enumerate(lines[:20]):
        # Must not contain title or catalog punctuation or responsibility terms
        if any(w in l.lower() for w in ["organizad", "editor", "coord", "compilad", "/", "–", "--", " : ", " ed.", "dados", "recurso"]):
            continue
        clean_l = re.sub(r"^(?:[A-Za-z]?\d{1,4}(?:\.\d+)?[a-z]?|\d{3})\s+", "", l).strip()
        clean_l = re.sub(r",\s*\d{4}-.*$", "", clean_l).strip()
        m_auth = re.match(
            r"^([A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõçA-ZÁÉÍÓÚÂÊÔÃÕÇ\s-]+,\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇa-záéíóúâêôãõç\.\s-]+)$",
            clean_l,
        )
        if m_auth:
            cand = m_auth.group(1).rstrip(".").strip()
            if not any(h in cand.lower() for h in ["biblioteca", "sistema", "sindicato", "câmara", "processos", "elaborada"]):
                auth_name = cand
                auth_idx = idx
                break

    target_lines = lines[auth_idx + 1:] if auth_idx != -1 else lines

    # Filter out publisher address/boilerplate lines
    filtered_target = []
    for l in target_lines:
        if any(h in l.lower() for h in [
            "e-mail:", "fone", "fax", "rua ", "cep ", "copyright", "todos os direitos",
            "ficha catalogr", "biblioteca", "sindicato nacional", "nenhuma parte", "dados de odinright",
            "catalogação na publicação:"
        ]):
            continue
        filtered_target.append(l)

    joined = " ".join(filtered_target)
    # Strip leading Cutter if attached to first word or standalone e.g. N494, 302 T83, A496e, B521p
    joined = re.sub(r"^(?:\d{3}\s+)?[A-Za-z]?\d{1,4}(?:\.\d+)?[a-z]?\s+", "", joined)
    joined = re.sub(r"^\d{2}-\d{5}\s+", "", joined)

    # 2. Match Title / Responsibility . -- Edition . -- City : Publisher , Year
    m_body = re.search(
        r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ\d][^/:]+?)\s*(?:\[recurso[^\]]*\])?\s*(?::\s*([^/]+?)\s*)?/\s*(.+?)\.\s*(?:–|-{1,2}|\.)?\s*(?:(\d+ª?\s*\.?\s*ed(?:ição|\.)?)\s*(?:–|-{1,2}|\.)\s*)?(?:(?:Dados eletr[ôo]nicos|recurso digital)\s*\.?\s*(?:–|-{1,2}|\.)\s*)?([A-Za-zÁÉÍÓÚÂÊÔÃÕÇ\s,-]+?)\s*:\s*([A-Za-zÁÉÍÓÚÂÊÔÃÕÇ\s,-]+?),?\s*(\d{4})",
        joined,
        re.I,
    )
    if not m_body:
        return None

    raw_title = m_body.group(1).strip()
    raw_title = re.sub(r"^[A-Za-z]?\d{1,4}(?:\.\d+)?[a-z]?\s+", "", raw_title)
    raw_title = re.sub(r"\[recurso[^\]]*\]", "", raw_title, flags=re.I).strip()
    raw_title = sanitize_title_candidate(raw_title)
    if is_invalid_or_disclaimer_title(raw_title):
        raw_title = None
    sub_title = sanitize_title_candidate(m_body.group(2).strip()) if m_body.group(2) else None
    resp = m_body.group(3).strip()
    ed = m_body.group(4).strip() if m_body.group(4) else None
    city = m_body.group(5).strip()
    pub = m_body.group(6).strip()
    year = int(m_body.group(7))

    authors_list = []
    resp_clean = re.sub(r"^(?:organizad(?:or|ores|ora|oras)|edit(?:or|ores|ora|oras)|coord(?:enador|enadores)?|autores?)[:,\s]+", "", resp, flags=re.I).strip()
    first_resp = resp_clean.split(";")[0].strip()
    has_et_al = "[et al" in first_resp.lower() or "... [et al" in first_resp.lower() or "..." in first_resp
    first_resp_clean = re.sub(r"\s*\.{2,}\s*\[et al\.?\]|\s*\[et al\.?\]|\.\.\..*$", "", first_resp, flags=re.I).strip()
    resp_authors = [
        strip_author_prefixes(a.strip())
        for a in first_resp_clean.split(",")
        if len(a.strip()) > 3
    ]

    if auth_name:
        if len(resp_authors) > 1:
            authors_list = resp_authors
        else:
            if "," in auth_name:
                sp = auth_name.split(",", 1)
                authors_list = [f"{sp[1].strip()} {sp[0].strip()}"]
            else:
                authors_list = [auth_name]
    elif resp_authors:
        authors_list = resp_authors
        if has_et_al:
            authors_list.append("et al.")

    # Extract ISBN if on page
    isbn_val = None
    m_isbn = re.search(r"ISBN(?:-1[03])?[:\s]+([0-9Xx -]{10,17})", page_text, re.I)
    if m_isbn:
        raw_isbn = re.sub(r"[^0-9X]", "", m_isbn.group(1))
        if len(raw_isbn) in (10, 13) and is_valid_isbn(raw_isbn, strict=False):
            isbn_val = raw_isbn

    # Extract subject area from first catalog entry e.g. '1. Neuropsicologia.' or '1. Psicologia clínica.'
    area = None
    m_subject = re.search(r"\b1\.\s+([A-Za-zÁÉÍÓÚÂÊÔÃÕÇ\s-]+?)(?:\.|\s+2\.|\s+I\.)", page_text)
    if m_subject:
        cand_subj = m_subject.group(1).strip()
        cand_subj_lower = unicodedata.normalize("NFKD", cand_subj).encode("ascii", "ignore").decode().lower()
        invalid_areas = {
            "titulo", "autor", "brasil", "recurso", "serie", "coautor", "orientador",
            "orientadora", "universidade", "faculdade", "instituto", "edicao", "volume",
            "cdd", "cdu", "ilustrado", "bibliografia", "isbn", "issn", "doi",
        }
        if len(cand_subj) >= 3 and cand_subj_lower not in invalid_areas and not any(cand_subj_lower.startswith(w) for w in ["titulo", "autor"]):
            area = cand_subj.title()

    return {
        "title": raw_title,
        "subtitle": sub_title,
        "authors": authors_list,
        "edition": ed,
        "city": city,
        "publisher": pub,
        "year": year,
        "isbn": isbn_val,
        "area": area,
    }


def parse_academic_thesis(text: str) -> Optional[Dict[str, Any]]:
    """Parse academic monograph (tese/dissertação) statement."""
    m = re.search(
        r"(?:Tese|Disserta[çc][ãa]o)\s+apresentada\s+ao\s+Programa\s+de\s+P[oó]s-?\s*Gradua[çc][ãa]o.*?como\s+requisito\s+para\s+(?:a\s+)?obten[çc][ãa]o\s+do\s+t[ií]tulo\s+de\s+(Doutor[a-z]?|Mestre)",
        text,
        re.I | re.DOTALL,
    )
    if not m:
        return None

    res: Dict[str, Any] = {
        "degree": m.group(1),
        "institution": None,
        "year": None,
        "city": None,
    }

    # Extract institution
    m_inst = re.search(
        r"(PUC-?CAMPINAS|Universidade Federal de [^\n\r,]+|Universidade Estadual de [^\n\r,]+|USP|UNICAMP|UNESP|UFSCAR)",
        text,
        re.I,
    )
    if m_inst:
        res["institution"] = re.sub(r"\s+", " ", m_inst.group(1)).strip()

    # Extract year
    m_year = re.search(r"\b(20[0-2]\d)\b", text)
    if m_year:
        res["year"] = int(m_year.group(1))

    return res


def parse_thesis_resumo_citation(doc: pymupdf.Document) -> Optional[Dict[str, Any]]:
    """Parse formal thesis reference from the Resumo page according to ABNT NBR 14724."""
    for page_idx in range(min(15, len(doc))):
        text = doc[page_idx].get_text("text") or ""
        if "resumo" not in text.lower() and "abstract" not in text.lower():
            continue
        m = re.search(
            r"(?:Resumo|Abstract)\s*\n+([A-ZÁÉÍÓÚÂÊÔÃÕÇ-]+,\s+[A-Za-zÁÉÍÓÚÂÊÔÃÕÇ\s\.]+?)\.\s*[“\"]?([^”\"\n\r:]+?)[”\"]?(?::\s*([^–\.\n\r]+?))?\.\s*(\d{4})\s*[\.\-–]\s*(?:.*?\b)?(Disserta[çc][ãa]o|Tese)",
            text,
            re.I,
        )
        if m:
            author_raw = m.group(1).strip()
            title = m.group(2).strip()
            subtitle = m.group(3).strip() if m.group(3) else None
            year = int(m.group(4))
            doc_type = m.group(5).capitalize()

            if "," in author_raw:
                sp = author_raw.split(",", 1)
                author = f"{sp[1].strip()} {sp[0].strip().title()}"
            else:
                author = author_raw.title()

            inst = None
            m_inst = re.search(
                r"(Pontif[ií]cia Universidade Cat[oó]lica de [^\n\r,]+|Universidade Federal de [^\n\r,]+|Universidade Estadual de [^\n\r,]+|PUC-?CAMPINAS|UFSCAR|USP|UNICAMP|UNESP)",
                text,
                re.I,
            )
            if m_inst:
                inst = re.sub(r"\s+", " ", m_inst.group(1)).strip()

            city = None
            m_city = re.search(r",\s*([A-Za-zÁÉÍÓÚÂÊÔÃÕÇ\s]+),\s*\d{4}\.", text)
            if m_city:
                city = m_city.group(1).strip()

            return {
                "author": author,
                "title": title,
                "subtitle": subtitle,
                "year": year,
                "institution": inst,
                "city": city,
                "degree": doc_type,
            }
    return None


def parse_thesis_folha_de_rosto(doc: pymupdf.Document) -> Optional[Dict[str, Any]]:
    """Extrai autor e título da folha de rosto de tese/dissertação acadêmica."""
    for page_idx in range(min(6, len(doc))):
        text = doc[page_idx].get_text("text") or ""
        m_apres = re.search(
            r"(?:Tese|Disserta[çc][ãa]o)\s+(?:apresentada|defendida)\s+(?:ao|à|como)",
            text,
            re.I,
        )
        if not m_apres:
            continue
        lines_before = [l.strip() for l in text[:m_apres.start()].splitlines() if l.strip()]
        meaningful = [
            l for l in lines_before
            if not re.search(r"\b(?:universidade|faculdade|instituto|programa|p[oó]s-?gradua|departamento|campus|centro|puc|usp|unicamp|unesp)\b", l, re.I)
            and len(l) >= 3
        ]
        if len(meaningful) >= 2:
            cand_auth = meaningful[0]
            cand_title = " ".join(meaningful[1:])
            if len(cand_auth.split()) in (2, 3, 4) and not re.search(r"\b(?:doutor|mestre|orientador|curso)\b", cand_auth, re.I):
                clean_title = sanitize_title_candidate(cand_title)
                if clean_title and not is_invalid_or_disclaimer_title(clean_title):
                    return {
                        "author": strip_author_prefixes(cand_auth.title()),
                        "title": clean_title,
                    }
    return None


def extract_candidate_metadata(
    sample_text: str,
    pdf_path: Optional[str] = None,
    total_pages: int = 0,
) -> ExtractedCandidates:
    """Extract preliminary metadata candidates from native text, page-by-page CIP, and filename."""
    candidates = ExtractedCandidates(sample_text=sample_text[:4000])

    # 1. Document structural parsing (CIP card and Thesis Resumo) and front matter - HIGHEST PRIORITY
    head_text = ""
    thesis_resumo_found = False
    pdf_title_cand: Optional[str] = None
    pdf_author_cand: Optional[str] = None

    if pdf_path and os.path.exists(pdf_path):
        try:
            with pymupdf.open(pdf_path) as doc:
                total_p = len(doc)
                pdf_meta = doc.metadata or {}
                raw_prop_title = pdf_meta.get("title")
                if raw_prop_title:
                    clean_prop_t = sanitize_title_candidate(str(raw_prop_title).strip())
                    if clean_prop_t and not is_invalid_or_disclaimer_title(clean_prop_t):
                        if not re.search(r"\b(?:microsoft\s+word|scanner|print|scan|powerpoint|untitled|adobe|latex|acrobat|documento)\b", clean_prop_t, re.I):
                            pdf_title_cand = clean_prop_t

                raw_prop_author = pdf_meta.get("author")
                if raw_prop_author:
                    clean_prop_a = strip_author_prefixes(str(raw_prop_author).strip())
                    if len(clean_prop_a) >= 3 and not re.search(r"\b(?:microsoft|scanner|print|scan|user|admin|usuario|desconhecido)\b", clean_prop_a, re.I):
                        pdf_author_cand = clean_prop_a

                # 1a. Page-by-page CIP extraction
                for page_idx in range(min(6, total_p)):
                    p_text = doc[page_idx].get_text("text") or ""
                    cip_meta = parse_page_cip(p_text)
                    if cip_meta:
                        if cip_meta.get("title") and len(cip_meta["title"]) > 3:
                            candidates.raw_title = cip_meta["title"]
                            candidates.title_source = "ficha_catalografica"
                        if cip_meta.get("subtitle"):
                            candidates.raw_subtitle = cip_meta["subtitle"]
                        if cip_meta.get("authors"):
                            candidates.raw_authors = cip_meta["authors"]
                            candidates.author_source = "ficha_catalografica"
                        if cip_meta.get("publisher"):
                            candidates.raw_publisher = cip_meta["publisher"]
                        if cip_meta.get("edition"):
                            candidates.raw_edition = cip_meta["edition"]
                        if cip_meta.get("year"):
                            candidates.raw_year = cip_meta["year"]
                        if cip_meta.get("city"):
                            candidates.raw_city = cip_meta["city"]
                        if cip_meta.get("isbn"):
                            candidates.isbn = cip_meta["isbn"]
                            candidates.isbn_source = "ficha_catalografica"
                        if cip_meta.get("area"):
                            candidates.raw_area = cip_meta["area"]
                        break

                # 1b. Thesis Resumo citation parsing
                if not candidates.raw_title or not candidates.raw_authors:
                    thesis_resumo = parse_thesis_resumo_citation(doc)
                    if thesis_resumo:
                        thesis_resumo_found = True
                        if not candidates.raw_title and thesis_resumo.get("title"):
                            candidates.raw_title = thesis_resumo["title"]
                            candidates.title_source = "resumo_academico"
                        if not candidates.raw_subtitle and thesis_resumo.get("subtitle"):
                            candidates.raw_subtitle = thesis_resumo.get("subtitle")
                        if not candidates.raw_authors and thesis_resumo.get("author"):
                            candidates.raw_authors = [thesis_resumo["author"]]
                            candidates.author_source = "resumo_academico"
                        if not candidates.raw_year and thesis_resumo.get("year"):
                            candidates.raw_year = thesis_resumo["year"]
                        if thesis_resumo.get("institution") and not candidates.raw_publisher:
                            candidates.raw_publisher = thesis_resumo["institution"]
                        if thesis_resumo.get("city") and not candidates.raw_city:
                            candidates.raw_city = thesis_resumo["city"]

                # 1c. Thesis Folha de Rosto fallback parsing
                if not candidates.raw_title or not candidates.raw_authors:
                    thesis_rosto = parse_thesis_folha_de_rosto(doc)
                    if thesis_rosto:
                        if not candidates.raw_title and thesis_rosto.get("title"):
                            candidates.raw_title = thesis_rosto["title"]
                            candidates.title_source = "folha_de_rosto"
                        if not candidates.raw_authors and thesis_rosto.get("author"):
                            candidates.raw_authors = [thesis_rosto["author"]]
                            candidates.author_source = "folha_de_rosto"

                # 2. Extract DOI and ISSN strictly from front pages (pages 1 to 5)
                head_text = "\n".join(doc[i].get_text("text") or "" for i in range(min(5, total_p)))

                # 4. If no ISBN in front matter, check colophon / back pages
                if not candidates.isbn:
                    check_pages = list(range(max(0, total_p - 30), total_p))
                    for p_num in check_pages:
                        p_txt = doc[p_num].get_text("text") or ""
                        p_low = p_txt.lower()
                        if any(ad in p_low for ad in [
                            "compre agora e leia", "outras obras", "leia também", "do mesmo autor",
                            "compre agora", "compre já", "comprar livro", "conheça também"
                        ]):
                            continue
                        m_isbn = re.search(r"\bISBN(?:-1[03])?[:\s]+([0-9Xx -]{10,17})\b", p_txt, re.I)
                        if m_isbn:
                            raw_val = m_isbn.group(1)
                            cleaned_digits = re.sub(r"[^0-9X]", "", raw_val)
                            if len(cleaned_digits) in (10, 13) and is_valid_isbn(cleaned_digits, strict=False):
                                candidates.isbn = cleaned_digits
                                candidates.isbn_source = "colofao_ou_contracapa"
                                break
        except Exception as e:
            logger.debug("Page structural parsing exception: %s", e)
    else:
        # Fallback to sample_text when no pdf_path provided
        cip_meta = parse_page_cip(sample_text[:10000])
        if cip_meta:
            if cip_meta.get("title") and len(cip_meta["title"]) > 3:
                candidates.raw_title = cip_meta["title"]
                candidates.title_source = "ficha_catalografica"
            if cip_meta.get("subtitle"):
                candidates.raw_subtitle = cip_meta["subtitle"]
            if cip_meta.get("authors"):
                candidates.raw_authors = cip_meta["authors"]
                candidates.author_source = "ficha_catalografica"
            if cip_meta.get("publisher"):
                candidates.raw_publisher = cip_meta["publisher"]
            if cip_meta.get("edition"):
                candidates.raw_edition = cip_meta["edition"]
            if cip_meta.get("year"):
                candidates.raw_year = cip_meta["year"]
            if cip_meta.get("city"):
                candidates.raw_city = cip_meta["city"]
            if cip_meta.get("isbn"):
                candidates.isbn = cip_meta["isbn"]
                candidates.isbn_source = "ficha_catalografica"
            if cip_meta.get("area"):
                candidates.raw_area = cip_meta["area"]

        ref_cut = re.split(r"\n\s*(?:refer[êe]ncias|references|bibliografia)\b", sample_text, flags=re.I)
        head_text = ref_cut[0] if ref_cut else sample_text

    # Garante que head_text corte referências bibliográficas para não herdar DOIs de obras citadas
    ref_split = re.split(r"\n\s*(?:refer[êe]ncias|references|bibliografia|obras\s+citadas)\b", head_text, flags=re.I)
    clean_head_text = ref_split[0] if ref_split else head_text

    # Identificação contextual de tese/dissertação para evitar associação indevida de DOIs de terceiros
    thesis_in_progress = bool(parse_academic_thesis(sample_text) or thesis_resumo_found)

    doi_candidates = []
    for m in DOI_REGEX.finditer(clean_head_text):
        raw_doi_match = m.group(0).rstrip(".;,")
        norm_d = normalizar_doi(raw_doi_match)
        if not norm_d:
            continue
        start_pos = max(0, m.start() - 150)
        end_pos = min(len(clean_head_text), m.end() + 150)
        context = clean_head_text[start_pos:end_pos].lower()
        is_citation_context = any(
            re.search(pat, context) for pat in [
                r"\b(?:in:|em:|apud|citado\s+por|citado\s+em|p\.\s*\d+[-–]\d+|pp\.\s*\d+|v\.\s*\d+,\s*n\.\s*\d+)\b",
                r"\b(?:recuperado\s+de|dispon[íi]vel\s+em|acesso\s+em)\b",
                r"\bet\s+al\.\b",
            ]
        )
        if is_citation_context:
            logger.debug("Descartando DOI citado em referência ou citação textual: %s", norm_d)
            continue
        if thesis_in_progress and not any(lbl in context for lbl in ["doi da tese", "doi da disserta", "doi do trabalho", "handle", "reposit"]):
            logger.debug("Descartando DOI em monografia/tese (provável artigo citado): %s", norm_d)
            continue
        doi_candidates.append(norm_d)

    if doi_candidates:
        candidates.doi = doi_candidates[0]
        candidates.doi_source = "cabecalho"

    # Extract strict ISSN from front matter
    issn_match = ISSN_REGEX.search(clean_head_text)
    if issn_match:
        issn_cand = issn_match.group(1).strip()
        if is_valid_issn(issn_cand, strict=False):
            candidates.issn = issn_cand
            candidates.issn_source = "cabecalho"

    # Extract ISBN from front matter if not already found in CIP
    if not candidates.isbn:
        isbn_match = re.search(r"\bISBN(?:-1[03])?[:\s]+([0-9Xx -]{10,17})\b", clean_head_text, re.I)
        if isbn_match:
            raw_val = isbn_match.group(1)
            cleaned_digits = re.sub(r"[^0-9X]", "", raw_val)
            if len(cleaned_digits) in (10, 13) and is_valid_isbn(cleaned_digits, strict=False):
                candidates.isbn = cleaned_digits
                candidates.isbn_source = "cabecalho"

    # 5. Filename metadata fallback
    fn_meta = parse_filename_extended(pdf_path) if pdf_path else {}
    fn_title = fn_meta.get("title")
    fn_author = fn_meta.get("author")
    fn_year = fn_meta.get("year")
    fn_publisher = fn_meta.get("publisher")

    # 6. Academic thesis detection
    thesis_meta = parse_academic_thesis(sample_text)
    if thesis_meta:
        if thesis_meta.get("institution") and not candidates.raw_publisher:
            candidates.raw_publisher = thesis_meta["institution"]
        if thesis_meta.get("year") and not candidates.raw_year:
            candidates.raw_year = thesis_meta["year"]

    # 7. Check for publisher via regex
    if not candidates.raw_publisher:
        publisher_match = re.search(
            r"(?:editora|publisher|editorial|published by|edusc|artmed|novatec|companhia das letras)[:\s]+([^\n\r,]+)",
            sample_text,
            re.IGNORECASE,
        )
        if publisher_match:
            candidates.raw_publisher = publisher_match.group(1).strip()

    # 8. Fallback to clean lines from native text if title or author is still missing
    raw_lines = [l.strip() for l in sample_text.splitlines() if l.strip()]
    meaningful_lines = []
    for line in raw_lines:
        lower_line = line.lower()
        if any(re.search(pat, lower_line) for pat in IGNORED_PATTERNS):
            continue
        if len(line) < 3:
            continue
        meaningful_lines.append(line)

    if not candidates.raw_title and meaningful_lines:
        for cand_l in meaningful_lines[:15]:
            clean_l = sanitize_title_candidate(cand_l)
            if clean_l and not is_invalid_or_disclaimer_title(clean_l):
                candidates.raw_title = clean_l
                candidates.title_source = "texto_nativo"
                break

    # Limpeza e sanitização de marcadores residuais de página e cabeçalhos no título
    if candidates.raw_title:
        candidates.raw_title = sanitize_title_candidate(candidates.raw_title)

    if not candidates.raw_authors and len(meaningful_lines) > 1:
        for l in meaningful_lines[1:5]:
            if any(kw in l.lower() for kw in ["por:", "autor:", "autores:", "authors:"]):
                clean_a = re.sub(r"^(?:por|autores?|authors?)[:\s]+", "", l, flags=re.I).strip()
                parsed_a = [
                    strip_author_prefixes(a.strip())
                    for a in re.split(r",|;|\se\s", clean_a)
                    if len(a.strip()) > 2
                ]
                if parsed_a:
                    candidates.raw_authors = parsed_a
                    candidates.author_source = "texto_nativo"
                break

    # 9. Disambiguate Title vs Author:
    cand_title_str = (candidates.raw_title or "").strip()
    words = cand_title_str.split()
    looks_like_person_name = (
        len(words) in (2, 3, 4)
        and cand_title_str.isupper()
        and not any(w.lower() in ("psicologia", "historia", "manual", "curso", "introducao", "direito", "educacao", "politica", "clinica", "ensaio", "teoria", "analise", "estudo", "revistas", "revista", "artigo", "livro", "tese", "relatorio", "social") for w in words)
        and all(len(w) >= 2 for w in words)
    )
    if looks_like_person_name:
        if not candidates.raw_authors:
            candidates.raw_authors = [cand_title_str.title()]
            candidates.author_source = "texto_nativo"
        candidates.raw_title = None
        candidates.title_source = None

    if candidates.raw_title and fn_author:
        fn_auth_clean = re.sub(r"[^\w]", "", fn_author.lower())
        title_clean = re.sub(r"[^\w]", "", candidates.raw_title.lower())
        fn_tokens = {w for w in re.split(r"\W+", fn_author.lower()) if len(w) >= 4}
        title_tokens = {w for w in re.split(r"\W+", candidates.raw_title.lower()) if len(w) >= 4}
        if fn_auth_clean in title_clean or title_clean in fn_auth_clean or (fn_tokens and title_tokens and fn_tokens.intersection(title_tokens)):
            if not candidates.raw_authors:
                candidates.raw_authors = [candidates.raw_title.title()]
                candidates.author_source = "texto_nativo"
            if fn_title:
                candidates.raw_title = fn_title
                candidates.title_source = "nome_arquivo"
            elif len(meaningful_lines) > 1 and meaningful_lines[0] == candidates.raw_title:
                candidates.raw_title = meaningful_lines[1]
                candidates.title_source = "texto_nativo"
            else:
                candidates.raw_title = None
                candidates.title_source = None

    # 10. Prioridade para propriedades internas do PDF (doc.metadata) quando o texto nativo for disclaimer ou ausente
    if not candidates.raw_title or is_invalid_or_disclaimer_title(candidates.raw_title):
        if pdf_title_cand and not is_invalid_or_disclaimer_title(pdf_title_cand):
            candidates.raw_title = pdf_title_cand
            candidates.title_source = "propriedades_pdf"

    if not candidates.raw_authors and pdf_author_cand:
        candidates.raw_authors = [pdf_author_cand]
        candidates.author_source = "propriedades_pdf"

    # 11. Multi-word filename title priority over single-word, boilerplate or identifier extracted line
    if fn_title:
        fn_title_clean = sanitize_title_candidate(fn_title) or fn_title
        fn_words = fn_title_clean.split()
        cand_words = (candidates.raw_title or "").split()
        cand_lower = (candidates.raw_title or "").lower()

        deve_usar_fn_title = (
            not candidates.raw_title
            or is_invalid_or_disclaimer_title(candidates.raw_title)
            or candidates.raw_title.startswith("---")
            or "página" in cand_lower
            or len(candidates.raw_title) < 4
            or cand_lower in GENERIC_SINGLE_WORD_TITLES
            or (len(cand_words) <= 1 and len(fn_words) >= 2)
            or (len(cand_words) == 1 and cand_lower in ("criar", "artigo", "livro", "capítulo", "texto"))
            or (bool(fn_author) and len(fn_words) >= 2 and (candidates.raw_title.endswith(".") or "sobre" in cand_lower or "texto" in cand_lower))
        )
        if deve_usar_fn_title and not is_invalid_or_disclaimer_title(fn_title_clean):
            candidates.raw_title = fn_title_clean
            candidates.title_source = "nome_arquivo"

    # Verificação de segurança final contra títulos formados por identificadores ou disclaimers
    if candidates.raw_title and is_invalid_or_disclaimer_title(candidates.raw_title):
        if fn_title and not is_invalid_or_disclaimer_title(fn_title):
            candidates.raw_title = fn_title
            candidates.title_source = "nome_arquivo"
        else:
            candidates.raw_title = None
            candidates.title_source = None

    if not candidates.raw_authors and fn_author:
        candidates.raw_authors = [fn_author]
        candidates.author_source = "nome_arquivo"

    if not candidates.raw_publisher and fn_publisher:
        candidates.raw_publisher = fn_publisher

    if not candidates.raw_year and fn_year:
        candidates.raw_year = fn_year

    # 11. Clean Title / Subtitle separation
    if candidates.raw_title and ":" in candidates.raw_title and not candidates.raw_subtitle:
        parts = candidates.raw_title.split(":", 1)
        candidates.raw_title = parts[0].strip()
        candidates.raw_subtitle = parts[1].strip()

    # Strip author prefixes from all authors
    if candidates.raw_authors:
        candidates.raw_authors = [strip_author_prefixes(a) for a in candidates.raw_authors if a.strip()]

    return candidates


class JevClassifier:
    """Classifier and validator leveraging TypeSafe AI's Jev model with calibrated fallback."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        modo: str = "auto",
        cache_path: Optional[Path] = None,
        cache_file: Optional[Path] = None,
        **kwargs,
    ):
        if kwargs:
            import warnings
            for arg in kwargs:
                warnings.warn(
                    f"O parâmetro '{arg}' em JevClassifier foi descontinuado e não tem mais efeito.",
                    DeprecationWarning,
                    stacklevel=2,
                )
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY")
        self.modo = (modo or "auto").strip().lower()
        if self.modo in ("remoto", "typesafe", "remote", "jev_remoto"):
            self.modo = "jev_remoto"
        elif self.modo in ("local", "deterministico", "heuristico"):
            self.modo = "local"
        else:
            self.modo = "auto"

        if self.modo == "jev_remoto" and not self.api_key:
            from .config import ErroDeConfiguracao
            raise ErroDeConfiguracao(
                "Modo classificador remoto exclusivo ('jev_remoto') selecionado, mas TYPESAFE_API_KEY não foi configurada."
            )

        self._cache_path = cache_file or cache_path or Path.home() / ".cache" / "organizador_pdf" / "jev_cache.json"
        self._cache: Dict[str, Any] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            if self._cache_path.exists():
                import json
                self._cache = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("Não foi possível carregar cache do Jev: %s", e)
            self._cache = {}

    def _save_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            import json
            self._cache_path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug("Não foi possível persistir cache do Jev: %s", e)

    @staticmethod
    def _compute_fingerprint(text: str, total_pages: int) -> str:
        import hashlib
        data = f"{text[:3000]}|{total_pages}".encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def classify_and_validate(
        self,
        pdf_path: str,
        max_paginas: int = 10,
        max_caracteres: int = 30000,
        texto_pre_extraido: Optional[str] = None,
        total_paginas: Optional[int] = None,
    ) -> JevValidationResult:
        """Analyze PDF structure, sample text, and candidate metadata."""
        if texto_pre_extraido is not None:
            combined_text = texto_pre_extraido[:max_caracteres] if max_caracteres else texto_pre_extraido
            total_pages = total_paginas or 0
            candidates = extract_candidate_metadata(combined_text, pdf_path=None, total_pages=total_pages)
        else:
            combined_text, pages_text = extract_native_sample_text(
                pdf_path, head_pages=max_paginas, tail_pages=max_paginas
            )
            if max_caracteres and len(combined_text) > max_caracteres:
                combined_text = combined_text[:max_caracteres]
            total_pages = total_paginas or 0
            candidates = extract_candidate_metadata(combined_text, pdf_path=pdf_path, total_pages=total_pages)

        if not combined_text.strip():
            from .converter import ErroPdfEscaneado
            raise ErroPdfEscaneado(
                "Nenhum texto nativo extraível — o PDF provavelmente é digitalizado/escaneado. "
                "Dica: use uma ferramenta de OCR (ex.: ocrmypdf) para adicionar camada de texto antes de catalogar."
            )

        # 1. Modo remoto exclusivo ('jev_remoto')
        if self.modo == "jev_remoto":
            if not self.api_key:
                from .config import ErroDeConfiguracao
                raise ErroDeConfiguracao(
                    "Modo remoto exclusivo ('jev_remoto') selecionado, mas TYPESAFE_API_KEY não foi configurada."
                )
            try:
                import typesafe_sdk  # noqa: F401
            except ModuleNotFoundError:
                raise ErroDeClassificacaoRemota(
                    "Modo remoto exclusivo ('jev_remoto') selecionado, mas o pacote 'typesafe-sdk' "
                    "não está instalado. Instale com: pip install '.[typesafe]'"
                )

            # Idempotência: verifica cache local para evitar reenvio cobrado após erro de gravação
            fingerprint = self._compute_fingerprint(combined_text, total_pages)
            if fingerprint in self._cache:
                logger.info("Reutilizando classificação Jev em cache para %s.", Path(pdf_path).name)
                cached = self._cache[fingerprint]
                raw_data = dict(cached.get("raw_jev_data", {}))
                raw_data["cached"] = True
                return JevValidationResult(
                    classification=cached["classification"],
                    classification_confidence=cached["confidence"],
                    probabilities=cached["probabilities"],
                    candidates=candidates,
                    provider="typesafe",
                    raw_jev_data=raw_data,
                )

            try:
                res = self._run_jev_sdk(combined_text, candidates, total_pages)
                self._cache[fingerprint] = {
                    "classification": res.classification,
                    "confidence": res.classification_confidence,
                    "probabilities": res.probabilities,
                    "raw_jev_data": res.raw_jev_data,
                }
                self._save_cache()
                return res
            except Exception as e:
                logger.error("Falha irrecuperável na chamada remota TypeSafe AI em modo exclusivo: %s", e)
                raise ErroDeClassificacaoRemota(f"Falha na chamada ao serviço remoto TypeSafe AI: {e}") from e

        # 2. Modo estritamente local
        elif self.modo == "local":
            logger.info("Executando classificador local heurístico para %s (%d páginas).", Path(pdf_path).name, total_pages)
            res = self._run_calibrated_fallback(combined_text, candidates, total_pages)
            res.provider = "deterministico_local"
            return res

        # 3. Modo automático ('auto')
        else:
            if self.api_key:
                try:
                    import typesafe_sdk  # noqa: F401
                    sdk_ok = True
                except ModuleNotFoundError:
                    sdk_ok = False
                    logger.warning(
                        "TYPESAFE_API_KEY configurada, mas 'typesafe-sdk' não está instalado. "
                        "Utilizando motor heurístico local."
                    )

                if sdk_ok:
                    fingerprint = self._compute_fingerprint(combined_text, total_pages)
                    if fingerprint in self._cache:
                        cached = self._cache[fingerprint]
                        raw_data = dict(cached.get("raw_jev_data", {}))
                        raw_data["cached"] = True
                        return JevValidationResult(
                            classification=cached["classification"],
                            classification_confidence=cached["confidence"],
                            probabilities=cached["probabilities"],
                            candidates=candidates,
                            provider="typesafe",
                            raw_jev_data=raw_data,
                        )
                    try:
                        res = self._run_jev_sdk(combined_text, candidates, total_pages)
                        self._cache[fingerprint] = {
                            "classification": res.classification,
                            "confidence": res.classification_confidence,
                            "probabilities": res.probabilities,
                            "raw_jev_data": res.raw_jev_data,
                        }
                        self._save_cache()
                        return res
                    except Exception as e:
                        logger.warning("Falha na chamada TypeSafe SDK em modo auto (%s). Utilizando motor heurístico local.", e)
                        res = self._run_calibrated_fallback(combined_text, candidates, total_pages)
                        res.provider = "deterministico_local"
                        return res

            res = self._run_calibrated_fallback(combined_text, candidates, total_pages)
            res.provider = "deterministico_local"
            return res

    def _run_jev_sdk(self, text: str, candidates: ExtractedCandidates, total_pages: int = 0) -> JevValidationResult:
        """Call TypeSafe AI Jev System One model using Choice and Noul primitives."""
        from typesafe_sdk import Choice, Noul, TypeSafeClient

        state = {
            "document_sample": text[:3000],
            "total_pages": total_pages,
            "candidate_metadata": {
                "title": candidates.raw_title,
                "authors": candidates.raw_authors,
                "publisher": candidates.raw_publisher,
                "doi": candidates.doi,
                "isbn": candidates.isbn,
                "issn": candidates.issn,
            },
        }

        questions = {
            "classification": Choice(
                instructions=(
                    "Classifique esta publicação em exatamente uma das categorias: "
                    "artigo, livro, tese, revista, apostila, capitulo_livro, relatorio, trabalho_evento, outros."
                ),
                criteria={
                    "artigo": "Artigo científico ou acadêmico com resumo/abstract, referências bibliográficas, DOI ou filiação institucional",
                    "livro": "Livro comercial completo publicado com ISBN, editora comercial ou catálogo editorial",
                    "tese": "Tese de doutorado, dissertação de mestrado ou trabalho de conclusão de curso acadêmico",
                    "revista": "Revista periódica, magazine informativo, colunas de variedades ou publicação seriada",
                    "apostila": "Apostila didática, material didático de curso/EAD, notas de aula para estudantes",
                    "capitulo_livro": "Capítulo de livro, excerto, parte ou trecho de coletânea com obra organizadora ou paginação parcial",
                    "relatorio": "Relatório técnico, de pesquisa, governamental ou institucional",
                    "trabalho_evento": "Trabalho publicado em anais de evento científico, congresso, simpósio ou conferência",
                    "outros": "Documento não enquadrado nas categorias anteriores",
                },
            ),
            "has_valid_title": Noul(instructions="O título candidato representa com precisão o documento?"),
            "has_valid_authors": Noul(instructions="A autoria candidata reflete os autores reais do documento?"),
            "has_valid_publisher": Noul(instructions="A editora/instituição identificada é legítima?"),
            "has_valid_isbn": Noul(instructions="O ISBN indicado no documento é válido e pertence à publicação?"),
            "has_valid_doi": Noul(instructions="O DOI indicado no documento é válido e pertence à publicação?"),
            "has_valid_issn": Noul(instructions="O ISSN indicado no documento é válido e pertence à publicação?"),
        }

        with TypeSafeClient(api_key=self.api_key) as client:
            response = client.system_one(state=state, questions=questions)

        choice_obj = response.choices.get("classification")
        chosen_class: PublicationType = "outros"
        chosen_confidence = 0.0
        if choice_obj:
            val = str(choice_obj.choice).lower().strip()
            if val == "artigo_cientifico":
                val = "artigo"
            if val in ("artigo", "livro", "tese", "revista", "apostila", "capitulo_livro", "relatorio", "trabalho_evento", "outros"):
                chosen_class = val  # type: ignore
            chosen_confidence = float(getattr(choice_obj, "confidence", 0.95))

        probabilities = {
            "title": float(response.nouls["has_valid_title"].noul) if "has_valid_title" in response.nouls else 0.0,
            "authors": float(response.nouls["has_valid_authors"].noul) if "has_valid_authors" in response.nouls else 0.0,
            "publisher": float(response.nouls["has_valid_publisher"].noul) if "has_valid_publisher" in response.nouls else 0.0,
            "isbn": float(response.nouls["has_valid_isbn"].noul) if "has_valid_isbn" in response.nouls else 0.0,
            "doi": float(response.nouls["has_valid_doi"].noul) if "has_valid_doi" in response.nouls else 0.0,
            "issn": float(response.nouls["has_valid_issn"].noul) if "has_valid_issn" in response.nouls else 0.0,
        }

        return JevValidationResult(
            classification=chosen_class,
            classification_confidence=chosen_confidence,
            probabilities=probabilities,
            candidates=candidates,
            provider="typesafe",
            raw_jev_data={"choice": chosen_class, "confidence": chosen_confidence, "nouls": probabilities},
        )

    def _run_calibrated_fallback(
        self,
        text: str,
        candidates: ExtractedCandidates,
        total_pages: int = 0,
    ) -> JevValidationResult:
        """Calibrated decision logic replicating Jev System One scoring with domain heuristics."""
        lower_text = text.lower()

        scores: Dict[PublicationType, float] = {
            "artigo": 0.0,
            "livro": 0.0,
            "tese": 0.0,
            "revista": 0.0,
            "apostila": 0.0,
            "capitulo_livro": 0.0,
            "relatorio": 0.0,
            "trabalho_evento": 0.0,
            "outros": 0.1,
        }

        # 1. Courseware / Apostila Indicators (Highest specificity, using strict word boundaries)
        courseware_markers = [
            r"disciplina na modalidade a distância",
            r"modalidade a distância",
            r"unisulvirtual",
            r"\bead\b",
            r"livro didático",
            r"material didático",
            r"design instrucional",
            r"professoras conteudistas",
            r"professores conteudistas",
            r"\bapostila\b",
            r"notas de aula",
            r"exercícios propostos",
            r"plano de ensino",
        ]
        has_courseware = any(re.search(pat, lower_text) for pat in courseware_markers)
        if has_courseware:
            scores["apostila"] += 16.0
            scores["revista"] -= 10.0
            scores["tese"] -= 12.0
            scores["livro"] -= 6.0

        # 2. Academic Thesis / Dissertation (Teses, Dissertações -> 'tese')
        thesis_markers = [
            "tese apresentada",
            "dissertação apresentada",
            "requisito para a obtenção do título de doutor",
            "requisito para obtenção do título de doutor",
            "requisito para obtenção do título de mestre",
            "requisito para a obtenção do título de mestre",
            "requisito para obtenção do grau de doutor",
            "requisito para obtenção do grau de mestre",
            "programa de pós-graduação stricto sensu",
            "defesa de tese",
            "defesa de dissertação",
            "banca examinadora",
        ]
        if any(w in lower_text for w in thesis_markers) and not has_courseware:
            scores["tese"] += 14.0
            scores["livro"] -= 6.0
            scores["revista"] -= 10.0
            scores["artigo"] -= 8.0

        # 3. Book Indicators
        if candidates.isbn:
            scores["livro"] += 12.0
            scores["tese"] -= 12.0  # Livro com ISBN comercial não é tese acadêmica não publicada
        if any(w in lower_text for w in ["ficha catalográfica", "cip-brasil", "catalogação na publicação", "cdd", "cdu"]):
            scores["livro"] += 5.0
        pub_lower = (candidates.raw_publisher or "").lower()
        if any(cp in pub_lower for cp in ["artmed", "edusc", "companhia das letras", "zahar", "cortez", "vozes", "autêntica", "saraiva", "novatec", "elsevier", "atlas"]):
            scores["livro"] += 10.0
            scores["tese"] -= 14.0
        if any(w in lower_text for w in ["sumário", "capítulo 1", "capítulo i", "prefácio", "agradecimentos"]):
            scores["livro"] += 2.0
        if total_pages > 70 and not has_courseware:
            scores["livro"] += 3.5

        # 4. Scientific Article Indicators
        if candidates.doi:
            scores["artigo"] += 8.0
            scores["apostila"] -= 10.0
        if any(w in lower_text for w in ["abstract", "resumo", "introdução", "introduction", "keywords", "palavras-chave"]):
            if total_pages <= 50:
                scores["artigo"] += 3.0
        if any(w in lower_text for w in ["referências bibliográficas", "references", "metodologia"]):
            if total_pages <= 50:
                scores["artigo"] += 2.0
        if total_pages > 70:
            scores["artigo"] -= 6.0

        # 5. Magazine Indicators
        if candidates.issn and not any(w in lower_text for w in thesis_markers):
            scores["revista"] += 4.0
        if any(w in lower_text for w in ["revista", "magazine", "edição n", "ano i", "ano ii"]) and total_pages <= 60:
            scores["revista"] += 2.0

        # 6. Capítulo de Livro / Excerto de Obra Maior
        capitulo_markers = [
            r"\bcap[íi]tulo\s+\d+\b",
            r"\bin:\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ\s,-]+?\((?:org|coord|ed)\.?\)",
            r"\bcolet[âa]nea\b",
            r"\bexcerto\b",
        ]
        if any(re.search(pat, lower_text) for pat in capitulo_markers) and total_pages <= 50:
            scores["capitulo_livro"] += 12.0
            scores["livro"] -= 4.0

        # 7. Relatório Técnico / Institucional
        relatorio_markers = [
            r"relat[oó]rio\s+t[eé]cnico",
            r"relat[oó]rio\s+de\s+pesquisa",
            r"relat[oó]rio\s+final",
            r"relat[oó]rio\s+anual",
            r"technical\s+report",
            r"working\s+paper",
            r"documento\s+de\s+trabalho",
        ]
        if any(re.search(pat, lower_text) for pat in relatorio_markers) and total_pages <= 100:
            scores["relatorio"] += 11.0
            scores["artigo"] -= 5.0

        # 8. Trabalho em Evento / Anais
        evento_markers = [
            r"anais\s+do\s+",
            r"proceedings\s+of",
            r"congresso\s+nacional",
            r"simp[oó]sio\s+brasileiro",
            r"encontro\s+nacional",
            r"trabalho\s+apresentado\s+no",
        ]
        if any(re.search(pat, lower_text) for pat in evento_markers) and total_pages <= 30:
            scores["trabalho_evento"] += 10.0

        # 9. Scanned / Short / Image-only PDF Heuristics
        if len(text.strip()) < 300:
            if total_pages > 60:
                scores["livro"] += 5.0
            elif total_pages <= 30:
                scores["artigo"] += 3.5

        sorted_scores = sorted(scores.items(), key=lambda k: k[1], reverse=True)
        best_class, best_score = sorted_scores[0]
        second_class, second_score = sorted_scores[1] if len(sorted_scores) > 1 else ("outros", 0.0)
        margin = max(0.0, best_score - max(0.0, second_score))

        is_scanned_or_short = len(text.strip()) < 300
        has_identifier = bool(candidates.doi or candidates.isbn or candidates.issn)

        if candidates.doi and best_class == "artigo":
            confidence = 0.95
        elif candidates.isbn and best_class == "livro":
            confidence = 0.95
        elif candidates.issn and best_class == "revista":
            confidence = 0.95
        elif best_class in ("tese", "apostila") and best_score >= 12.0:
            confidence = 0.92
        elif best_class == "outros":
            confidence = 0.30 if is_scanned_or_short else 0.35
        elif is_scanned_or_short and not has_identifier and best_score < 10.0:
            confidence = 0.40
        elif margin < 1.0:
            confidence = round(min(0.48, max(0.35, 0.35 + 0.13 * margin)), 2)
        else:
            total_positive = sum(s for s in scores.values() if s > 0) or 1.0
            ratio = best_score / total_positive
            confidence = round(min(0.90, max(0.52, 0.45 + 0.45 * ratio)), 2)

        # Calibrated Noul probabilities
        doi_prob = 0.98 if candidates.doi and "/" in candidates.doi else 0.0
        isbn_prob = 0.98 if candidates.isbn and is_valid_isbn(candidates.isbn, strict=False) else 0.0
        issn_prob = 0.97 if candidates.issn and "-" in candidates.issn else 0.0
        title_prob = 0.96 if candidates.raw_title and len(candidates.raw_title) > 4 and not candidates.raw_title.startswith("---") else 0.20
        authors_prob = 0.95 if candidates.raw_authors else 0.20
        publisher_prob = 0.95 if candidates.raw_publisher else 0.20

        probabilities = {
            "title": title_prob,
            "authors": authors_prob,
            "publisher": publisher_prob,
            "isbn": isbn_prob,
            "doi": doi_prob,
            "issn": issn_prob,
        }

        return JevValidationResult(
            classification=best_class,
            classification_confidence=confidence,
            probabilities=probabilities,
            candidates=candidates,
            provider="deterministico_local",
            raw_jev_data={
                "calibrated_scores": scores,
                "best_class": best_class,
                "best_score": round(best_score, 2),
                "second_class": second_class,
                "second_score": round(second_score, 2),
                "margin": round(margin, 2),
            },
        )

    classify_and_extract = classify_and_validate

