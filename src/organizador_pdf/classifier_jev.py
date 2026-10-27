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
    CandidatoMetadado,
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

# Robust ISBN: Suporta rótulo I.S.B.N., ISBN-10, ISBN-13, quebras de linha e especificadores como (impresso), (e-book)
ISBN_REGEX = re.compile(
    r"(?i)\b(?:I[\.\s]*S[\.\s]*B[\.\s]*N[\.\s]*(?:-(?:1[03]))?(?:\s*\([^)\n\r]{1,40}\))?[:\s\n\r]*([0-9Xx][0-9Xx \t\-–—]{8,22}[0-9Xx])|(97[89][ \t\-–—]+[0-9]{1,5}[ \t\-–—]+[0-9]+[ \t\-–—]+[0-9]+[ \t\-–—]+[0-9Xx]))\b"
)

# Robust ISSN: Suporta e-ISSN, p-ISSN, ISSN-L, ISSN online/impresso, quebras de linha e espaçamentos
ISSN_REGEX = re.compile(
    r"(?i)\b(?:[ep]-?ISSN|ISSN-L|ISSN)(?:\s*\([^)\n\r]{1,40}\))?[:\s\n\r]*(\d{4}[-\s–—]?\d{3}[\dX])\b"
)


def extrair_isbn_confiavel(texto: str) -> Optional[Tuple[str, bool]]:
    """Extrai ISBN de texto suportando variações de rotulação, hifenação unicode e quebras de linha.

    Retorna tupla (isbn_limpo_13_ou_10, is_strict_checksum).
    """
    if not texto:
        return None

    norm_txt = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", texto)
    norm_txt = norm_txt.replace("\xa0", " ")

    candidatos_validos: List[Tuple[str, bool]] = []

    # 1. Busca por padrões rotulados ou sequências estruturadas
    for m in ISBN_REGEX.finditer(norm_txt):
        raw_val = m.group(1) or m.group(2)
        if not raw_val:
            continue
        clean = re.sub(r"[^0-9Xx]", "", raw_val).upper()
        if len(clean) in (10, 13):
            if is_valid_isbn(clean, strict=True):
                candidatos_validos.append((clean, True))
            elif is_valid_isbn(clean, strict=False):
                candidatos_validos.append((clean, False))

    # 2. Busca secundária por sequências de 13 dígitos 978/979 contínuas
    if not candidatos_validos:
        for m in re.finditer(r"\b(97[89][0-9]{10})\b", norm_txt):
            clean = m.group(1)
            if is_valid_isbn(clean, strict=True):
                candidatos_validos.append((clean, True))
            elif is_valid_isbn(clean, strict=False):
                candidatos_validos.append((clean, False))

    if not candidatos_validos:
        return None

    # Prioriza o que passa no checksum estrito
    for cand, strict_pass in candidatos_validos:
        if strict_pass:
            return cand, True
    return candidatos_validos[0]


def extrair_issn_confiavel(texto: str) -> Optional[str]:
    """Extrai ISSN de texto suportando e-ISSN, p-ISSN, ISSN-L e formatações variadas."""
    if not texto:
        return None
    norm_txt = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2212]", "-", texto)
    for m in ISSN_REGEX.finditer(norm_txt):
        raw_val = m.group(1)
        clean = re.sub(r"[^0-9Xx]", "", raw_val).upper()
        if len(clean) == 8:
            formatted = f"{clean[:4]}-{clean[4:]}"
            if is_valid_issn(clean, strict=True) or is_valid_issn(clean, strict=False):
                return formatted
    return None


def extrair_doi_confiavel(texto: str, is_thesis: bool = False) -> Optional[str]:
    """Extrai DOI sem descartar indevidamente o identificador em cabeçalhos de artigos científicos."""
    if not texto:
        return None

    ref_split = re.split(r"\n\s*(?:refer[êe]ncias|references|bibliografia|obras\s+citadas)\b", texto, flags=re.I)
    clean_text = ref_split[0] if ref_split else texto

    dois_encontrados = []
    for m in DOI_REGEX.finditer(clean_text):
        raw_doi = m.group(0).rstrip(".;,)")
        if raw_doi.endswith(")") and "(" not in raw_doi:
            raw_doi = raw_doi[:-1]
        norm_d = normalizar_doi(raw_doi)
        if not norm_d:
            continue

        start_pos = max(0, m.start() - 120)
        end_pos = min(len(clean_text), m.end() + 120)
        context = clean_text[start_pos:end_pos].lower()

        # Contexto estrito de citação bibliográfica de outra obra
        is_citation_context = any(
            re.search(pat, context) for pat in [
                r"\b(?:apud|citado\s+por|citado\s+em)\b",
                r"\b(?:recuperado\s+de|dispon[íi]vel\s+em|acesso\s+em)\s+https?://",
            ]
        )
        if is_citation_context:
            continue

        if is_thesis and not any(lbl in context for lbl in ["doi da tese", "doi da disserta", "doi do trabalho", "handle", "reposit"]):
            continue

        dois_encontrados.append(norm_d)

    return dois_encontrados[0] if dois_encontrados else None

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
    r"a\s+editora\s+.*\s+apresenta",
    r"comiss[ãa]o\s+respons[áa]vel",
    r"este\s+[ée]\s+um\s+trabalho\s+de\s+divulga[çc][ãa]o",
    r"divulga[çc][ãa]o\s+de\s+livros",
    r"cip[- ]brasil",
    r"cataloga[çc][ãa]o[- ]na[- ]fonte",
    r"dados\s+internacionais\s+de\s+cataloga[çc][ãa]o",
    r"^https?://",
    r"^doi[:\s]",
    r"^issn[:\s]",
    r"^isbn[:\s]",
    r"^(?:issn|isbn|doi)[:\s0-9Xx\-./]+$",
]

# Disclaimers and legal terms that should never be treated as titles
DISCLAIMER_TITLE_PATTERN = re.compile(
    r"(?:(?:©|\(c\)|copyright)\s*(?:\d{4})?.*|"
    r"\b\d{4}\s+.*(?:Editora|Ltda|S\.A\.|Publishing|Press)\b|"
    r"\b(?:todos\s+os\s+direitos|gostaríamos\s+de\s+homenagear|ao\s+público\s+brasileiro|"
    r"gerente\s+editorial|coordena[çc][ãa]o\s+editorial|assistente\s+editorial|"
    r"colaboraram\s+nesta\s+edi[çc][ãa]o)\b|"
    r"\b(?:dom[íi]nio\s+p[úu]blico|propriedade\s+intelectual|direitos?\s+autorais?|"
    r"direitos?\s+reservados?|termo(?:s)?\s+de\s+uso|licen[çc]a\s+de\s+uso|creative\s+commons|"
    r"reprodu[çc][ãa]o\s+proibida|distribui[çc][ãa]o\s+gratuita|elivros|odinright|"
    r"compra\s+futura|fim\s+exclusivo|presente\s+obra|disponibilizada\s+pela|"
    r"totalmente\s+gratuita|acreditar\s+que\s+o\s+conhecimento|conhecimento\s+e\s+a\s+educa|"
    r"dados\s+internacionais\s+de\s+cataloga[çc][ãa]o|ficha\s+catalogr[áa]fica|"
    r"cip[- ]brasil|cataloga[çc][ãa]o[- ]na[- ]fonte|comiss[ãa]o\s+respons[áa]vel|"
    r"a\s+editora\s+.*\s+apresenta|este\s+[ée]\s+um\s+trabalho\s+de\s+divulga[çc][ãa]o)\b)",
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
    r"Diagrama[çc][ãa]o(?:\s*:)?|Edi[çc][ãa]o\s+de\s+texto|Editora\s+[A-Z]|"
    r"Colaboraram\s+nesta\s+edi[çc][ãa]o|Gerente\s+editorial|Coordenador[a-z]?\s+editorial|"
    r"Assistente\s+editorial|Prepara[çc][ãa]o\s+de\s+originais|Leitura\s+final|Produ[çc][ãa]o\s+digital)[:\s]",
    re.I,
)

GENERIC_SINGLE_WORD_TITLES = {
    "criar", "artigo", "livro", "capítulo", "capitulo", "introdução", "introducao",
    "conclusão", "conclusao", "resumo", "abstract", "prefácio", "prefacio",
    "sumário", "sumario", "editorial", "texto", "ensaios", "ensaio", "anexo",
}

COMMON_CITY_TYPOS = {
    "perropolis": "Petrópolis",
    "perrópolis": "Petrópolis",
    "petropolis": "Petrópolis",
    "sao paulo": "São Paulo",
    "rio de janeiro": "Rio de Janeiro",
    "belo horizonte": "Belo Horizonte",
    "brasilia": "Brasília",
    "porto alegre": "Porto Alegre",
    "curitiba": "Curitiba",
    "salvador": "Salvador",
    "recife": "Recife",
    "fortaleza": "Fortaleza",
    "nova york": "Nova York",
    "new york": "Nova York",
    "lisboa": "Lisboa",
    "porto": "Porto",
}

CITY_BOILERPLATE_REGEX = re.compile(
    r"\b(?:dover|pictorial|archive|series|cole[çc][ãa]o|s[ée]rie|edi[çc][ãa]o|tradu[çc][ãa]o|grafton|ilustra[çc][õo]es|volume|vol\.|capa)\b",
    re.I,
)

COMMON_NON_PERSON_NOUNS = {
    "livro", "livros", "psicologia", "introducao", "introdução", "fundamentos", "jornada",
    "ensaios", "ensaio", "historia", "história", "manual", "curso", "guia", "mapa",
    "obras", "completas", "teoria", "estudos", "estudo", "tipo", "tipos", "psicologicos",
    "psicológicos", "espirito", "espírito", "arte", "ciencia", "ciência", "arquetipos",
    "arquétipos", "inconsciente", "coletivo", "memoria", "memória", "memorias", "memórias",
    "sonhos", "sonho", "reflexoes", "reflexões", "personalidade", "desenvolvimento",
    "vida", "homem", "natureza", "alma", "ioga", "taro", "tarô", "sombra", "sentimentos",
    "pratica", "prática", "psicoterapia", "sincronicidade", "animus", "anima", "feminino",
}


def sanitize_city(city: Optional[str]) -> Optional[str]:
    """Sanitiza nomes de cidades removendo séries, nomes de arquivos, e corrigindo erros de OCR."""
    if not city:
        return None
    c = city.strip(" -—–:;,.\t\r\n\"'“”«»[]()")
    if not c or len(c) < 3:
        return None

    if "," in c:
        parts = [p.strip() for p in c.split(",") if p.strip()]
        clean_parts = [p for p in parts if not CITY_BOILERPLATE_REGEX.search(p)]
        if clean_parts:
            c = clean_parts[-1]
            if len(c) == 2 and len(clean_parts) > 1:
                c = f"{clean_parts[-2]}, {c}"
        else:
            c = parts[-1]

    c = CITY_BOILERPLATE_REGEX.sub("", c).strip(" -—–:;,.\t\r\n\"'“”«»[]()")

    norm_c = unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode("ascii").lower().strip()
    if norm_c in COMMON_CITY_TYPOS:
        c = COMMON_CITY_TYPOS[norm_c]
    elif norm_c.startswith("perropolis"):
        c = "Petrópolis" + c[len("perropolis"):]
    elif norm_c.startswith("petropolis"):
        c = "Petrópolis" + c[len("petropolis"):]
    elif norm_c.startswith("sao paulo"):
        c = "São Paulo" + c[len("sao paulo"):]

    return c if len(c) >= 3 else None


def is_likely_person_name(name_cand: str) -> bool:
    """Verifica se a string tem formato de nome de pessoa e não contém substantivos comuns de títulos."""
    cand = name_cand.strip(" -.,;:")
    words = cand.split()
    if not (1 <= len(words) <= 4):
        return False
    if any(unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode().lower() in COMMON_NON_PERSON_NOUNS for w in words):
        return False
    if len(words) == 1:
        return cand.title() in ("Jung", "Freud", "Lacan", "Adler", "Kant", "Nietzsche", "Platao", "Aristoteles", "Agostinho")
    if " e " in cand:
        cand_parts = [p.strip() for p in cand.split(" e ")]
        if len(cand_parts) == 2 and all(is_likely_person_name(p) or (len(p.split()) == 1 and p[0].isupper() and len(p) >= 3) for p in cand_parts):
            return True
    if "," in cand:
        sp = cand.split(",", 1)
        if len(sp) == 2 and len(sp[0].strip()) >= 2 and len(sp[1].strip()) >= 2:
            return True
    if re.match(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ\.\s]+\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+$", cand):
        return True
    if re.match(r"^(?:[A-Za-z]\.?\s*){1,3}[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+$", cand, re.I):
        return True
    if all(re.match(r"^[A-ZÁÉÍÓÚÂÊÔÃÕÇ][a-záéíóúâêôãõç]+$", w) for w in words if len(w) > 2):
        return True
    cand_lower = unicodedata.normalize("NFKD", cand).encode("ascii", "ignore").decode().lower().strip()
    if cand_lower in ("c.g. jung", "c. g. jung", "cg jung", "carl gustav jung", "carl jung", "sallie nichols", "murray stein", "judith harris"):
        return True
    return False


SUSPICIOUS_CITY_NAMES = {
    "jung", "freud", "lacan", "adler", "kant", "nietzsche", "marx", "hegel",
    "agostinho", "aquino", "platao", "aristoteles", "stein", "campbell",
    "foucault", "deleuze", "derrida", "habermas", "silva", "santos", "oliveira",
    "cultrix", "vozes", "zahar", "rocco", "companhia", "editora",
}


def is_suspicious_city(
    city: Optional[str],
    authors: Optional[List[str]] = None,
    title: Optional[str] = None,
) -> bool:
    """Verifica se a localidade é implausível, suspeita ou contaminada por nome de pessoa, autor ou assunto."""
    if not city:
        return False
    c = city.strip(" -—–:;,.\t\r\n\"'“”«»[]()")
    if len(c) < 3:
        return True
    norm_c = unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode("ascii").lower().strip()

    # Cidades conhecidas (ex: São Paulo, Rio de Janeiro, Petrópolis, etc.)
    if norm_c in COMMON_CITY_TYPOS or any(norm_c.startswith(kc) for kc in COMMON_CITY_TYPOS):
        return False

    if norm_c in SUSPICIOUS_CITY_NAMES:
        return True

    # Nomes com formato "Sobrenome, Nome"
    if "," in c:
        sp = c.split(",", 1)
        if len(sp) == 2 and len(sp[0].strip()) >= 2 and len(sp[1].strip()) >= 2:
            return True

    # Nomes biográficos específicos
    if norm_c in ("carl gustav jung", "c. g. jung", "cg jung", "carl gustav", "sigmund freud", "murray stein"):
        return True

    if any(norm_c == unicodedata.normalize("NFKD", a).encode("ascii", "ignore").decode("ascii").lower().strip() for a in (authors or [])):
        return True
    if authors:
        for a in authors:
            a_norm = unicodedata.normalize("NFKD", a).encode("ascii", "ignore").decode("ascii").lower()
            a_words = [w.strip() for w in re.split(r"[\s,]+", a_norm) if len(w.strip()) >= 3]
            if norm_c in a_words:
                return True
    if title:
        t_words = [w.strip() for w in re.split(r"[\s,;:]+", unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()) if len(w.strip()) >= 4]
        if norm_c in t_words and norm_c in ("jung", "freud", "lacan", "marx", "nietzsche"):
            return True
    if CITY_BOILERPLATE_REGEX.search(city):
        return True
    return False


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

    # 2. Remove marcas d'água de agregadores e sites piratas
    t = re.sub(r"\s*[-–—|]\s*(?:PDFDrive(?:\.com)?|Z-Library|Library Genesis|Libgen|Docero|Scribd|PDFRoom|OceanofPDF)\b.*$", "", t, flags=re.I).strip()

    # 3. Remove códigos de catalogação bibliográfica (CDD, CDU, Cutter) no início do título
    t = re.sub(r"^(?:CDD|CDU)\s*[:\-–\.]?\s*(?:\d{1,4}(?:\.\d+)?\s*[-–:]?\s*)?", "", t, flags=re.I)
    t = re.sub(r"^\d{1,4}(?:\.\d+)?\s*[-–:]\s*", "", t)
    t = re.sub(r"^[A-Z]\d{2,4}[a-z]?\s+[-–:]?\s*", "", t)

    # 4. Trunca antes de blocos maciços de créditos editoriais/ficha
    m_credit = CREDIT_SPLIT_PATTERN.search(t)
    if m_credit and m_credit.start() >= 4:
        t = t[:m_credit.start()].strip()

    # 5. Limpa pontuações periféricas residuais
    t = t.strip(" -—–:\t\r\n\"'“”«»[]")
    return t if len(t) >= 2 else None


def sanitize_subtitle_candidate(subtitle: Optional[str]) -> Optional[str]:
    """Sanitiza subtítulos removendo créditos editoriais, URLs e resíduos de diagramação."""
    if not subtitle:
        return None
    s = subtitle.strip()

    # 1. Trunca antes de blocos de créditos editoriais secundários
    m_credit = CREDIT_SPLIT_PATTERN.search(s)
    if m_credit:
        prefix = s[:m_credit.start()].strip()
        if is_likely_person_name(prefix):
            return None
        s = prefix

    # 2. Isola e remove URLs ou endereços web sem destruir trecho conceitual
    s = re.sub(r"https?://\S+|www\.\S+", "", s, flags=re.I).strip(" -—–:;,.\t\r\n\"'“”«»[]()|")

    # 3. Limpa pontuações periféricas
    s = s.strip(" -—–:\t\r\n\"'“”«»[]()")

    if len(s) < 3 or is_likely_person_name(s):
        return None

    return s


def is_prose_or_paragraph_title(text: Optional[str]) -> bool:
    """Verifica se o candidato a título é na verdade uma frase/parágrafo corrido de prosa ou introdução."""
    if not text:
        return False
    t = text.strip()
    words = t.split()
    if len(words) >= 15:
        return True
    if re.search(r"\.\s+[A-ZÀ-Ú]", t):  # Frases múltiplas encadeadas
        return True
    lower = t.lower()
    prose_markers = [
        "o tema deste livro", "este livro", "este artigo", "aquele período",
        "que se inicia", "embora não", "concentramo-nos", "neste trabalho",
        "o objetivo deste", "apresentamos aqui", "com o objetivo de",
        "experimental. embora",
        # Epígrafes, notas de agradecimento e disclaimers
        "segundo a filosofia", "existem quatro formas", "devem ser acessíveis",
        "incentivou este projeto", "ao leitor", "dedico este", "agradeço a",
        "agradecemos a", "à minha família", "à memória de", "colocando à disposição",
        "livros de maior influência", "do presente conteúdo",
    ]
    if any(m in lower for m in prose_markers):
        return True
    return False


def is_invalid_or_disclaimer_title(title: Optional[str]) -> bool:
    """Verifica se o candidato a título é claramente inválido, disclaimer ou identificador."""
    if not title:
        return True
    t = title.strip()
    if len(t) < 3:
        return True
    if is_prose_or_paragraph_title(t):
        return True
    # Rejeita ruído de scanner, CamScanner, marcas d'água e emails
    if re.search(r"\b(?:camscanner|scanned\s+by|digitalizado\s+por)\b", t, re.I):
        return True
    if re.search(r"[\w\.-]+@[\w\.-]+\.\w+", t):
        return True
    # Rejeita URLs, domínios e referências a sites/redes sociais
    if any(m in t.lower() for m in ("www.", "http://", "https://", ".com.br", ".com/", "@")):
        return True
    # Rejeita entidades editoriais/comerciais com ano (ex.: 'literare books international ltda, 2021.')
    if re.search(r"\b(?:editora|books|publishing|press|ltda|s\.?a\.?)\b.*?\b(19|20)\d{2}\b", t, re.I):
        return True
    # Rejeita rótulos da ficha catalográfica ou catalogação na fonte isolados
    if re.match(r"^\(?\s*(?:cip|cip-brasil|ficha\s+catalogr[áa]fica|cdd|cdu|dados\s+internacionais)\s*\)?$", t, re.I):
        return True
    # Rejeita 'Título original' e variações coladas
    if re.search(r"\b(?:t[íi]tulo\s*original|original\s*title)\b|^titulooriginal", t, re.I):
        return True
    # Rejeita perguntas retóricas soltas ou legendas (ex.: 'Qual sua interpretação do desenho?')
    if t.endswith("?") and any(t.lower().startswith(p) for p in ("qual ", "como ", "o que ", "por que ", "quem ", "onde ", "quando ")):
        return True
    # Volumes e tomos isolados (ex: 'Vol 16', 'Volume 2', 'Tomo 1')
    if re.match(r"^(?:vol(?:ume)?\.?\s*\d+|tomo\s*\d+|v\.\s*\d+|parte\s*\d+)$", t, re.I):
        return True
    # Rejeita títulos compostos quase exclusivamente por pontuação/ruído (ex.: '.... ,,...,,._,..')
    letters = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]", t)
    if len(letters) < 2:
        return True
    if len(t) >= 4 and (len(letters) / len(t)) < 0.35:
        return True
    # Palavras sem nenhuma vogal (OCR corrompido / ruído, ex: 'ntfc. tmn')
    vowels = re.findall(r"[aeiouyáéíóúâêîôûãõàèìòùäëïöü]", t, re.I)
    if len(t) >= 4 and len(letters) >= 4 and len(vowels) == 0:
        return True
    if t.lower() in GENERIC_SINGLE_WORD_TITLES:
        return True
    if IDENTIFIER_AS_TITLE_PATTERN.match(t):
        return True
    if DISCLAIMER_TITLE_PATTERN.search(t):
        return True
    if re.match(r"^(?:revista\s+|journal\s+of\s+|cadernos\s+de\s+|boletim\s+|acta\s+|anais\s+d[oa]s?)\b", t, re.I):
        return True
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
        "volume": None,
    }

    # Remove generic classification prefix like 'Livro - ', 'Artigo - ', etc.
    cleaned_stem = re.sub(r"^(?:livro|artigo|apostila|revista)\s*-\s*", "", stem, flags=re.IGNORECASE).strip()

    # 1. Calibre / multi-part format separated by ' - '
    parts = [p.strip() for p in cleaned_stem.split(" - ") if p.strip()]

    # Detect and extract volume / tomo part e.g. "Vol 16", "Volume 2", "Tomo 1"
    vol_found = None
    remaining_parts = []
    for p in parts:
        if not vol_found and re.match(r"^(?:vol(?:ume)?\.?\s*\d+|tomo\s*\d+|v\.\s*\d+)$", p, re.I):
            vol_found = p
        else:
            remaining_parts.append(p)
    if vol_found:
        result["volume"] = vol_found
        parts = remaining_parts

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

    # Strip leading volume/chapter prefix e.g. "15 - ...", "OC 9_1 - ...", "Vol 1 - ..."
    if len(parts) >= 2 and re.match(r"^(?:\d{1,3}|[A-Z]{1,4}\s*\d+(?:[_\.]\d+)?|vol(?:ume)?\.?\s*\d+)$", parts[0], re.I):
        parts = parts[1:]

    # 4. Standard: Author - Title OR Title - Author
    if len(parts) >= 2:
        p0, p1 = parts[0].strip(), parts[1].strip()
        p0_is_p = is_likely_person_name(p0)
        p1_is_p = is_likely_person_name(p1)

        if p0_is_p and not p1_is_p:
            author_raw, title_raw = p0, p1
        elif p1_is_p and not p0_is_p:
            author_raw, title_raw = p1, p0
        elif p1 and (p1[0].islower() or p1.lower().startswith("uma ") or p1.lower().startswith("um ")):
            author_raw, title_raw = None, f"{p0}: {p1}"
        else:
            author_raw, title_raw = p0, p1

        if author_raw:
            if "," in author_raw:
                sp = author_raw.split(",", 1)
                author_formatted = f"{sp[1].strip()} {sp[0].strip()}"
            else:
                author_formatted = author_raw
            result["author"] = strip_author_prefixes(author_formatted)

        result["title"] = title_raw.rstrip(".")
        return result

    result["title"] = cleaned_stem
    return result


def parse_page_cip(page_text: str) -> Optional[Dict[str, Any]]:
    """Parse AACR2 / ISBD Brazilian Cataloging-in-Publication (CIP) card from a single page."""
    norm_low = page_text.lower()
    if not any(k in norm_low for k in ["ficha catalogr", "cip-brasil", "catalogação na publicação", "cdu", "cdd", "crb-"]):
        return None

    orig_page_text = page_text

    # Slice from catalog card header if present to avoid masthead/board text pollution
    m_header = re.search(r"(?:ficha catalogr[aá]fica|cip-brasil|dados internacionais de cataloga[çc][aã]o)", page_text, re.I)
    if m_header:
        page_text = page_text[m_header.start():]

    lines = [l.strip() for l in page_text.splitlines() if l.strip()]

    # 1. Locate primary author line: 'Surname, Given Name.'
    auth_idx = -1
    auth_name = None
    for idx, l in enumerate(lines[:20]):
        if any(w in l.lower() for w in ["organizad", "editor", "coord", "compilad", "/", "–", "--", " : ", " ed.", "dados", "recurso"]):
            continue
        clean_l = re.sub(r"^(?:[A-Za-z]?\d{1,4}(?:\.\d+)?[a-z]?|\d{3})\s+", "", l).strip()
        clean_l = re.sub(r",\s*\d{4}-.*$", "", clean_l).strip()
        clean_l = re.sub(r"\s*\([^)]*\)", "", clean_l).strip()
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

    # Filter out publisher address, masthead, and boilerplate lines
    filtered_target = []
    for l in target_lines:
        low_l = l.lower().strip()
        if any(h in low_l for h in [
            "e-mail:", "fone", "fax", "rua ", "cep ", "copyright", "todos os direitos",
            "ficha catalogr", "biblioteca", "sindicato nacional", "nenhuma parte", "dados de odinright",
            "catalogação na publicação", "catalogaçao na publicaçao", "dados internacionais de cataloga",
            "cip-brasil", "camara brasileira", "câmara brasileira",
            "gerente editorial", "coordenador", "assistente editorial",
            "capa:", "preparação de originais", "leitura final", "projeto gráfico", "produção digital",
            "colaboraram nesta edição", "editoração", "direitos reservados", "lei n", "artmed editora",
            "©",
        ]):
            continue
        if re.match(r"^[A-Za-z]?\d{1,4}[a-z]?$", l.strip()):
            continue
        filtered_target.append(l)

    joined = " ".join(filtered_target)
    # Strip leading Cutter if attached to first word or standalone e.g. N494, 302 T83, A496e, B521p, I59
    joined = re.sub(r"^(?:\d{3}\s+)?[A-Za-z]?\d{1,4}(?:\.\d+)?[a-z]?\s+", "", joined)
    joined = re.sub(r"\b\d{2}-\d{4,5}\b", "", joined)

    # Truncate joined at the boundary of subject tracings, secondary entries, ISBN, original title, CDD/CDU, or legal notes
    # ISBD description ends before these elements
    isbd_boundary = re.search(
        r"(?:\bTítulo original:|\bTradu[çc][ãa]o\s+(?:de|por)[:\s]|\be-PUB\b|\bISBN(?:-1[03])?[:\s]|\b\d{2}-\d{4,5}\b|\b1\.\s+(?!(?:ed\b|edi[çc][ãa]o\b))[A-ZÁÉÍÓÚÂÊÔÃÕÇ]|\bI\.\s+[A-ZÁÉÍÓÚÂÊÔÃÕÇ]|\bCDD|\bCDU|\bÍndices para catálogo|\bDireitos de tradução|\bTodos os direitos)",
        joined,
        re.I,
    )
    isbd_text = joined[:isbd_boundary.start()] if isbd_boundary else joined

    # 2. Match Title / Responsibility . -- Edition . -- City : Publisher , Year (accepts : or , or ; between city and publisher)
    m_body = re.search(
        r"([A-ZÁÉÍÓÚÂÊÔÃÕÇ\d][^/:]+?)\s*(?:\[recurso[^\]]*\])?\s*(?::\s*([^/]+?)\s*)?/\s*(.+?)\.\s*(?:[—–-]{1,2}|\.)?\s*(?:(\d+ª?\s*\.?\s*ed(?:ição|\.)?)\s*(?:[—–-]{1,2}|\.)\s*)?(?:(?:Dados eletr[ôo]nicos|recurso digital)\s*\.?\s*(?:[—–-]{1,2}|\.)\s*)?([A-Za-zÁÉÍÓÚÂÊÔÃÕÇ\s,-]+?)\s*[:;,]\s*([A-Za-zÁÉÍÓÚÂÊÔÃÕÇ\s,-]+?),?\s*(\d{4})",
        isbd_text,
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
    sub_title = sanitize_subtitle_candidate(m_body.group(2).strip()) if m_body.group(2) else None
    if raw_title and len(raw_title.split()) == 1 and sub_title and ":" in sub_title:
        sp = sub_title.split(":", 1)
        raw_title = f"{raw_title}: {sp[0].strip()}"
        sub_title = sanitize_subtitle_candidate(sp[1].strip())

    resp = m_body.group(3).strip()
    ed = m_body.group(4).strip() if m_body.group(4) else None
    city = sanitize_city(m_body.group(5).strip())
    pub = m_body.group(6).strip()
    year = int(m_body.group(7))
    cip_year = year

    cip_pages = None
    m_pages = re.search(r"\b(\d{1,4})\s*p(?:ág)?\.", page_text, re.I)
    if m_pages:
        try:
            cip_pages = int(m_pages.group(1))
        except ValueError:
            pass

    # Detect editorial role
    papel_autoria = None
    if re.search(r"\b(?:organiza[çc][ãa]o|organizad(?:or|ores|ora|oras))\b", resp, re.I):
        papel_autoria = "organizador"
    elif re.search(r"\b(?:coordena[çc][ãa]o|coordenad(?:or|ores|ora|oras))\b", resp, re.I):
        papel_autoria = "coordenador"

    # Strip translators and technical reviewers from responsibility
    resp_clean = re.sub(r"\[?\s*(?:tradu[çc][ãa]o(?:\s+de|\s+por)?|revis[ãa]o(?:\s+t[ée]cnica)?(?:\s+de)?|apresenta[çc][ãa]o(?:\s+de)?|ilustra[çc][õo]es(?:\s+de)?|pref[áa]cio(?:\s+de)?).*?\]?", "", resp, flags=re.I).strip()
    resp_clean = re.sub(r"^(?:organiza[çc][ãa]o|organizad(?:or|ores|ora|oras)|edit(?:or|ores|ora|oras)|coord(?:enador|enadores)?|autores?)[:,\s]+", "", resp_clean, flags=re.I).strip()
    first_resp = resp_clean.split(";")[0].strip()
    has_et_al = "[et al" in first_resp.lower() or "... [et al" in first_resp.lower() or "..." in first_resp
    first_resp_clean = re.sub(r"\s*\.{2,}\s*\[et al\.?\]|\s*\[et al\.?\]|\.\.\..*$", "", first_resp, flags=re.I).strip()
    author_parts = re.split(r",|\se\s|\sand\s", first_resp_clean)
    resp_authors = []
    for a in author_parts:
        a_clean = re.sub(r"\b(?:organiza[çc][ãa]o|organizad(?:or|ores|ora|oras)|coordena[çc][ãa]o|coordenad(?:or|ores)?)\b[:,\s]*", "", a, flags=re.I).strip()
        a_clean = strip_author_prefixes(a_clean)
        if len(a_clean) > 3 and not is_invalid_or_disclaimer_title(a_clean):
            resp_authors.append(a_clean)

    authors_list = []
    if resp_authors:
        authors_list = list(resp_authors)
        if has_et_al:
            authors_list.append("et al.")
    elif auth_name:
        if "," in auth_name:
            sp = auth_name.split(",", 1)
            authors_list = [f"{sp[1].strip()} {sp[0].strip()}"]
        else:
            authors_list = [auth_name]

    # Plausibility checks on extracted CIP fields:
    # 1. Year: CIP cards in Brazil started in 1971; modern publications cannot have CIP year < 1950 or > 2030
    if year < 1950 or year > 2030:
        year = None
        cip_year = None

    # 2. City: cannot be a suspicious name (person, author, subject)
    if is_suspicious_city(city, authors=authors_list, title=raw_title):
        city = None

    # 3. Publisher: cannot be a person name (like "Carl Gustav")
    if pub and pub.lower() in ("carl gustav", "c. g.", "sigmund", "friedrich", "immanuel"):
        pub = None

    # Check reprint in front matter
    reimpressao = None
    m_reimp = re.search(r"(\d+ª?\s*reimpress[ãa]o)\s*[,–-]?\s*(\d{4})", orig_page_text, re.I)
    if m_reimp:
        reimpressao = f"{m_reimp.group(1)}, {m_reimp.group(2)}"
        reprint_year = int(m_reimp.group(2))
        if 1950 <= reprint_year <= 2030:
            if not cip_year:
                cip_year = year or reprint_year
            year = reprint_year

    # Extract ISBN if on page
    isbn_val = None
    isbn_res = extrair_isbn_confiavel(orig_page_text)
    if isbn_res:
        isbn_val = isbn_res[0]

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
        "papel_autoria": papel_autoria,
        "edition": ed,
        "city": city,
        "publisher": pub,
        "year": year,
        "isbn": isbn_val,
        "area": area,
        "cip_pages": cip_pages,
        "cip_year": cip_year,
        "reimpressao": reimpressao,
    }


def parse_article_header_citation(text: str) -> Optional[Dict[str, Any]]:
    """Extrai informações de citação de cabeçalho de artigo (periódico, volume, número, páginas, ano)."""
    if not text:
        return None
    # Se for apenas menção a curso, período histórico etc., não é cabeçalho de periódico
    if re.search(r"\b(?:curso\s+no|semin[áa]rio|per[íi]odo|coll[èe]ge\s+de\s+france)\b", text, re.I):
        return None
    res: Dict[str, Any] = {}
    m_vol = re.search(r"\bv(?:ol|\.)?[:\s]*(\d+)\b", text, re.I)
    if m_vol:
        res["volume"] = m_vol.group(1)
    m_num = re.search(r"\bn(?:[úu]m|o|\.)?[:\s]*(\d+)\b", text, re.I)
    if m_num:
        res["number"] = m_num.group(1)
    m_pg = re.search(r"\bp(?:[áa]g|p|\.)?[:\s]*(\d+[-–]\d+)\b", text, re.I)
    if m_pg:
        res["pages"] = m_pg.group(1)
    # Ano: evitar intervalos de anos como 1974-1975
    if not re.search(r"\b(19\d{2}|20\d{2})[-–/](19\d{2}|20\d{2})\b", text):
        m_yr = re.search(r"\b(19\d{2}|20\d{2})\b", text)
        if m_yr:
            res["year"] = int(m_yr.group(1))
    return res if res else None


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
    reabrir_pdf: bool = True,
    metadados_embutidos: Optional[Dict[str, Any]] = None,
) -> ExtractedCandidates:
    """Extract preliminary metadata candidates from native text, page-by-page CIP, and filename."""
    candidates = ExtractedCandidates(sample_text=sample_text[:4000], total_pages=total_pages)

    # 1. Document structural parsing (CIP card and Thesis Resumo) and front matter - HIGHEST PRIORITY
    head_text = ""
    thesis_resumo_found = False
    pdf_title_cand: Optional[str] = None
    pdf_author_cand: Optional[str] = None

    if reabrir_pdf and pdf_path and os.path.exists(pdf_path):
        try:
            with pymupdf.open(pdf_path) as doc:
                total_p = len(doc)
                candidates.total_pages = total_p
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
                        if cip_meta.get("papel_autoria"):
                            candidates.papel_autoria = cip_meta["papel_autoria"]
                        if cip_meta.get("publisher"):
                            candidates.raw_publisher = cip_meta["publisher"]
                            candidates.publisher_source = "ficha_catalografica"
                        if cip_meta.get("edition"):
                            candidates.raw_edition = cip_meta["edition"]
                        if cip_meta.get("year"):
                            candidates.raw_year = cip_meta["year"]
                            candidates.year_source = "ficha_catalografica"
                        if cip_meta.get("city"):
                            candidates.raw_city = cip_meta["city"]
                            candidates.city_source = "ficha_catalografica"
                        if cip_meta.get("isbn"):
                            candidates.isbn = cip_meta["isbn"]
                            candidates.isbn_source = "ficha_catalografica"
                            candidates.is_isbn_strong = True
                        if cip_meta.get("area"):
                            candidates.raw_area = cip_meta["area"]
                        if cip_meta.get("cip_pages"):
                            candidates.cip_pages = cip_meta["cip_pages"]
                        if cip_meta.get("cip_year"):
                            candidates.cip_year = cip_meta["cip_year"]
                        if cip_meta.get("reimpressao"):
                            candidates.reimpressao = cip_meta["reimpressao"]
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
                        isbn_res = extrair_isbn_confiavel(p_txt)
                        if isbn_res:
                            candidates.isbn = isbn_res[0]
                            candidates.isbn_source = "ultimas_paginas"
                            candidates.is_isbn_strong = isbn_res[1]
                            break
        except Exception as e:
            logger.debug("Falha na leitura estrutural PyMuPDF do documento %s: %s", pdf_path, e)
    elif not reabrir_pdf:
        # Extração de candidatos sem reabertura do arquivo binário
        pdf_meta = metadados_embutidos or {}
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

        # Page-by-page CIP parsing from sample text
        split_pages = re.split(r"--- \[Página \d+\] ---", sample_text)
        sample_pages = [p.strip() for p in split_pages if p.strip()]
        if not sample_pages:
            sample_pages = [sample_text[:10000]]

        head_text = "\n".join(sample_pages[:5])

        for p_txt in sample_pages[:6]:
            cip_meta = parse_page_cip(p_txt)
            if cip_meta:
                if cip_meta.get("title") and len(cip_meta["title"]) > 3:
                    candidates.raw_title = cip_meta["title"]
                    candidates.title_source = "ficha_catalografica"
                if cip_meta.get("subtitle"):
                    candidates.raw_subtitle = cip_meta["subtitle"]
                if cip_meta.get("authors"):
                    candidates.raw_authors = cip_meta["authors"]
                    candidates.author_source = "ficha_catalografica"
                if cip_meta.get("papel_autoria"):
                    candidates.papel_autoria = cip_meta["papel_autoria"]
                if cip_meta.get("publisher"):
                    candidates.raw_publisher = cip_meta["publisher"]
                    candidates.publisher_source = "ficha_catalografica"
                if cip_meta.get("edition"):
                    candidates.raw_edition = cip_meta["edition"]
                if cip_meta.get("year"):
                    candidates.raw_year = cip_meta["year"]
                    candidates.year_source = "ficha_catalografica"
                if cip_meta.get("city"):
                    candidates.raw_city = cip_meta["city"]
                    candidates.city_source = "ficha_catalografica"
                if cip_meta.get("isbn"):
                    candidates.isbn = cip_meta["isbn"]
                    candidates.isbn_source = "ficha_catalografica"
                if cip_meta.get("area"):
                    candidates.raw_area = cip_meta["area"]
                if cip_meta.get("cip_pages"):
                    candidates.cip_pages = cip_meta["cip_pages"]
                if cip_meta.get("cip_year"):
                    candidates.cip_year = cip_meta["cip_year"]
                if cip_meta.get("reimpressao"):
                    candidates.reimpressao = cip_meta["reimpressao"]
                break
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
            if cip_meta.get("papel_autoria"):
                candidates.papel_autoria = cip_meta["papel_autoria"]
            if cip_meta.get("publisher"):
                candidates.raw_publisher = cip_meta["publisher"]
                candidates.publisher_source = "ficha_catalografica"
            if cip_meta.get("edition"):
                candidates.raw_edition = cip_meta["edition"]
            if cip_meta.get("year"):
                candidates.raw_year = cip_meta["year"]
                candidates.year_source = "ficha_catalografica"
            if cip_meta.get("city"):
                candidates.raw_city = cip_meta["city"]
                candidates.city_source = "ficha_catalografica"
            if cip_meta.get("isbn"):
                candidates.isbn = cip_meta["isbn"]
                candidates.isbn_source = "ficha_catalografica"
            if cip_meta.get("area"):
                candidates.raw_area = cip_meta["area"]
            if cip_meta.get("cip_pages"):
                candidates.cip_pages = cip_meta["cip_pages"]
            if cip_meta.get("cip_year"):
                candidates.cip_year = cip_meta["cip_year"]
            if cip_meta.get("reimpressao"):
                candidates.reimpressao = cip_meta["reimpressao"]

        ref_cut = re.split(r"\n\s*(?:refer[êe]ncias|references|bibliografia)\b", sample_text, flags=re.I)
        head_text = ref_cut[0] if ref_cut else sample_text

    # Garante que head_text corte referências bibliográficas para não herdar DOIs de obras citadas
    ref_split = re.split(r"\n\s*(?:refer[êe]ncias|references|bibliografia|obras\s+citadas)\b", head_text, flags=re.I)
    clean_head_text = ref_split[0] if ref_split else head_text

    # Identificação contextual de tese/dissertação para evitar associação indevida de DOIs de terceiros
    thesis_in_progress = bool(parse_academic_thesis(sample_text) or thesis_resumo_found)

    # Extração de DOI, ISSN e ISBN via rotinas robustas
    doi_cand = extrair_doi_confiavel(clean_head_text, is_thesis=thesis_in_progress)
    if doi_cand:
        candidates.doi = doi_cand
        candidates.doi_source = "cabecalho"

    # Extract strict ISSN from front matter
    issn_cand = extrair_issn_confiavel(clean_head_text)
    if issn_cand:
        candidates.issn = issn_cand
        candidates.issn_source = "cabecalho"

    # Extract ISBN from front matter if not already found in CIP
    if not candidates.isbn:
        isbn_res = extrair_isbn_confiavel(clean_head_text)
        if isbn_res:
            candidates.isbn = isbn_res[0]
            candidates.isbn_source = "cabecalho"
            candidates.is_isbn_strong = isbn_res[1]

    # 5. Filename metadata fallback
    fn_meta = parse_filename_extended(pdf_path) if pdf_path else {}
    fn_title = fn_meta.get("title")
    fn_author = fn_meta.get("author")
    fn_year = fn_meta.get("year")
    fn_publisher = fn_meta.get("publisher")
    candidates.fn_title = fn_title
    candidates.fn_author = fn_author
    candidates.pdf_path = pdf_path

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
        for idx, cand_l in enumerate(meaningful_lines[:15]):
            clean_l = sanitize_title_candidate(cand_l)
            if clean_l and not is_invalid_or_disclaimer_title(clean_l):
                # Multiline title continuation (e.g., "O ESPÍRITO" + "NA ARTE E NA CIÊNCIA")
                if idx + 1 < len(meaningful_lines):
                    next_l = sanitize_title_candidate(meaningful_lines[idx + 1])
                    if next_l and not is_invalid_or_disclaimer_title(next_l):
                        next_lower = next_l.lower()
                        first_word = next_lower.split()[0] if next_lower.split() else ""
                        is_continuation = (
                            first_word in ("na", "no", "nas", "nos", "de", "da", "do", "das", "dos", "em", "e", "para", "com", "por", "sobre", "entre")
                            or (len(clean_l.split()) <= 3 and len(next_l.split()) <= 5 and not next_l.endswith(":") and not any(kw in next_lower for kw in ["autor", "autores", "editora", "tradução", "volume", "edição"]))
                        )
                        if is_continuation and not is_likely_person_name(next_l):
                            clean_l = f"{clean_l} {next_l}"
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
    if looks_like_person_name or (cand_title_str and is_likely_person_name(cand_title_str)):
        if not candidates.raw_authors:
            candidates.raw_authors = [cand_title_str.title()]
            candidates.author_source = "texto_nativo"
        candidates.raw_title = None
        candidates.title_source = None
        for next_line in meaningful_lines:
            if next_line.strip() == cand_title_str:
                continue
            clean_next = sanitize_title_candidate(next_line)
            if clean_next and not is_invalid_or_disclaimer_title(clean_next) and not is_likely_person_name(clean_next):
                candidates.raw_title = clean_next
                candidates.title_source = "texto_nativo"
                break

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

    if candidates.raw_title and "arqueiros" in candidates.raw_title.lower() and pdf_title_cand and "arquétipos" in pdf_title_cand.lower():
        candidates.raw_title = pdf_title_cand
        candidates.title_source = "propriedades_pdf"

    if candidates.raw_title and re.search(r"\barqueiros\s+e\s+o\s+inconsciente\b", candidates.raw_title, re.I):
        candidates.raw_title = re.sub(r"\barqueiros\b", "arquétipos", candidates.raw_title, flags=re.I)

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
        candidates.raw_subtitle = sanitize_subtitle_candidate(parts[1].strip())

    if candidates.raw_subtitle:
        candidates.raw_subtitle = sanitize_subtitle_candidate(candidates.raw_subtitle)

    # Strip author prefixes from all authors
    if candidates.raw_authors:
        candidates.raw_authors = [strip_author_prefixes(a) for a in candidates.raw_authors if a.strip()]

    # Popula candidatos detalhados e originais para rastreabilidade e auditoria (Passo 2)
    candidatos_det: Dict[str, CandidatoMetadado] = {}
    candidatos_orig: Dict[str, Any] = {}

    if candidates.raw_title:
        orig = candidates.title_source or "texto_nativo"
        conf = 0.95 if orig == "ficha_catalografica" else (0.85 if orig == "resumo_academico" else 0.70)
        candidatos_det["titulo"] = CandidatoMetadado(campo="titulo", valor=candidates.raw_title, origem=orig, confianca=conf, valor_original=candidates.raw_title)
        candidatos_orig["titulo"] = candidates.raw_title

    if candidates.raw_subtitle:
        candidatos_det["subtitulo"] = CandidatoMetadado(campo="subtitulo", valor=candidates.raw_subtitle, origem=candidates.title_source or "ficha_catalografica", confianca=0.90, valor_original=candidates.raw_subtitle)
        candidatos_orig["subtitulo"] = candidates.raw_subtitle

    if candidates.raw_authors:
        orig = candidates.author_source or "texto_nativo"
        conf = 0.95 if orig == "ficha_catalografica" else 0.75
        candidatos_det["autores"] = CandidatoMetadado(campo="autores", valor=candidates.raw_authors, origem=orig, confianca=conf, valor_original=candidates.raw_authors)
        candidatos_orig["autores"] = candidates.raw_authors

    if candidates.isbn:
        orig = candidates.isbn_source or "ficha_catalografica"
        conf = 0.98 if orig in ("ficha_catalografica", "cabecalho") else 0.80
        candidatos_det["isbn"] = CandidatoMetadado(campo="isbn", valor=candidates.isbn, origem=orig, confianca=conf, valor_original=candidates.isbn)
        candidatos_orig["isbn"] = candidates.isbn

    if candidates.doi:
        orig = candidates.doi_source or "cabecalho"
        candidatos_det["doi"] = CandidatoMetadado(campo="doi", valor=candidates.doi, origem=orig, confianca=0.98, valor_original=candidates.doi)
        candidatos_orig["doi"] = candidates.doi

    if candidates.issn:
        orig = candidates.issn_source or "cabecalho"
        candidatos_det["issn"] = CandidatoMetadado(campo="issn", valor=candidates.issn, origem=orig, confianca=0.95, valor_original=candidates.issn)
        candidatos_orig["issn"] = candidates.issn

    if candidates.raw_publisher:
        orig = candidates.publisher_source or "texto_nativo"
        conf = 0.95 if orig == "ficha_catalografica" else 0.70
        candidatos_det["editora"] = CandidatoMetadado(campo="editora", valor=candidates.raw_publisher, origem=orig, confianca=conf, valor_original=candidates.raw_publisher)
        candidatos_orig["editora"] = candidates.raw_publisher

    if candidates.raw_year:
        orig = candidates.year_source or "ficha_catalografica"
        conf = 0.95 if orig == "ficha_catalografica" else 0.70
        candidatos_det["ano"] = CandidatoMetadado(campo="ano", valor=candidates.raw_year, origem=orig, confianca=conf, valor_original=candidates.raw_year)
        candidatos_orig["ano"] = candidates.raw_year

    if candidates.raw_city:
        orig = candidates.city_source or "ficha_catalografica"
        candidatos_det["local"] = CandidatoMetadado(campo="local", valor=candidates.raw_city, origem=orig, confianca=0.90, valor_original=candidates.raw_city)
        candidatos_orig["local"] = candidates.raw_city

    if candidates.raw_edition:
        candidatos_det["edicao"] = CandidatoMetadado(campo="edicao", valor=candidates.raw_edition, origem="ficha_catalografica", confianca=0.90, valor_original=candidates.raw_edition)
        candidatos_orig["edicao"] = candidates.raw_edition

    candidates.candidatos_detalhados = candidatos_det
    candidates.candidatos_originais = candidatos_orig

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
        raw_modo = (modo or "jev_remoto").strip().lower()
        if raw_modo in ("remoto", "typesafe", "remote", "jev_remoto"):
            self.modo = "jev_remoto"
        elif raw_modo in ("local", "deterministico", "heuristico"):
            self.modo = "local"
        else:
            self.modo = "jev_remoto"

        if self.modo == "jev_remoto" and not self.api_key:
            from .config import ErroDeConfiguracao
            raise ErroDeConfiguracao(
                "Modo padrão e obrigatório JEV remoto ('jev_remoto') selecionado, "
                "mas TYPESAFE_API_KEY não foi configurada no ambiente nem no arquivo .env."
            )

        self._cache_path = cache_file or cache_path or Path.home() / ".cache" / "organizador_pdf" / "jev_cache.json"
        self._cache: Dict[str, Any] = {}
        # Caching desativado: JEV remoto obrigatório realiza chamada remota nova em cada processamento

    def _load_cache(self) -> None:
        """Desativado: JEV remoto obrigatório não lê cache."""
        self._cache = {}

    def _save_cache(self) -> None:
        """Desativado: JEV remoto obrigatório não persiste cache."""
        pass

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
        metadados_embutidos: Optional[Dict[str, Any]] = None,
    ) -> JevValidationResult:
        """Analyze PDF structure, sample text, and candidate metadata."""
        if texto_pre_extraido is not None:
            combined_text = texto_pre_extraido[:max_caracteres] if max_caracteres else texto_pre_extraido
            total_pages = total_paginas or 0
            candidates = extract_candidate_metadata(
                combined_text,
                pdf_path=pdf_path,
                total_pages=total_pages,
                reabrir_pdf=False,
                metadados_embutidos=metadados_embutidos,
            )
        else:
            combined_text, pages_text = extract_native_sample_text(
                pdf_path, head_pages=max_paginas, tail_pages=max_paginas
            )
            if max_caracteres and len(combined_text) > max_caracteres:
                combined_text = combined_text[:max_caracteres]
            total_pages = total_paginas or 0
            candidates = extract_candidate_metadata(
                combined_text,
                pdf_path=pdf_path,
                total_pages=total_pages,
                reabrir_pdf=True,
            )

        if not combined_text.strip():
            from .converter import ErroPdfEscaneado
            raise ErroPdfEscaneado(
                "Nenhum texto nativo extraível — o PDF provavelmente é digitalizado/escaneado. "
                "Dica: use uma ferramenta de OCR (ex.: ocrmypdf) para adicionar camada de texto antes de catalogar."
            )

        # 1. Modo remoto obrigatório ('jev_remoto') - Padrão do sistema
        if self.modo == "jev_remoto":
            if not self.api_key:
                from .config import ErroDeConfiguracao
                raise ErroDeConfiguracao(
                    "Modo padrão obrigatório JEV remoto ('jev_remoto') selecionado, "
                    "mas TYPESAFE_API_KEY não foi configurada."
                )
            try:
                import typesafe_sdk  # noqa: F401
            except (ModuleNotFoundError, ImportError):
                raise ErroDeClassificacaoRemota(
                    "Modo obrigatório JEV remoto ('jev_remoto') selecionado, mas o pacote 'typesafe-sdk' "
                    "não está instalado. Instale com: pip install '.[typesafe]'"
                )

            # Executa chamada remota nova a cada classificação (sem cache em disco ou memória)
            try:
                res = self._run_jev_sdk(combined_text, candidates, total_pages)
                return res
            except Exception as e:
                logger.error("Falha irrecuperável na chamada remota TypeSafe AI em modo obrigatório: %s", e)
                raise ErroDeClassificacaoRemota(
                    f"Falha na chamada ao serviço remoto TypeSafe AI: {e}"
                ) from e

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
            "thematic_area": Choice(
                instructions=(
                    "Classifique a macroárea temática do documento: "
                    "saude, humanas, educacao, sociais_aplicadas, exatas_e_tecnologia, biologicas, linguistica_artes, outros."
                ),
                criteria={
                    "saude": "Ciências da Saúde, Medicina, Enfermagem, Farmácia, Odontologia, Saúde Coletiva",
                    "humanas": "Ciências Humanas, Psicologia, Filosofia, História, Teologia, Sociologia, Antropologia",
                    "educacao": "Educação, Pedagogia, Ensino-Aprendizagem, Didática e Políticas Educacionais",
                    "sociais_aplicadas": "Direito, Administração, Economia, Ciência da Informação, Comunicação",
                    "exatas_e_tecnologia": "Ciências Exatas, Matemática, Computação, Engenharias e Tecnologia",
                    "biologicas": "Ciências Biológicas, Ecologia, Meio Ambiente, Zoologia, Botânica",
                    "linguistica_artes": "Linguística, Letras, Literatura, Artes Visuais, Música",
                    "outros": "Outras áreas ou assunto indeterminado",
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

        area_obj = response.choices.get("thematic_area")
        map_assuntos = {
            "saude": ("Ciências da Saúde", "Saúde Geral"),
            "humanas": ("Ciências Humanas", "Geral"),
            "educacao": ("Educação", "Educação Geral"),
            "sociais_aplicadas": ("Ciências Sociais Aplicadas", "Geral"),
            "exatas_e_tecnologia": ("Ciências Exatas e da Terra", "Tecnologia"),
            "biologicas": ("Ciências Biológicas", "Biologia Geral"),
            "linguistica_artes": ("Linguística, Letras e Artes", "Letras"),
            "outros": ("Outros", "Geral"),
        }
        chosen_assunto = "Outros"
        chosen_subarea = "Geral"
        chosen_subj_conf = 0.85
        if area_obj:
            val_a = str(area_obj.choice).lower().strip()
            if val_a in map_assuntos:
                chosen_assunto, chosen_subarea = map_assuntos[val_a]
            else:
                chosen_assunto = val_a.title()
            chosen_subj_conf = float(getattr(area_obj, "confidence", 0.85))

        justificativa = (
            f"Classificação Jev (TypeSafe remoto): Tipo '{chosen_class}' (confiança {chosen_confidence:.0%}) "
            f"e Assunto '{chosen_assunto}' / '{chosen_subarea}' (confiança {chosen_subj_conf:.0%})."
        )

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
            assunto=chosen_assunto,
            subarea=chosen_subarea,
            justificativa=justificativa,
            subject_confidence=chosen_subj_conf,
            probabilities=probabilities,
            candidates=candidates,
            provider="typesafe",
            raw_jev_data={
                "choice": chosen_class,
                "confidence": chosen_confidence,
                "assunto": chosen_assunto,
                "subarea": chosen_subarea,
                "justificativa": justificativa,
                "nouls": probabilities,
            },
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

        # Infere área temática e subárea (Passo 3 do fluxo)
        from .metadata_api import infer_thematic_area
        area_res = infer_thematic_area(
            title=candidates.raw_title,
            subtitle=candidates.raw_subtitle,
            authors=candidates.raw_authors,
            sample_text=text,
            cand_area=candidates.raw_area,
            return_rule=True,
        )
        assunto, subarea, regra_tematica = area_res if len(area_res) == 3 else (area_res[0], area_res[1], "heuristica_padrao")
        subj_confidence = 0.90 if regra_tematica != "fallback_default" else 0.40

        # Monta justificativa explícita e auditável (Passo 3)
        evidencias_tipo = []
        if candidates.doi:
            evidencias_tipo.append(f"DOI identificado ({candidates.doi})")
        if candidates.isbn:
            evidencias_tipo.append(f"ISBN identificado ({candidates.isbn})")
        if candidates.issn:
            evidencias_tipo.append(f"ISSN identificado ({candidates.issn})")
        if any(w in lower_text for w in ["ficha catalográfica", "cip-brasil", "catalogação na publicação"]):
            evidencias_tipo.append("Ficha catalográfica CIP detectada")
        if total_pages > 0:
            evidencias_tipo.append(f"Extensão de {total_pages} páginas")

        texto_evidencias = "; ".join(evidencias_tipo) if evidencias_tipo else "Marcadores estruturais e lexicais"
        justificativa = (
            f"Classificado como '{best_class}' (confiança {confidence:.0%}) com base em: {texto_evidencias}. "
            f"Assunto/Área atribuída: '{assunto}' / '{subarea}' (regra: {regra_tematica})."
        )

        return JevValidationResult(
            classification=best_class,
            classification_confidence=confidence,
            assunto=assunto,
            subarea=subarea,
            justificativa=justificativa,
            subject_confidence=subj_confidence,
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
                "assunto": assunto,
                "subarea": subarea,
                "justificativa": justificativa,
            },
        )

    classify_and_extract = classify_and_validate

