"""ABNT Reference Formatter (NBR 6023:2018).

Receives consolidated metadata and generates standardized ABNT citations.
Author rules:
- Up to 3 authors: list all authors separated by semicolon.
- 4 or more authors: list the first author followed by 'et al.'
- No author: entry by title with the first word capitalized in uppercase.
"""

from __future__ import annotations

import re
from typing import List, Optional
from .models import PublicationMetadata, PublicationType

# Known agnomes / name suffixes
NAME_SUFFIXES = {"filho", "junior", "júnior", "neto", "sobrinho", "segundo", "terceiro", "iv"}
PREPOSITIONS = {"da", "de", "do", "das", "dos", "e", "van", "von", "del", "della"}


def format_single_author_abnt(author_name: str) -> str:
    """Format an author's name into ABNT format: LASTNAME, Firstnames.

    Examples:
        'João da Silva' -> 'SILVA, João da'
        'Carlos Alberto Souza Júnior' -> 'SOUZA JÚNIOR, Carlos Alberto'
        'SILVA, Maria' -> 'SILVA, Maria'
        'Professor: Marcelo Andrade' -> 'ANDRADE, Marcelo'
        'Fabiana Follador e Ambrosio' -> 'FOLLADOR E AMBROSIO, Fabiana'
    """
    clean = author_name.strip()
    if not clean:
        return ""

    # Strip prefixes like 'Professor:', 'Prof.', 'Dr.', 'Dra.', 'Organizadores,', etc.
    clean = re.sub(
        r"^(?:prof(?:essor|a)?|dr[a-z]?|ph\.?d\.?|msc\.?|coord(?:enador)?|orientador[a-z]?|organizad(?:or|ores)[a-z]?)[:\.\s]+",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip()
    clean = clean.strip("\"'“”«»–- ")

    # If already in 'LASTNAME, Firstnames' format, validate uppercase
    if "," in clean:
        parts = clean.split(",", 1)
        last = parts[0].strip().upper()
        rest = parts[1].strip()
        return f"{last}, {rest}" if rest else last

    tokens = clean.split()
    if len(tokens) == 1:
        return tokens[0].upper()

    # Detect compound surnames connected by 'e' (ex.: 'Fabiana Follador e Ambrosio')
    if len(tokens) >= 3 and tokens[-2].lower() == "e" and tokens[-3].lower() not in PREPOSITIONS:
        last_compound = f"{tokens[-3]} e {tokens[-1]}".upper()
        first_names = " ".join(tokens[:-3])
        return f"{last_compound}, {first_names}" if first_names else last_compound

    # Check for agnomes / suffixes (e.g. Junior, Filho, Neto)
    if tokens[-1].lower() in NAME_SUFFIXES and len(tokens) >= 2:
        last = f"{tokens[-2]} {tokens[-1]}".upper()
        first_names = " ".join(tokens[:-2])
    else:
        last = tokens[-1].upper()
        first_names = " ".join(tokens[:-1])

    return f"{last}, {first_names}" if first_names else last


def format_authors_abnt(authors: List[str]) -> str:
    """Format a list of authors following ABNT NBR 6023 rules.

    - 1 to 3 authors: all listed, separated by semicolon.
    - 4 or more: first author followed by 'et al.'
    - 'et al.' in raw input triggers truncated citation.
    """
    if not authors:
        return ""

    # Clean empty authors or placeholder tokens
    clean_authors = [
        a.strip() for a in authors
        if a.strip() and not re.search(r"^(?:et\s+al\.?|\[et\s+al\.?\]|\.\.\.|organizad|coord)", a.strip(), re.I)
    ]
    if not clean_authors:
        return ""

    has_raw_et_al = (
        len(authors) > len(clean_authors)
        or any(re.search(r"\bet\s+al\b", a, re.I) for a in authors)
    )

    if len(clean_authors) >= 4 or has_raw_et_al:
        first = format_single_author_abnt(clean_authors[0])
        first = re.sub(r"\s+et\s+al\.?$", "", first, flags=re.I).strip()
        return f"{first} et al."

    formatted = [format_single_author_abnt(a) for a in clean_authors]
    return "; ".join(formatted)


class ABNTFormatter:
    """Formatter for ABNT NBR 6023 citations across different publication types."""

    @classmethod
    def format(cls, metadata: PublicationMetadata) -> str:
        """Route metadata to specific citation template based on classification."""
        classification = metadata.classification.lower() if metadata.classification else "outros"

        if classification in ("artigo", "artigo_cientifico"):
            res = cls._format_scientific_article(metadata)
        elif classification in ("tese", "dissertacao_tese"):
            res = cls._format_thesis(metadata)
        elif classification == "livro":
            res = cls._format_book(metadata)
        elif classification == "revista":
            res = cls._format_magazine(metadata)
        elif classification == "apostila":
            res = cls._format_courseware(metadata)
        else:
            res = cls._format_generic(metadata)

        # Post-processing cleanup: avoid double periods (e.g. '1. ed..' -> '1. ed.') and extra spaces
        res = re.sub(r"\.{2,}", ".", res)
        res = re.sub(r"\s{2,}", " ", res)
        return res.strip()

    @classmethod
    def _format_thesis(cls, m: PublicationMetadata) -> str:
        """Format thesis / dissertation reference (ABNT NBR 6023:2018):
        AUTHOR(S). **Title**: subtitle. Institution, City, Year.
        """
        authors_str = format_authors_abnt(m.authors)
        title_part = cls._build_title_part(m.title, m.subtitle, bold_main=True, has_authors=bool(authors_str))

        parts = []
        if authors_str:
            parts.append(f"{authors_str}.")

        parts.append(f"{title_part}.")

        year_str = str(m.year) if m.year else "[20--]"
        institution = m.publisher or "[Instituição não identificada]"
        city = m.city or "[S. l.]"

        parts.append(f"Tese / Dissertação – {institution}, {city}, {year_str}.")

        if m.identifiers.doi:
            doi_val = m.identifiers.doi
            if not doi_val.startswith("http"):
                doi_val = f"https://doi.org/{doi_val}"
            parts.append(f"Disponível em: <{doi_val}>.")

        return " ".join(parts)

    @classmethod
    def _format_book(cls, m: PublicationMetadata) -> str:
        """Format book reference:
        AUTHOR(S). **Title**: subtitle. Edition. City: Publisher, Year.
        """
        authors_str = format_authors_abnt(m.authors)
        title_part = cls._build_title_part(m.title, m.subtitle, bold_main=True, has_authors=bool(authors_str))

        parts = []
        if authors_str:
            parts.append(f"{authors_str}.")

        parts.append(f"{title_part}.")

        if m.edition:
            ed_str = m.edition if "ed." in m.edition.lower() else f"{m.edition} ed."
            parts.append(f"{ed_str}.")

        city = m.city or "[S. l.]"
        publisher = m.publisher or "[s. n.]"
        year_str = str(m.year) if m.year else "[20--]"
        parts.append(f"{city}: {publisher}, {year_str}.")

        return " ".join(parts)

    @classmethod
    def _format_scientific_article(cls, m: PublicationMetadata) -> str:
        """Format scientific article reference:
        AUTHOR(S). Article title: subtitle. **Journal Name**, City, v. X, n. Y, p. P-P, year. DOI: ...
        """
        authors_str = format_authors_abnt(m.authors)
        # In ABNT, the article title is in regular text, journal name is highlighted (bold)
        article_title = m.title.strip()
        if m.subtitle:
            article_title = f"{article_title}: {m.subtitle.strip()}"

        parts = []
        if authors_str:
            parts.append(f"{authors_str}.")
            parts.append(f"{article_title}.")
        else:
            # First word uppercase if no author
            tokens = article_title.split(" ", 1)
            first = tokens[0].upper()
            rest = f" {tokens[1]}" if len(tokens) > 1 else ""
            parts.append(f"{first}{rest}.")

        journal = m.journal or m.publisher
        if journal:
            journal_bold = f"**{journal}**"
            loc_parts = [journal_bold]
        else:
            loc_parts = ["[S. l.: s. n.]"]

        if m.city:
            loc_parts.append(m.city)
        if m.volume:
            vol_str = m.volume if "v." in m.volume.lower() else f"v. {m.volume}"
            loc_parts.append(vol_str)
        if m.number:
            num_str = m.number if "n." in m.number.lower() else f"n. {m.number}"
            loc_parts.append(num_str)
        if m.pages:
            pg_str = m.pages if "p." in m.pages.lower() else f"p. {m.pages}"
            loc_parts.append(pg_str)

        year_str = str(m.year) if m.year else "[20--]"
        loc_parts.append(year_str)

        parts.append(", ".join(loc_parts) + ".")

        if m.identifiers.doi:
            doi_val = m.identifiers.doi
            if not doi_val.startswith("http"):
                doi_val = f"https://doi.org/{doi_val}"
            parts.append(f"Disponível em: <{doi_val}>.")

        return " ".join(parts)

    @classmethod
    def _format_magazine(cls, m: PublicationMetadata) -> str:
        """Format magazine article/issue reference."""
        authors_str = format_authors_abnt(m.authors)
        title_str = m.title.strip()
        if m.subtitle:
            title_str = f"{title_str}: {m.subtitle.strip()}"

        magazine_name = m.journal or m.publisher or "Revista"
        parts = []
        if authors_str:
            parts.append(f"{authors_str}.")
            parts.append(f"{title_str}.")
        else:
            tokens = title_str.split(" ", 1)
            first = tokens[0].upper()
            rest = f" {tokens[1]}" if len(tokens) > 1 else ""
            parts.append(f"{first}{rest}.")

        parts.append(f"**{magazine_name}**,")
        city = m.city or "[S. l.]"
        year_str = str(m.year) if m.year else "[20--]"
        parts.append(f"{city}, {year_str}.")

        return " ".join(parts)

    @classmethod
    def _format_courseware(cls, m: PublicationMetadata) -> str:
        """Format courseware / apostila reference:
        AUTHOR(S). **Title**: subtitle. City: Institution, Year. (Apostila).
        """
        authors_str = format_authors_abnt(m.authors)
        title_part = cls._build_title_part(m.title, m.subtitle, bold_main=True, has_authors=bool(authors_str))

        parts = []
        if authors_str:
            parts.append(f"{authors_str}.")

        parts.append(f"{title_part}.")

        if m.edition:
            ed_str = m.edition if "ed." in m.edition.lower() else f"{m.edition} ed."
            parts.append(f"{ed_str}.")

        city = m.city or "[S. l.]"
        institution = m.publisher or "Instituição de Ensino"
        year_str = str(m.year) if m.year else "[20--]"
        parts.append(f"{city}: {institution}, {year_str}.")
        parts.append("(Apostila).")

        return " ".join(parts)

    @classmethod
    def _format_generic(cls, m: PublicationMetadata) -> str:
        """Format generic document."""
        authors_str = format_authors_abnt(m.authors)
        title_part = cls._build_title_part(m.title, m.subtitle, bold_main=True, has_authors=bool(authors_str))

        parts = []
        if authors_str:
            parts.append(f"{authors_str}.")
        parts.append(f"{title_part}.")

        city = m.city or "[S. l.]"
        publisher = m.publisher or "[s. n.]"
        year_str = str(m.year) if m.year else "[20--]"
        parts.append(f"{city}: {publisher}, {year_str}.")

        return " ".join(parts)

    @staticmethod
    def _build_title_part(title: str, subtitle: Optional[str], bold_main: bool, has_authors: bool) -> str:
        clean_title = title.strip()
        if not has_authors:
            # If no author, first word in uppercase
            tokens = clean_title.split(" ", 1)
            first = tokens[0].upper()
            rest = f" {tokens[1]}" if len(tokens) > 1 else ""
            clean_title = f"{first}{rest}"

        if bold_main:
            main_part = f"**{clean_title}**"
        else:
            main_part = clean_title

        if subtitle and subtitle.strip():
            return f"{main_part}: {subtitle.strip()}"
        return main_part


def format_abnt_reference(metadata: PublicationMetadata) -> str:
    """Função utilitária para formatar a referência ABNT a partir de Metadados."""
    return ABNTFormatter.format(metadata)
