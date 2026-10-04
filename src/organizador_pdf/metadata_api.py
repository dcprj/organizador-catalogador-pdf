"""Metadata Enrichment Service.

Queries Crossref, Google Books, OpenLibrary, Brasil API (CBL), and OpenAlex public APIs
to fetch and cross-reference publication metadata.

Includes fallback logic: If Jev does not find identifiers or yields probability < 0.95,
executes fallback search by title across the APIs with strict relevance and author filtering.
"""

import os
import re
import logging
from typing import Optional, List, Dict, Any, Set
import requests
from dotenv import load_dotenv

from .models import (
    PublicationMetadata,
    Identifiers,
    JevValidationResult,
    PublicationType,
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


def is_author_compatible(candidate_authors: List[str], retrieved_authors: List[str]) -> bool:
    """Validate that candidate authors and retrieved API authors are compatible.

    If no candidate authors are known, any retrieved author is acceptable.
    If candidate authors are known, at least one key name token (e.g. surname) must match.
    """
    if not candidate_authors or not retrieved_authors:
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


class MetadataEnricher:
    """Consolidates metadata by querying public academic, book, and national registry APIs."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, online: bool = True):
        self.timeout = timeout
        self.online = online
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.google_books_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")

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

        doi = candidates.doi
        isbn = candidates.isbn
        issn = candidates.issn
        title_candidate = candidates.raw_title

        doi_prob = probabilities.get("doi", 0.0)
        isbn_prob = probabilities.get("isbn", 0.0)

        # Baseline metadata initialized with candidate extraction
        metadata = PublicationMetadata(
            title=title_candidate or "Publicação Sem Título",
            subtitle=candidates.raw_subtitle,
            authors=candidates.raw_authors or [],
            publisher=candidates.raw_publisher,
            edition=candidates.raw_edition,
            year=candidates.raw_year,
            city=candidates.raw_city,
            area=candidates.raw_area,
            classification=jev_result.classification,
            identifiers=Identifiers(doi=doi, isbn=isbn, issn=issn),
        )

        if not self.online:
            logger.info("Enriquecimento online desativado (modo offline). Mantendo metadados candidatos.")
            return metadata

        sources_consulted: List[str] = []

        # 1. Query by DOI if present (direct authoritative identifier lookup)
        if doi:
            logger.info("DOI found (%s, prob=%.2f). Querying Crossref & OpenAlex.", doi, doi_prob)
            cr_meta = self.fetch_crossref_by_doi(doi)
            if cr_meta:
                self._merge_metadata(metadata, cr_meta, authoritative=True)
                sources_consulted.append("Crossref (DOI)")

            oa_meta = self.fetch_openalex_by_doi(doi)
            if oa_meta:
                self._merge_metadata(metadata, oa_meta, authoritative=True)
                sources_consulted.append("OpenAlex (DOI)")

        # 2. Query by ISBN if present (direct authoritative identifier lookup)
        if isbn:
            logger.info("ISBN found (%s, prob=%.2f). Querying Brasil API, Google Books & OpenLibrary.", isbn, isbn_prob)
            br_meta = self.fetch_brasil_api_isbn(isbn)
            if br_meta:
                self._merge_metadata(metadata, br_meta, authoritative=True)
                sources_consulted.append("Brasil API / CBL (ISBN)")

            gb_meta = self.fetch_google_books_by_isbn(isbn)
            if gb_meta:
                self._merge_metadata(metadata, gb_meta, authoritative=not bool(br_meta))
                sources_consulted.append("Google Books (ISBN)")

            ol_meta = self.fetch_openlibrary_by_isbn(isbn)
            if ol_meta:
                self._merge_metadata(metadata, ol_meta, authoritative=not bool(br_meta or gb_meta))
                sources_consulted.append("OpenLibrary (ISBN)")

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

        metadata.source_apis = sources_consulted

        # Evaluate needs_review (quarantine threshold)
        if jev_result.classification_confidence < 0.50 and not sources_consulted and not metadata.identifiers.isbn and not metadata.identifiers.doi:
            metadata.needs_review = True
            metadata.review_reasons.append("Baixa confiança na classificação (System One) e nenhum identificador confirmado nas bases públicas")

        if metadata.title in ("Sem Título", "Publicação Sem Título") or len(metadata.title) < 4:
            metadata.needs_review = True
            metadata.review_reasons.append("Documento sem título bibliográfico identificável")

        return metadata

    def _apply_title_fallback(
        self,
        title: str,
        classification: PublicationType,
        metadata: PublicationMetadata,
        sources_consulted: List[str],
    ) -> None:
        """Search APIs by title according to publication type priority with relevance and author checks."""
        candidate_authors = metadata.authors or []

        if classification in ("artigo", "artigo_cientifico"):
            # Priority: Crossref -> OpenAlex
            cr_meta = self.fetch_crossref_by_title(title)
            if (
                cr_meta
                and is_title_relevant(title, cr_meta.get("title"))
                and is_author_compatible(candidate_authors, cr_meta.get("authors", []))
            ):
                self._merge_metadata(metadata, cr_meta, authoritative=False)
                sources_consulted.append("Crossref (Title Search)")
            else:
                oa_meta = self.fetch_openalex_by_title(title)
                if (
                    oa_meta
                    and is_title_relevant(title, oa_meta.get("title"))
                    and is_author_compatible(candidate_authors, oa_meta.get("authors", []))
                ):
                    self._merge_metadata(metadata, oa_meta, authoritative=False)
                    sources_consulted.append("OpenAlex (Title Search)")

        elif classification in ("livro", "apostila"):
            # Priority: Google Books -> OpenLibrary
            gb_meta = self.fetch_google_books_by_title(title)
            if (
                gb_meta
                and is_title_relevant(title, gb_meta.get("title"))
                and is_author_compatible(candidate_authors, gb_meta.get("authors", []))
            ):
                self._merge_metadata(metadata, gb_meta, authoritative=False)
                sources_consulted.append("Google Books (Title Search)")
            else:
                ol_meta = self.fetch_openlibrary_by_title(title)
                if (
                    ol_meta
                    and is_title_relevant(title, ol_meta.get("title"))
                    and is_author_compatible(candidate_authors, ol_meta.get("authors", []))
                ):
                    self._merge_metadata(metadata, ol_meta, authoritative=False)
                    sources_consulted.append("OpenLibrary (Title Search)")

        elif classification == "tese":
            # Priority: OpenAlex -> Crossref -> Google Books
            oa_meta = self.fetch_openalex_by_title(title)
            if (
                oa_meta
                and is_title_relevant(title, oa_meta.get("title"))
                and is_author_compatible(candidate_authors, oa_meta.get("authors", []))
            ):
                self._merge_metadata(metadata, oa_meta, authoritative=False)
                sources_consulted.append("OpenAlex (Title Search)")
            else:
                cr_meta = self.fetch_crossref_by_title(title)
                if (
                    cr_meta
                    and is_title_relevant(title, cr_meta.get("title"))
                    and is_author_compatible(candidate_authors, cr_meta.get("authors", []))
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
                and is_author_compatible(candidate_authors, gb_meta.get("authors", []))
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
                    and is_author_compatible(candidate_authors, ol_meta.get("authors", []))
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
                        and is_author_compatible(candidate_authors, cr_meta.get("authors", []))
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
                            and is_author_compatible(candidate_authors, oa_meta.get("authors", []))
                        ):
                            self._merge_metadata(metadata, oa_meta, authoritative=False)
                            sources_consulted.append("OpenAlex (Title Search)")
                            if oa_meta.get("journal") or oa_meta.get("doi"):
                                metadata.classification = "artigo"

    # ----------------- BRASIL API (CBL) -----------------
    def fetch_brasil_api_isbn(self, isbn: str) -> Optional[Dict[str, Any]]:
        """Fetch official Brazilian ISBN registration data from Brasil API (CBL / Mercado Editorial)."""
        clean_isbn = re.sub(r"[^0-9X]", "", isbn)
        url = f"https://brasilapi.com.br/api/isbn/v1/{clean_isbn}"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                raw_title = data.get("title")
                if not raw_title:
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

                return {
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
        return None

    # ----------------- CROSSREF API -----------------
    def fetch_crossref_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """Fetch metadata from Crossref API by DOI."""
        url = f"https://api.crossref.org/works/{doi}"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                item = resp.json().get("message", {})
                return self._parse_crossref_item(item)
        except requests.RequestException as e:
            logger.debug("Crossref DOI lookup error: %s", e)
        return None

    def fetch_crossref_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search Crossref API by title."""
        url = "https://api.crossref.org/works"
        params = {"query.title": title, "rows": 3}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                items = resp.json().get("message", {}).get("items", [])
                for item in items:
                    parsed = self._parse_crossref_item(item)
                    if is_title_relevant(title, parsed.get("title")):
                        return parsed
        except requests.RequestException as e:
            logger.debug("Crossref title search error: %s", e)
        return None

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
        url = "https://www.googleapis.com/books/v1/volumes"
        params = {"q": f"isbn:{isbn}"}
        return self._query_google_books(url, params)

    def fetch_google_books_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search Google Books API by title."""
        url = "https://www.googleapis.com/books/v1/volumes"
        params = {"q": f"intitle:{title}", "maxResults": 3}
        return self._query_google_books(url, params)

    def _query_google_books(self, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            req_params = dict(params)
            if self.google_books_api_key:
                req_params["key"] = self.google_books_api_key
            resp = self.session.get(url, params=req_params, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    # Select the item with the most complete metadata (publisher and publishedDate)
                    best_item = items[0]
                    for it in items:
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
        url = f"https://openlibrary.org/isbn/{isbn}.json"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "title": data.get("title"),
                    "subtitle": data.get("subtitle"),
                    "publisher": data.get("publishers", [None])[0] if data.get("publishers") else None,
                    "year": int(data["publish_date"][-4:]) if data.get("publish_date") and data["publish_date"][-4:].isdigit() else None,
                    "isbn": isbn,
                }
        except requests.RequestException as e:
            logger.debug("OpenLibrary ISBN error: %s", e)
        return None

    def fetch_openlibrary_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search OpenLibrary API by title."""
        url = "https://openlibrary.org/search.json"
        params = {"q": title, "limit": 5}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                docs = resp.json().get("docs", [])
                for doc in docs:
                    doc_title = doc.get("title")
                    if is_title_relevant(title, doc_title):
                        isbns = doc.get("isbn", [])
                        return {
                            "title": doc_title,
                            "authors": doc.get("author_name", []),
                            "publisher": doc.get("publisher", [None])[0] if doc.get("publisher") else None,
                            "year": doc.get("first_publish_year"),
                            "isbn": isbns[0] if isbns else None,
                        }
        except requests.RequestException as e:
            logger.debug("OpenLibrary title search error: %s", e)
        return None

    # ----------------- OPENALEX API -----------------
    def fetch_openalex_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """Fetch publication metadata from OpenAlex by DOI."""
        url = f"https://api.openalex.org/works/https://doi.org/{doi}"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                return self._parse_openalex_item(resp.json())
        except requests.RequestException as e:
            logger.debug("OpenAlex DOI lookup error: %s", e)
        return None

    def fetch_openalex_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search OpenAlex by title."""
        url = "https://api.openalex.org/works"
        params = {"search": title, "per-page": 3}
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                for item in results:
                    parsed = self._parse_openalex_item(item)
                    if is_title_relevant(title, parsed.get("title")):
                        return parsed
        except requests.RequestException as e:
            logger.debug("OpenAlex title search error: %s", e)
        return None

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
            if authoritative:
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

        if extra.get("publisher"):
            if authoritative or not base.publisher:
                base.publisher = extra["publisher"]

        if extra.get("year"):
            if authoritative or not base.year:
                base.year = extra["year"]

        if extra.get("city") and not base.city:
            base.city = extra["city"]

        if extra.get("edition") and not base.edition:
            base.edition = extra["edition"]

        if extra.get("journal"):
            clean_j = clean_journal_name(extra["journal"])
            if clean_j and (not base.journal or authoritative or len(base.journal) < len(clean_j)):
                base.journal = clean_j

        if extra.get("volume") and not base.volume:
            base.volume = str(extra["volume"])

        if extra.get("number") and not base.number:
            base.number = str(extra["number"])

        if extra.get("pages") and not base.pages:
            base.pages = str(extra["pages"])

        if extra.get("url") and not base.url:
            base.url = extra["url"]

        # Identifiers
        if extra.get("doi") and not base.identifiers.doi:
            base.identifiers.doi = extra["doi"]
        if extra.get("isbn") and not base.identifiers.isbn:
            base.identifiers.isbn = extra["isbn"]
        if extra.get("issn") and not base.identifiers.issn:
            base.identifiers.issn = extra["issn"]

        # Thematic Area
        if extra.get("area") and not base.area:
            base.area = extra["area"]
