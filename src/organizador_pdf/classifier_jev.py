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
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import pymupdf
from dotenv import load_dotenv

from .models import (
    PublicationType,
    ExtractedCandidates,
    JevValidationResult,
)

load_dotenv()

logger = logging.getLogger(__name__)

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
    r"^\[PAGE BREAK\]",
    r"^-{3,}\s*\[PAGE BREAK\]\s*-{3,}",
    r"^-{3,}$",
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
    r"^https?://",
    r"^doi:",
    r"^issn:",
    r"^isbn:",
]


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


def extract_native_sample_text(
    pdf_path: str,
    head_pages: int = 10,
    tail_pages: int = 10,
    max_pages: Optional[int] = None,
) -> Tuple[str, List[str]]:
    """Extract selectable native text from the first 10 and last 10 pages of a PDF.

    Strictly native extraction without OCR. Captures front matter (title, authors, CIP)
    and back matter (references, colophon, publication details).
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if max_pages is not None:
        head_pages = max_pages
        tail_pages = 0

    pages_text: List[str] = []
    with pymupdf.open(pdf_path) as doc:
        total = len(doc)
        if total <= (head_pages + tail_pages):
            page_indices = list(range(total))
        else:
            page_indices = list(range(head_pages)) + list(range(total - tail_pages, total))

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
    sub_title = m_body.group(2).strip() if m_body.group(2) else None
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
        if len(raw_isbn) in (10, 13):
            isbn_val = raw_isbn

    # Extract subject area from first catalog entry e.g. '1. Neuropsicologia.' or '1. Psicologia clínica.'
    area = None
    m_subject = re.search(r"\b1\.\s+([A-Za-zÁÉÍÓÚÂÊÔÃÕÇ\s-]+?)(?:\.|\s+2\.)", page_text)
    if m_subject:
        cand_subj = m_subject.group(1).strip()
        if len(cand_subj) >= 3 and not any(w in cand_subj.lower() for w in ["título", "autor", "brasil", "recurso"]):
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
    for page_idx in range(min(12, len(doc))):
        text = doc[page_idx].get_text("text") or ""
        if "resumo" not in text.lower():
            continue
        m = re.search(
            r"Resumo\s*\n+([A-ZÁÉÍÓÚÂÊÔÃÕÇ-]+,\s+[A-Za-zÁÉÍÓÚÂÊÔÃÕÇ\s\.]+?)\.\s*[“\"]?([^”\"\n\r:]+?)[”\"]?(?::\s*([^–\.\n\r]+?))?\.\s*(\d{4})\.\s*(Disserta[çc][ãa]o|Tese)",
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


def extract_candidate_metadata(
    sample_text: str,
    pdf_path: Optional[str] = None,
    total_pages: int = 0,
) -> ExtractedCandidates:
    """Extract preliminary metadata candidates from native text, page-by-page CIP, and filename."""
    candidates = ExtractedCandidates(sample_text=sample_text[:4000])

    # 1. Document structural parsing (CIP card and Thesis Resumo) - HIGHEST PRIORITY
    if pdf_path and os.path.exists(pdf_path):
        try:
            with pymupdf.open(pdf_path) as doc:
                # 1a. Page-by-page CIP extraction
                for page_idx in range(min(6, len(doc))):
                    p_text = doc[page_idx].get_text("text") or ""
                    cip_meta = parse_page_cip(p_text)
                    if cip_meta:
                        if cip_meta.get("title") and len(cip_meta["title"]) > 3:
                            candidates.raw_title = cip_meta["title"]
                        if cip_meta.get("subtitle"):
                            candidates.raw_subtitle = cip_meta["subtitle"]
                        if cip_meta.get("authors"):
                            candidates.raw_authors = cip_meta["authors"]
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
                        if cip_meta.get("area"):
                            candidates.raw_area = cip_meta["area"]
                        break

                # 1b. Thesis Resumo citation parsing
                if not candidates.raw_title or not candidates.raw_authors:
                    thesis_resumo = parse_thesis_resumo_citation(doc)
                    if thesis_resumo:
                        candidates.raw_title = thesis_resumo["title"]
                        candidates.raw_subtitle = thesis_resumo.get("subtitle")
                        if thesis_resumo.get("author"):
                            candidates.raw_authors = [thesis_resumo["author"]]
                        if thesis_resumo.get("year"):
                            candidates.raw_year = thesis_resumo["year"]
                        if thesis_resumo.get("institution") and not candidates.raw_publisher:
                            candidates.raw_publisher = thesis_resumo["institution"]
                        if thesis_resumo.get("city") and not candidates.raw_city:
                            candidates.raw_city = thesis_resumo["city"]
        except Exception as e:
            logger.debug("Page structural parsing exception: %s", e)

    # 2. Extract DOI and ISSN STRICTLY from front pages (pages 1 to 5)
    # Never extract DOIs/ISSNs from bibliography or reference lists at the end of books/theses!
    head_text = ""
    if pdf_path and os.path.exists(pdf_path):
        try:
            with pymupdf.open(pdf_path) as doc:
                head_text = "\n".join(doc[i].get_text("text") or "" for i in range(min(5, len(doc))))
        except Exception:
            head_text = sample_text
    else:
        # Cut sample_text before bibliography section
        ref_cut = re.split(r"\n\s*(?:refer[êe]ncias|references|bibliografia)\b", sample_text, flags=re.I)
        head_text = ref_cut[0] if ref_cut else sample_text

    # Extract DOI from front matter
    doi_match = DOI_REGEX.search(head_text)
    if doi_match:
        candidates.doi = doi_match.group(0).rstrip(".;,")

    # Extract strict ISSN from front matter
    issn_match = ISSN_REGEX.search(head_text)
    if issn_match:
        candidates.issn = issn_match.group(1).strip()

    # 3. Extract ISBN from front pages if not already in CIP
    if not candidates.isbn:
        isbn_match = re.search(r"\bISBN(?:-1[03])?[:\s]+([0-9Xx -]{10,17})\b", head_text, re.I)
        if isbn_match:
            raw_val = isbn_match.group(1)
            cleaned_digits = re.sub(r"[^0-9X]", "", raw_val)
            if len(cleaned_digits) in (10, 13):
                candidates.isbn = cleaned_digits

    # 4. If no ISBN in front matter, check colophon / back pages (skipping commercial catalog ads)
    if not candidates.isbn and pdf_path and os.path.exists(pdf_path):
        try:
            with pymupdf.open(pdf_path) as doc:
                total_p = len(doc)
                check_pages = list(range(max(0, total_p - 30), total_p))
                for p_num in check_pages:
                    p_txt = doc[p_num].get_text("text") or ""
                    p_low = p_txt.lower()
                    # Skip commercial advertisements for other titles
                    if any(ad in p_low for ad in [
                        "compre agora e leia", "outras obras", "leia também", "do mesmo autor",
                        "compre agora", "compre já", "comprar livro", "conheça também"
                    ]):
                        continue
                    # Prefer explicit ISBN with label on colophon/copyright pages
                    m_isbn = re.search(r"\bISBN(?:-1[03])?[:\s]+([0-9Xx -]{10,17})\b", p_txt, re.I)
                    if m_isbn:
                        raw_val = m_isbn.group(1)
                        cleaned_digits = re.sub(r"[^0-9X]", "", raw_val)
                        if len(cleaned_digits) in (10, 13):
                            candidates.isbn = cleaned_digits
                            break
        except Exception as e:
            logger.debug("Back-page ISBN search exception: %s", e)

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
        candidates.raw_title = meaningful_lines[0]

    if not candidates.raw_authors and len(meaningful_lines) > 1:
        for l in meaningful_lines[1:5]:
            if any(kw in l.lower() for kw in ["por:", "autor:", "autores:", "authors:"]):
                clean_a = re.sub(r"^(?:por|autores?|authors?)[:\s]+", "", l, flags=re.I).strip()
                candidates.raw_authors = [
                    strip_author_prefixes(a.strip())
                    for a in re.split(r",|;|\se\s", clean_a)
                    if len(a.strip()) > 2
                ]
                break

    # 9. Disambiguate Title vs Author:
    # If raw_title is identical or shares surname with candidate author's name, raw_title is the author!
    if candidates.raw_title and fn_author:
        fn_auth_clean = re.sub(r"[^\w]", "", fn_author.lower())
        title_clean = re.sub(r"[^\w]", "", candidates.raw_title.lower())
        fn_tokens = {w for w in re.split(r"\W+", fn_author.lower()) if len(w) >= 4}
        title_tokens = {w for w in re.split(r"\W+", candidates.raw_title.lower()) if len(w) >= 4}
        if fn_auth_clean in title_clean or title_clean in fn_auth_clean or (fn_tokens and title_tokens and fn_tokens.intersection(title_tokens)):
            # raw_title is actually the author!
            if not candidates.raw_authors:
                candidates.raw_authors = [candidates.raw_title.title()]
            if fn_title:
                candidates.raw_title = fn_title
            elif len(meaningful_lines) > 1 and meaningful_lines[0] == candidates.raw_title:
                candidates.raw_title = meaningful_lines[1]

    # 10. Multi-word filename title priority over single-word or boilerplate extracted line
    if fn_title:
        fn_words = fn_title.split()
        cand_words = (candidates.raw_title or "").split()
        if (
            not candidates.raw_title
            or candidates.raw_title.startswith("---")
            or len(candidates.raw_title) < 4
            or any(b in (candidates.raw_title or "").lower() for b in ["elivros", "odinright", "presente obra", "disponibilizada", "compra futura", "fim exclusivo"])
            or (len(cand_words) <= 1 and len(fn_words) >= 2)
        ):
            candidates.raw_title = fn_title

    if not candidates.raw_authors and fn_author:
        candidates.raw_authors = [fn_author]

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

    def __init__(self, api_key: Optional[str] = None, **kwargs):
        if kwargs:
            import warnings
            for arg in kwargs:
                warnings.warn(
                    f"O parâmetro '{arg}' em JevClassifier foi descontinuado e não tem mais efeito.",
                    DeprecationWarning,
                    stacklevel=2,
                )
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY")

    def classify_and_validate(
        self,
        pdf_path: str,
        max_paginas: int = 10,
        max_caracteres: int = 30000,
    ) -> JevValidationResult:
        """Analyze PDF structure, sample text, and candidate metadata."""
        combined_text, pages_text = extract_native_sample_text(
            pdf_path, head_pages=max_paginas, tail_pages=max_paginas
        )
        if max_caracteres and len(combined_text) > max_caracteres:
            combined_text = combined_text[:max_caracteres]
        total_pages = 0
        try:
            with pymupdf.open(pdf_path) as doc:
                total_pages = len(doc)
        except Exception:
            pass

        candidates = extract_candidate_metadata(combined_text, pdf_path=pdf_path, total_pages=total_pages)

        # Attempt to run through TypeSafe SDK if API key is present
        if self.api_key:
            try:
                import typesafe_sdk  # noqa: F401
            except ModuleNotFoundError:
                logger.warning(
                    "TYPESAFE_API_KEY configurada, mas o pacote 'typesafe-sdk' não está instalado "
                    "(instale com: pip install '.[typesafe]'). Utilizando motor determinístico local."
                )
                return self._run_calibrated_fallback(combined_text, candidates, total_pages)

            try:
                return self._run_jev_sdk(combined_text, candidates, total_pages)
            except Exception as e:
                logger.warning("TypeSafe SDK call failed (%s). Falling back to calibrated rules.", e)
                return self._run_calibrated_fallback(combined_text, candidates, total_pages)
        else:
            logger.info("Executing calibrated System One validator for %s (%d pages).", Path(pdf_path).name, total_pages)
            return self._run_calibrated_fallback(combined_text, candidates, total_pages)

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
                    "artigo, livro, tese, revista, apostila, outros."
                ),
                criteria={
                    "artigo": "Artigo científico ou acadêmico com resumo/abstract, referências bibliográficas, DOI ou filiação institucional",
                    "livro": "Livro comercial publicado com ISBN, editora comercial ou catálogo editorial",
                    "tese": "Tese de doutorado, dissertação de mestrado ou trabalho de conclusão de curso acadêmico",
                    "revista": "Revista periódica, magazine informativo, colunas de variedades ou publicação seriada",
                    "apostila": "Apostila didática, material didático de curso/EAD, notas de aula para estudantes",
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
            if val in ("artigo", "livro", "tese", "revista", "apostila", "outros"):
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
        if has_courseware and not candidates.doi:
            scores["apostila"] += 14.0
            scores["revista"] -= 10.0

        # 2. Academic Thesis / Dissertation (Teses, Dissertações -> 'tese')
        thesis_markers = [
            "tese apresentada",
            "dissertação apresentada",
            "requisito para a obtenção do título de doutor",
            "requisito para obtenção do título de doutor",
            "requisito para obtenção do título de mestre",
            "requisito para a obtenção do título de mestre",
            "programa de pós-graduação stricto sensu",
            "programa de pós-graduação",
        ]
        if any(w in lower_text for w in thesis_markers):
            scores["tese"] += 14.0
            scores["livro"] -= 6.0
            scores["revista"] -= 10.0
            scores["artigo"] -= 8.0

        # 3. Book Indicators
        if candidates.isbn:
            scores["livro"] += 6.0
        if any(w in lower_text for w in ["ficha catalográfica", "cip-brasil", "catalogação na publicação", "cdd", "cdu"]):
            scores["livro"] += 5.0
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

        # 6. Scanned / Short / Image-only PDF Heuristics
        if len(text.strip()) < 300:
            if total_pages > 60:
                scores["livro"] += 5.0
            elif total_pages <= 30:
                scores["artigo"] += 3.5

        best_class = max(scores, key=lambda k: scores[k])
        best_score = max(0.1, scores[best_class])
        total_positive = sum(s for s in scores.values() if s > 0) or 1.0
        confidence = min(0.99, max(0.50, round(best_score / total_positive, 3)))

        # Calibrated Noul probabilities
        doi_prob = 0.98 if candidates.doi and "/" in candidates.doi else 0.0
        isbn_prob = 0.98 if candidates.isbn and len(candidates.isbn) in (10, 13) else 0.0
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
            raw_jev_data={"calibrated_scores": scores},
        )

    classify_and_extract = classify_and_validate

