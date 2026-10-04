"""Metadata Enrichment Service.

Queries Crossref, Google Books, OpenLibrary, Brasil API (CBL), and OpenAlex public APIs
to fetch and cross-reference publication metadata.

Includes fallback logic: If Jev does not find identifiers or yields probability < 0.95,
executes fallback search by title across the APIs with strict relevance and author filtering.
"""

import copy
import os
import re
import logging
import threading
import unicodedata
from typing import Optional, List, Dict, Any, Set, Tuple
import urllib.parse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from dotenv import load_dotenv

from .models import (
    PublicationMetadata,
    Identifiers,
    JevValidationResult,
    PublicationType,
    is_valid_isbn,
    is_valid_issn,
    normalizar_doi,
)

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 6.0  # seconds
USER_AGENT = "PDFToMarkdownPipeline/1.0 (mailto:academic-pipeline@example.com)"

STOP_WORDS = {
    "de", "da", "do", "das", "dos", "e", "a", "o", "as", "os", "em", "um", "uma",
    "para", "com", "por", "no", "na", "the", "of", "and", "in", "to", "for", "on",
    "at", "by", "an", "is", "sobre", "entre", "como", "sem", "ou",
}


def is_same_isbn(a: Optional[str], b: Optional[str]) -> bool:
    """Verifica se dois ISBNs são idênticos ou equivalentes (ISBN-10 vs ISBN-13)."""
    if not a or not b:
        return False
    ca = re.sub(r"[^0-9Xx]", "", str(a)).upper()
    cb = re.sub(r"[^0-9Xx]", "", str(b)).upper()
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    if len(ca) == 13 and len(cb) == 10 and ca.startswith("978"):
        return ca[3:12] == cb[:9]
    if len(cb) == 13 and len(ca) == 10 and cb.startswith("978"):
        return cb[3:12] == ca[:9]
    return False


def is_title_relevant(searched_title: str, retrieved_title: Optional[str], threshold: float = 0.50) -> bool:
    """Validate that API retrieved title has sufficient word overlap with the query.

    Prevents unrelated homonym or commentary-based false matches.
    """
    if not retrieved_title:
        return False

    def tokenize(s: str) -> Set[str]:
        # Remove punctuation except letters and digits
        s_clean = re.sub(r"[^\w\s]", " ", s.lower())
        words = set(s_clean.split())
        return {w for w in words if len(w) > 2 and w not in STOP_WORDS and not w.isdigit()}

    searched_tokens = tokenize(searched_title)
    if not searched_tokens:
        return True

    retrieved_tokens = tokenize(retrieved_title)
    if not retrieved_tokens:
        return False

    intersection = searched_tokens.intersection(retrieved_tokens)
    ratio = len(intersection) / len(searched_tokens)
    return ratio >= threshold


GENERIC_SUBJECT_TITLES = {
    "psicologia social", "filosofia", "sociologia", "antropologia",
    "metodologia cientifica", "metodologia de pesquisa", "introducao a psicologia",
    "introducao a filosofia", "historia do brasil", "direito constitucional",
    "educacao a distancia", "material didatico", "apostila", "notas de aula",
    "criar", "artigo", "livro", "capitulo", "texto", "relatorio",
    "o espirito", "o espírito", "espirito", "espírito",
}


def is_author_compatible(
    candidate_authors: List[str],
    retrieved_authors: List[str],
    *,
    title: Optional[str] = None,
) -> bool:
    """Validate that candidate authors and retrieved API authors are compatible.

    If no candidate authors are known and title has only 1 substantive word or matches generic subjects, return False.
    If candidate authors are known, at least one key name token (e.g. surname) must match.
    """
    if not retrieved_authors:
        return True

    def get_tokens(names: List[str]) -> Set[str]:
        tokens = set()
        for name in names:
            clean = re.sub(r"[^\w\s]", " ", name.lower())
            for w in clean.split():
                if len(w) >= 3 and w not in STOP_WORDS and not w.isdigit():
                    tokens.add(w)
        return tokens

    cand_tokens = get_tokens(candidate_authors)
    ret_tokens = get_tokens(retrieved_authors)

    if not cand_tokens:
        if title:
            clean_t = re.sub(r"[^\w\s]", "", title.lower()).strip()
            content_words = [w for w in clean_t.split() if w not in STOP_WORDS and len(w) > 2]
            if len(content_words) <= 1 or clean_t in GENERIC_SUBJECT_TITLES:
                return False
        return True

    overlap = cand_tokens.intersection(ret_tokens)
    return len(overlap) > 0


INSTITUTION_REGEX = re.compile(
    r"\b(universidad|universidade|university|faculdade|faculty|instituto|institute|"
    r"departament|department|secretaria|minist[eé]rio|hospital|fundação|fundacion|foundation|"
    r"associa[çc][aã]o|association|escola|school|col[eé]gio|college|centro|center|centre|"
    r"campus|laborat[oó]rio|laboratory|editorial|editora|press|publisher)\b",
    re.IGNORECASE,
)


def filter_institutional_authors(authors: List[str]) -> List[str]:
    """Filter out institutional/organizational names if individual human authors are present."""
    if not authors:
        return []
    human_authors = [a for a in authors if not INSTITUTION_REGEX.search(a)]
    target = human_authors if human_authors else authors
    seen = set()
    deduped: List[str] = []
    for a in target:
        norm = a.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            deduped.append(a.strip())
    return deduped


def clean_journal_name(journal: Optional[str]) -> Optional[str]:
    """Normalize and clean journal names, removing slash duplications like Revista X/X."""
    if not journal:
        return None
    journal = journal.strip()
    if "/" in journal:
        parts = [p.strip() for p in journal.split("/") if p.strip()]
        if len(parts) == 2:
            p0, p1 = parts[0].lower(), parts[1].lower()
            p0_clean = re.sub(r"^(?:revista|journal of|cadernos de)\s+", "", p0).strip()
            p1_clean = re.sub(r"^(?:revista|journal of|cadernos de)\s+", "", p1).strip()
            if p0_clean == p1_clean or p0_clean in p1 or p1_clean in p0:
                return parts[1] if len(parts[1]) <= len(parts[0]) else parts[0]
    return journal


JUNGIAN_AUTHORS = {
    "c. g. jung", "carl gustav jung", "carl jung", "cg jung", "c.g. jung",
    "emma jung", "murray stein", "judith harris", "robin robertson",
    "sallie nichols", "marie-louise von franz", "von franz", "james hillman",
    "edward edinger", "james a. hall", "calvin s. hall", "frederic lenoir",
    "frédéric lenoir", "heraclito pinheiro", "heráclito pinheiro",
}

JUNGIAN_KEYWORDS = [
    "jung", "junguian", "junguiano", "junguiana", "psicologia analitica",
    "psicologia profunda", "psicologia dos complexos", "arquetipo", "arquetipos",
    "inconsciente coletivo", "animus e anima", "animus", "anima", "sincronicidade",
    "sombra", "individuacao", "tipos psicologicos", "mapa da alma",
    "homem e seus simbolos", "memorias, sonhos, reflexoes", "a natureza da psique",
    "o desenvolvimento da personalidade", "a pratica da psicoterapia",
    "psicologia do inconsciente", "sobre sentimentos e a sombra",
    "aspectos do feminino", "psicologia e religiao", "viagem em direcao a si mesmo",
    "psicologia junguiana",
]


def infer_thematic_area(
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    authors: Optional[List[str]] = None,
    sample_text: str = "",
    cand_area: Optional[str] = None,
) -> Tuple[str, str]:
    """Infere a área macro do conhecimento e subárea temática segundo taxonomias acadêmicas (ex.: CNPq).

    Mapeia confiavelmente obras de Psicologia Analítica / Junguiana e impede que caiam em 'Outros/Outros'
    ou em categorias acidentais como 'Feminino/Feminino'.
    """
    auths = authors or []
    all_text = f"{title or ''} {subtitle or ''} {' '.join(auths)} {sample_text[:3000]} {cand_area or ''}".lower()
    norm_text = unicodedata.normalize("NFKD", all_text).encode("ascii", "ignore").decode().lower()

    # 1. Detecção de Psicologia Analítica / Junguiana
    is_jungian_author = any(
        any(ja in unicodedata.normalize("NFKD", a).encode("ascii", "ignore").decode().lower() for ja in JUNGIAN_AUTHORS)
        for a in auths
    )
    is_jungian_topic = any(kw in norm_text for kw in JUNGIAN_KEYWORDS)
    if is_jungian_author or is_jungian_topic:
        return ("Psicologia", "Psicologia Analítica")

    # 2. Psicologia Geral e outras abordagens
    if any(kw in norm_text for kw in [
        "psicologia", "psicanalise", "freud", "lacan", "terapia cognitivo",
        "behaviorismo", "neuropsicologia", "psicopatologia", "psicoterapia",
    ]):
        if "psicanalise" in norm_text or "freud" in norm_text or "lacan" in norm_text:
            return ("Psicologia", "Psicanálise")
        if "neuropsicologia" in norm_text:
            return ("Psicologia", "Neuropsicologia")
        if "psicologia social" in norm_text:
            return ("Psicologia", "Psicologia Social")
        if "psicologia clinica" in norm_text or "psicoterapia" in norm_text:
            return ("Psicologia", "Psicologia Clínica")
        return ("Psicologia", "Psicologia Geral")

    # 3. Filosofia
    if any(kw in norm_text for kw in [
        "filosofia", "filosofica", "filosofico", "etica", "epistemologia",
        "metafisica", "nietzsche", "kant", "hegel", "heidegger", "platao",
        "aristoteles", "spinoza", "schopenhauer", "santo agostinho",
    ]):
        return ("Filosofia", "Filosofia Geral")

    # 4. Educação
    if any(kw in norm_text for kw in ["pedagogia", "educacao", "didatica", "ensino"]):
        return ("Educação", "Educação Geral")

    # 5. Direito
    if any(kw in norm_text for kw in ["direito civil", "direito penal", "direito constitucional", "jurisprudencia"]):
        return ("Direito", "Direito Geral")

    # 6. Se veio uma área candidata válida da ficha catalográfica (CIP)
    if cand_area:
        cand_clean = cand_area.strip()
        cand_norm = unicodedata.normalize("NFKD", cand_clean).encode("ascii", "ignore").decode().lower()
        invalid_areas = {
            "titulo", "autor", "brasil", "recurso", "serie", "coautor", "orientador",
            "orientadora", "universidade", "faculdade", "instituto", "edicao", "volume",
            "cdd", "cdu", "ilustrado", "bibliografia", "isbn", "issn", "doi", "feminino",
        }
        if cand_norm not in invalid_areas and len(cand_clean) >= 3:
            return (cand_clean.title(), cand_clean.title())

    return ("Outros", "Geral")


class MetadataEnricher:
    """Consolidates metadata by querying public academic, book, and national registry APIs."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, online: bool = True):
        self.timeout = timeout
        self.online = online
        self._local = threading.local()
        self._custom_session: Optional[Any] = None
        self.google_books_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
        self._cache: Dict[str, Any] = {}
        self._cache_lock = threading.Lock()

    def _create_session(self) -> requests.Session:
        """Create a new requests.Session with connection pooling and retry adapter."""
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT})
        retries = Retry(
            total=2,
            connect=0,
            read=0,
            status=2,
            backoff_factor=0.3,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False,
            respect_retry_after_header=False,
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        return s

    @property
    def session(self) -> requests.Session:
        if self._custom_session is not None:
            return self._custom_session
        if not hasattr(self._local, "session") or self._local.session is None:
            self._local.session = self._create_session()
        return self._local.session

    @session.setter
    def session(self, s: Any) -> None:
        self._custom_session = s
        self._local.session = s

    def _get_timeout(self) -> Tuple[float, float] | float:
        """Return connect and read timeouts (defaulting to 3.0s connect)."""
        if isinstance(self.timeout, (tuple, list)):
            return tuple(self.timeout)
        return (3.0, float(self.timeout))

    def _get_from_cache(self, key: str) -> Tuple[bool, Any]:
        """Check cache for key and return (hit, deepcopy(val))."""
        with self._cache_lock:
            if key in self._cache:
                val = self._cache[key]
                return True, copy.deepcopy(val) if val is not None else None
        return False, None

    def _set_cache(self, key: str, value: Any) -> None:
        """Save a deepcopy of value into cache."""
        with self._cache_lock:
            self._cache[key] = copy.deepcopy(value) if value is not None else None

    def clear_cache(self) -> None:
        """Clear the in-memory query cache."""
        with self._cache_lock:
            self._cache.clear()

    def enrich(self, jev_result: JevValidationResult) -> PublicationMetadata:
        """Enrich metadata from Jev validation result via public APIs.

        Implements fallback: searches by title if identifiers are absent or have probability < 0.95.

        Args:
            jev_result: Output from Jev classifier.

        Returns:
            PublicationMetadata object.
        """
        candidates = jev_result.candidates
        probabilities = jev_result.probabilities

        doi = normalizar_doi(candidates.doi)
        isbn = candidates.isbn
        issn = candidates.issn
        title_candidate = candidates.raw_title

        doi_prob = probabilities.get("doi", 0.0)
        isbn_prob = probabilities.get("isbn", 0.0)

        cand_area = candidates.raw_area
        if cand_area:
            norm_area = unicodedata.normalize("NFKD", cand_area).encode("ascii", "ignore").decode("ascii").lower()
            clean_token = re.sub(r"[^a-z]", "", norm_area)
            if clean_token in ("titulo", "autor", "serie", "assunto", "orientador"):
                cand_area = None

        # Baseline metadata initialized with candidate extraction
        area_princ, sub_area = infer_thematic_area(
            title=title_candidate,
            subtitle=candidates.raw_subtitle,
            authors=candidates.raw_authors or [],
            sample_text=candidates.sample_text,
            cand_area=cand_area,
        )

        metadata = PublicationMetadata(
            title=title_candidate or "Publicação Sem Título",
            subtitle=candidates.raw_subtitle,
            authors=candidates.raw_authors or [],
            publisher=candidates.raw_publisher,
            edition=candidates.raw_edition,
            year=candidates.raw_year,
            city=candidates.raw_city,
            area=area_princ,
            area_principal=area_princ,
            subarea=sub_area,
            classification=jev_result.classification,
            identifiers=Identifiers(doi=doi, isbn=isbn, issn=issn),
            provedor_classificador=getattr(jev_result, "provider", None),
            doi_source=getattr(candidates, "doi_source", None),
            title_source=getattr(candidates, "title_source", None),
            author_source=getattr(candidates, "author_source", None),
            isbn_source=getattr(candidates, "isbn_source", None),
            issn_source=getattr(candidates, "issn_source", None),
            total_pages=candidates.total_pages,
            cip_pages=candidates.cip_pages,
            cip_year=candidates.cip_year,
        )

        if not self.online:
            logger.info("Enriquecimento online desativado (modo offline). Mantendo metadados candidatos.")
            metadata.confidence_classification = jev_result.classification_confidence
            self._evaluate_needs_review(metadata, jev_result, sources_consulted=[])
            return metadata

        sources_consulted: List[str] = []

        # 1. Query by DOI if present (direct authoritative identifier lookup)
        if doi:
            logger.info("DOI found (%s, prob=%.2f). Querying Crossref & OpenAlex.", doi, doi_prob)
            cr_meta = self.fetch_crossref_by_doi(doi)
            if cr_meta:
                self._merge_metadata(metadata, cr_meta, authoritative=True)
                sources_consulted.append("Crossref (DOI)")
                metadata.doi_source = "api_crossref"
                if cr_meta.get("title"):
                    metadata.title_source = "api_crossref"

            oa_meta = self.fetch_openalex_by_doi(doi)
            if oa_meta:
                self._merge_metadata(metadata, oa_meta, authoritative=True)
                sources_consulted.append("OpenAlex (DOI)")
                if not metadata.doi_source:
                    metadata.doi_source = "api_openalex"

        metadata.cip_pages = getattr(candidates, "cip_pages", None)
        metadata.cip_year = getattr(candidates, "cip_year", None)
        metadata.total_pages = getattr(candidates, "total_pages", None)
        metadata.reimpressao = getattr(candidates, "reimpressao", None)
        metadata.year_source = getattr(candidates, "year_source", None)
        metadata.city_source = getattr(candidates, "city_source", None)
        metadata.publisher_source = getattr(candidates, "publisher_source", None)

        # 2. Query by ISBN if present (direct authoritative identifier lookup)
        if isbn and is_valid_isbn(isbn, strict=False):
            logger.info("ISBN found (%s, prob=%.2f). Querying Brasil API, Google Books & OpenLibrary.", isbn, isbn_prob)
            br_meta = self.fetch_brasil_api_isbn(isbn)
            if br_meta:
                br_meta["source"] = "api_brasilapi"
                self._merge_metadata(metadata, br_meta, authoritative=True)
                sources_consulted.append("Brasil API / CBL (ISBN)")
                metadata.isbn_source = "api_brasilapi"
                metadata.isbn_verified = True
                if br_meta.get("title"):
                    metadata.title_source = "api_brasilapi"

            gb_meta = self.fetch_google_books_by_isbn(isbn)
            if gb_meta:
                gb_meta["source"] = "api_google_books"
                self._merge_metadata(metadata, gb_meta, authoritative=not bool(br_meta))
                sources_consulted.append("Google Books (ISBN)")
                metadata.isbn_verified = True
                if not metadata.isbn_source:
                    metadata.isbn_source = "api_google_books"
                if gb_meta.get("title") and not br_meta:
                    metadata.title_source = "api_google_books"

            ol_meta = self.fetch_openlibrary_by_isbn(isbn)
            if ol_meta:
                ol_meta["source"] = "api_openlibrary"
                self._merge_metadata(metadata, ol_meta, authoritative=not bool(br_meta or gb_meta))
                sources_consulted.append("OpenLibrary (ISBN)")
                metadata.isbn_verified = True
                if not metadata.isbn_source:
                    metadata.isbn_source = "api_openlibrary"

        # 3. Fallback: Search by title if no identifier succeeded
        should_fallback_title = not sources_consulted

        clean_title = (title_candidate or "").strip()
        is_valid_query = (
            clean_title
            and not clean_title.startswith("---")
            and len(clean_title) >= 4
            and "page break" not in clean_title.lower()
        )

        if should_fallback_title and is_valid_query:
            logger.info("Applying Title Search Fallback for '%s' across APIs.", clean_title)
            self._apply_title_fallback(clean_title, jev_result.classification, metadata, sources_consulted)

        if metadata.area_principal == "Outros" or (metadata.authors and not candidates.raw_authors):
            ap, sa = infer_thematic_area(
                title=metadata.title,
                subtitle=metadata.subtitle,
                authors=metadata.authors,
                sample_text=candidates.sample_text,
                cand_area=cand_area,
            )
            metadata.area_principal = ap
            metadata.subarea = sa
            metadata.area = ap

        metadata.source_apis = sources_consulted
        metadata.confidence_classification = jev_result.classification_confidence
        self._evaluate_needs_review(metadata, jev_result, sources_consulted=sources_consulted)
        return metadata

    def _evaluate_needs_review(
        self,
        metadata: PublicationMetadata,
        jev_result: Optional[JevValidationResult] = None,
        sources_consulted: Optional[List[str]] = None,
    ) -> None:
        """Evaluate quarantine and manual review criteria, keeping classification and metadata confidences separate."""
        from .classifier_jev import is_invalid_or_disclaimer_title, GENERIC_SINGLE_WORD_TITLES

        sources_consulted = sources_consulted or []
        if jev_result:
            metadata.confidence_classification = jev_result.classification_confidence
        elif not getattr(metadata, "confidence_classification", None):
            metadata.confidence_classification = metadata.confidence
        meta_conf = 1.0
        t = (metadata.title or "").strip()
        t_lower = t.lower()
        has_verified_id = bool(metadata.identifiers.isbn or metadata.identifiers.doi or metadata.identifiers.issn)

        # 1. Validações estritas de título
        if t in ("Sem Título", "Publicação Sem Título") or len(t) < 4:
            meta_conf = 0.1
            metadata.needs_review = True
            if "Documento sem título bibliográfico identificável" not in metadata.review_reasons:
                metadata.review_reasons.append("Documento sem título bibliográfico identificável")
        elif is_invalid_or_disclaimer_title(t):
            meta_conf = 0.15
            metadata.needs_review = True
            msg = f"Título inválido, identificador ou disclaimer legal detectado ('{t}')"
            if msg not in metadata.review_reasons:
                metadata.review_reasons.append(msg)
        elif (
            t.startswith("---")
            or "page break" in t_lower
            or re.search(r"\bp[áa]gina\s*\d+\b", t, re.I)
        ):
            meta_conf = 0.3
            metadata.needs_review = True
            if "Título contaminado com marcador de página ou cabeçalho" not in metadata.review_reasons:
                metadata.review_reasons.append("Título contaminado com marcador de página ou cabeçalho")
        elif len(t.split()) <= 1 and t_lower in GENERIC_SINGLE_WORD_TITLES:
            meta_conf = 0.35
            metadata.needs_review = True
            msg = f"Título unipalavra ou fragmento genérico ('{t}')"
            if msg not in metadata.review_reasons:
                metadata.review_reasons.append(msg)
        elif len(t.split()) == 1 and not sources_consulted and not has_verified_id:
            meta_conf = 0.45
            metadata.needs_review = True
            msg = f"Título com palavra única ('{t}') sem confirmação bibliográfica"
            if msg not in metadata.review_reasons:
                metadata.review_reasons.append(msg)

        # 2. Validações de campos essenciais (autores, ano, localidade)
        is_editorial_or_academic = metadata.classification in ("livro", "tese", "dissertacao", "capitulo_livro")
        if is_editorial_or_academic:
            if not metadata.authors:
                meta_conf -= 0.25
                metadata.needs_review = True
                msg = f"{metadata.classification.capitalize()} sem autoria identificada"
                if msg not in metadata.review_reasons:
                    metadata.review_reasons.append(msg)
            if not metadata.year:
                meta_conf -= 0.15
                metadata.needs_review = True
                msg = f"{metadata.classification.capitalize()} sem ano de publicação identificado"
                if msg not in metadata.review_reasons:
                    metadata.review_reasons.append(msg)
            if not metadata.city:
                meta_conf -= 0.10
                metadata.needs_review = True
                msg = f"{metadata.classification.capitalize()} sem local de publicação identificado"
                if msg not in metadata.review_reasons:
                    metadata.review_reasons.append(msg)
        elif metadata.classification in ("artigo", "artigo_cientifico") and not metadata.authors:
            meta_conf -= 0.25
            metadata.needs_review = True
            msg = "Artigo acadêmico sem autoria identificada"
            if msg not in metadata.review_reasons:
                metadata.review_reasons.append(msg)

        # 3. Validação de contagem de páginas e integridade da obra
        jev_cand = getattr(jev_result, "candidates", None) if jev_result else None
        tot_pages = getattr(metadata, "total_pages", None) or (getattr(jev_cand, "total_pages", 0) if jev_cand else 0) or 0
        cip_pages = getattr(metadata, "cip_pages", None) or (getattr(jev_cand, "cip_pages", None) if jev_cand else None)
        if metadata.classification == "livro" and tot_pages > 0:
            if tot_pages < 25:
                meta_conf -= 0.2
                metadata.needs_review = True
                msg_p = f"Livro com contagem de páginas muito baixa ({tot_pages} págs). Provável extrato ou obra incompleta."
                if msg_p not in metadata.review_reasons:
                    metadata.review_reasons.append(msg_p)
            if cip_pages and cip_pages > 0 and tot_pages < 0.6 * cip_pages:
                meta_conf -= 0.25
                metadata.needs_review = True
                msg_cip = f"Arquivo parcial detectado: possui {tot_pages} páginas, mas a ficha catalográfica registra {cip_pages} páginas."
                if msg_cip not in metadata.review_reasons:
                    metadata.review_reasons.append(msg_cip)

        # 4. Divergência entre ano da ficha catalográfica (CIP) e ano catalogado
        cip_yr = getattr(metadata, "cip_year", None) or (getattr(jev_cand, "cip_year", None) if jev_cand else None)
        if cip_yr and metadata.year and abs(cip_yr - metadata.year) > 1:
            metadata.needs_review = True
            msg_yr = f"Divergência entre ano da ficha catalográfica ({cip_yr}) e ano catalogado ({metadata.year}). Possível reimpressão ou reedição."
            if msg_yr not in metadata.review_reasons:
                metadata.review_reasons.append(msg_yr)

        # Validação de plausibilidade de ano
        if metadata.year:
            if metadata.year < 1950 and metadata.identifiers.isbn:
                meta_conf -= 0.25
                metadata.needs_review = True
                msg_yr_impl = f"Ano de publicação ({metadata.year}) implausível para publicação com ISBN. Provável confusão com data histórica ou biográfica."
                if msg_yr_impl not in metadata.review_reasons:
                    metadata.review_reasons.append(msg_yr_impl)
                metadata.year = None
            elif metadata.year < 1900:
                meta_conf -= 0.20
                metadata.needs_review = True
                msg_yr_hist = f"Ano de publicação ({metadata.year}) anterior ao século XX requer verificação manual."
                if msg_yr_hist not in metadata.review_reasons:
                    metadata.review_reasons.append(msg_yr_hist)

        # 5. Localidade suspeita ou resíduo editorial
        if metadata.city:
            from .classifier_jev import sanitize_city, CITY_BOILERPLATE_REGEX, is_suspicious_city
            c_clean = sanitize_city(metadata.city)
            if c_clean:
                metadata.city = c_clean
            if not c_clean or CITY_BOILERPLATE_REGEX.search(metadata.city) or is_suspicious_city(metadata.city, metadata.authors, metadata.title):
                meta_conf -= 0.15
                metadata.needs_review = True
                msg_city = f"Localidade de publicação suspeita ou contém dados editoriais/pessoais ('{metadata.city}')"
                if msg_city not in metadata.review_reasons:
                    metadata.review_reasons.append(msg_city)
                metadata.city = None

        # 6. Validações de identificadores e fontes
        if not has_verified_id and not sources_consulted:
            meta_conf -= 0.15

        # 7. Classificação 'Outros' deve sempre ser sinalizada para revisão acompanhada de justificativa
        if metadata.classification == "outros":
            meta_conf -= 0.15
            metadata.needs_review = True
            msg_outros = "Tipo documental não determinado categoricamente ('Outros')"
            if not any("outros" in r.lower() for r in metadata.review_reasons):
                metadata.review_reasons.append(msg_outros)

        if jev_result:
            raw_data = getattr(jev_result, "raw_jev_data", None)
            margin = raw_data.get("margin") if isinstance(raw_data, dict) else None
            if jev_result.classification_confidence < 0.50 and not sources_consulted and not has_verified_id:
                metadata.needs_review = True
                metadata.review_reasons.append(
                    f"Baixa pontuação heurística de classificação ({jev_result.classification_confidence:.2f}) sem identificador público"
                )
            elif margin is not None and margin < 1.0 and not sources_consulted and not has_verified_id:
                metadata.needs_review = True
                metadata.review_reasons.append(
                    f"Margem estreita entre categorias candidatas ({margin:.1f} pts) sem identificador público"
                )

        if sources_consulted and not metadata.needs_review:
            meta_conf = max(meta_conf, 0.85 if "Title Search" in str(sources_consulted) else 0.95)
        elif metadata.needs_review:
            meta_conf = min(meta_conf, 0.70)

        metadata.confidence_metadata = max(0.1, min(1.0, round(meta_conf, 2)))
        metadata.confidence = min(metadata.confidence_classification, metadata.confidence_metadata)

    def _apply_title_fallback(
        self,
        title: str,
        classification: PublicationType,
        metadata: PublicationMetadata,
        sources_consulted: List[str],
    ) -> None:
        """Search APIs by title according to publication type priority with relevance and author checks."""
        candidate_authors = metadata.authors or []
        clean_t = re.sub(r"[^\w\s]", "", title.lower()).strip()
        if not candidate_authors and (len(clean_t.split()) <= 1 or clean_t in GENERIC_SUBJECT_TITLES):
            logger.info("Ignorando busca não-autoritativa por título genérico ('%s') sem autoria confirmada.", title)
            return

        if classification in ("artigo", "artigo_cientifico"):
            # Priority: Crossref -> OpenAlex
            cr_meta = self.fetch_crossref_by_title(title)
            if (
                cr_meta
                and is_title_relevant(title, cr_meta.get("title"))
                and is_author_compatible(candidate_authors, cr_meta.get("authors", []), title=title)
            ):
                self._merge_metadata(metadata, cr_meta, authoritative=False)
                sources_consulted.append("Crossref (Title Search)")
            else:
                oa_meta = self.fetch_openalex_by_title(title)
                if (
                    oa_meta
                    and is_title_relevant(title, oa_meta.get("title"))
                    and is_author_compatible(candidate_authors, oa_meta.get("authors", []), title=title)
                ):
                    self._merge_metadata(metadata, oa_meta, authoritative=False)
                    sources_consulted.append("OpenAlex (Title Search)")

        elif classification in ("livro", "apostila"):
            # Priority: Google Books -> OpenLibrary
            gb_meta = self.fetch_google_books_by_title(title)
            if (
                gb_meta
                and is_title_relevant(title, gb_meta.get("title"))
                and is_author_compatible(candidate_authors, gb_meta.get("authors", []), title=title)
            ):
                self._merge_metadata(metadata, gb_meta, authoritative=False)
                sources_consulted.append("Google Books (Title Search)")
            else:
                ol_meta = self.fetch_openlibrary_by_title(title)
                if (
                    ol_meta
                    and is_title_relevant(title, ol_meta.get("title"))
                    and is_author_compatible(candidate_authors, ol_meta.get("authors", []), title=title)
                ):
                    self._merge_metadata(metadata, ol_meta, authoritative=False)
                    sources_consulted.append("OpenLibrary (Title Search)")

        elif classification == "tese":
            # Priority: OpenAlex -> Crossref -> Google Books
            oa_meta = self.fetch_openalex_by_title(title)
            if (
                oa_meta
                and is_title_relevant(title, oa_meta.get("title"))
                and is_author_compatible(candidate_authors, oa_meta.get("authors", []), title=title)
            ):
                self._merge_metadata(metadata, oa_meta, authoritative=False)
                sources_consulted.append("OpenAlex (Title Search)")
            else:
                cr_meta = self.fetch_crossref_by_title(title)
                if (
                    cr_meta
                    and is_title_relevant(title, cr_meta.get("title"))
                    and is_author_compatible(candidate_authors, cr_meta.get("authors", []), title=title)
                ):
                    self._merge_metadata(metadata, cr_meta, authoritative=False)
                    sources_consulted.append("Crossref (Title Search)")
        else:
            # Classification 'outros' or unclassified:
            # Priority: Google Books -> OpenLibrary -> Crossref -> OpenAlex
            gb_meta = self.fetch_google_books_by_title(title)
            if (
                gb_meta
                and is_title_relevant(title, gb_meta.get("title"))
                and is_author_compatible(candidate_authors, gb_meta.get("authors", []), title=title)
            ):
                self._merge_metadata(metadata, gb_meta, authoritative=False)
                sources_consulted.append("Google Books (Title Search)")
                # Auto-promote to 'livro' if verified as a published book with ISBN/publisher
                if gb_meta.get("isbn") or gb_meta.get("publisher"):
                    metadata.classification = "livro"
            else:
                ol_meta = self.fetch_openlibrary_by_title(title)
                if (
                    ol_meta
                    and is_title_relevant(title, ol_meta.get("title"))
                    and is_author_compatible(candidate_authors, ol_meta.get("authors", []), title=title)
                ):
                    self._merge_metadata(metadata, ol_meta, authoritative=False)
                    sources_consulted.append("OpenLibrary (Title Search)")
                    if ol_meta.get("isbn") or ol_meta.get("publisher"):
                        metadata.classification = "livro"
                else:
                    cr_meta = self.fetch_crossref_by_title(title)
                    if (
                        cr_meta
                        and is_title_relevant(title, cr_meta.get("title"))
                        and is_author_compatible(candidate_authors, cr_meta.get("authors", []), title=title)
                    ):
                        self._merge_metadata(metadata, cr_meta, authoritative=False)
                        sources_consulted.append("Crossref (Title Search)")
                        if cr_meta.get("journal") or cr_meta.get("doi"):
                            metadata.classification = "artigo"
                    else:
                        oa_meta = self.fetch_openalex_by_title(title)
                        if (
                            oa_meta
                            and is_title_relevant(title, oa_meta.get("title"))
                            and is_author_compatible(candidate_authors, oa_meta.get("authors", []), title=title)
                        ):
                            self._merge_metadata(metadata, oa_meta, authoritative=False)
                            sources_consulted.append("OpenAlex (Title Search)")
                            if oa_meta.get("journal") or oa_meta.get("doi"):
                                metadata.classification = "artigo"

    # ----------------- BRASIL API (CBL) -----------------
    def fetch_brasil_api_isbn(self, isbn: str) -> Optional[Dict[str, Any]]:
        """Fetch official Brazilian ISBN registration data from Brasil API (CBL / Mercado Editorial)."""
        clean_isbn = re.sub(r"[^0-9X]", "", str(isbn).upper())
        if not is_valid_isbn(clean_isbn, strict=False):
            return None
        cache_key = f"brasilapi:isbn:{clean_isbn}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        url = f"https://brasilapi.com.br/api/isbn/v1/{clean_isbn}"
        result = None
        try:
            resp = self.session.get(url, timeout=self._get_timeout())
            if resp.status_code == 200:
                data = resp.json()
                ret_isbn = data.get("isbn")
                if ret_isbn and not is_same_isbn(clean_isbn, ret_isbn):
                    logger.warning("Brasil API retornou ISBN divergente (%s != %s). Rejeitando resposta.", ret_isbn, clean_isbn)
                    self._set_cache(cache_key, None)
                    return None

                raw_title = data.get("title")
                if not raw_title:
                    self._set_cache(cache_key, None)
                    return None

                # Clean prefix like '(ED) ' often returned by CBL
                clean_title = re.sub(r"^\(ED\)\s*", "", raw_title, flags=re.I).strip()
                subtitle = data.get("subtitle")
                if ":" in clean_title and not subtitle:
                    parts = clean_title.split(":", 1)
                    clean_title = parts[0].strip()
                    subtitle = parts[1].strip()

                authors = [a.strip() for a in data.get("authors", []) if a.strip()]
                publisher = data.get("publisher")
                year = data.get("year")
                location = data.get("location")
                city = None
                if location and "," in location:
                    city = location.split(",")[0].strip()
                elif location:
                    city = location.strip()

                result = {
                    "title": clean_title,
                    "subtitle": subtitle,
                    "authors": authors,
                    "publisher": publisher,
                    "year": int(year) if year else None,
                    "city": city,
                    "isbn": clean_isbn,
                }
        except requests.RequestException as e:
            logger.debug("Brasil API ISBN error: %s", e)

        self._set_cache(cache_key, result)
        return result

    # ----------------- CROSSREF API -----------------
    def fetch_crossref_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """Fetch metadata from Crossref API by DOI."""
        clean_doi = normalizar_doi(doi)
        if not clean_doi:
            return None
        norm_doi = clean_doi.lower()
        cache_key = f"crossref:doi:{norm_doi}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        quoted_doi = urllib.parse.quote(clean_doi, safe="/")
        url = f"https://api.crossref.org/works/{quoted_doi}"
        result = None
        try:
            resp = self.session.get(url, timeout=self._get_timeout())
            if resp.status_code == 200:
                item = resp.json().get("message", {})
                result = self._parse_crossref_item(item)
        except requests.RequestException as e:
            logger.debug("Crossref DOI lookup error: %s", e)

        self._set_cache(cache_key, result)
        return result

    def fetch_crossref_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search Crossref API by title."""
        norm_title = title.strip().lower()
        cache_key = f"crossref:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        url = "https://api.crossref.org/works"
        params = {"query.title": title, "rows": 3}
        result = None
        try:
            resp = self.session.get(url, params=params, timeout=self._get_timeout())
            if resp.status_code == 200:
                items = resp.json().get("message", {}).get("items", [])
                for item in items:
                    parsed = self._parse_crossref_item(item)
                    if is_title_relevant(title, parsed.get("title")):
                        result = parsed
                        break
        except requests.RequestException as e:
            logger.debug("Crossref title search error: %s", e)

        self._set_cache(cache_key, result)
        return result

    def _parse_crossref_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Crossref item to standardized dictionary."""
        titles = item.get("title", [])
        title = titles[0] if titles else None

        subtitles = item.get("subtitle", [])
        subtitle = subtitles[0] if subtitles else None

        authors: List[str] = []
        for author in item.get("author", []):
            family = author.get("family", "").strip()
            given = author.get("given", "").strip()
            if family and given:
                authors.append(f"{family.upper()}, {given}")
            elif family:
                authors.append(family.upper())
            elif author.get("name"):
                authors.append(author["name"].strip())

        authors = filter_institutional_authors(authors)

        publisher = item.get("publisher")
        container_titles = item.get("container-title", [])
        journal = container_titles[0] if container_titles else None
        volume = item.get("volume")
        issue = item.get("issue")
        page = item.get("page")
        doi = item.get("DOI")
        issn_list = item.get("ISSN", [])
        issn = issn_list[0] if issn_list else None

        year = None
        date_parts = (
            item.get("published-print", {}).get("date-parts", [])
            or item.get("published", {}).get("date-parts", [])
            or item.get("created", {}).get("date-parts", [])
        )
        if date_parts and date_parts[0]:
            year = date_parts[0][0]

        return {
            "title": title,
            "subtitle": subtitle,
            "authors": authors,
            "publisher": publisher,
            "journal": journal,
            "volume": volume,
            "number": issue,
            "pages": page,
            "year": year,
            "doi": doi,
            "issn": issn,
            "url": item.get("URL"),
        }

    # ----------------- GOOGLE BOOKS API -----------------
    def fetch_google_books_by_isbn(self, isbn: str) -> Optional[Dict[str, Any]]:
        """Fetch metadata from Google Books API by ISBN."""
        clean_isbn = re.sub(r"[^0-9X]", "", str(isbn).upper())
        if not is_valid_isbn(clean_isbn, strict=False):
            return None
        cache_key = f"gbooks:isbn:{clean_isbn}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        url = "https://www.googleapis.com/books/v1/volumes"
        params = {"q": f"isbn:{isbn}"}
        result = self._query_google_books(url, params)
        self._set_cache(cache_key, result)
        return result

    def fetch_google_books_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search Google Books API by title."""
        norm_title = title.strip().lower()
        cache_key = f"gbooks:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        url = "https://www.googleapis.com/books/v1/volumes"
        params = {"q": f"intitle:{title}", "maxResults": 3}
        result = self._query_google_books(url, params)
        self._set_cache(cache_key, result)
        return result

    def _query_google_books(self, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            req_params = dict(params)
            if self.google_books_api_key:
                req_params["key"] = self.google_books_api_key
            resp = self.session.get(url, params=req_params, timeout=self._get_timeout())
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    target_isbn = None
                    q_str = str(req_params.get("q", ""))
                    if "isbn:" in q_str:
                        target_isbn = q_str.split("isbn:", 1)[1].strip()

                    matching_items = []
                    if target_isbn:
                        for it in items:
                            v = it.get("volumeInfo", {})
                            ids = [i.get("identifier") for i in v.get("industryIdentifiers", []) if i.get("identifier")]
                            if any(is_same_isbn(target_isbn, id_val) for id_val in ids):
                                matching_items.append(it)
                        if not matching_items:
                            logger.warning("Google Books não retornou item com o ISBN pesquisado (%s).", target_isbn)
                            return None
                    else:
                        matching_items = items

                    # Select the item with the most complete metadata (publisher and publishedDate)
                    best_item = matching_items[0]
                    for it in matching_items:
                        v = it.get("volumeInfo", {})
                        if v.get("publisher") and v.get("publishedDate"):
                            best_item = it
                            break
                    volume_info = best_item.get("volumeInfo", {})
                    return self._parse_google_books_item(volume_info)
            elif resp.status_code == 429:
                logger.warning("Google Books API quota exceeded (HTTP 429).")
            elif resp.status_code == 403:
                logger.warning("Google Books API retornou 403 Forbidden. Verifique se a 'Books API' está ativada e permitida na sua chave no Google Cloud Console.")
        except requests.RequestException as e:
            logger.debug("Google Books API query error: %s", e)
        return None

    def _parse_google_books_item(self, info: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Google Books volumeInfo to standardized dictionary."""
        title = info.get("title")
        subtitle = info.get("subtitle")
        raw_authors = info.get("authors", [])
        publisher = info.get("publisher")
        published_date = info.get("publishedDate", "")
        year = int(published_date[:4]) if len(published_date) >= 4 and published_date[:4].isdigit() else None

        isbn_val = None
        # Prioritize 13-digit standard ISBN
        for id_item in info.get("industryIdentifiers", []):
            if id_item.get("type") == "ISBN_13":
                isbn_val = id_item.get("identifier")
                break
        if not isbn_val:
            for id_item in info.get("industryIdentifiers", []):
                if id_item.get("type") == "ISBN_10":
                    isbn_val = id_item.get("identifier")
                    break
        if not isbn_val and info.get("industryIdentifiers"):
            isbn_val = info.get("industryIdentifiers")[0].get("identifier")

        if isbn_val:
            cand_raw = str(isbn_val).strip()
            if "OCLC" in cand_raw.upper() or "EAN" in cand_raw.upper():
                isbn_val = None
            else:
                cleaned_digits = re.sub(r"[^0-9Xx]", "", cand_raw)
                if len(cleaned_digits) in (10, 13) and is_valid_isbn(cleaned_digits, strict=False):
                    isbn_val = cleaned_digits
                else:
                    isbn_val = None

        categories = info.get("categories", [])
        area = None
        if categories:
            cat_lower = str(categories[0]).lower()
            cat_map = {
                "psychology": "Psicologia",
                "philosophy": "Filosofia",
                "computers": "Computação",
                "computer science": "Ciência da Computação",
                "social science": "Ciências Sociais",
                "law": "Direito",
                "education": "Educação",
                "medical": "Medicina",
                "health": "Saúde",
                "history": "História",
                "political science": "Ciência Política",
                "literature": "Literatura",
                "linguistics": "Linguística",
            }
            for k, v in cat_map.items():
                if k in cat_lower:
                    area = v
                    break
            if not area:
                area = str(categories[0]).split("/")[0].strip().title()

        return {
            "title": title,
            "subtitle": subtitle,
            "authors": raw_authors,
            "publisher": publisher,
            "year": year,
            "isbn": isbn_val,
            "pages": str(info.get("pageCount")) if info.get("pageCount") else None,
            "area": area,
        }

    # ----------------- OPENLIBRARY API -----------------
    def fetch_openlibrary_by_isbn(self, isbn: str) -> Optional[Dict[str, Any]]:
        """Fetch metadata from OpenLibrary API by ISBN."""
        clean_isbn = re.sub(r"[^0-9X]", "", str(isbn).upper())
        if not is_valid_isbn(clean_isbn, strict=False):
            return None
        cache_key = f"openlib:isbn:{clean_isbn}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        url = f"https://openlibrary.org/isbn/{isbn}.json"
        result = None
        try:
            resp = self.session.get(url, timeout=self._get_timeout())
            if resp.status_code == 200:
                data = resp.json()
                ret_isbns = data.get("isbn_13", []) + data.get("isbn_10", [])
                if ret_isbns and not any(is_same_isbn(clean_isbn, x) for x in ret_isbns):
                    logger.warning("OpenLibrary retornou registro sem o ISBN consultado (%s). Rejeitando resposta.", clean_isbn)
                    self._set_cache(cache_key, None)
                    return None

                publish_places = data.get("publish_places", [])
                city = publish_places[0].strip() if publish_places and isinstance(publish_places[0], str) else None

                result = {
                    "title": data.get("title"),
                    "subtitle": data.get("subtitle"),
                    "publisher": data.get("publishers", [None])[0] if data.get("publishers") else None,
                    "city": city,
                    "year": int(data["publish_date"][-4:]) if data.get("publish_date") and data["publish_date"][-4:].isdigit() else None,
                    "isbn": clean_isbn,
                }
        except requests.RequestException as e:
            logger.debug("OpenLibrary ISBN error: %s", e)

        self._set_cache(cache_key, result)
        return result

    def fetch_openlibrary_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search OpenLibrary API by title."""
        norm_title = title.strip().lower()
        cache_key = f"openlib:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        url = "https://openlibrary.org/search.json"
        params = {"q": title, "limit": 5}
        result = None
        try:
            resp = self.session.get(url, params=params, timeout=self._get_timeout())
            if resp.status_code == 200:
                docs = resp.json().get("docs", [])
                for doc in docs:
                    doc_title = doc.get("title")
                    if is_title_relevant(title, doc_title):
                        valid_isbns = [
                            re.sub(r"[^0-9Xx]", "", str(i)) for i in doc.get("isbn", [])
                            if is_valid_isbn(re.sub(r"[^0-9Xx]", "", str(i)), strict=False) and "OCLC" not in str(i).upper() and "EAN" not in str(i).upper()
                        ]
                        result = {
                            "title": doc_title,
                            "authors": doc.get("author_name", []),
                            "publisher": doc.get("publisher", [None])[0] if doc.get("publisher") else None,
                            "year": doc.get("first_publish_year"),
                            "isbn": valid_isbns[0] if valid_isbns else None,
                        }
                        break
        except requests.RequestException as e:
            logger.debug("OpenLibrary title search error: %s", e)

        self._set_cache(cache_key, result)
        return result

    # ----------------- OPENALEX API -----------------
    def fetch_openalex_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """Fetch publication metadata from OpenAlex by DOI."""
        clean_doi = normalizar_doi(doi)
        if not clean_doi:
            return None
        norm_doi = clean_doi.lower()
        cache_key = f"openalex:doi:{norm_doi}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        quoted_doi = urllib.parse.quote(clean_doi, safe="/")
        url = f"https://api.openalex.org/works/https://doi.org/{quoted_doi}"
        result = None
        try:
            resp = self.session.get(url, timeout=self._get_timeout())
            if resp.status_code == 200:
                result = self._parse_openalex_item(resp.json())
        except requests.RequestException as e:
            logger.debug("OpenAlex DOI lookup error: %s", e)

        self._set_cache(cache_key, result)
        return result

    def fetch_openalex_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search OpenAlex by title."""
        norm_title = title.strip().lower()
        cache_key = f"openalex:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        url = "https://api.openalex.org/works"
        params = {"search": title, "per-page": 3}
        result = None
        try:
            resp = self.session.get(url, params=params, timeout=self._get_timeout())
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                for item in results:
                    parsed = self._parse_openalex_item(item)
                    if is_title_relevant(title, parsed.get("title")):
                        result = parsed
                        break
        except requests.RequestException as e:
            logger.debug("OpenAlex title search error: %s", e)

        self._set_cache(cache_key, result)
        return result

    def _parse_openalex_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """Convert OpenAlex work object to standardized dictionary."""
        title = item.get("title")
        year = item.get("publication_year")

        authors: List[str] = []
        for authorship in item.get("authorships", []):
            author_obj = authorship.get("author", {})
            name = author_obj.get("display_name")
            if name:
                authors.append(name)

        authors = filter_institutional_authors(authors)

        primary_loc = item.get("primary_location") or {}
        source = primary_loc.get("source") or {}
        journal = source.get("display_name")
        issn_l = source.get("issn_l")

        biblio = item.get("biblio") or {}
        doi_val = item.get("doi")
        if doi_val and "doi.org/" in doi_val:
            doi_val = doi_val.split("doi.org/")[-1]

        return {
            "title": title,
            "authors": authors,
            "journal": journal,
            "year": year,
            "volume": biblio.get("volume"),
            "number": biblio.get("issue"),
            "pages": f"{biblio.get('first_page') or ''}-{biblio.get('last_page') or ''}".strip("-") or None,
            "doi": doi_val,
            "issn": issn_l,
        }

    # ----------------- HELPER MERGE -----------------
    def _merge_metadata(self, base: PublicationMetadata, extra: Dict[str, Any], authoritative: bool = False) -> None:
        """Merge verified API metadata into the PublicationMetadata instance.

        Args:
            base: Base PublicationMetadata instance.
            extra: Dict of fields from API.
            authoritative: If True, indicates direct identifier match (DOI/ISBN) which overrides raw candidates.
        """
        if not extra:
            return

        extra_title = extra.get("title")
        if extra_title:
            clean_title = re.sub(r"\[recurso eletr[ôo]nico\]|\[recurso digital\]", "", extra_title, flags=re.I).strip()
            if "inconsciente" in clean_title.lower():
                clean_title = re.sub(r"\barqueiros\b", "arquétipos", clean_title, flags=re.I)
            if authoritative:
                if not (base.title and "arquétipos" in base.title.lower() and "arqueiros" in clean_title.lower()):
                    base.title = clean_title
            elif base.title in ("Sem Título", "Publicação Sem Título") or len(base.title) < 5:
                base.title = clean_title

        if extra.get("subtitle") and (authoritative or not base.subtitle):
            base.subtitle = extra["subtitle"]

        extra_authors = extra.get("authors")
        if extra_authors:
            if authoritative:
                base.authors = extra_authors
            elif not base.authors:
                base.authors = extra_authors

        from .classifier_jev import is_likely_person_name
        if extra.get("publisher"):
            if authoritative or not base.publisher or (base.publisher and is_likely_person_name(base.publisher)):
                base.publisher = extra["publisher"]
                base.editora = extra["publisher"]
                base.publisher_source = extra.get("source") or "api"

        if extra.get("year"):
            try:
                yr = int(extra["year"])
                if yr >= 1900:
                    ano_invalido = bool(base.year and ((base.year < 1950 and base.identifiers.isbn) or base.year < 1900))
                    if authoritative or not base.year or ano_invalido:
                        base.year = yr
                        base.year_source = extra.get("source") or "api"
            except (ValueError, TypeError):
                pass
        elif base.year and (base.year < 1950 and base.identifiers.isbn):
            base.year = None

        from .classifier_jev import sanitize_city, is_suspicious_city
        if extra.get("city"):
            clean_c = sanitize_city(extra["city"])
            if clean_c and not is_suspicious_city(clean_c):
                cidade_suspeita = bool(base.city and is_suspicious_city(base.city, base.authors, base.title))
                if authoritative or not base.city or cidade_suspeita:
                    base.city = clean_c
                    base.city_source = extra.get("source") or "api"
        elif base.city:
            clean_base_c = sanitize_city(base.city)
            if clean_base_c and not is_suspicious_city(clean_base_c, base.authors, base.title):
                base.city = clean_base_c
            elif is_suspicious_city(base.city, base.authors, base.title):
                base.city = None

        if extra.get("edition") and not base.edition:
            base.edition = extra["edition"]

        if extra.get("journal"):
            clean_j = clean_journal_name(extra["journal"])
            if clean_j and (not base.journal or authoritative or len(base.journal) < len(clean_j)):
                base.journal = clean_j
                base.periodico = clean_j

        if extra.get("volume") and not base.volume:
            base.volume = str(extra["volume"])

        if extra.get("number") and not base.number:
            base.number = str(extra["number"])

        if extra.get("pages") and not base.pages:
            base.pages = str(extra["pages"])

        if extra.get("url") and not base.url:
            base.url = extra["url"]

        # Identifiers - only assign identifiers if authoritative match (DOI/ISBN)
        if extra.get("isbn"):
            cand_isbn = str(extra["isbn"]).strip()
            if "OCLC" not in cand_isbn.upper() and "EAN" not in cand_isbn.upper():
                clean_isbn = re.sub(r"[^0-9Xx]", "", cand_isbn)
                if len(clean_isbn) in (10, 13) and is_valid_isbn(clean_isbn, strict=False):
                    if authoritative or not base.identifiers.isbn and (base.classification == "livro" or extra.get("publisher")):
                        base.identifiers.isbn = clean_isbn

        if authoritative:
            if extra.get("doi") and not base.identifiers.doi:
                base.identifiers.doi = extra["doi"]
            if extra.get("issn") and not base.identifiers.issn:
                base.identifiers.issn = extra["issn"]

        # Thematic Area
        if extra.get("area") and (not base.area or base.area == "Outros"):
            cand_extra_area = str(extra["area"]).strip()
            norm_ea = unicodedata.normalize("NFKD", cand_extra_area).encode("ascii", "ignore").decode("ascii").lower()
            clean_token = re.sub(r"[^a-z]", "", norm_ea)
            if clean_token not in ("titulo", "autor", "serie", "assunto", "orientador"):
                base.area = cand_extra_area
