"""Metadata Enrichment Service.

Queries Crossref, Google Books, OpenLibrary, Brasil API (CBL), and OpenAlex public APIs
to fetch and cross-reference publication metadata.

Includes fallback logic: If Jev does not find identifiers or yields probability < 0.95,
executes fallback search by title across the APIs with strict relevance and author filtering.
"""

import copy
import functools
import html as html_lib
import json
import os
import re
import time
import logging
import threading
import unicodedata
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Tuple
import urllib.parse
import requests
from requests.adapters import HTTPAdapter
from datetime import datetime, timezone
from urllib3.util import Retry
from dotenv import load_dotenv

from .taxonomia import ALIASES_ASSUNTOS_INGLES, CATALOGO_TAXONOMICO, MACRO_CDD

from .models import (
    PublicationMetadata,
    Identifiers,
    JevValidationResult,
    PublicationType,
    StatusConsultaProvedor,
    ConsultaFonteExterna,
    ExtractedCandidates,
    RegistroRastreabilidade,
    ResultadoExtracaoTexto,
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


def normalizar_para_busca(texto: str) -> str:
    """Normaliza texto sem acentos, sem pontuação em excesso, em minúsculas."""
    if not texto:
        return ""
    norm = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in norm if not unicodedata.combining(c))
    sem_pont = re.sub(r"[^\w\s]", " ", sem_acento.lower())
    return re.sub(r"\s+", " ", sem_pont).strip()


def extrair_sobrenomes(names: List[str]) -> Set[str]:
    """Extrai sobrenomes normalizados de uma lista de nomes de autores."""
    surnames = set()
    for name in names:
        if not name:
            continue
        norm = normalizar_para_busca(name)
        if not norm:
            continue
        if "," in name:
            parts = [p.strip() for p in name.split(",") if p.strip()]
            surn = normalizar_para_busca(parts[0])
            if len(surn) >= 3 and surn not in STOP_WORDS:
                surnames.add(surn)
        else:
            tokens = [t for t in norm.split() if len(t) >= 3 and t not in STOP_WORDS and not t.isdigit()]
            if tokens:
                surnames.add(tokens[-1])
    return surnames


def is_title_relevant(searched_title: str, retrieved_title: Optional[str], threshold: float = 0.50) -> bool:
    """Validate that API retrieved title has sufficient word overlap and sequence similarity.

    Normalizes accents, handles subtitles, and avoids false positive matches.
    """
    if not retrieved_title or not searched_title:
        return False

    import difflib

    norm_s = normalizar_para_busca(searched_title)
    norm_r = normalizar_para_busca(retrieved_title)
    if not norm_s or not norm_r:
        return False

    if norm_s == norm_r or norm_s in norm_r or norm_r in norm_s:
        return True

    def tokenize(s: str) -> Set[str]:
        words = set(s.split())
        return {w for w in words if len(w) > 2 and w not in STOP_WORDS and not w.isdigit()}

    searched_tokens = tokenize(norm_s)
    if not searched_tokens:
        return True

    retrieved_tokens = tokenize(norm_r)
    if not retrieved_tokens:
        return False

    intersection = searched_tokens.intersection(retrieved_tokens)
    ratio = len(intersection) / len(searched_tokens)

    # Sequence similarity as complementary deterministic signal
    seq_ratio = difflib.SequenceMatcher(None, norm_s, norm_r).ratio()

    # Subtitle separation: check main title match
    for sep in (":", " - ", " — "):
        if sep in searched_title or sep in retrieved_title:
            main_s = searched_title.split(sep)[0]
            main_r = retrieved_title.split(sep)[0]
            norm_ms = normalizar_para_busca(main_s)
            norm_mr = normalizar_para_busca(main_r)
            tok_ms = tokenize(norm_ms)
            tok_mr = tokenize(norm_mr)
            if tok_ms and tok_mr:
                main_ratio = len(tok_ms.intersection(tok_mr)) / len(tok_ms)
                if main_ratio >= threshold:
                    return True

    return ratio >= threshold or (seq_ratio >= (threshold + 0.15) and len(intersection) >= 1)


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

    CRÍTICO: Ausência de autores no retorno externo significa 'sem evidência para comparar',
    NUNCA 'autoria compatível'.
    Exige coincidência de sobrenome normalizado, rejeitando palavras comuns isoladas.
    """
    if not retrieved_authors:
        return False

    cand_surnames = extrair_sobrenomes(candidate_authors)
    ret_surnames = extrair_sobrenomes(retrieved_authors)

    if not cand_surnames:
        if title:
            norm_t = normalizar_para_busca(title)
            content_words = [w for w in norm_t.split() if w not in STOP_WORDS and len(w) > 2]
            if len(content_words) <= 1 or norm_t in GENERIC_SUBJECT_TITLES:
                return False
            return True
        return False

    if not ret_surnames:
        return False

    overlap = cand_surnames.intersection(ret_surnames)
    if overlap:
        return True

    for c_surn in cand_surnames:
        for r_name in retrieved_authors:
            norm_r = normalizar_para_busca(r_name)
            if c_surn in norm_r.split():
                return True

    return False


INSTITUTION_REGEX = re.compile(
    r"\b(universidad(?:es)?|universidade(?:s)?|university|universities|faculdade(?:s)?|facultad(?:es)?|faculty|instituto(?:s)?|institute(?:s)?|"
    r"departamento(?:s)?|department(?:s)?|secretaria(?:s)?|minist[eé]rio(?:s)?|hospital(?:es)?|fundação|fundações|fundacion(?:es)?|foundation(?:s)?|"
    r"associa[çc][aã]o|associações|asociaci[oó]n(?:es)?|association(?:s)?|escola(?:s)?|school(?:s)?|col[eé]gio(?:s)?|college(?:s)?|centro(?:s)?|center(?:s)?|centre(?:s)?|"
    r"campus|laborat[oó]rio(?:s)?|laboratory|laboratories|editorial(?:es)?|editora(?:s)?|press|publisher(?:s)?)\b",
    re.IGNORECASE,
)


def filter_institutional_authors(authors: List[str]) -> List[str]:
    """Filter out institutional/organizational names."""
    if not authors:
        return []
    human_authors = [a for a in authors if not INSTITUTION_REGEX.search(a)]
    seen = set()
    deduped: List[str] = []
    for a in human_authors:
        norm = a.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            deduped.append(a.strip())
    return deduped


def confirmar_mesma_obra(
    candidates: ExtractedCandidates,
    api_data: Dict[str, Any],
    provider_name: str,
    is_identifier_match: bool = False,
) -> Tuple[bool, Optional[str]]:
    """Confirma com rigor se os metadados da API externa pertencem à mesma obra do PDF (Passo 5).

    Não aceita resultados apenas por semelhança vaga de título.
    Retorna (corresponde: bool, divergencia_detectada: Optional[str]).
    """
    if not api_data:
        return False, None

    from .classifier_jev import is_invalid_or_disclaimer_title

    api_title = api_data.get("title")
    api_authors = api_data.get("authors") or []
    doc_title = candidates.raw_title
    doc_authors = candidates.raw_authors or []

    # Se a busca foi por identificador direto (DOI ou ISBN)
    if is_identifier_match:
        # Contexto do identificador:
        isbn_cand = getattr(candidates, "isbn", None)
        isbn_src = getattr(candidates, "isbn_source", None)
        is_isbn_strong = bool(
            isbn_cand
            and is_valid_isbn(isbn_cand, strict=False)
            and (
                isbn_src in ("ficha_catalografica", "folha_de_rosto", "expediente", "cabecalho", "propriedades_pdf")
                or getattr(candidates, "is_isbn_strong", False)
            )
        )

        # Se os autores candidatos são puramente institucionais, filtra para não rejeitar falsamente a base
        human_doc_authors = filter_institutional_authors(doc_authors)
        autores_compativeis = (
            is_author_compatible(human_doc_authors, api_authors, title=doc_title)
            if (human_doc_authors and api_authors)
            else True
        )

        # Verifica se o título do documento coincide com o periódico ou revista
        api_journal = api_data.get("journal")
        titulo_e_periodico = False
        if doc_title and (api_journal or any(term in doc_title.lower() for term in ("revista", "journal", "cadernos", "anais", "boletim", "vol.", "fascículo"))):
            if api_journal and is_title_relevant(doc_title, api_journal, threshold=0.30):
                titulo_e_periodico = True
            elif any(term in doc_title.lower() for term in ("revista", "journal", "cadernos", "anais", "boletim")):
                titulo_e_periodico = True

        # Verifica se o título candidato é indicador de série, curso ou coleção (ex: "Curso no Collège de France")
        titulo_e_serie = False
        if doc_title and re.search(r"(?i)\b(?:curso(?:\s+(?:no|do|de|da))?|coll[eè]ge\s+de\s+france|cole[çc][aã]o|s[eé]rie|volume|fasc[íi]culo|tomo|obras\s+completas)\b", doc_title):
            titulo_e_serie = True

        # Verifica se o título da API ou subtítulo está contido no texto do PDF ou amostra
        sample_low = (getattr(candidates, "sample_text", "") or "").lower()
        titulo_em_sample = False
        if api_title and len(api_title) >= 4 and (api_title.lower() in sample_low or (doc_title and api_title.lower() in doc_title.lower())):
            titulo_em_sample = True
        # Corroboração adicional: título da API presente (normalizado) na amostra ou compatível com o nome do arquivo
        norm_api_t = normalizar_para_busca(api_title or "")
        norm_sample = normalizar_para_busca(getattr(candidates, "sample_text", "") or "")
        fn_title = getattr(candidates, "fn_title", None)
        if len(norm_api_t) >= 8 and norm_api_t in norm_sample:
            titulo_em_sample = True
        elif fn_title and api_title and is_title_relevant(fn_title, api_title, threshold=0.50):
            titulo_em_sample = True

        titulo_relevante = False
        if doc_title and api_title:
            titulo_relevante = is_title_relevant(doc_title, api_title, threshold=0.20)
            doc_t_clean = re.sub(r"[^\w\s]", "", doc_title.lower()).strip()
            api_t_clean = re.sub(r"[^\w\s]", "", api_title.lower()).strip()
            if doc_t_clean and api_t_clean and (doc_t_clean in api_t_clean or api_t_clean in doc_t_clean):
                titulo_relevante = True

        # Caso de ISBN em contexto forte (ficha catalográfica, folha de rosto, expediente, colofão inicial):
        # O registro bibliográfico confirmado de bases oficiais (Brasil API/CBL, OpenLibrary, Google Books)
        # governa os campos bibliográficos canônicos e não deve ser bloqueado por ruído de OCR ou cabeçalho de curso/série.
        if is_isbn_strong:
            divergencia = None
            if not titulo_relevante and not titulo_e_periodico:
                divergencia = (
                    f"Divergência entre candidato local ('{doc_title}') e registro canônico confirmado no {provider_name} "
                    f"('{api_title}') para ISBN confirmado. Registro canônico adotado como âncora da obra."
                )
            elif human_doc_authors and api_authors and not autores_compativeis:
                divergencia = (
                    f"Divergência entre autoria local {human_doc_authors[:2]} e autoria canônica {api_authors[:2]} "
                    f"({provider_name}) para ISBN confirmado. Registro canônico adotado."
                )
            return True, divergencia

        # Título da API corroborado pelo texto/nome do arquivo: adota os metadados canônicos sem falso conflito
        # contra título/autores ruidosos (cabeçalhos de seção, afiliações) extraídos do PDF.
        if titulo_em_sample:
            return True, None

        # Para outros identificadores ou identificadores em contexto fraco (ex: citações ou referências no fim):
        if doc_title and api_title:
            if not titulo_relevante and not titulo_e_periodico and not titulo_e_serie and not titulo_em_sample and not (autores_compativeis and human_doc_authors):
                divergencia = (
                    f"Divergência de obra para identificador ({provider_name}): "
                    f"o PDF aparenta ser '{doc_title}', mas a base externa registrou '{api_title}'."
                )
                return False, divergencia

        if human_doc_authors and api_authors:
            if not autores_compativeis and not titulo_e_serie and not titulo_em_sample:
                divergencia = (
                    f"Divergência de autoria para identificador ({provider_name}): "
                    f"PDF indica {human_doc_authors[:2]}, mas a base externa registrou {api_authors[:2]}."
                )
                return False, divergencia

        return True, None

    # Se a busca foi por título/autoria (sem identificador):
    if not doc_title or not api_title:
        return False, None

    if is_invalid_or_disclaimer_title(doc_title) or is_invalid_or_disclaimer_title(api_title):
        return False, None

    # Título precisa de pelo menos 60% de tokens significativos em comum
    if not is_title_relevant(doc_title, api_title, threshold=0.60):
        return False, None

    # Se o PDF tem autores humanos conhecidos, a autoria deve ser compatível
    human_doc_authors = filter_institutional_authors(doc_authors)
    if human_doc_authors:
        if not api_authors or not is_author_compatible(human_doc_authors, api_authors, title=doc_title):
            return False, None
    else:
        # Se o PDF NÃO tem autores humanos conhecidos:
        # Não confirmamos apenas por semelhança vaga de título.
        doc_t_clean = re.sub(r"[^\w\s]", "", doc_title.lower()).strip()
        content_words = [w for w in doc_t_clean.split() if w not in STOP_WORDS and len(w) > 2]
        if len(content_words) < 2 or doc_t_clean in GENERIC_SUBJECT_TITLES:
            return False, None

        ano_doc = candidates.raw_year
        ano_api = api_data.get("year")
        ano_bate = bool(ano_doc and ano_api and abs(int(ano_doc) - int(ano_api)) <= 1)

        sample = (candidates.sample_text or "").lower()
        api_t_clean = re.sub(r"[^\w\s]", "", api_title.lower()).strip()
        titulo_no_sample = bool(api_t_clean and len(api_t_clean) > 8 and api_t_clean in sample)
        autores_api_no_sample = False
        if api_authors:
            for a in api_authors:
                surn = a.split(",")[0].strip().lower() if "," in a else a.split()[-1].strip().lower()
                if len(surn) >= 4 and surn in sample:
                    autores_api_no_sample = True
                    break

        alta_similaridade = is_title_relevant(doc_title, api_title, threshold=0.80) and len(content_words) >= 2

        if not (ano_bate or titulo_no_sample or autores_api_no_sample or alta_similaridade):
            return False, None

    return True, None


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


def tem_campos_essenciais_completos(meta: Any, tipo: str) -> bool:
    """Verifica se os campos bibliográficos fundamentais para o tipo documental estão preenchidos."""
    titulo = meta.title if hasattr(meta, "title") else getattr(meta, "titulo", None)
    if not titulo or titulo in ("Sem Título", "Publicação Sem Título") or len(titulo) < 3:
        return False
    autores = meta.authors if hasattr(meta, "authors") else getattr(meta, "autores", None)
    if not autores:
        return False

    t_norm = (tipo or "").lower()
    if t_norm in ("artigo", "artigo_cientifico"):
        # Artigo: Título, Autores, Periódico, Ano, e (Volume, Páginas ou DOI)
        periodico = getattr(meta, "journal", None) or getattr(meta, "periodico", None) or getattr(meta, "editora_ou_periodico", None) or getattr(meta, "publisher", None)
        ano = getattr(meta, "year", None) or getattr(meta, "ano", None)
        ids = getattr(meta, "identifiers", None)
        doi = getattr(ids, "doi", None) if ids else getattr(meta, "doi", None)
        paginas = getattr(meta, "pages", None) or getattr(meta, "paginas", None)
        vol = getattr(meta, "volume", None)
        return bool(periodico and ano and (doi or paginas or vol))
    elif t_norm in ("livro", "apostila"):
        # Livro: Título, Autores, Editora, Ano, e (Localidade, Edição ou ISBN)
        editora = getattr(meta, "publisher", None) or getattr(meta, "editora", None) or getattr(meta, "editora_ou_periodico", None)
        ano = getattr(meta, "year", None) or getattr(meta, "ano", None)
        ids = getattr(meta, "identifiers", None)
        isbn = getattr(ids, "isbn", None) if ids else getattr(meta, "isbn", None)
        cidade = getattr(meta, "city", None) or getattr(meta, "local", None)
        edicao = getattr(meta, "edition", None) or getattr(meta, "edicao", None)
        return bool(editora and ano and (cidade or edicao or isbn))
    elif t_norm in ("tese", "dissertacao", "dissertacao_tese"):
        # Tese/Dissertação: Título, Autores, Instituição (publisher), Ano
        inst = getattr(meta, "publisher", None) or getattr(meta, "editora", None)
        ano = getattr(meta, "year", None) or getattr(meta, "ano", None)
        return bool(inst and ano)
    else:
        ano = getattr(meta, "year", None) or getattr(meta, "ano", None)
        pub = getattr(meta, "publisher", None) or getattr(meta, "journal", None)
        return bool(ano and pub)


def gerar_variantes_de_busca(title: str, authors: Optional[List[str]] = None) -> List[str]:
    """Gera variações justificadas de busca por título sem ampliar ruído (Passo 7)."""
    if not title:
        return []
    variantes = [title.strip()]

    # 1. Título principal sem subtítulo
    for sep in (":", " - ", " — "):
        if sep in title:
            main_part = title.split(sep)[0].strip()
            if len(main_part) >= 4 and main_part not in variantes:
                variantes.append(main_part)

    # 2. Título normalizado sem acentos
    norm_t = normalizar_para_busca(title)
    if norm_t and norm_t not in [normalizar_para_busca(v) for v in variantes]:
        variantes.append(norm_t)

    # 3. Título principal combinado com sobrenome do primeiro autor
    if authors:
        surnames = extrair_sobrenomes(authors)
        if surnames:
            first_surn = sorted(list(surnames))[0]
            cand_comb = f"{variantes[0]} {first_surn}".strip()
            if cand_comb not in variantes:
                variantes.append(cand_comb)

    return variantes[:3]


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


def _limpar_amostra_para_tematica(sample_text: str) -> str:
    """Limpa referências, agradecimentos e notas biográficas/minicurrículos do autor.

    Evita que formações secundárias do autor (ex.: 'especialista em Gestalt-terapia')
    ou referências bibliográficas distorçam a classificação temática do documento.
    """
    if not sample_text:
        return ""
    text = sample_text
    # 1. Trunca antes de referências bibliográficas
    text = re.split(r"(?i)\n\s*(?:refer[êe]ncias|references|bibliografia|obras\s+citadas)\b", text)[0]

    # 2. Remove blocos biográficos e minicurrículos acadêmicos
    biography_pattern = re.compile(
        r"(?i)(?:^|\n)\s*(?:sobre\s+(?:o|a|os|as)\s+autor(?:es|a|as)?|dados\s+biogr[áa]ficos|"
        r"mini[- ]?curr[íi]culo|nota\s+biogr[áa]fica|dados\s+d[oa]\s+autor[a-z]?|"
        r"(?:p[óo]s[- ]doutor[a-z]?|doutor[a-z]?|mestre|especialista|graduado|bacharel|professor[a-z]?|docente)\s+em\b)[^\n]*\n"
        r"(?:[^\n]+\n){0,10}",
    )
    text = biography_pattern.sub("\n", text)
    return text


def _anexar_subjects(result: Optional[Dict[str, Any]], raw: Any) -> None:
    """Preserva em ``result["subjects"]`` (lista de str, sem duplicatas) descritores retornados por APIs.

    Aceita strings, listas de strings ou dicts com ``name``/``display_name``. Só cria a chave se houver conteúdo.
    """
    if not isinstance(result, dict) or not raw:
        return
    itens = raw if isinstance(raw, (list, tuple, set)) else [raw]
    subjects: List[str] = []
    for it in itens:
        if isinstance(it, dict):
            it = it.get("name") or it.get("display_name")
        if not isinstance(it, str):
            continue
        for parte in re.split(r"\s*/\s*", it.strip()) if "/" in it else [it.strip()]:
            if parte and parte not in subjects:
                subjects.append(parte)
    if subjects:
        result["subjects"] = subjects


_CDD_RE = re.compile(r"(\d{3}(?:\.\d+)?)")


def _norm_ascii(text: Optional[str]) -> str:
    raw = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    raw = re.sub(r"[-_–—/]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


@functools.lru_cache(maxsize=4096)
def _termo_regex(term: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"s?(?![a-z0-9])")


def _contar_termos(norm_text: str, termos: Tuple[str, ...]) -> int:
    """Conta termos distintos (palavra inteira/expressão) presentes no texto normalizado."""
    if not norm_text:
        return 0
    vistos: Set[str] = set()
    for t in termos:
        nt = _norm_ascii(t)
        if not nt:
            continue
        chave = nt[:-1] if len(nt) > 4 and nt.endswith("s") else nt
        if chave in vistos:
            continue
        if _termo_regex(nt).search(norm_text):
            vistos.add(chave)
    return len(vistos)


def _expandir_aliases_ingles(subject_norm: str) -> str:
    """Anexa traduções canônicas PT-BR a um assunto normalizado em inglês (apenas para ``subjects``)."""
    extras: List[str] = []
    if subject_norm in ALIASES_ASSUNTOS_INGLES:
        extras.append(ALIASES_ASSUNTOS_INGLES[subject_norm])
    else:
        for chave, traducao in ALIASES_ASSUNTOS_INGLES.items():
            if re.search(r"(?<![a-z0-9])" + re.escape(chave) + r"(?![a-z0-9])", subject_norm):
                extras.append(traducao)
    return " ".join([subject_norm] + extras) if extras else subject_norm


def _variantes_autor(author: str) -> List[str]:
    """Normaliza autor e gera a variante 'Nome Sobrenome' a partir de 'SOBRENOME, Nome'."""
    norm = _norm_ascii(author).strip()
    variantes = [norm]
    if "," in norm:
        sobrenome, _, nome = norm.partition(",")
        variantes.append(f"{nome.strip()} {sobrenome.strip()}".strip())
    return variantes


_GRUPOS_AREA_JEV: Tuple[frozenset, ...] = (
    frozenset({"computacao", "ciencia da computacao", "ciencias da computacao", "informatica", "tecnologia", "ciencias exatas e da terra"}),
    frozenset({"ciencias da saude", "saude", "medicina", "ciencias medicas"}),
    frozenset({"ciencias sociais", "sociologia", "antropologia", "ciencia politica"}),
    frozenset({"administracao", "gestao", "negocios", "ciencias contabeis"}),
    frozenset({"linguistica letras e artes", "letras", "linguistica", "artes"}),
)

_LOCK_CADENCIA = threading.Lock()
_ULTIMA_CHAMADA_PROVEDOR: Dict[str, float] = {}
_INTERVALOS_CADENCIA_PADRAO = {
    "google_books": 0.35,
    "openalex": 0.25,
    "openlibrary": 0.20,
    "crossref": 0.15,
    "brasil_api": 0.15,
}


def _aguardar_cadencia_provedor(provedor: str, min_provider_interval: Optional[float] = None) -> None:
    """Garante cadência mínima entre requisições HTTP reais por provedor (thread-safe, 0.0 sob pytest)."""
    if os.getenv("PYTEST_CURRENT_TEST") and min_provider_interval is None:
        return
    intervalo = min_provider_interval
    if intervalo is None:
        env_ms = os.getenv("ORGPDF_MIN_INTERVAL_MS")
        if env_ms:
            try:
                intervalo = float(env_ms) / 1000.0
            except ValueError:
                intervalo = None
    if intervalo is None:
        intervalo = _INTERVALOS_CADENCIA_PADRAO.get(provedor, 0.15)
    if intervalo <= 0:
        return
    with _LOCK_CADENCIA:
        agora = time.time()
        ultima = _ULTIMA_CHAMADA_PROVEDOR.get(provedor, 0.0)
        espera = intervalo - (agora - ultima)
        if espera > 0:
            time.sleep(espera)
        _ULTIMA_CHAMADA_PROVEDOR[provedor] = time.time()


def _normalizar_area_jev_fallback(jev_assunto: Optional[str], jev_subarea: Optional[str]) -> Tuple[str, str]:
    """Normaliza classificações brutas do JEV para o catálogo canônico."""
    assunto_norm = _norm_ascii(jev_assunto or "").strip().lower()
    subarea_norm = _norm_ascii(jev_subarea or "").strip().lower()

    # Crianças - Desenvolvimento
    if "criancas desenvolvimento" in assunto_norm or "desenvolvimento infantil" in assunto_norm or "criancas desenvolvimento" in subarea_norm:
        return "Psicologia", "Psicologia do Desenvolvimento"

    # Ciências Exatas e da Terra / Tecnologia
    if "ciencias exatas e da terra" in assunto_norm or "exatas" in assunto_norm:
        if "tecnologia" in subarea_norm or any(t in subarea_norm for t in ("comput", "dado", "software", "program", "rede", "sistem", "ia", "algorit")):
            return "Computação", "Computação Geral"
        return "Computação", "Computação Geral"

    # Tecnologia isolada
    if assunto_norm == "tecnologia":
        return "Computação", "Computação Geral"

    # Ciências Sociais Aplicadas
    if "ciencias sociais aplicadas" in assunto_norm:
        if "direito" in subarea_norm:
            return "Direito", "Direito Geral"
        if "economia" in subarea_norm:
            return "Economia", "Economia Política"
        if any(t in subarea_norm for t in ("administra", "gestao", "negocio", "marketing")):
            return "Administração", "Estratégia e Gestão"
        if subarea_norm in ("geral", ""):
            return "Outros", "Geral"
        return "Administração", "Estratégia e Gestão"

    area = (jev_assunto or "Outros").strip()
    subarea = (jev_subarea or "Geral").strip()
    return area, subarea


def _areas_jev_compativeis(
    meta_area: Optional[str],
    jev_area: Optional[str],
    meta_sub: Optional[str] = None,
    jev_sub: Optional[str] = None,
) -> bool:
    """Compara macroáreas do motor temático e do JEV por igualdade, grupo de equivalência ou subárea específica."""
    na, nb = _norm_ascii(meta_area), _norm_ascii(jev_area)
    if na and na == nb:
        return True
    if na and nb and any(na in g and nb in g for g in _GRUPOS_AREA_JEV):
        return True
    ns, nj = _norm_ascii(meta_sub), _norm_ascii(jev_sub)
    return bool(ns and nj and ns not in {"geral", "outros"} and nj not in {"geral", "outros"} and ns == nj)


def _normalizar_cdd(cdd: Optional[str]) -> str:
    if not cdd:
        return ""
    m = _CDD_RE.search(str(cdd))
    return m.group(1) if m else ""


def infer_thematic_area(
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    authors: Optional[List[str]] = None,
    sample_text: str = "",
    cand_area: Optional[str] = None,
    return_rule: bool = False,
    external_subjects: Optional[List[str]] = None,
    cdd: Optional[str] = None,
) -> Tuple[str, str] | Tuple[str, str, str]:
    """Infere (Assunto Principal, Especificação) por motor determinístico ponderado por evidências.

    Pontuação por subárea do catálogo (``taxonomia.CATALOGO_TAXONOMICO``):
    assunto interno CIP (``cand_area``) +12/termo e assuntos externos (``external_subjects``, com
    aliases em inglês traduzidos) +8/termo; CDD/CDU específico +10 e macroárea +4 (Saúde não recebe
    +4 em CDD 616.891*/616.89075); título/subtítulo +15/termo; autor canônico +8 (exceto se o título
    declara outra subárea específica); amostra de texto +1.5/termo distinto, com teto de +6.
    O termo genérico "psiquiatria" em assuntos/amostra é amortecido quando o título não o declara e
    há subárea de Psicologia com pontuação >= 7.
    Subáreas específicas vencem guarda-chuvas, que só vencem sem especialidade com >= 3.0 pontos.
    """
    auths = authors or []
    clean_sample = _limpar_amostra_para_tematica(sample_text or "")
    title_norm = _norm_ascii(f"{title or ''} {subtitle or ''}")
    norm_sample = _norm_ascii(clean_sample[:3500])

    invalid_areas = {
        "titulo", "autor", "brasil", "recurso", "serie", "coautor", "orientador",
        "orientadora", "universidade", "faculdade", "instituto", "edicao", "volume",
        "cdd", "cdu", "ilustrado", "bibliografia", "isbn", "issn", "doi", "feminino",
    }
    subjects_raw: List[str] = []
    cand_valido = bool(
        cand_area and _norm_ascii(cand_area) not in invalid_areas and len(cand_area.strip()) >= 3
    )
    if cand_valido:
        subjects_raw.append(cand_area.strip())  # type: ignore[union-attr]
    ext_norm_parts: List[str] = []
    for s in external_subjects or []:
        if s and _norm_ascii(s) not in invalid_areas and len(s.strip()) >= 3:
            subjects_raw.append(s.strip())
            ext_norm_parts.append(_expandir_aliases_ingles(_norm_ascii(s)))
    cand_norm_txt = _norm_ascii(cand_area) if cand_valido else ""
    ext_norm_txt = " | ".join(ext_norm_parts)
    subjects_norm = " | ".join(p for p in (cand_norm_txt, ext_norm_txt) if p)

    author_variants = [v for a in auths for v in _variantes_autor(a)]

    cdd_norm = _normalizar_cdd(cdd)
    cdd_best_len = 0
    cdd_hits: Dict[int, int] = {}
    if cdd_norm:
        for idx, ent in enumerate(CATALOGO_TAXONOMICO):
            ml = max((len(p) for p in ent.cdd if cdd_norm.startswith(p)), default=0)
            if ml:
                cdd_hits[idx] = ml
                cdd_best_len = max(cdd_best_len, ml)
    macro_areas: set = set()
    if cdd_norm:
        try:
            cdd_int = int(cdd_norm[:3])
        except ValueError:
            cdd_int = -1
        for area_name, faixas in MACRO_CDD.items():
            if any(lo <= cdd_int <= hi for lo, hi in faixas):
                macro_areas.add(area_name)

    cdd_psico_especifico = cdd_norm.startswith(("616.891", "616.89075"))

    scores: List[float] = []
    title_hits: List[int] = []
    for idx, ent in enumerate(CATALOGO_TAXONOMICO):
        n_cand = _contar_termos(cand_norm_txt, ent.keywords)
        n_all = _contar_termos(subjects_norm, ent.keywords)
        s_subj = 12.0 * n_cand + 8.0 * max(0, n_all - n_cand)
        s_title_n = _contar_termos(title_norm, ent.keywords)
        s_cdd = 0.0
        if cdd_hits.get(idx) and cdd_hits[idx] == cdd_best_len:
            s_cdd += 10.0
        if ent.area in macro_areas and not (
            ent.area == "Ciências da Saúde" and cdd_psico_especifico
        ):
            s_cdd += 4.0
        s_sample = min(6.0, 1.5 * _contar_termos(norm_sample, ent.keywords))
        scores.append(s_subj + 15.0 * s_title_n + s_cdd + s_sample)
        title_hits.append(s_title_n)

    # Autores canônicos (+7), exceto quando o título declara explicitamente outra subárea específica
    title_declares_specific = any(
        title_hits[i] > 0 and not e.umbrella for i, e in enumerate(CATALOGO_TAXONOMICO)
    )
    if author_variants:
        for idx, ent in enumerate(CATALOGO_TAXONOMICO):
            if not ent.authors:
                continue
            if title_declares_specific and title_hits[idx] == 0 and not ent.umbrella:
                continue
            n = sum(
                1 for a in ent.authors
                if any(_termo_regex(_norm_ascii(a)).search(v) for v in author_variants)
            )
            scores[idx] += 8.0 * n

    # Amortecimento do co-assunto genérico "Psiquiatria" quando o título não o declara e há Psicologia forte
    if (
        "psiquiatria" not in title_norm
        and "psiquiatric" not in title_norm
        and any(e.area == "Psicologia" and scores[i] >= 7.0 for i, e in enumerate(CATALOGO_TAXONOMICO))
    ):
        for idx, ent in enumerate(CATALOGO_TAXONOMICO):
            if ent.subarea != "Psiquiatria":
                continue
            kws_sem_generico = tuple(k for k in ent.keywords if _norm_ascii(k) != "psiquiatria")
            n_cand = _contar_termos(cand_norm_txt, ent.keywords)
            n_all_old = _contar_termos(subjects_norm, ent.keywords)
            n_all_new = _contar_termos(subjects_norm, kws_sem_generico)
            n_cand_new = _contar_termos(cand_norm_txt, kws_sem_generico)
            old_subj = 12.0 * n_cand + 8.0 * max(0, n_all_old - n_cand)
            new_subj = 12.0 * n_cand + 8.0 * max(0, n_all_new - n_cand_new)
            old_sample = min(6.0, 1.5 * _contar_termos(norm_sample, ent.keywords))
            new_sample = min(6.0, 1.5 * _contar_termos(norm_sample, kws_sem_generico))
            scores[idx] += (new_subj - old_subj) + (new_sample - old_sample)

    best_spec = max(
        ((s, -i) for i, s in enumerate(scores) if not CATALOGO_TAXONOMICO[i].umbrella and s >= 3.0),
        default=None,
    )
    best_umb = max(
        ((s, -i) for i, s in enumerate(scores) if CATALOGO_TAXONOMICO[i].umbrella and s >= 1.5),
        default=None,
    )
    chosen = best_spec or best_umb

    area, subarea, rule = "Outros", "Geral", "fallback_default"
    if chosen is not None:
        ent = CATALOGO_TAXONOMICO[-chosen[1]]
        area, subarea, rule = ent.area, ent.subarea, ent.rule
        if ent.subarea == "Filosofia Contemporânea":
            foucault_ctx = f"{title_norm} {norm_sample} {subjects_norm} {' '.join(author_variants)}"
            if "foucault" in foucault_ctx:
                rule = "descritores_foucault"
    elif cand_area and subjects_raw and subjects_raw[0] == cand_area.strip():
        # Fallback: área candidata válida da ficha catalográfica (CIP) ajustada à taxonomia
        cand_clean = subjects_raw[0]
        cand_norm = _norm_ascii(cand_clean)
        if "psicologia" in cand_norm:
            area, subarea, rule = "Psicologia", cand_clean.title(), f"area_cip_{cand_clean}"
        elif "filosofia" in cand_norm:
            area, subarea, rule = "Filosofia", cand_clean.title(), f"area_cip_{cand_clean}"
        elif "fisiologia" in cand_norm:
            area, subarea, rule = "Fisiologia", cand_clean.title(), f"area_cip_{cand_clean}"
        elif "direito" in cand_norm:
            area, subarea, rule = "Direito", cand_clean.title(), f"area_cip_{cand_clean}"
        elif "educacao" in cand_norm or "pedagogia" in cand_norm:
            area, subarea, rule = "Educação", cand_clean.title(), f"area_cip_{cand_clean}"
        else:
            area, subarea, rule = cand_clean.title(), cand_clean.title(), f"area_cip_{cand_clean}"

    if return_rule:
        return (area, subarea, rule)
    return (area, subarea)


def _eh_erro_transitorio_cache(val: Any) -> bool:
    """True quando o item de cache representa erro transitório (HTTP 429/5xx ou falha de conexão/timeout)."""
    if not isinstance(val, (tuple, list)) or len(val) < 2:
        return False
    st = val[1]
    transitorios = (StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE, StatusConsultaProvedor.FALHA_CONEXAO)
    if isinstance(st, StatusConsultaProvedor):
        return st in transitorios
    return isinstance(st, str) and st in ("ERRO_HTTP_OU_LIMITE", "FALHA_CONEXAO")


def _serialize_cache_item(item: Any) -> Any:
    """Serializa estruturas de dados de cache bibliográfico para JSON seguro."""
    if isinstance(item, StatusConsultaProvedor):
        return {"__enum_StatusConsultaProvedor__": item.value}
    if isinstance(item, tuple):
        return [_serialize_cache_item(x) for x in item]
    if isinstance(item, list):
        return [_serialize_cache_item(x) for x in item]
    if isinstance(item, dict):
        return {k: _serialize_cache_item(v) for k, v in item.items()}
    return item


def _deserialize_cache_item(raw: Any) -> Any:
    """Deserializa estruturas de dados de cache bibliográfico a partir de JSON."""
    if isinstance(raw, dict):
        if "__enum_StatusConsultaProvedor__" in raw:
            try:
                return StatusConsultaProvedor(raw["__enum_StatusConsultaProvedor__"])
            except Exception:
                return StatusConsultaProvedor.CONFIRMADO
        return {k: _deserialize_cache_item(v) for k, v in raw.items()}
    if isinstance(raw, list):
        return tuple(_deserialize_cache_item(x) for x in raw)
    return raw


class MetadataEnricher:
    """Consolidates metadata by querying public academic, book, and national registry APIs."""

    _openalex_last_request_time: float = 0.0
    _openalex_circuit_breaker_until: float = 0.0
    _OPENALEX_MIN_INTERVAL: float = 0.15
    _OPENALEX_COOLDOWN_SECONDS: float = 30.0

    @staticmethod
    def _aguardar_cadencia_provedor(provedor: str, min_interval: Optional[float] = None, min_provider_interval: Optional[float] = None) -> None:
        interval = min_interval if min_interval is not None else min_provider_interval
        _aguardar_cadencia_provedor(provedor, min_provider_interval=interval)

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        online: bool = True,
        enable_disk_cache: Optional[bool] = None,
        cache_bibliografico: Optional[bool] = None,
    ):
        self.timeout = timeout
        self.online = online
        self._local = threading.local()
        self._custom_session: Optional[Any] = None
        self.google_books_api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
        self._cache: Dict[str, Any] = {}
        self._cache_lock = threading.Lock()

        env_cache = os.getenv("ORGPDF_CACHE_BIBLIOGRAFICO")
        if cache_bibliografico is False or (env_cache and env_cache.strip().lower() in ("false", "0", "nao", "no")):
            self._enable_disk_cache = False
            self._enable_cache = False
        else:
            self._enable_cache = True
            if cache_bibliografico is not None:
                self._enable_disk_cache = cache_bibliografico
            elif env_cache is not None:
                self._enable_disk_cache = env_cache.strip().lower() in ("true", "1", "sim", "yes")
            elif enable_disk_cache is not None:
                self._enable_disk_cache = enable_disk_cache
            else:
                self._enable_disk_cache = ("PYTEST_CURRENT_TEST" not in os.environ) or bool(os.getenv("ORGPDF_FORCE_DISK_CACHE"))

        cache_dir_env = os.getenv("ORGPDF_CACHE_DIR")
        self._cache_dir = Path(cache_dir_env) if cache_dir_env else (Path.home() / ".cache" / "organizador_pdf")
        self._cache_file = self._cache_dir / "bibliographic_cache.json"

        if self._enable_disk_cache:
            self._load_disk_cache()

    def _load_disk_cache(self) -> None:
        """Carrega consultas bibliográficas em cache de disco persistente."""
        try:
            if self._cache_file.exists():
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                    if isinstance(raw_data, dict):
                        for k, v in raw_data.items():
                            item = _deserialize_cache_item(v)
                            if _eh_erro_transitorio_cache(item):
                                continue  # purga erros transitórios (429/5xx/timeout) gravados em execuções antigas
                            self._cache[k] = item
        except Exception as exc:
            logger.debug("Aviso ao carregar cache bibliográfico do disco: %s", exc)

    def _save_disk_cache(self) -> None:
        """Salva consultas bibliográficas no disco de forma atômica e segura."""
        if not getattr(self, "_enable_disk_cache", True):
            return
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            serialized = {
                k: _serialize_cache_item(v)
                for k, v in self._cache.items()
                if not _eh_erro_transitorio_cache(v)
            }
            tmp_path = self._cache_file.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(serialized, f, ensure_ascii=False, indent=2)
            tmp_path.replace(self._cache_file)
        except Exception as exc:
            logger.debug("Aviso ao persistir cache bibliográfico no disco: %s", exc)

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

    @property
    def provider_status(self) -> Dict[str, StatusConsultaProvedor]:
        if not hasattr(self._local, "provider_status") or self._local.provider_status is None:
            self._local.provider_status = {}
        return self._local.provider_status

    @provider_status.setter
    def provider_status(self, val: Dict[str, StatusConsultaProvedor]) -> None:
        self._local.provider_status = val

    def set_provider_status(self, provider: str, status: StatusConsultaProvedor) -> None:
        """Define o status da consulta para um determinado provedor."""
        self.provider_status[provider] = status

    @property
    def consultas_realizadas(self) -> List[ConsultaFonteExterna]:
        if not hasattr(self._local, "consultas") or self._local.consultas is None:
            self._local.consultas = []
        return self._local.consultas

    @consultas_realizadas.setter
    def consultas_realizadas(self, val: List[ConsultaFonteExterna]) -> None:
        self._local.consultas = val

    @property
    def sources_consulted(self) -> List[ConsultaFonteExterna]:
        return self.consultas_realizadas

    def _registrar_consulta(
        self,
        fonte: str,
        consulta: str,
        status: StatusConsultaProvedor,
        resultado_encontrado: bool = False,
        tem_texto_integral: bool = False,
        url_texto_integral: Optional[str] = None,
        apenas_referencia: bool = False,
        metadados: Optional[Dict[str, Any]] = None,
        erro_ou_aviso: Optional[str] = None,
        cache_hit: bool = False,
        origem_resposta: str = "rede_ao_vivo",
        motivo_dispensa: Optional[str] = None,
    ) -> ConsultaFonteExterna:
        registro = ConsultaFonteExterna(
            fonte=fonte,
            consulta=consulta,
            data_consulta=datetime.now(timezone.utc).isoformat(),
            status=status,
            resultado_encontrado=resultado_encontrado,
            tem_texto_integral=tem_texto_integral,
            url_texto_integral=url_texto_integral,
            apenas_referencia=apenas_referencia,
            metadados_recuperados=metadados or {},
            erro_ou_aviso=erro_ou_aviso,
            cache_hit=cache_hit,
            origem_resposta=origem_resposta,
            motivo_dispensa=motivo_dispensa,
        )
        if not hasattr(self._local, "consultas") or self._local.consultas is None:
            self._local.consultas = []
        self._local.consultas.append(registro)
        slug_fonte = fonte.lower().replace(" ", "_").replace("/", "_")
        self.set_provider_status(slug_fonte, status)
        return registro

    def _registrar_consulta_cache_hit(
        self,
        fonte: str,
        consulta: str,
        status: StatusConsultaProvedor,
        resultado_encontrado: bool = False,
        metadados: Optional[Dict[str, Any]] = None,
        erro_ou_aviso: Optional[str] = None,
    ) -> ConsultaFonteExterna:
        """Registra consulta atendida pelo cache em disco, identificando cache_hit e origem_resposta."""
        return self._registrar_consulta(
            fonte=fonte,
            consulta=consulta,
            status=status,
            resultado_encontrado=resultado_encontrado,
            metadados=metadados,
            erro_ou_aviso=erro_ou_aviso,
            cache_hit=True,
            origem_resposta="cache_disco",
        )

    def _registrar_dispensa(
        self,
        fonte: str,
        consulta: str,
        motivo: str = "Campos essenciais já confirmados por outra fonte autoritativa",
    ) -> ConsultaFonteExterna:
        """Registra dispensa formal de fonte com motivo rastreável."""
        return self._registrar_consulta(
            fonte=fonte,
            consulta=consulta,
            status=StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO,
            resultado_encontrado=False,
            cache_hit=False,
            origem_resposta="dispensada",
            motivo_dispensa=motivo,
        )

    def _atualizar_status_consulta(
        self,
        fonte: str,
        novo_status: StatusConsultaProvedor,
        erro_ou_aviso: Optional[str] = None,
    ) -> None:
        """Atualiza o status da consulta mais recente para a fonte indicada."""
        if hasattr(self._local, "consultas") and self._local.consultas:
            for c in reversed(self._local.consultas):
                if c.fonte.lower() == fonte.lower() or c.fonte.lower().startswith(fonte.lower()):
                    c.status = novo_status
                    if erro_ou_aviso:
                        c.erro_ou_aviso = erro_ou_aviso
                    break
        slug_fonte = fonte.lower().replace(" ", "_").replace("/", "_")
        self.set_provider_status(slug_fonte, novo_status)

    def _get_timeout(self) -> Tuple[float, float] | float:
        """Return connect and read timeouts (defaulting to 3.0s connect)."""
        if isinstance(self.timeout, (tuple, list)):
            return tuple(self.timeout)
        return (3.0, float(self.timeout))

    def _get_from_cache(self, key: str) -> Tuple[bool, Any]:
        """Check cache for key and return (hit, deepcopy(val))."""
        if not getattr(self, "_enable_cache", True):
            return False, None
        with self._cache_lock:
            if key in self._cache:
                val = self._cache[key]
                return True, copy.deepcopy(val) if val is not None else None
        return False, None

    def _set_cache(self, key: str, value: Any) -> None:
        """Save a deepcopy of value into cache and persist to disk if disk cache enabled."""
        if not getattr(self, "_enable_cache", True):
            return
        with self._cache_lock:
            self._cache[key] = copy.deepcopy(value) if value is not None else None
            if getattr(self, "_enable_disk_cache", True):
                self._save_disk_cache()

    def clear_cache(self) -> None:
        """Clear the in-memory and disk query cache."""
        with self._cache_lock:
            self._cache.clear()
            try:
                if self._cache_file.exists():
                    self._cache_file.unlink(missing_ok=True)
            except Exception:
                pass

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
        area_princ, sub_area, matched_rule = infer_thematic_area(
            title=title_candidate,
            subtitle=candidates.raw_subtitle,
            authors=candidates.raw_authors or [],
            sample_text=candidates.sample_text,
            cand_area=cand_area,
            external_subjects=list(getattr(candidates, "raw_subjects", None) or []),
            cdd=getattr(candidates, "raw_cdd", None),
            return_rule=True,
        )

        metadata = PublicationMetadata(
            title=title_candidate or "Publicação Sem Título",
            subtitle=candidates.raw_subtitle,
            authors=candidates.raw_authors or [],
            papel_autoria=getattr(candidates, "papel_autoria", None) or "autor",
            publisher=candidates.raw_publisher,
            edition=candidates.raw_edition,
            year=candidates.raw_year,
            city=candidates.raw_city,
            area=area_princ,
            area_principal=area_princ,
            subarea=sub_area,
            regra_classificacao_tematica=matched_rule,
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

        self.provider_status = {}
        self.consultas_realizadas = []
        self._local.external_subjects = []

        jev_assunto = getattr(jev_result, "assunto", "Outros")
        jev_subarea = getattr(jev_result, "subarea", "Geral")

        eh_generico_jev = (
            not jev_assunto
            or jev_assunto.lower() in (
                "outros", "geral", "humanas", "ciencias humanas", "ciências humanas",
                "exatas", "biologicas", "biológicas", "sociais"
            )
        )

        if not eh_generico_jev and (matched_rule in ("padrao_geral", "fallback_default") or area_princ == "Outros") and jev_assunto != "Outros":
            norm_area, norm_sub = _normalizar_area_jev_fallback(jev_assunto, jev_subarea)
            metadata.area_principal = norm_area
            metadata.area = norm_area
            if norm_sub and norm_sub.lower() != "geral":
                metadata.subarea = norm_sub
            else:
                metadata.subarea = norm_sub or "Geral"
        else:
            metadata.area_principal = area_princ
            metadata.area = area_princ
            metadata.subarea = sub_area

        metadata.justificativa_jev = getattr(jev_result, "justificativa", "")
        metadata.assunto_jev = getattr(jev_result, "assunto", "Outros")
        metadata.subarea_jev = getattr(jev_result, "subarea", "Geral")
        metadata.justificativa_classificacao_tematica = (
            f"Classificação temática consolidada em {metadata.area_principal} / {metadata.subarea} "
            f"pela regra '{metadata.regra_classificacao_tematica or 'inferencia_local'}'."
        )
        metadata.candidatos_originais = getattr(candidates, "candidatos_originais", {})

        # Inicializa a reconciliação campo a campo com os dados de origem do próprio PDF
        if metadata.title and metadata.title != "Publicação Sem Título":
            metadata.reconciliacao_campos["titulo"] = {
                "valor_escolhido": metadata.title,
                "fonte": candidates.title_source or "pdf_local",
                "evidencia": f"Origem: {candidates.title_source or 'pdf_local'}",
                "valor_original": candidates.raw_title,
            }
        if metadata.subtitle:
            metadata.reconciliacao_campos["subtitulo"] = {
                "valor_escolhido": metadata.subtitle,
                "fonte": "pdf_local",
                "evidencia": "Extraído da página inicial do PDF",
                "valor_original": candidates.raw_subtitle,
            }
        if metadata.authors:
            metadata.reconciliacao_campos["autores"] = {
                "valor_escolhido": metadata.authors,
                "fonte": candidates.author_source or "pdf_local",
                "evidencia": f"Origem: {candidates.author_source or 'pdf_local'}",
                "valor_original": candidates.raw_authors,
            }
        if metadata.publisher:
            metadata.reconciliacao_campos["editora"] = {
                "valor_escolhido": metadata.publisher,
                "fonte": candidates.publisher_source or "pdf_local",
                "evidencia": f"Origem: {candidates.publisher_source or 'pdf_local'}",
                "valor_original": candidates.raw_publisher,
            }
        if metadata.year:
            metadata.reconciliacao_campos["ano"] = {
                "valor_escolhido": metadata.year,
                "fonte": candidates.year_source or "pdf_local",
                "evidencia": f"Origem: {candidates.year_source or 'pdf_local'}",
                "valor_original": candidates.raw_year,
            }
        if metadata.city:
            metadata.reconciliacao_campos["local"] = {
                "valor_escolhido": metadata.city,
                "fonte": candidates.city_source or "pdf_local",
                "evidencia": f"Origem: {candidates.city_source or 'pdf_local'}",
                "valor_original": candidates.raw_city,
            }
        if metadata.edition:
            metadata.reconciliacao_campos["edicao"] = {
                "valor_escolhido": metadata.edition,
                "fonte": "ficha_catalografica" if candidates.raw_edition else "pdf_local",
                "evidencia": "Ficha catalográfica ou texto inicial",
                "valor_original": candidates.raw_edition,
            }
        if metadata.identifiers.isbn:
            metadata.reconciliacao_campos["isbn"] = {
                "valor_escolhido": metadata.identifiers.isbn,
                "fonte": candidates.isbn_source or "pdf_local",
                "evidencia": f"Origem: {candidates.isbn_source or 'pdf_local'}",
                "valor_original": candidates.isbn,
            }
        if metadata.identifiers.doi:
            metadata.reconciliacao_campos["doi"] = {
                "valor_escolhido": metadata.identifiers.doi,
                "fonte": candidates.doi_source or "pdf_local",
                "evidencia": f"Origem: {candidates.doi_source or 'pdf_local'}",
                "valor_original": candidates.doi,
            }
        if metadata.identifiers.issn:
            metadata.reconciliacao_campos["issn"] = {
                "valor_escolhido": metadata.identifiers.issn,
                "fonte": candidates.issn_source or "pdf_local",
                "evidencia": f"Origem: {candidates.issn_source or 'pdf_local'}",
                "valor_original": candidates.issn,
            }

        if not self.online:
            logger.info("Enriquecimento online desativado (modo offline). Mantendo metadados candidatos.")
            for p in (
                "brasil_api", "google_books", "openlibrary", "crossref", "datacite", "openalex",
                "doaj", "pubmed", "europe_pmc", "eric", "scielo", "bvs", "redalyc", "google_scholar"
            ):
                self.set_provider_status(p, StatusConsultaProvedor.NAO_CONSULTADO)
            metadata.status_por_provedor = {k: v.value for k, v in self.provider_status.items()}
            metadata.confidence_classification = jev_result.classification_confidence
            self._avaliar_qualidade_reconciliacao(metadata, candidates, jev_result)
            self._evaluate_needs_review(metadata, jev_result, sources_consulted=[])
            self._montar_registro_rastreabilidade(metadata, candidates, jev_result)
            return metadata

        sources_consulted: List[str] = []

        # 1. Query by DOI if present (direct authoritative identifier lookup)
        if doi:
            logger.info("DOI found (%s, prob=%.2f). Querying Crossref, DataCite, OpenAlex, DOAJ, Europe PMC & PubMed.", doi, doi_prob)
            cr_meta = self.fetch_crossref_by_doi(doi)
            if cr_meta:
                corresp, div = confirmar_mesma_obra(candidates, cr_meta, "Crossref", is_identifier_match=True)
                if corresp:
                    self._merge_metadata(metadata, cr_meta, authoritative=True, provider="Crossref", candidates=candidates)
                    sources_consulted.append("Crossref (DOI)")
                    metadata.doi_source = "api_crossref"
                    if cr_meta.get("title"):
                        metadata.title_source = "api_crossref"
                    if div:
                        metadata.conflitos_registrados.append(div)
                elif div:
                    metadata.conflitos_registrados.append(div)
                    metadata.needs_review = True

            # DataCite como conector complementar para DOI (trabalhos acadêmicos, datasets, repositórios)
            # Não presumir que ausência em Crossref significa ausência total
            if "Crossref (DOI)" not in sources_consulted or not tem_campos_essenciais_completos(metadata, metadata.classification):
                dc_meta = self.fetch_datacite_by_doi(doi)
                if dc_meta:
                    corresp, div = confirmar_mesma_obra(candidates, dc_meta, "DataCite", is_identifier_match=True)
                    if corresp:
                        self._merge_metadata(metadata, dc_meta, authoritative=True, provider="DataCite", candidates=candidates)
                        sources_consulted.append("DataCite (DOI)")
                        if not metadata.doi_source:
                            metadata.doi_source = "api_datacite"
                        if div:
                            metadata.conflitos_registrados.append(div)
                    elif div:
                        metadata.conflitos_registrados.append(div)
                        metadata.needs_review = True
            else:
                self.set_provider_status("datacite", StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO)
                self._registrar_dispensa("DataCite", f"doi:{doi}", "Campos essenciais já confirmados com alta confiança por Crossref")

            # Consulta OpenAlex se ainda houver campos essenciais pendentes ou DOI não confirmado
            if not tem_campos_essenciais_completos(metadata, metadata.classification):
                oa_meta = self.fetch_openalex_by_doi(doi)
                if oa_meta:
                    corresp, div = confirmar_mesma_obra(candidates, oa_meta, "OpenAlex", is_identifier_match=True)
                    if corresp:
                        self._merge_metadata(metadata, oa_meta, authoritative=True, provider="OpenAlex", candidates=candidates)
                        sources_consulted.append("OpenAlex (DOI)")
                        if not metadata.doi_source:
                            metadata.doi_source = "api_openalex"
                        if div:
                            metadata.conflitos_registrados.append(div)
                    elif div:
                        metadata.conflitos_registrados.append(div)
                        metadata.needs_review = True
            else:
                self.set_provider_status("openalex", StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO)
                self._registrar_dispensa("OpenAlex", f"doi:{doi}", "Campos essenciais já confirmados por fonte anterior")

            if not tem_campos_essenciais_completos(metadata, metadata.classification):
                doaj_meta = self.fetch_doaj_by_doi(doi)
                if doaj_meta:
                    corresp, div = confirmar_mesma_obra(candidates, doaj_meta, "DOAJ", is_identifier_match=True)
                    if corresp:
                        self._merge_metadata(metadata, doaj_meta, authoritative=True, provider="DOAJ", candidates=candidates)
                        sources_consulted.append("DOAJ (DOI)")
                        if div:
                            metadata.conflitos_registrados.append(div)
                    elif div:
                        metadata.conflitos_registrados.append(div)
                        metadata.needs_review = True
            else:
                self.set_provider_status("doaj", StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO)
                self._registrar_dispensa("DOAJ", f"doi:{doi}", "Campos essenciais já confirmados por fonte anterior")

            epmc_meta = self.fetch_europe_pmc_by_doi(doi)
            if epmc_meta:
                corresp, div = confirmar_mesma_obra(candidates, epmc_meta, "Europe PMC", is_identifier_match=True)
                if corresp:
                    self._merge_metadata(metadata, epmc_meta, authoritative=True, provider="Europe PMC", candidates=candidates)
                    sources_consulted.append("Europe PMC (DOI)")
                    if div:
                        metadata.conflitos_registrados.append(div)
                elif div:
                    metadata.conflitos_registrados.append(div)
                    metadata.needs_review = True

            pm_meta = self.fetch_pubmed_by_doi(doi)
            if pm_meta:
                corresp, div = confirmar_mesma_obra(candidates, pm_meta, "PubMed", is_identifier_match=True)
                if corresp:
                    self._merge_metadata(metadata, pm_meta, authoritative=True, provider="PubMed", candidates=candidates)
                    sources_consulted.append("PubMed (DOI)")
                    if div:
                        metadata.conflitos_registrados.append(div)
                elif div:
                    metadata.conflitos_registrados.append(div)
                    metadata.needs_review = True

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
                corresp, div = confirmar_mesma_obra(candidates, br_meta, "Brasil API / CBL", is_identifier_match=True)
                if corresp:
                    br_meta["source"] = "api_brasilapi"
                    self._merge_metadata(metadata, br_meta, authoritative=True, provider="Brasil API / CBL", candidates=candidates)
                    sources_consulted.append("Brasil API / CBL (ISBN)")
                    metadata.isbn_source = "api_brasilapi"
                    if br_meta.get("title"):
                        metadata.title_source = "api_brasilapi"
                    if div:
                        metadata.conflitos_registrados.append(div)
                elif div:
                    metadata.conflitos_registrados.append(div)
                    metadata.needs_review = True

            if not tem_campos_essenciais_completos(metadata, metadata.classification):
                gb_meta = self.fetch_google_books_by_isbn(isbn)
                if gb_meta:
                    corresp, div = confirmar_mesma_obra(candidates, gb_meta, "Google Books", is_identifier_match=True)
                    if corresp:
                        gb_meta["source"] = "api_google_books"
                        self._merge_metadata(metadata, gb_meta, authoritative=True, provider="Google Books", candidates=candidates)
                        sources_consulted.append("Google Books (ISBN)")
                        if not metadata.isbn_source:
                            metadata.isbn_source = "api_google_books"
                        if gb_meta.get("title") and not br_meta:
                            metadata.title_source = "api_google_books"
                        if div:
                            metadata.conflitos_registrados.append(div)
                    elif div:
                        metadata.conflitos_registrados.append(div)
                        metadata.needs_review = True
            else:
                self.set_provider_status("google_books", StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO)
                self._registrar_dispensa("Google Books", f"isbn:{isbn}", "Campos essenciais já confirmados por Brasil API / CBL")

            if not tem_campos_essenciais_completos(metadata, metadata.classification):
                ol_meta = self.fetch_openlibrary_by_isbn(isbn)
                if ol_meta:
                    corresp, div = confirmar_mesma_obra(candidates, ol_meta, "OpenLibrary", is_identifier_match=True)
                    if corresp:
                        ol_meta["source"] = "api_openlibrary"
                        self._merge_metadata(metadata, ol_meta, authoritative=True, provider="OpenLibrary", candidates=candidates)
                        sources_consulted.append("OpenLibrary (ISBN)")
                        if not metadata.isbn_source:
                            metadata.isbn_source = "api_openlibrary"
                        if div:
                            metadata.conflitos_registrados.append(div)
                    elif div:
                        metadata.conflitos_registrados.append(div)
                        metadata.needs_review = True
            else:
                self.set_provider_status("openlibrary", StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO)
                self._registrar_dispensa("OpenLibrary", f"isbn:{isbn}", "Campos essenciais já confirmados por fontes anteriores")

            # Fallbacks adicionais por ISBN válido quando autoria ou ano seguem ausentes
            if self.online and (not metadata.authors or not metadata.year):
                expected_title = metadata.title or title_candidate
                cr_meta = self.fetch_crossref_by_isbn(isbn)
                if cr_meta:
                    corresp, div = confirmar_mesma_obra(candidates, cr_meta, "Crossref", is_identifier_match=True)
                    if corresp:
                        self._merge_metadata(metadata, cr_meta, authoritative=False, provider="Crossref", candidates=candidates)
                        sources_consulted.append("Crossref (ISBN)")
                        if not metadata.isbn_source:
                            metadata.isbn_source = "api_crossref"
                        if div:
                            metadata.conflitos_registrados.append(div)
                    elif div:
                        metadata.conflitos_registrados.append(div)
                        metadata.needs_review = True
                if not metadata.authors or not metadata.year:
                    oa_isbn_meta = self.fetch_openalex_by_isbn(isbn, expected_title=expected_title)
                    if oa_isbn_meta:
                        corresp, div = confirmar_mesma_obra(candidates, oa_isbn_meta, "OpenAlex", is_identifier_match=True)
                        if corresp:
                            self._merge_metadata(metadata, oa_isbn_meta, authoritative=False, provider="OpenAlex", candidates=candidates)
                            sources_consulted.append("OpenAlex (ISBN)")
                            if not metadata.isbn_source:
                                metadata.isbn_source = "api_openalex"
                            if div:
                                metadata.conflitos_registrados.append(div)
                        elif div:
                            metadata.conflitos_registrados.append(div)
                            metadata.needs_review = True
                if not metadata.authors:
                    web_meta = self.fetch_web_search_by_isbn(
                        isbn,
                        expected_title=expected_title,
                        sample_text=candidates.sample_text if candidates else None,
                    )
                    if web_meta:
                        self._merge_metadata(metadata, web_meta, authoritative=False, provider="Web Search (ISBN)", candidates=candidates)
                        sources_consulted.append("Web Search (ISBN)")

        # Consolidar status por provedor e isbn_verified (CA-02)
        metadata.status_por_provedor = {
            k: (v.value if hasattr(v, "value") else str(v))
            for k, v in self.provider_status.items()
        }
        isbn_confirmado = any(
            status_val == StatusConsultaProvedor.CONFIRMADO.value
            for prov, status_val in metadata.status_por_provedor.items()
            if prov in ("brasil_api", "google_books", "openlibrary", "crossref", "openalex")
        )
        metadata.isbn_verified = bool(isbn_confirmado)

        # Se nenhum provedor confirmou mas houve resposta sem confirmação formal
        sem_confirmacao = any(
            status_val == StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO.value
            for prov, status_val in metadata.status_por_provedor.items()
            if prov in ("brasil_api", "google_books", "openlibrary")
        )
        if not isbn_confirmado and sem_confirmacao:
            metadata.needs_review = True
            msg_nc = "Identificador ISBN sem confirmação explícita no retorno da API pública"
            if msg_nc not in metadata.review_reasons:
                metadata.review_reasons.append(msg_nc)
            metadata.confidence_metadata = min(getattr(metadata, "confidence_metadata", 1.0), 0.7)

        # 3. Fallback: Search by title if no identifier succeeded OR essential fields are still incomplete
        should_fallback_title = (not sources_consulted) or (not tem_campos_essenciais_completos(metadata, metadata.classification))

        from .classifier_jev import is_invalid_or_disclaimer_title

        clean_title = (title_candidate or "").strip()
        is_valid_query = (
            clean_title
            and not clean_title.startswith("---")
            and len(clean_title) >= 4
            and "page break" not in clean_title.lower()
            and not is_invalid_or_disclaimer_title(clean_title)
        )

        if should_fallback_title and is_valid_query:
            logger.info("Applying Title Search Fallback for '%s' across APIs.", clean_title)
            self._apply_title_fallback(
                clean_title,
                jev_result.classification,
                metadata,
                sources_consulted,
                sample_text=candidates.sample_text if candidates else None,
            )

        # Fallback complementar pelo Nome do Arquivo caso ainda faltem fontes ou campos essenciais
        if (not sources_consulted or not tem_campos_essenciais_completos(metadata, metadata.classification)):
            fn_title = getattr(candidates, "fn_title", None)
            if fn_title and fn_title != clean_title and len(fn_title) >= 4 and not is_invalid_or_disclaimer_title(fn_title):
                logger.info("Applying Filename Title Search Fallback for '%s' across APIs.", fn_title)
                fn_authors = [getattr(candidates, "fn_author")] if getattr(candidates, "fn_author", None) else None
                self._apply_title_fallback(
                    fn_title,
                    jev_result.classification,
                    metadata,
                    sources_consulted,
                    sample_text=candidates.sample_text if candidates else None,
                    query_authors=fn_authors,
                )

        # Corroboração dupla de autoria local (fn_author + sample_text) quando buscas não retornaram autor
        if not metadata.authors and getattr(candidates, "fn_author", None):
            from .classifier_jev import is_likely_author_list, normalizar_lista_autores
            fn_auth = candidates.fn_author
            if is_likely_author_list(fn_auth):
                auth_norm = _norm_ascii(fn_auth).lower()
                auth_tokens = [
                    t for t in re.split(r"\W+", auth_norm)
                    if len(t) >= 4 and t not in {"silva", "santos", "oliveira", "souza", "costa", "pereira", "rodrigues", "almeida"}
                ]
                sample_norm = _norm_ascii(getattr(candidates, "sample_text", "") or "").lower()
                if auth_tokens and any(tok in sample_norm for tok in auth_tokens):
                    cleaned_auths = normalizar_lista_autores([fn_auth], book_title=metadata.title)
                    if cleaned_auths:
                        metadata.authors = cleaned_auths
                        metadata.autor_principal = cleaned_auths[0]
                        metadata.author_source = "corroboracao_arquivo_texto"

        # Reavaliação temática final com todas as evidências (assuntos de APIs, CIP, CDD, título, autores)
        external_subjects: List[str] = list(getattr(self._local, "external_subjects", None) or [])
        for subj in (getattr(candidates, "raw_subjects", None) or []):
            if subj and subj not in external_subjects:
                external_subjects.append(subj)

        # Se o título consolidado for curto ou genérico, injeta fn_title nos assuntos externos para preservar o sinal
        fn_title_cand = getattr(candidates, "fn_title", None)
        if fn_title_cand and (not metadata.title or len(metadata.title) < 10 or is_invalid_or_disclaimer_title(metadata.title)):
            if fn_title_cand not in external_subjects:
                external_subjects.append(fn_title_cand)
        ap, sa, mr = infer_thematic_area(
            title=metadata.title,
            subtitle=metadata.subtitle,
            authors=metadata.authors,
            sample_text=candidates.sample_text,
            cand_area=cand_area,
            external_subjects=external_subjects,
            cdd=getattr(candidates, "raw_cdd", None),
            return_rule=True,
        )
        # Preserva classificação anterior quando a nova não encontra nada e já havia resultado específico
        if not (ap == "Outros" and metadata.area_principal not in (None, "", "Outros")):
            metadata.area_principal = ap
            metadata.subarea = sa
            metadata.area = ap
            metadata.regra_classificacao_tematica = mr

        # Corroboração Dupla de Autoria Local (fn_author + sample_text) quando APIs externas retornam sem autor
        if not metadata.authors and getattr(candidates, "fn_author", None):
            from .classifier_jev import is_likely_author_list, normalizar_lista_autores
            cand_fn_a = candidates.fn_author.strip()
            if is_likely_author_list(cand_fn_a):
                fn_auth_clean = re.sub(r"(?i)\s+et\s+al\.?$", "", cand_fn_a).strip()
                tokens = [t.strip() for t in re.split(r"[\s,]+", fn_auth_clean) if t.strip()]
                cand_surnames = [
                    unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
                    for t in tokens if len(t) >= 4
                ]
                sample_norm = unicodedata.normalize("NFKD", candidates.sample_text or "").encode("ascii", "ignore").decode().lower()
                if cand_surnames and any(s in sample_norm for s in cand_surnames):
                    norm_a = normalizar_lista_autores([cand_fn_a])
                    if norm_a:
                        metadata.authors = norm_a
                        metadata.author_source = "corroboracao_arquivo_texto"

        metadata.source_apis = sources_consulted
        metadata.fontes_consultadas = list(self.consultas_realizadas)
        metadata.confidence_classification = jev_result.classification_confidence
        self._avaliar_qualidade_reconciliacao(metadata, candidates, jev_result)
        self._evaluate_needs_review(metadata, jev_result, sources_consulted=sources_consulted)
        self._montar_registro_rastreabilidade(metadata, candidates, jev_result)
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

        # Diferenciação rigorosa dos 3 níveis de identificadores (Achado 3)
        raw_isbn = metadata.identifiers.isbn or (jev_result.candidates.isbn if jev_result else None)
        raw_doi = metadata.identifiers.doi or (jev_result.candidates.doi if jev_result else None)
        raw_issn = metadata.identifiers.issn or (jev_result.candidates.issn if jev_result else None)

        identificador_extraido = bool(raw_isbn or raw_doi or raw_issn)
        identificador_validado = (
            (bool(raw_isbn) and is_valid_isbn(raw_isbn, strict=True))
            or (bool(raw_doi) and bool(normalizar_doi(raw_doi)))
            or (bool(raw_issn) and is_valid_issn(raw_issn, strict=True))
        )
        identificador_confirmado_obra = bool(
            metadata.isbn_verified
            or (metadata.doi_source and "api_" in str(metadata.doi_source))
            or any("DOI" in s or "ISBN" in s for s in sources_consulted)
        )
        has_verified_id = identificador_confirmado_obra

        title_source_aval = getattr(metadata, "title_source", None)
        titulo_de_fonte_oficial = bool(
            has_verified_id
            or title_source_aval in (
                "api_crossref", "api_brasilapi", "api_google_books", "api_openlibrary", "api_datacite", "api_openalex",
            )
        )

        # 1. Validações estritas de título
        t_words = [w for w in re.split(r"[\s\-_/]+", t) if w]
        if t in ("Sem Título", "Publicação Sem Título") or len(t) < 4:
            meta_conf = 0.1
            metadata.needs_review = True
            if "Documento sem título bibliográfico identificável" not in metadata.review_reasons:
                metadata.review_reasons.append("Documento sem título bibliográfico identificável")
        elif is_invalid_or_disclaimer_title(t) and not titulo_de_fonte_oficial:
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
        elif len(t_words) <= 1 and t_lower in GENERIC_SINGLE_WORD_TITLES:
            meta_conf = 0.35
            metadata.needs_review = True
            msg = f"Título unipalavra ou fragmento genérico ('{t}')"
            if msg not in metadata.review_reasons:
                metadata.review_reasons.append(msg)
        elif len(t_words) == 1 and not sources_consulted and not has_verified_id:
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
                meta_conf -= 0.10
                # Ano ausente só deve exigir revisão se a autoria também for desconhecida e sem identificador
                if not metadata.authors and not has_verified_id:
                    metadata.needs_review = True
                    msg = f"{metadata.classification.capitalize()} sem ano e sem autoria identificada"
                    if msg not in metadata.review_reasons:
                        metadata.review_reasons.append(msg)
            if not metadata.city:
                # Cidade/local de publicação ausente é metadado incompleto ([S. l.]), não incerteza de identidade
                meta_conf -= 0.05
        elif metadata.classification in ("artigo", "artigo_cientifico") and not metadata.authors:
            meta_conf -= 0.25
            if not has_verified_id:
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
        # 8. Validação de classificação temática e confidencialidade
        assunto_meta = metadata.area_principal or metadata.area
        subarea_meta = metadata.subarea
        subj_conf = getattr(jev_result, "subject_confidence", 0.0) if jev_result else 0.0
        assunto_inconclusivo = (
            (assunto_meta in ("Outros", "outros", "Desconhecido", "") and subarea_meta in ("Geral", "Outros", "") and subj_conf <= 0.0)
            or (jev_result and getattr(jev_result, "subject_confidence", 1.0) <= 0.0 and getattr(jev_result, "assunto", "") in ("Outros", "outros", ""))
        )
        if assunto_inconclusivo and not has_verified_id:
            meta_conf = min(meta_conf, 0.50)
            metadata.needs_review = True
            msg_assunto = "Classificação temática inconclusiva (assunto Outros/Geral com confiança temática zero)"
            if msg_assunto not in metadata.review_reasons:
                metadata.review_reasons.append(msg_assunto)

        # Incompatibilidade entre classificação e destino estrutural
        if metadata.classification in ("outros", "relatorio") and not has_verified_id:
            if jev_result and getattr(jev_result, "classification", "") in ("artigo", "artigo_cientifico"):
                metadata.needs_review = True
                msg_incomp = f"Incompatibilidade entre tipo JEV ('{jev_result.classification}') e tipo classificado ('{metadata.classification}')"
                if msg_incomp not in metadata.review_reasons:
                    metadata.review_reasons.append(msg_incomp)

        # Validação estrita de evidência estrutural do título contra fragmentos de prosa
        title_source = getattr(metadata, "title_source", None)
        is_suspicious_prose = bool(
            len(t.split()) > 8 or t.endswith(".") or (len(t) > 60 and ("," in t or ";" in t))
        )
        if title_source == "texto_nativo" and is_suspicious_prose and not sources_consulted and not has_verified_id:
            meta_conf = min(meta_conf, 0.60)
            metadata.needs_review = True
            msg_ev = f"Título extraído de texto corrido ('{t[:40]}...') com características de prosa sem validação estrutural"
            if msg_ev not in metadata.review_reasons:
                metadata.review_reasons.append(msg_ev)

        # Regra de corte de confiança de metadados: abaixo de 0.70 sem identificador público verificado exige revisão
        if meta_conf < 0.70 and not has_verified_id:
            metadata.needs_review = True
            msg_conf = f"Confiança de metadados insuficiente ({meta_conf:.2f}) sem identificador bibliográfico verificado"
            if not any("confiança de metadados" in r.lower() for r in metadata.review_reasons):
                metadata.review_reasons.append(msg_conf)

        if sources_consulted and not metadata.needs_review:
            meta_conf = max(meta_conf, 0.85 if "Title Search" in str(sources_consulted) else 0.95)
        elif metadata.needs_review:
            meta_conf = min(meta_conf, 0.70)

        metadata.confidence_metadata = max(0.1, min(1.0, round(meta_conf, 2)))
        metadata.confidence = min(metadata.confidence_classification, metadata.confidence_metadata)

        # Cálculo das 7 decisões bibliográficas separadas (Passo 9)
        tem_titulo_solido = bool(
            t
            and t not in ("Sem Título", "Publicação Sem Título")
            and len(t) >= 4
            and (titulo_de_fonte_oficial or not is_invalid_or_disclaimer_title(t))
            and not t.startswith("---")
            and not (len([w for w in re.split(r"[\s\-_/]+", t) if w]) <= 1 and t_lower in GENERIC_SINGLE_WORD_TITLES)
        )
        tem_autores = bool(metadata.authors)
        # Ignora divergências que representem reconciliação pela base oficial canônica
        conflitos_reais = [
            c for c in metadata.conflitos_registrados
            if not any(padrao in c.lower() for padrao in ("adotado como âncora", "registro canônico adotado", "adotad", "mantido ano explícito", "mantido ano explicito"))
        ]
        tem_conflitos = len(conflitos_reais) > 0

        # 1. Identidade Corroborada por Fonte vs. Identidade Plausível Local
        tem_fonte_confirmadora = False
        fonte_confirmadora_desc = ""

        if identificador_confirmado_obra and not tem_conflitos and tem_autores:
            tem_fonte_confirmadora = True
            id_tipo = "DOI" if metadata.identifiers.doi else "ISBN"
            fonte_confirmadora_desc = f"Identificador {id_tipo} confirmado em base bibliográfica oficial com autoria compatível"
        elif any("Title Search" in s for s in sources_consulted) and not tem_conflitos and tem_autores:
            tem_fonte_confirmadora = True
            fonte_confirmadora_desc = "Busca por título confirmada com alta relevância e autoria compatível em base bibliográfica externa"
        elif any(s in ("Crossref (DOI)", "Brasil API / CBL (ISBN)", "Google Books (ISBN)", "DataCite (DOI)", "OpenAlex (DOI)", "OpenLibrary (ISBN)", "Crossref (ISBN)", "OpenAlex (ISBN)", "Web Search (ISBN)") for s in sources_consulted) and not tem_conflitos and tem_autores:
            tem_fonte_confirmadora = True
            fonte_confirmadora_desc = f"Consulta direta confirmada ({', '.join(sources_consulted)}) com autoria compatível"
        elif getattr(metadata, "title_source", "") == "ficha_catalografica" and getattr(metadata, "cip_pages", None) and tem_titulo_solido and tem_autores and not tem_conflitos:
            tem_fonte_confirmadora = True
            fonte_confirmadora_desc = "Ficha catalográfica formal (CIP/AACR2) identificada no documento com dados de autoria"

        identidade_corroborada_fonte = bool(tem_fonte_confirmadora and not tem_conflitos)
        identidade_plausivel_local = bool(tem_titulo_solido and tem_autores and not tem_conflitos)
        identidade_confirmada = bool(identidade_corroborada_fonte)

        # Evidência documental explícita que sustenta a decisão
        if tem_conflitos:
            evidencia_identidade = f"Identidade não confirmada devido a divergências entre fontes ({'; '.join(conflitos_reais)})"
        elif identidade_corroborada_fonte:
            evidencia_identidade = f"Corroborada por fonte independente: {fonte_confirmadora_desc}"
        elif identidade_plausivel_local:
            if not self.online:
                evidencia_identidade = "Identidade plausível extraída da estrutura do documento (folha de rosto/texto nativo; modo offline sem consultas de rede)"
            else:
                evidencia_identidade = "Identidade plausível extraída da estrutura do documento (folha de rosto/texto nativo), sem corroboração por fonte externa independente"
        else:
            razoes_id = []
            if not tem_titulo_solido:
                razoes_id.append("título ausente ou inválido")
            if not tem_autores:
                razoes_id.append("autoria não identificada")
            evidencia_identidade = f"Identidade não confirmada ({', '.join(razoes_id)})"

        integridade_arquivo_ok = True
        jev_cand = getattr(jev_result, "candidates", None) if jev_result else None
        tot_pages = getattr(metadata, "total_pages", None) or (getattr(jev_cand, "total_pages", 0) if jev_cand else 0) or 0
        cip_pages = getattr(metadata, "cip_pages", None) or (getattr(jev_cand, "cip_pages", None) if jev_cand else None)
        if metadata.classification == "livro" and tot_pages > 0:
            if tot_pages < 25 or (cip_pages and cip_pages > 0 and tot_pages < 0.6 * cip_pages):
                integridade_arquivo_ok = False
        if metadata.classification == "arquivo_parcial":
            integridade_arquivo_ok = False

        tipo_documental_confiante = bool(
            metadata.classification not in ("outros", "arquivo_parcial")
            and (
                jev_result is None
                or jev_result.classification_confidence >= 0.50
                or has_verified_id
                or (bool(sources_consulted) and metadata.classification in ("livro", "artigo", "artigo_cientifico", "tese", "dissertacao"))
            )
        )

        assunto_meta = metadata.area_principal or metadata.area
        subarea_meta = metadata.subarea
        assunto_confiante = not (
            assunto_meta in ("Outros", "outros", "Desconhecido", "")
            and subarea_meta in ("Geral", "Outros", "")
        )

        metadados_completos = tem_campos_essenciais_completos(metadata, metadata.classification)

        # Rastreabilidade estrita de busca externa
        fontes_pendentes: List[str] = []
        fontes_consultadas_ou_dispensadas: List[str] = []

        status_provedores = getattr(metadata, "status_por_provedor", {})
        for prov, st_val in status_provedores.items():
            if st_val in (
                StatusConsultaProvedor.FALHA_CONEXAO.value,
                StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE.value,
                StatusConsultaProvedor.REQUER_AUTENTICACAO_OU_CHAVE.value,
            ):
                fontes_pendentes.append(f"{prov} ({st_val})")
            elif st_val in (
                StatusConsultaProvedor.CONFIRMADO.value,
                StatusConsultaProvedor.NAO_ENCONTRADO.value,
                StatusConsultaProvedor.DIVERGENTE.value,
                StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO.value,
                StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO.value,
                StatusConsultaProvedor.NAO_SE_APLICA.value,
                StatusConsultaProvedor.BLOQUEIO_PRIVACIDADE.value,
            ):
                fontes_consultadas_ou_dispensadas.append(prov)

        total_consultas_registradas = len(sources_consulted) + len(fontes_consultadas_ou_dispensadas)

        if not self.online:
            busca_externa_esgotada = False
            status_busca_externa = "desativada_offline"
        elif fontes_pendentes:
            busca_externa_esgotada = False
            status_busca_externa = "pendencias_de_rede"
        elif total_consultas_registradas == 0:
            busca_externa_esgotada = False
            status_busca_externa = "incompleta_sem_consultas"
        else:
            busca_externa_esgotada = True
            status_busca_externa = "concluida_esgotada"

        campos_irresolvidos: List[str] = []
        if not tem_titulo_solido:
            campos_irresolvidos.append("titulo")
        if not metadata.authors:
            campos_irresolvidos.append("autores")
        if not metadata.year:
            campos_irresolvidos.append("ano")
        if metadata.classification in ("livro", "apostila") and not metadata.publisher:
            campos_irresolvidos.append("editora")
        if metadata.classification in ("artigo", "artigo_cientifico") and not (getattr(metadata, "journal", None) or getattr(metadata, "periodico", None)):
            campos_irresolvidos.append("periodico")

        # Critério de Decisão de Identidade e Revisão Manual
        identidade_valida_para_fluxo = (
            identidade_confirmada
            or identidade_plausivel_local
        )

        motivos_revisao: List[str] = []
        if not identidade_valida_para_fluxo:
            motivos_revisao.append("Incerteza de identidade bibliográfica (título ou autor inválidos)")
        if not tipo_documental_confiante:
            motivos_revisao.append("Tipo documental incerto ou não classificado (confiança < 0.50)")
        if not integridade_arquivo_ok:
            motivos_revisao.append("Integridade física do arquivo PDF suspeita (páginas insuficientes ou parcial)")
        if tem_conflitos:
            motivos_revisao.append(f"Conflito bibliográfico irreconciliável ({'; '.join(conflitos_reais)})")
        if not assunto_confiante and not has_verified_id:
            motivos_revisao.append("Área temática genérica ou desconhecida sem identificador oficial")
        if metadata.needs_review:
            for r in metadata.review_reasons:
                if r not in motivos_revisao:
                    motivos_revisao.append(r)

        revisao_manual_necessaria = len(motivos_revisao) > 0
        motivo_revisao_manual = "; ".join(motivos_revisao) if motivos_revisao else None

        metadata.identidade_confirmada = identidade_confirmada
        metadata.identidade_corroborada_fonte = identidade_corroborada_fonte
        metadata.identidade_plausivel_local = identidade_plausivel_local
        metadata.evidencia_identidade = evidencia_identidade
        metadata.status_busca_externa = status_busca_externa
        metadata.metadados_completos = metadados_completos
        metadata.tipo_documental_confiante = tipo_documental_confiante
        metadata.assunto_confiante = assunto_confiante
        metadata.integridade_arquivo_ok = integridade_arquivo_ok
        metadata.busca_externa_esgotada = busca_externa_esgotada
        metadata.revisao_manual_necessaria = revisao_manual_necessaria
        metadata.motivo_revisao_manual = motivo_revisao_manual
        metadata.fontes_pendentes = fontes_pendentes
        metadata.campos_irresolvidos = campos_irresolvidos
        metadata.needs_review = revisao_manual_necessaria

    def _apply_title_fallback(
        self,
        title: str,
        classification: PublicationType,
        metadata: PublicationMetadata,
        sources_consulted: List[str],
        sample_text: Optional[str] = None,
        query_authors: Optional[List[str]] = None,
    ) -> None:
        """Search APIs by title according to publication type priority with relevance and author checks."""
        from .classifier_jev import is_invalid_or_disclaimer_title

        if not title or is_invalid_or_disclaimer_title(title):
            logger.info("Ignorando busca não-autoritativa por título inválido ou marca d'água ('%s').", title)
            return

        candidate_authors = metadata.authors or query_authors or []
        clean_t = re.sub(r"[^\w\s]", "", title.lower()).strip()
        clean_t_words = [w for w in re.split(r"[\s\-_/]+", clean_t) if w]
        if not candidate_authors and (len(clean_t_words) <= 1 or clean_t in GENERIC_SUBJECT_TITLES):
            logger.info("Ignorando busca não-autoritativa por título genérico ('%s') sem autoria confirmada.", title)
            return

        variantes = gerar_variantes_de_busca(title, candidate_authors)
        cand_obj = ExtractedCandidates(
            raw_title=metadata.title if (metadata.title and not is_invalid_or_disclaimer_title(metadata.title)) else title,
            raw_authors=candidate_authors,
            raw_year=metadata.year,
            raw_publisher=metadata.publisher,
            sample_text=sample_text,
        )

        def _tentar_incorporar(res: Optional[Dict[str, Any]], provider_name: str) -> bool:
            if not res:
                return False
            corresp, div = confirmar_mesma_obra(cand_obj, res, provider_name, is_identifier_match=False)
            if corresp:
                self._atualizar_status_consulta(provider_name, StatusConsultaProvedor.CONFIRMADO)
                self._merge_metadata(metadata, res, authoritative=False, provider=provider_name)
                tag_src = f"{provider_name} (Title Search)"
                if tag_src not in sources_consulted:
                    sources_consulted.append(tag_src)
                return True
            elif div:
                self._atualizar_status_consulta(provider_name, StatusConsultaProvedor.DIVERGENTE, erro_ou_aviso=div)
                metadata.conflitos_registrados.append(div)
                metadata.needs_review = True
            return False

        def _consultar_provedor_com_variantes(fetch_fn, provider_name: str) -> bool:
            """Executa busca com variações justificadas de título até obter resultado válido."""
            for q in variantes:
                res = fetch_fn(q)
                if _tentar_incorporar(res, provider_name):
                    return True
            return False

        if classification in ("artigo", "artigo_cientifico"):
            # 1. Google Acadêmico opcional com chave (se não houver chave, registra status e não finge consulta)
            if os.getenv("SERPAPI_API_KEY") or os.getenv("GOOGLE_SCHOLAR_API_KEY"):
                self.fetch_google_scholar(title)

            provedores_artigo = [
                ("SciELO", lambda q: self.fetch_scielo(title=q)),
                ("OpenAlex", lambda q: self.fetch_openalex_by_title(q)),
                ("DOAJ", lambda q: self.fetch_doaj_by_title(q)),
            ]

            area_norm = (metadata.area_principal or "").lower()
            if any(k in area_norm for k in ("psicologia", "fisiologia", "saude", "medicina", "biomedica")):
                provedores_artigo.extend([
                    ("Europe PMC", lambda q: self.fetch_europe_pmc_by_title(q)),
                    ("PubMed", lambda q: self.fetch_pubmed_by_title(q)),
                    ("BVS", lambda q: self.fetch_bvs(title=q)),
                ])
            elif "educa" in area_norm:
                provedores_artigo.append(("ERIC", lambda q: self.fetch_eric_by_title(q)))

            provedores_artigo.append(("Redalyc", lambda q: self.fetch_redalyc(title=q)))
            provedores_artigo.append(("Crossref", lambda q: self.fetch_crossref_by_title(q)))

            # Executa buscas progressivas: continua nas fontes restantes até que todos os campos essenciais estejam preenchidos
            for i, (prov_name, fetch_fn) in enumerate(provedores_artigo):
                _consultar_provedor_com_variantes(fetch_fn, prov_name)
                if tem_campos_essenciais_completos(metadata, classification):
                    logger.info("Busca bibliográfica por título satisfez campos essenciais para '%s' após %s.", title, prov_name)
                    for rem_prov, _ in provedores_artigo[i+1:]:
                        self.set_provider_status(rem_prov.lower().replace(" ", "_"), StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO)
                        self._registrar_dispensa(rem_prov, f"titulo:{title}", f"Campos essenciais já confirmados por {prov_name}")
                    break

        elif classification in ("livro", "apostila"):
            provedores_livro = [
                ("Google Books", lambda q: self.fetch_google_books_by_title(q)),
                ("OpenLibrary", lambda q: self.fetch_openlibrary_by_title(q)),
            ]
            for i, (prov_name, fetch_fn) in enumerate(provedores_livro):
                _consultar_provedor_com_variantes(fetch_fn, prov_name)
                if tem_campos_essenciais_completos(metadata, classification):
                    logger.info("Busca bibliográfica de livro satisfez campos essenciais para '%s' após %s.", title, prov_name)
                    for rem_prov, _ in provedores_livro[i+1:]:
                        self.set_provider_status(rem_prov.lower().replace(" ", "_"), StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO)
                        self._registrar_dispensa(rem_prov, f"titulo:{title}", f"Campos essenciais já confirmados por {prov_name}")
                    break

        elif classification in ("tese", "dissertacao", "dissertacao_tese"):
            provedores_tese = [
                ("Crossref", lambda q: self.fetch_crossref_by_title(q)),
                ("Google Books", lambda q: self.fetch_google_books_by_title(q)),
                ("OpenAlex", lambda q: self.fetch_openalex_by_title(q)),
            ]
            for i, (prov_name, fetch_fn) in enumerate(provedores_tese):
                _consultar_provedor_com_variantes(fetch_fn, prov_name)
                if tem_campos_essenciais_completos(metadata, classification):
                    for rem_prov, _ in provedores_tese[i+1:]:
                        self.set_provider_status(rem_prov.lower().replace(" ", "_"), StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO)
                        self._registrar_dispensa(rem_prov, f"titulo:{title}", f"Campos essenciais já confirmados por {prov_name}")
                    break

        else:
            # Classification 'outros' ou não classificada
            provedores_geral = [
                ("Google Books", lambda q: self.fetch_google_books_by_title(q)),
                ("OpenLibrary", lambda q: self.fetch_openlibrary_by_title(q)),
                ("Crossref", lambda q: self.fetch_crossref_by_title(q)),
                ("OpenAlex", lambda q: self.fetch_openalex_by_title(q)),
            ]
            for i, (prov_name, fetch_fn) in enumerate(provedores_geral):
                matched = _consultar_provedor_com_variantes(fetch_fn, prov_name)
                if matched:
                    if prov_name in ("Google Books", "OpenLibrary") and (metadata.publisher or metadata.identifiers.isbn):
                        metadata.classification = "livro"
                    elif prov_name in ("Crossref", "OpenAlex") and (getattr(metadata, "journal", None) or metadata.identifiers.doi):
                        metadata.classification = "artigo"
                    if tem_campos_essenciais_completos(metadata, metadata.classification):
                        for rem_prov, _ in provedores_geral[i+1:]:
                            self.set_provider_status(rem_prov.lower().replace(" ", "_"), StatusConsultaProvedor.DISPENSADO_JA_CONFIRMADO)
                            self._registrar_dispensa(rem_prov, f"titulo:{title}", f"Campos essenciais já confirmados por {prov_name}")
                        break


    # ----------------- BRASIL API (CBL) -----------------
    def fetch_brasil_api_isbn(self, isbn: str) -> Optional[Dict[str, Any]]:
        """Fetch official Brazilian ISBN registration data from Brasil API (CBL / Mercado Editorial)."""
        clean_isbn = re.sub(r"[^0-9X]", "", str(isbn).upper())
        if not is_valid_isbn(clean_isbn, strict=False):
            self.set_provider_status("brasil_api", StatusConsultaProvedor.NAO_CONSULTADO)
            return None
        cache_key = f"brasilapi:isbn:{clean_isbn}"
        hit, val = self._get_from_cache(cache_key)
        if hit and isinstance(val, tuple) and self.online:
            # Complementação única de entradas antigas confirmadas sem autoria (ex.: corrida Promise.any da Brasil API)
            res_old = val[0]
            if (
                res_old
                and val[1] == StatusConsultaProvedor.CONFIRMADO
                and not res_old.get("authors")
                and not res_old.get("_providers_checked")
            ):
                completo = self._brasil_api_complementar(clean_isbn, res_old, ("google-books", "cbl", "mercado-editorial"))
                completo["_providers_checked"] = True
                self._set_cache(cache_key, (completo, val[1], val[2] if len(val) >= 3 else None))
                hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                cached_err = val[2] if len(val) >= 3 else None
                self.set_provider_status("brasil_api", st)
                self._registrar_consulta(
                    fonte="Brasil API / CBL",
                    consulta=f"isbn:{clean_isbn}",
                    status=st,
                    resultado_encontrado=bool(res),
                    metadados=res or {},
                    erro_ou_aviso=cached_err,
                )
                return res
            st = StatusConsultaProvedor.CONFIRMADO if val else StatusConsultaProvedor.NAO_ENCONTRADO
            self._registrar_consulta(
                fonte="Brasil API / CBL",
                consulta=f"isbn:{clean_isbn}",
                status=st,
                resultado_encontrado=bool(val),
                metadados=val or {},
            )
            return val

        url = f"https://brasilapi.com.br/api/isbn/v1/{clean_isbn}"
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        erro_aviso: Optional[str] = None
        http_code: Optional[int] = None
        try:
            _aguardar_cadencia_provedor("brasil_api")
            resp = self.session.get(url, timeout=self._get_timeout())
            http_code = resp.status_code if isinstance(resp.status_code, int) else None
            if resp.status_code == 200:
                data = resp.json()
                ret_isbn = data.get("isbn")
                if ret_isbn:
                    if is_same_isbn(clean_isbn, ret_isbn):
                        status = StatusConsultaProvedor.CONFIRMADO
                    else:
                        logger.warning("Brasil API retornou ISBN divergente (%s != %s). Rejeitando resposta.", ret_isbn, clean_isbn)
                        status = StatusConsultaProvedor.DIVERGENTE
                        erro_aviso = f"ISBN retornado divergente: {ret_isbn} != {clean_isbn}"
                        self._set_cache(cache_key, (None, status, erro_aviso))
                        self.set_provider_status("brasil_api", status)
                        self._registrar_consulta(
                            fonte="Brasil API / CBL",
                            consulta=f"isbn:{clean_isbn}",
                            status=status,
                            resultado_encontrado=False,
                            erro_ou_aviso=erro_aviso,
                        )
                        return None
                else:
                    status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO

                raw_title = data.get("title")
                if not raw_title:
                    status = StatusConsultaProvedor.NAO_ENCONTRADO
                    erro_aviso = "Registro retornado sem título"
                    self._set_cache(cache_key, (None, status, erro_aviso))
                    self.set_provider_status("brasil_api", status)
                    self._registrar_consulta(
                        fonte="Brasil API / CBL",
                        consulta=f"isbn:{clean_isbn}",
                        status=status,
                        resultado_encontrado=False,
                        erro_ou_aviso=erro_aviso,
                    )
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
                    "isbn": clean_isbn if status == StatusConsultaProvedor.CONFIRMADO else None,
                }
                _anexar_subjects(result, data.get("subjects") or data.get("subject"))
            elif resp.status_code == 404:
                status = StatusConsultaProvedor.NAO_ENCONTRADO
            elif resp.status_code == 429:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = "HTTP 429: Limite de requisições excedido na Brasil API / CBL."
            elif resp.status_code in (500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Instabilidade ou erro interno do servidor na Brasil API / CBL."
            else:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Resposta inesperada da Brasil API / CBL."
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.debug("Brasil API connection/timeout error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Falha de conexão / timeout na Brasil API / CBL: {e}"
        except requests.RequestException as e:
            logger.debug("Brasil API ISBN error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Erro na requisição à Brasil API / CBL: {e}"

        # Multi-provider: completa autoria/ano ausentes (ou tenta provedores individuais em 404/5xx p/ ISBN brasileiro)
        eh_isbn_brasileiro = clean_isbn.startswith(("97885", "97865", "85", "65"))
        precisa_complemento = bool(result and (not result.get("authors") or not result.get("year")))
        falhou_para_isbn_br = bool(
            result is None
            and eh_isbn_brasileiro
            and http_code is not None
            and (http_code == 404 or 500 <= http_code <= 599)
        )
        if precisa_complemento or falhou_para_isbn_br:
            completo = self._brasil_api_complementar(clean_isbn, result)
            if completo:
                completo["_providers_checked"] = True
                result = completo
                status = StatusConsultaProvedor.CONFIRMADO
                erro_aviso = None

        self._set_cache(cache_key, (result, status, erro_aviso))
        self.set_provider_status("brasil_api", status)
        self._registrar_consulta(
            fonte="Brasil API / CBL",
            consulta=f"isbn:{clean_isbn}",
            status=status,
            resultado_encontrado=bool(result),
            metadados=result or {},
            erro_ou_aviso=erro_aviso,
        )
        return result

    fetch_brasil_api_by_isbn = fetch_brasil_api_isbn

    def _brasil_api_complementar(
        self,
        clean_isbn: str,
        result: Optional[Dict[str, Any]],
        providers: Tuple[str, ...] = ("cbl", "mercado-editorial", "google-books", "open-library"),
    ) -> Optional[Dict[str, Any]]:
        """Consulta provedores individuais da Brasil API e mescla campos ausentes (autores, ano, etc.)."""
        base: Optional[Dict[str, Any]] = dict(result) if result else None
        for prov in providers:
            if base and base.get("authors") and base.get("year"):
                break
            try:
                resp = self.session.get(
                    f"https://brasilapi.com.br/api/isbn/v1/{clean_isbn}?providers={prov}",
                    timeout=self._get_timeout(),
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except (requests.RequestException, ValueError) as e:
                logger.debug("Brasil API provider %s error: %s", prov, e)
                continue
            if not isinstance(data, dict):
                continue
            ret_isbn = data.get("isbn")
            if ret_isbn and not is_same_isbn(clean_isbn, ret_isbn):
                continue
            authors = [a.strip() for a in (data.get("authors") or []) if isinstance(a, str) and a.strip()]
            year_raw = data.get("year")
            try:
                year = int(year_raw) if year_raw else None
            except (TypeError, ValueError):
                year = None
            location = data.get("location")
            city = location.split(",")[0].strip() if isinstance(location, str) and location else None
            if base is None:
                raw_title = data.get("title")
                if not raw_title:
                    continue
                base = {
                    "title": re.sub(r"^\(ED\)\s*", "", raw_title, flags=re.I).strip(),
                    "subtitle": data.get("subtitle"),
                    "authors": authors,
                    "publisher": data.get("publisher"),
                    "year": year,
                    "city": city,
                    "isbn": clean_isbn,
                }
                _anexar_subjects(base, data.get("subjects") or data.get("subject"))
                continue
            if not base.get("authors") and authors:
                base["authors"] = authors
            if not base.get("year") and year:
                base["year"] = year
            for campo, valor in (("subtitle", data.get("subtitle")), ("publisher", data.get("publisher")), ("city", city)):
                if valor and not base.get(campo):
                    base[campo] = valor
            if not base.get("subjects"):
                _anexar_subjects(base, data.get("subjects") or data.get("subject"))
        if base is not None and not base.get("isbn"):
            base["isbn"] = clean_isbn
        return base

    # ----------------- CROSSREF API -----------------
    def fetch_crossref_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """Fetch metadata from Crossref API by DOI."""
        clean_doi = normalizar_doi(doi)
        if not clean_doi:
            self.set_provider_status("crossref", StatusConsultaProvedor.NAO_CONSULTADO)
            return None
        norm_doi = clean_doi.lower()
        cache_key = f"crossref:doi:{norm_doi}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                cached_err = val[2] if len(val) >= 3 else None
                self.set_provider_status("crossref", st)
                self._registrar_consulta(
                    fonte="Crossref",
                    consulta=f"doi:{clean_doi}",
                    status=st,
                    resultado_encontrado=bool(res),
                    metadados=res or {},
                    erro_ou_aviso=cached_err,
                )
                return res
            st = StatusConsultaProvedor.CONFIRMADO if val else StatusConsultaProvedor.NAO_ENCONTRADO
            self._registrar_consulta(
                fonte="Crossref",
                consulta=f"doi:{clean_doi}",
                status=st,
                resultado_encontrado=bool(val),
                metadados=val or {},
            )
            return val

        quoted_doi = urllib.parse.quote(clean_doi, safe="/")
        url = f"https://api.crossref.org/works/{quoted_doi}"
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        erro_aviso: Optional[str] = None
        try:
            _aguardar_cadencia_provedor("crossref")
            resp = self.session.get(url, timeout=self._get_timeout())
            if resp.status_code == 200:
                item = resp.json().get("message", {})
                result = self._parse_crossref_item(item)
                status = StatusConsultaProvedor.CONFIRMADO if result else StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
            elif resp.status_code == 404:
                status = StatusConsultaProvedor.NAO_ENCONTRADO
            elif resp.status_code == 429:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = "HTTP 429: Limite de taxa (rate limit) excedido na API Crossref."
            elif resp.status_code in (500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Instabilidade/erro de servidor na API Crossref."
            else:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Resposta inesperada da API Crossref."
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.debug("Crossref DOI lookup connection/timeout error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Falha de conexão ou timeout ao consultar Crossref por DOI: {e}"
        except requests.RequestException as e:
            logger.debug("Crossref DOI lookup error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Erro na requisição à API Crossref por DOI: {e}"

        self._set_cache(cache_key, (result, status, erro_aviso))
        self.set_provider_status("crossref", status)
        self._registrar_consulta(
            fonte="Crossref",
            consulta=f"doi:{clean_doi}",
            status=status,
            resultado_encontrado=bool(result),
            metadados=result or {},
            erro_ou_aviso=erro_aviso,
        )
        return result

    # ----------------- DATACITE API -----------------
    def _parse_datacite_item(self, attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Parse DataCite REST API work attributes into standardized dictionary."""
        titles = attrs.get("titles", [])
        title = titles[0].get("title") if titles else None

        creators = attrs.get("creators", [])
        authors = []
        for c in creators:
            name = c.get("name")
            if not name and (c.get("familyName") or c.get("givenName")):
                fn = c.get("familyName", "")
                gn = c.get("givenName", "")
                name = f"{fn}, {gn}".strip(", ")
            if name:
                authors.append(name)

        year = attrs.get("publicationYear")
        if not year and attrs.get("published"):
            m_yr = re.search(r"\b(19\d\d|20\d\d)\b", str(attrs.get("published")))
            if m_yr:
                year = int(m_yr.group(1))

        publisher = attrs.get("publisher")
        doi = attrs.get("doi")
        url = attrs.get("url")
        res_type = attrs.get("types", {}).get("resourceTypeGeneral") or ""

        return {
            "title": title,
            "authors": authors,
            "year": int(year) if year else None,
            "publisher": publisher,
            "doi": normalizar_doi(doi) if doi else None,
            "url": url,
            "resource_type": res_type,
            "source": "api_datacite",
        }

    def fetch_datacite_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """Fetch metadata from DataCite API by DOI."""
        clean_doi = normalizar_doi(doi)
        if not clean_doi:
            self.set_provider_status("datacite", StatusConsultaProvedor.NAO_CONSULTADO)
            return None
        norm_doi = clean_doi.lower()
        cache_key = f"datacite:doi:{norm_doi}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                cached_err = val[2] if len(val) >= 3 else None
                self.set_provider_status("datacite", st)
                self._registrar_consulta(
                    fonte="DataCite",
                    consulta=f"doi:{clean_doi}",
                    status=st,
                    resultado_encontrado=bool(res),
                    metadados=res or {},
                    erro_ou_aviso=cached_err,
                )
                return res
            st = StatusConsultaProvedor.CONFIRMADO if val else StatusConsultaProvedor.NAO_ENCONTRADO
            self._registrar_consulta(
                fonte="DataCite",
                consulta=f"doi:{clean_doi}",
                status=st,
                resultado_encontrado=bool(val),
                metadados=val or {},
            )
            return val

        quoted_doi = urllib.parse.quote(clean_doi, safe="/")
        url = f"https://api.datacite.org/dois/{quoted_doi}"
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        erro_aviso: Optional[str] = None
        try:
            _aguardar_cadencia_provedor("crossref")
            resp = self.session.get(url, timeout=self._get_timeout())
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                attrs = data.get("attributes", {})
                result = self._parse_datacite_item(attrs)
                status = StatusConsultaProvedor.CONFIRMADO if result else StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
            elif resp.status_code == 404:
                status = StatusConsultaProvedor.NAO_ENCONTRADO
            elif resp.status_code == 429:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = "HTTP 429: Limite de taxa (rate limit) excedido na API DataCite."
            elif resp.status_code in (500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Instabilidade/erro de servidor na API DataCite."
            else:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Resposta inesperada da API DataCite."
        except (requests.Timeout, requests.ConnectionError) as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Falha de conexão ou timeout ao consultar DataCite por DOI: {e}"
        except Exception as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Erro na requisição à API DataCite por DOI: {e}"

        self._set_cache(cache_key, (result, status, erro_aviso))
        self.set_provider_status("datacite", status)
        self._registrar_consulta(
            fonte="DataCite",
            consulta=f"doi:{clean_doi}",
            status=status,
            resultado_encontrado=bool(result),
            metadados=result or {},
            erro_ou_aviso=erro_aviso,
        )
        return result

    def fetch_crossref_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search Crossref API by title."""
        norm_title = title.strip().lower()
        cache_key = f"crossref:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                cached_err = val[2] if len(val) >= 3 else None
            else:
                res = val
                st = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO if res else StatusConsultaProvedor.NAO_ENCONTRADO
                cached_err = None
            self._registrar_consulta(
                fonte="Crossref",
                consulta=f"title:{title}",
                status=st,
                resultado_encontrado=bool(res),
                metadados=res or {},
                erro_ou_aviso=cached_err,
            )
            return res

        url = "https://api.crossref.org/works"
        params = {"query.title": title, "rows": 3}
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        erro_aviso: Optional[str] = None
        try:
            _aguardar_cadencia_provedor("crossref")
            resp = self.session.get(url, params=params, timeout=self._get_timeout())
            if resp.status_code == 200:
                items = resp.json().get("message", {}).get("items", [])
                for item in items:
                    parsed = self._parse_crossref_item(item)
                    if is_title_relevant(title, parsed.get("title")):
                        result = parsed
                        break
                status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO if result else StatusConsultaProvedor.NAO_ENCONTRADO
            elif resp.status_code == 429:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = "HTTP 429: Limite de taxa (rate limit) excedido na API Crossref."
            elif resp.status_code in (500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Instabilidade/erro de servidor na API Crossref."
            else:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Resposta inesperada da API Crossref."
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.debug("Crossref title search connection/timeout error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Falha de conexão ou timeout ao consultar Crossref por título: {e}"
        except requests.RequestException as e:
            logger.debug("Crossref title search error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Erro na requisição ao Crossref por título: {e}"

        self._set_cache(cache_key, (result, status, erro_aviso))
        self._registrar_consulta(
            fonte="Crossref",
            consulta=f"title:{title}",
            status=status,
            resultado_encontrado=bool(result),
            metadados=result or {},
            erro_ou_aviso=erro_aviso,
        )
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

        parsed = {
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
        _anexar_subjects(parsed, item.get("subject"))
        return parsed

    # ----------------- GOOGLE BOOKS API -----------------
    def fetch_google_books_by_isbn(self, isbn: str) -> Optional[Dict[str, Any]]:
        """Fetch metadata from Google Books API by ISBN."""
        clean_isbn = re.sub(r"[^0-9X]", "", str(isbn).upper())
        if not is_valid_isbn(clean_isbn, strict=False):
            self.set_provider_status("google_books", StatusConsultaProvedor.NAO_CONSULTADO)
            return None
        cache_key = f"gbooks:isbn:{clean_isbn}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple) and len(val) == 2:
                res, st = val
                self.set_provider_status("google_books", st)
                self._registrar_consulta(
                    fonte="Google Books",
                    consulta=f"isbn:{clean_isbn}",
                    status=st,
                    resultado_encontrado=bool(res),
                    metadados=res or {},
                )
                return res
            st = StatusConsultaProvedor.CONFIRMADO if val else StatusConsultaProvedor.NAO_ENCONTRADO
            self._registrar_consulta(
                fonte="Google Books",
                consulta=f"isbn:{clean_isbn}",
                status=st,
                resultado_encontrado=bool(val),
                metadados=val or {},
            )
            return val

        url = "https://www.googleapis.com/books/v1/volumes"
        params = {"q": f"isbn:{isbn}"}
        result = self._query_google_books(url, params)
        st = self.provider_status.get("google_books", StatusConsultaProvedor.NAO_ENCONTRADO)
        self._set_cache(cache_key, (result, st))
        self._registrar_consulta(
            fonte="Google Books",
            consulta=f"isbn:{clean_isbn}",
            status=st,
            resultado_encontrado=bool(result),
            metadados=result or {},
        )
        return result

    def fetch_google_books_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search Google Books API by title."""
        norm_title = title.strip().lower()
        cache_key = f"gbooks:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple) and len(val) == 2:
                val, st = val
            else:
                st = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO if val else StatusConsultaProvedor.NAO_ENCONTRADO
            self._registrar_consulta(
                fonte="Google Books",
                consulta=f"title:{title}",
                status=st,
                resultado_encontrado=bool(val),
                metadados=val or {},
            )
            return val

        url = "https://www.googleapis.com/books/v1/volumes"
        params = {"q": f"intitle:{title}", "maxResults": 3}
        result = self._query_google_books(url, params)
        if result:
            st = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
        elif getattr(self._local, "gb_last_error", False):
            st = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
        else:
            st = StatusConsultaProvedor.NAO_ENCONTRADO
        self._set_cache(cache_key, (result, st))
        self._registrar_consulta(
            fonte="Google Books",
            consulta=f"title:{title}",
            status=st,
            resultado_encontrado=bool(result),
            metadados=result or {},
        )
        return result

    def _query_google_books(self, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        is_isbn_query = "isbn:" in str(params.get("q", ""))
        self._local.gb_last_error = False
        try:
            req_params = dict(params)
            if self.google_books_api_key:
                req_params["key"] = self.google_books_api_key
            _aguardar_cadencia_provedor("google_books")
            resp = self.session.get(url, params=req_params, timeout=self._get_timeout())
            if isinstance(resp.status_code, int) and resp.status_code == 429:
                sleep_s = 0.0 if os.getenv("PYTEST_CURRENT_TEST") else 1.0
                if sleep_s > 0:
                    time.sleep(sleep_s)
                _aguardar_cadencia_provedor("google_books")
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
                            if is_isbn_query:
                                self.set_provider_status("google_books", StatusConsultaProvedor.DIVERGENTE)
                            return None
                        else:
                            if is_isbn_query:
                                self.set_provider_status("google_books", StatusConsultaProvedor.CONFIRMADO)
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
                else:
                    if is_isbn_query:
                        self.set_provider_status("google_books", StatusConsultaProvedor.NAO_ENCONTRADO)
            elif resp.status_code == 404:
                if is_isbn_query:
                    self.set_provider_status("google_books", StatusConsultaProvedor.NAO_ENCONTRADO)
            elif resp.status_code in (429, 403):
                logger.warning("Google Books API quota exceeded or forbidden (HTTP %s).", resp.status_code)
                self._local.gb_last_error = True
                if is_isbn_query:
                    self.set_provider_status("google_books", StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE)
            elif resp.status_code in (500, 502, 503, 504):
                self._local.gb_last_error = True
                if is_isbn_query:
                    self.set_provider_status("google_books", StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE)
            else:
                self._local.gb_last_error = True
                if is_isbn_query:
                    self.set_provider_status("google_books", StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE)
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.debug("Google Books API connection/timeout error: %s", e)
            self._local.gb_last_error = True
            if is_isbn_query:
                self.set_provider_status("google_books", StatusConsultaProvedor.FALHA_CONEXAO)
        except requests.RequestException as e:
            logger.debug("Google Books API query error: %s", e)
            self._local.gb_last_error = True
            if is_isbn_query:
                self.set_provider_status("google_books", StatusConsultaProvedor.FALHA_CONEXAO)
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

        parsed_gb = {
            "title": title,
            "subtitle": subtitle,
            "authors": raw_authors,
            "publisher": publisher,
            "year": year,
            "isbn": isbn_val,
            "pages": str(info.get("pageCount")) if info.get("pageCount") else None,
            "area": area,
        }
        _anexar_subjects(parsed_gb, info.get("categories"))
        return parsed_gb

    # ----------------- OPENLIBRARY API -----------------
    def fetch_openlibrary_by_isbn(self, isbn: str) -> Optional[Dict[str, Any]]:
        """Fetch metadata from OpenLibrary API by ISBN."""
        clean_isbn = re.sub(r"[^0-9X]", "", str(isbn).upper())
        if not is_valid_isbn(clean_isbn, strict=False):
            self.set_provider_status("openlibrary", StatusConsultaProvedor.NAO_CONSULTADO)
            return None
        cache_key = f"openlib:isbn:{clean_isbn}"
        hit, val = self._get_from_cache(cache_key)
        if hit and isinstance(val, tuple) and self.online:
            res_old = val[0]
            if (
                res_old
                and val[1] == StatusConsultaProvedor.CONFIRMADO
                and not res_old.get("authors")
                and not res_old.get("_authors_checked")
            ):
                novo = self._openlibrary_jscmd_data(clean_isbn)
                completo = dict(res_old)
                if novo and novo[0] and novo[0].get("authors"):
                    for k, v in novo[0].items():
                        if v and not completo.get(k):
                            completo[k] = v
                completo["_authors_checked"] = True
                self._set_cache(cache_key, (completo, val[1], val[2] if len(val) >= 3 else None))
                hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                cached_err = val[2] if len(val) >= 3 else None
                self.set_provider_status("openlibrary", st)
                self._registrar_consulta(
                    fonte="OpenLibrary",
                    consulta=f"isbn:{clean_isbn}",
                    status=st,
                    resultado_encontrado=bool(res),
                    metadados=res or {},
                    erro_ou_aviso=cached_err,
                )
                return res
            st = StatusConsultaProvedor.CONFIRMADO if val else StatusConsultaProvedor.NAO_ENCONTRADO
            self._registrar_consulta(
                fonte="OpenLibrary",
                consulta=f"isbn:{clean_isbn}",
                status=st,
                resultado_encontrado=bool(val),
                metadados=val or {},
            )
            return val

        url = f"https://openlibrary.org/isbn/{isbn}.json"
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        erro_aviso: Optional[str] = None
        jscmd = self._openlibrary_jscmd_data(clean_isbn)
        if jscmd is not None:
            result, status, erro_aviso = jscmd
        else:
            try:
                resp = self.session.get(url, timeout=self._get_timeout())
                if resp.status_code == 200:
                    data = resp.json()
                    ret_isbns = data.get("isbn_13", []) + data.get("isbn_10", [])
                    if ret_isbns:
                        if any(is_same_isbn(clean_isbn, x) for x in ret_isbns):
                            status = StatusConsultaProvedor.CONFIRMADO
                        else:
                            logger.warning("OpenLibrary retornou registro sem o ISBN consultado (%s). Rejeitando resposta.", clean_isbn)
                            status = StatusConsultaProvedor.DIVERGENTE
                            erro_aviso = f"ISBN retornado divergente: {clean_isbn} não encontrado em {ret_isbns}"
                            self._set_cache(cache_key, (None, status, erro_aviso))
                            self.set_provider_status("openlibrary", status)
                            self._registrar_consulta(
                                fonte="OpenLibrary",
                                consulta=f"isbn:{clean_isbn}",
                                status=status,
                                resultado_encontrado=False,
                                erro_ou_aviso=erro_aviso,
                            )
                            return None
                    else:
                        status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO

                    publish_places = data.get("publish_places", [])
                    city = publish_places[0].strip() if publish_places and isinstance(publish_places[0], str) else None

                    result = {
                        "title": data.get("title"),
                        "subtitle": data.get("subtitle"),
                        "authors": self._openlibrary_resolver_autores(data),
                        "publisher": data.get("publishers", [None])[0] if data.get("publishers") else None,
                        "city": city,
                        "year": int(data["publish_date"][-4:]) if data.get("publish_date") and data["publish_date"][-4:].isdigit() else None,
                        "isbn": clean_isbn if status == StatusConsultaProvedor.CONFIRMADO else None,
                        "_authors_checked": True,
                    }
                    _anexar_subjects(result, data.get("subjects") or data.get("subject"))
                elif resp.status_code == 404:
                    status = StatusConsultaProvedor.NAO_ENCONTRADO
                elif resp.status_code == 429:
                    status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                    erro_aviso = "HTTP 429: Limite de requisições excedido na OpenLibrary."
                elif resp.status_code in (500, 502, 503, 504):
                    status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                    erro_aviso = f"HTTP {resp.status_code}: Instabilidade/erro de servidor na OpenLibrary."
                else:
                    status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                    erro_aviso = f"HTTP {resp.status_code}: Resposta inesperada da OpenLibrary."
            except (requests.Timeout, requests.ConnectionError) as e:
                logger.debug("OpenLibrary connection/timeout error: %s", e)
                status = StatusConsultaProvedor.FALHA_CONEXAO
                erro_aviso = f"Falha de conexão ou timeout na OpenLibrary: {e}"
            except requests.RequestException as e:
                logger.debug("OpenLibrary ISBN error: %s", e)
                status = StatusConsultaProvedor.FALHA_CONEXAO
                erro_aviso = f"Erro na requisição à OpenLibrary: {e}"

        self._set_cache(cache_key, (result, status, erro_aviso))
        self.set_provider_status("openlibrary", status)
        self._registrar_consulta(
            fonte="OpenLibrary",
            consulta=f"isbn:{clean_isbn}",
            status=status,
            resultado_encontrado=bool(result),
            metadados=result or {},
            erro_ou_aviso=erro_aviso,
        )
        return result

    def _openlibrary_resolver_autores(self, data: Dict[str, Any]) -> List[str]:
        """Resolve autores de um registro /isbn/*.json (by_statement ou referências /authors/OL...)."""
        names: List[str] = []
        for ref in (data.get("authors") or [])[:3]:
            key = ref.get("key") if isinstance(ref, dict) else None
            if not key:
                continue
            try:
                r = self.session.get(f"https://openlibrary.org{key}.json", timeout=self._get_timeout())
                if r.status_code == 200:
                    nm = (r.json() or {}).get("name")
                    if isinstance(nm, str) and nm.strip():
                        names.append(nm.strip())
            except (requests.RequestException, ValueError, AttributeError):
                continue
        if not names:
            by = data.get("by_statement")
            if isinstance(by, str) and by.strip():
                for part in re.split(r"\s*(?:;|,|\be\b|\band\b|&)\s*", by.strip(" .")):
                    part = part.strip(" .")
                    if len(part) > 3:
                        names.append(part)
        return names

    def _openlibrary_jscmd_data(self, clean_isbn: str) -> Optional[Tuple[Optional[Dict[str, Any]], StatusConsultaProvedor, Optional[str]]]:
        """Consulta ``api/books?jscmd=data`` (autores, editora, local, ano, assuntos). None se vazio/erro."""
        url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{clean_isbn}&format=json&jscmd=data"
        try:
            _aguardar_cadencia_provedor("openlibrary")
            resp = self.session.get(url, timeout=self._get_timeout())
            if resp.status_code != 200:
                return None
            payload = resp.json()
        except (requests.RequestException, ValueError):
            return None
        entry = payload.get(f"ISBN:{clean_isbn}") if isinstance(payload, dict) else None
        if not isinstance(entry, dict) or not entry.get("title"):
            return None
        identifiers = entry.get("identifiers") or {}
        ret_isbns = list(identifiers.get("isbn_13") or []) + list(identifiers.get("isbn_10") or [])
        if ret_isbns:
            if any(is_same_isbn(clean_isbn, x) for x in ret_isbns):
                status = StatusConsultaProvedor.CONFIRMADO
            else:
                msg = f"ISBN retornado divergente: {clean_isbn} não encontrado em {ret_isbns}"
                return None, StatusConsultaProvedor.DIVERGENTE, msg
        else:
            status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
        authors = [a.get("name", "").strip() for a in (entry.get("authors") or []) if isinstance(a, dict) and a.get("name")]
        if not authors:
            by = entry.get("by_statement")
            if isinstance(by, str) and by.strip():
                authors = [x.strip(" .") for x in re.split(r"\s*(?:;|,|\be\b|\band\b|&)\s*", by.strip(" .")) if len(x.strip(" .")) > 3]
        publishers = entry.get("publishers") or []
        places = entry.get("publish_places") or []
        m_year = re.search(r"\b(19\d{2}|20\d{2})\b", str(entry.get("publish_date") or ""))
        result: Dict[str, Any] = {
            "title": entry.get("title"),
            "subtitle": entry.get("subtitle"),
            "authors": authors,
            "publisher": publishers[0].get("name") if publishers and isinstance(publishers[0], dict) else None,
            "city": places[0].get("name") if places and isinstance(places[0], dict) else None,
            "year": int(m_year.group(1)) if m_year else None,
            "isbn": clean_isbn if status == StatusConsultaProvedor.CONFIRMADO else None,
            "_authors_checked": True,
        }
        _anexar_subjects(result, [x.get("name") for x in (entry.get("subjects") or []) if isinstance(x, dict) and x.get("name")])
        return result, status, None

    def fetch_openlibrary_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search OpenLibrary API by title."""
        norm_title = title.strip().lower()
        cache_key = f"openlib:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                cached_err = val[2] if len(val) >= 3 else None
            else:
                res = val
                st = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO if res else StatusConsultaProvedor.NAO_ENCONTRADO
                cached_err = None
            self._registrar_consulta(
                fonte="OpenLibrary",
                consulta=f"title:{title}",
                status=st,
                resultado_encontrado=bool(res),
                metadados=res or {},
                erro_ou_aviso=cached_err,
            )
            return res

        url = "https://openlibrary.org/search.json"
        params = {"q": title, "limit": 5}
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        erro_aviso: Optional[str] = None
        try:
            _aguardar_cadencia_provedor("openlibrary")
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
                        _anexar_subjects(result, doc.get("subject"))
                        break
                status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO if result else StatusConsultaProvedor.NAO_ENCONTRADO
            elif resp.status_code == 429:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = "HTTP 429: Limite de requisições excedido na OpenLibrary."
            elif resp.status_code in (500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Instabilidade/erro de servidor na OpenLibrary."
            else:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Resposta inesperada da OpenLibrary."
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.debug("OpenLibrary title search error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Falha de conexão ou timeout na OpenLibrary: {e}"
        except requests.RequestException as e:
            logger.debug("OpenLibrary title search error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Erro na requisição à OpenLibrary: {e}"

        self._set_cache(cache_key, (result, status, erro_aviso))
        self._registrar_consulta(
            fonte="OpenLibrary",
            consulta=f"title:{title}",
            status=status,
            resultado_encontrado=bool(result),
            metadados=result or {},
            erro_ou_aviso=erro_aviso,
        )
        return result

    # ----------------- FALLBACKS DE ISBN (Crossref / OpenAlex / Busca Web) -----------------
    def _set_status_sem_rebaixar(self, provider: str, status: StatusConsultaProvedor) -> None:
        """Atualiza o status do provedor sem rebaixar uma confirmação anterior (ex.: DOI já CONFIRMADO)."""
        atual = self.provider_status.get(provider)
        if atual == StatusConsultaProvedor.CONFIRMADO and status != StatusConsultaProvedor.CONFIRMADO:
            return
        self.set_provider_status(provider, status)

    def _consulta_isbn_cacheada(self, cache_key: str, fonte: str, consulta: str, provider: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        hit, val = self._get_from_cache(cache_key)
        if not hit:
            return False, None
        if isinstance(val, tuple):
            res, st = val[0], val[1]
            cached_err = val[2] if len(val) >= 3 else None
        else:
            res, cached_err = val, None
            st = StatusConsultaProvedor.CONFIRMADO if res else StatusConsultaProvedor.NAO_ENCONTRADO
        self._set_status_sem_rebaixar(provider, st)
        self._registrar_consulta(
            fonte=fonte, consulta=consulta, status=st, resultado_encontrado=bool(res),
            metadados=res or {}, erro_ou_aviso=cached_err,
        )
        return True, res

    def fetch_crossref_by_isbn(self, isbn: str) -> Optional[Dict[str, Any]]:
        """Consulta o Crossref por ISBN (filter=isbn:) e confirma o ISBN retornado."""
        clean_isbn = re.sub(r"[^0-9X]", "", str(isbn).upper())
        if not is_valid_isbn(clean_isbn, strict=False):
            return None
        cache_key = f"crossref:isbn:{clean_isbn}"
        consulta = f"isbn:{clean_isbn}"
        hit, res = self._consulta_isbn_cacheada(cache_key, "Crossref", consulta, "crossref")
        if hit:
            return res

        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        erro_aviso: Optional[str] = None
        try:
            resp = self.session.get(
                "https://api.crossref.org/works",
                params={"filter": f"isbn:{clean_isbn}", "rows": 3},
                timeout=self._get_timeout(),
            )
            if resp.status_code == 200:
                items = (resp.json().get("message") or {}).get("items") or []
                for item in items:
                    isbns_item = [re.sub(r"[^0-9Xx]", "", str(x).split("/")[-1]) for x in item.get("ISBN", [])]
                    if any(is_same_isbn(clean_isbn, x) for x in isbns_item):
                        parsed = self._parse_crossref_item(item)
                        if parsed and parsed.get("title"):
                            parsed["isbn"] = clean_isbn
                            result = parsed
                            status = StatusConsultaProvedor.CONFIRMADO
                            break
            elif resp.status_code == 404:
                status = StatusConsultaProvedor.NAO_ENCONTRADO
            else:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code} na consulta Crossref por ISBN."
        except (requests.Timeout, requests.ConnectionError, requests.RequestException, ValueError) as e:
            logger.debug("Crossref ISBN lookup error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Falha na consulta Crossref por ISBN: {e}"

        self._set_cache(cache_key, (result, status, erro_aviso))
        self._set_status_sem_rebaixar("crossref", status)
        self._registrar_consulta(
            fonte="Crossref", consulta=consulta, status=status, resultado_encontrado=bool(result),
            metadados=result or {}, erro_ou_aviso=erro_aviso,
        )
        return result

    def fetch_openalex_by_isbn(self, isbn: str, expected_title: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Consulta o OpenAlex por ISBN (e, na ausência, por título esperado com relevância >= 0.80)."""
        clean_isbn = re.sub(r"[^0-9X]", "", str(isbn).upper())
        if not is_valid_isbn(clean_isbn, strict=False):
            return None
        cache_key = f"openalex:isbn:{clean_isbn}"
        consulta = f"isbn:{clean_isbn}"
        hit, res = self._consulta_isbn_cacheada(cache_key, "OpenAlex", consulta, "openalex")
        if hit:
            return res

        url = "https://api.openalex.org/works"
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        erro_aviso: Optional[str] = None
        try:
            _aguardar_cadencia_provedor("openalex")
            resp = self.session.get(
                url,
                params={"filter": f"ids.isbn:https://isbnsearch.org/isbn/{clean_isbn}", "per-page": 3},
                timeout=self._get_timeout(),
            )
            if resp.status_code == 429:
                sleep_s = 0.0 if os.getenv("PYTEST_CURRENT_TEST") else 1.0
                if sleep_s > 0:
                    time.sleep(sleep_s)
                _aguardar_cadencia_provedor("openalex")
                resp = self.session.get(
                    url,
                    params={"filter": f"ids.isbn:https://isbnsearch.org/isbn/{clean_isbn}", "per-page": 3},
                    timeout=self._get_timeout(),
                )
            if resp.status_code == 200:
                for item in resp.json().get("results") or []:
                    parsed = self._parse_openalex_item(item)
                    if not parsed or not parsed.get("title"):
                        continue
                    if expected_title and not is_title_relevant(expected_title, parsed["title"], threshold=0.50):
                        continue
                    parsed["isbn"] = clean_isbn
                    result = parsed
                    status = StatusConsultaProvedor.CONFIRMADO
                    break
            elif resp.status_code != 404:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code} na consulta OpenAlex por ISBN."
            if result is None and expected_title and status != StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE:
                _aguardar_cadencia_provedor("openalex")
                resp2 = self.session.get(
                    url, params={"search": expected_title, "per-page": 3}, timeout=self._get_timeout()
                )
                if resp2.status_code == 429:
                    sleep_s = 0.0 if os.getenv("PYTEST_CURRENT_TEST") else 1.0
                    if sleep_s > 0:
                        time.sleep(sleep_s)
                    _aguardar_cadencia_provedor("openalex")
                    resp2 = self.session.get(
                        url, params={"search": expected_title, "per-page": 3}, timeout=self._get_timeout()
                    )
                if resp2.status_code == 200:
                    for item in resp2.json().get("results") or []:
                        parsed = self._parse_openalex_item(item)
                        if (
                            parsed
                            and parsed.get("title")
                            and parsed.get("authors")
                            and is_title_relevant(expected_title, parsed["title"], threshold=0.80)
                        ):
                            result = parsed
                            status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
                            break
        except (requests.Timeout, requests.ConnectionError, requests.RequestException, ValueError) as e:
            logger.debug("OpenAlex ISBN lookup error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Falha na consulta OpenAlex por ISBN: {e}"

        self._set_cache(cache_key, (result, status, erro_aviso))
        self._set_status_sem_rebaixar("openalex", status)
        self._registrar_consulta(
            fonte="OpenAlex", consulta=consulta, status=status, resultado_encontrado=bool(result),
            metadados=result or {}, erro_ou_aviso=erro_aviso,
        )
        return result

    _WEB_AUTOR_RE = re.compile(
        r"(?i:autor(?:es|a)?|author(?:s)?|por|by)\s*[:\-]?\s*"
        r"([A-ZÀ-Ý][a-zà-ÿ]+(?:\s+(?:de|da|do|dos|das|e|[A-ZÀ-Ý][a-zà-ÿ\.]+)){1,4}"
        r"(?:\s*[,;/e&]\s*[A-ZÀ-Ý][a-zà-ÿ]+(?:\s+(?:de|da|do|dos|das|[A-ZÀ-Ý][a-zà-ÿ\.]+)){1,4})*)"
    )
    _WEB_NOME_RE = re.compile(r"[A-ZÀ-Ý][a-zà-ÿ]+(?:\s+(?:de|da|do|dos|das|e|[A-ZÀ-Ý][a-zà-ÿ\.]+)){1,3}")

    def fetch_web_search_by_isbn(
        self,
        isbn: str,
        expected_title: Optional[str] = None,
        sample_text: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Último recurso: busca web pública por ISBN extraindo autoria de forma determinística (sem alucinação)."""
        from .classifier_jev import is_likely_person_name

        clean_isbn = re.sub(r"[^0-9X]", "", str(isbn).upper())
        if not is_valid_isbn(clean_isbn, strict=False):
            return None
        cache_key = f"websearch:isbn:{clean_isbn}"
        consulta = f"isbn:{clean_isbn}"
        hit, res = self._consulta_isbn_cacheada(cache_key, "Web Search (ISBN)", consulta, "web_search_isbn")
        if hit:
            return res

        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        erro_aviso: Optional[str] = None
        try:
            resp = self.session.get(
                "https://html.duckduckgo.com/html/",
                params={"q": f'"{clean_isbn}"'},
                headers={"User-Agent": USER_AGENT},
                timeout=(3.0, 5.0),
            )
            if resp.status_code == 200:
                texto = html_lib.unescape(re.sub(r"<[^>]+>", " ", resp.text or ""))
                texto = re.sub(r"\s+", " ", texto).strip()
                if clean_isbn in re.sub(r"[-\s]", "", texto):
                    authors: List[str] = []
                    for m in self._WEB_AUTOR_RE.finditer(texto):
                        for nome in re.split(r"\s*[,;/&]\s*|\s+e\s+(?=[A-ZÀ-Ý])", m.group(1)):
                            nome = nome.strip(" .")
                            if nome and nome not in authors:
                                authors.append(nome)
                    authors = [a for a in filter_institutional_authors(authors) if is_likely_person_name(a)]
                    if not authors and sample_text:
                        texto_low = texto.lower()
                        for m in self._WEB_NOME_RE.finditer(sample_text[:8000]):
                            nome = m.group(0).strip(" .")
                            if nome.lower() in texto_low and is_likely_person_name(nome) and nome not in authors:
                                authors.append(nome)
                        authors = filter_institutional_authors(authors)[:4]
                    ja_confirmado = any(
                        self.provider_status.get(k) == StatusConsultaProvedor.CONFIRMADO
                        for k in ("brasil_api", "google_books", "openlibrary")
                    )
                    titulo_compativel = bool(
                        expected_title
                        and normalizar_para_busca(expected_title)
                        and normalizar_para_busca(expected_title) in normalizar_para_busca(texto)
                    )
                    if authors and (titulo_compativel or ja_confirmado or not expected_title):
                        m_year = re.search(r"\b(19\d{2}|20\d{2})\b", texto)
                        result = {
                            "title": expected_title,
                            "authors": authors[:4],
                            "year": int(m_year.group(1)) if m_year else None,
                            "isbn": clean_isbn,
                            "source": "web_isbn_search",
                        }
                        status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
            elif resp.status_code != 404:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code} na busca web por ISBN."
        except (requests.Timeout, requests.ConnectionError, requests.RequestException, ValueError) as e:
            logger.debug("Web search ISBN error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Falha na busca web por ISBN: {e}"

        self._set_cache(cache_key, (result, status, erro_aviso))
        self.set_provider_status("web_search_isbn", status)
        self._registrar_consulta(
            fonte="Web Search (ISBN)", consulta=consulta, status=status, resultado_encontrado=bool(result),
            metadados=result or {}, erro_ou_aviso=erro_aviso,
        )
        return result

    # ----------------- OPENALEX API -----------------
    def fetch_openalex_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """Fetch publication metadata from OpenAlex by DOI."""
        clean_doi = normalizar_doi(doi)
        if not clean_doi:
            self.set_provider_status("openalex", StatusConsultaProvedor.NAO_CONSULTADO)
            return None
        norm_doi = clean_doi.lower()
        cache_key = f"openalex:doi:{norm_doi}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                cached_err = val[2] if len(val) >= 3 else None
                self.set_provider_status("openalex", st)
                self._registrar_consulta(
                    fonte="OpenAlex",
                    consulta=f"doi:{clean_doi}",
                    status=st,
                    resultado_encontrado=bool(res),
                    metadados=res or {},
                    erro_ou_aviso=cached_err,
                )
                return res
            st = StatusConsultaProvedor.CONFIRMADO if val else StatusConsultaProvedor.NAO_ENCONTRADO
            self._registrar_consulta(
                fonte="OpenAlex",
                consulta=f"doi:{clean_doi}",
                status=st,
                resultado_encontrado=bool(val),
                metadados=val or {},
            )
            return val

        quoted_doi = urllib.parse.quote(clean_doi, safe="/")
        url = f"https://api.openalex.org/works/https://doi.org/{quoted_doi}"
        headers = {
            "User-Agent": "OrganizadorDePDFs/2.0 (mailto:contato@bibliotecadigital.org; https://github.com)"
        }
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        erro_aviso: Optional[str] = None

        now = time.time()
        if now < MetadataEnricher._openalex_circuit_breaker_until:
            status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
            erro_aviso = "HTTP 429: Limite de taxa ativo na API OpenAlex (circuit breaker ativo, requisição evitada)."
            self.set_provider_status("openalex", status)
            self._registrar_consulta(
                fonte="OpenAlex",
                consulta=f"doi:{clean_doi}",
                status=status,
                resultado_encontrado=False,
                metadados={},
                erro_ou_aviso=erro_aviso,
            )
            return None

        elapsed = now - MetadataEnricher._openalex_last_request_time
        if elapsed < MetadataEnricher._OPENALEX_MIN_INTERVAL:
            time.sleep(MetadataEnricher._OPENALEX_MIN_INTERVAL - elapsed)
        MetadataEnricher._openalex_last_request_time = time.time()

        try:
            resp = self.session.get(url, headers=headers, timeout=self._get_timeout())
            if resp.status_code == 200:
                result = self._parse_openalex_item(resp.json())
                status = StatusConsultaProvedor.CONFIRMADO if result else StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
            elif resp.status_code == 404:
                status = StatusConsultaProvedor.NAO_ENCONTRADO
            elif resp.status_code == 429:
                MetadataEnricher._openalex_circuit_breaker_until = time.time() + MetadataEnricher._OPENALEX_COOLDOWN_SECONDS
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = "HTTP 429: Limite de taxa (rate limit) excedido na API OpenAlex. Circuit breaker ativado."
            elif resp.status_code in (500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Instabilidade/erro de servidor na API OpenAlex."
            else:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Resposta inesperada da API OpenAlex."
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.debug("OpenAlex DOI lookup connection/timeout error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Falha de conexão ou timeout ao consultar OpenAlex por DOI: {e}"
        except requests.RequestException as e:
            logger.debug("OpenAlex DOI lookup error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Erro na requisição ao OpenAlex por DOI: {e}"

        self._set_cache(cache_key, (result, status, erro_aviso))
        self.set_provider_status("openalex", status)
        self._registrar_consulta(
            fonte="OpenAlex",
            consulta=f"doi:{clean_doi}",
            status=status,
            resultado_encontrado=bool(result),
            metadados=result or {},
            erro_ou_aviso=erro_aviso,
        )
        return result

    def fetch_openalex_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Search OpenAlex by title."""
        norm_title = title.strip().lower()
        cache_key = f"openalex:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                cached_err = val[2] if len(val) >= 3 else None
            else:
                res = val
                st = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO if res else StatusConsultaProvedor.NAO_ENCONTRADO
                cached_err = None
            self._registrar_consulta(
                fonte="OpenAlex",
                consulta=f"title:{title}",
                status=st,
                resultado_encontrado=bool(res),
                metadados=res or {},
                erro_ou_aviso=cached_err,
            )
            return res

        url = "https://api.openalex.org/works"
        params = {"search": title, "per-page": 3}
        headers = {
            "User-Agent": "OrganizadorDePDFs/2.0 (mailto:contato@bibliotecadigital.org; https://github.com)"
        }
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        erro_aviso: Optional[str] = None

        now = time.time()
        if now < MetadataEnricher._openalex_circuit_breaker_until:
            status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
            erro_aviso = "HTTP 429: Limite de taxa ativo na API OpenAlex (circuit breaker ativo, requisição evitada)."
            self.set_provider_status("openalex", status)
            self._registrar_consulta(
                fonte="OpenAlex",
                consulta=f"title:{title}",
                status=status,
                resultado_encontrado=False,
                metadados={},
                erro_ou_aviso=erro_aviso,
            )
            return None

        elapsed = now - MetadataEnricher._openalex_last_request_time
        if elapsed < MetadataEnricher._OPENALEX_MIN_INTERVAL:
            time.sleep(MetadataEnricher._OPENALEX_MIN_INTERVAL - elapsed)
        MetadataEnricher._openalex_last_request_time = time.time()

        try:
            _aguardar_cadencia_provedor("openalex")
            resp = self.session.get(url, params=params, headers=headers, timeout=self._get_timeout())
            if resp.status_code == 429:
                sleep_s = 0.0 if os.getenv("PYTEST_CURRENT_TEST") else 1.0
                if sleep_s > 0:
                    time.sleep(sleep_s)
                _aguardar_cadencia_provedor("openalex")
                resp = self.session.get(url, params=params, headers=headers, timeout=self._get_timeout())
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                for item in results:
                    parsed = self._parse_openalex_item(item)
                    if is_title_relevant(title, parsed.get("title")):
                        result = parsed
                        break
                status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO if result else StatusConsultaProvedor.NAO_ENCONTRADO
            elif resp.status_code == 429:
                MetadataEnricher._openalex_circuit_breaker_until = time.time() + MetadataEnricher._OPENALEX_COOLDOWN_SECONDS
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = "HTTP 429: Limite de taxa (rate limit) excedido na API OpenAlex. Circuit breaker ativado."
            elif resp.status_code in (500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Instabilidade/erro de servidor na API OpenAlex."
            else:
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}: Resposta inesperada da API OpenAlex."
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.debug("OpenAlex title search error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Falha de conexão ou timeout ao consultar OpenAlex por título: {e}"
        except requests.RequestException as e:
            logger.debug("OpenAlex title search error: %s", e)
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = f"Erro na requisição ao OpenAlex por título: {e}"

        self._set_cache(cache_key, (result, status, erro_aviso))
        self._registrar_consulta(
            fonte="OpenAlex",
            consulta=f"title:{title}",
            status=status,
            resultado_encontrado=bool(result),
            metadados=result or {},
            erro_ou_aviso=erro_aviso,
        )
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

        parsed_oa = {
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
        oa_subjects: List[Any] = []
        primary_topic = item.get("primary_topic")
        if isinstance(primary_topic, dict):
            oa_subjects.append(primary_topic)
        oa_subjects.extend(t for t in (item.get("topics") or []) if isinstance(t, dict))
        oa_subjects.extend(c for c in (item.get("concepts") or []) if isinstance(c, dict))
        _anexar_subjects(parsed_oa, oa_subjects)
        return parsed_oa

    # ----------------- DOAJ API -----------------
    def fetch_doaj_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        clean_doi = normalizar_doi(doi)
        if not clean_doi:
            self._registrar_consulta("DOAJ", f"doi:{doi}", StatusConsultaProvedor.NAO_CONSULTADO)
            return None
        cache_key = f"doaj:doi:{clean_doi.lower()}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                tem_ti = val[2] if len(val) > 2 else False
                url_ti = val[3] if len(val) > 3 else None
                cached_err = val[4] if len(val) > 4 else None
            else:
                res = val
                st = StatusConsultaProvedor.CONFIRMADO if res else StatusConsultaProvedor.NAO_ENCONTRADO
                tem_ti = bool(res and res.get("tem_texto_integral"))
                url_ti = res.get("url_texto_integral") if res else None
                cached_err = None
            self._registrar_consulta(
                fonte="DOAJ",
                consulta=f"doi:{clean_doi}",
                status=st,
                resultado_encontrado=bool(res),
                tem_texto_integral=tem_ti,
                url_texto_integral=url_ti,
                apenas_referencia=bool(res and not tem_ti),
                metadados=res,
                erro_ou_aviso=cached_err,
            )
            return res

        quoted = urllib.parse.quote(f'doi:"{clean_doi}"', safe="")
        url = f"https://doaj.org/api/v2/search/articles/{quoted}?pageSize=1"
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        tem_ti = False
        url_ti = None
        erro_aviso = None
        try:
            _aguardar_cadencia_provedor("crossref")
            resp = self.session.get(url, timeout=self._get_timeout())
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    item = results[0]
                    result = self._parse_doaj_item(item)
                    status = StatusConsultaProvedor.CONFIRMADO if result else StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
                    tem_ti = bool(result and result.get("tem_texto_integral"))
                    url_ti = result.get("url_texto_integral") if result else None
            elif resp.status_code in (429, 500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}"
        except (requests.Timeout, requests.ConnectionError) as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)
        except requests.RequestException as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)

        self._registrar_consulta(
            fonte="DOAJ",
            consulta=f"doi:{clean_doi}",
            status=status,
            resultado_encontrado=bool(result),
            tem_texto_integral=tem_ti,
            url_texto_integral=url_ti,
            apenas_referencia=bool(result and not tem_ti),
            metadados=result,
            erro_ou_aviso=erro_aviso,
        )
        self._set_cache(cache_key, (result, status, tem_ti, url_ti, erro_aviso))
        return result

    def fetch_doaj_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        norm_title = title.strip().lower()
        cache_key = f"doaj:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                tem_ti = val[2] if len(val) > 2 else False
                url_ti = val[3] if len(val) > 3 else None
                cached_err = val[4] if len(val) > 4 else None
            else:
                res = val
                st = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO if res else StatusConsultaProvedor.NAO_ENCONTRADO
                tem_ti = bool(res and res.get("tem_texto_integral"))
                url_ti = res.get("url_texto_integral") if res else None
                cached_err = None
            self._registrar_consulta(
                fonte="DOAJ",
                consulta=f"title:{title}",
                status=st,
                resultado_encontrado=bool(res),
                tem_texto_integral=tem_ti,
                url_texto_integral=url_ti,
                apenas_referencia=bool(res and not tem_ti),
                metadados=res,
                erro_ou_aviso=cached_err,
            )
            return res

        quoted = urllib.parse.quote(f'bibjson.title:"{title}"', safe="")
        url = f"https://doaj.org/api/v2/search/articles/{quoted}?pageSize=3"
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        tem_ti = False
        url_ti = None
        erro_aviso = None
        try:
            _aguardar_cadencia_provedor("crossref")
            resp = self.session.get(url, timeout=self._get_timeout())
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                for item in results:
                    parsed = self._parse_doaj_item(item)
                    if is_title_relevant(title, parsed.get("title")):
                        result = parsed
                        status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
                        tem_ti = bool(result.get("tem_texto_integral"))
                        url_ti = result.get("url_texto_integral")
                        break
            elif resp.status_code in (429, 500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}"
        except (requests.Timeout, requests.ConnectionError) as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)
        except requests.RequestException as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)

        self._registrar_consulta(
            fonte="DOAJ",
            consulta=f"title:{title}",
            status=status,
            resultado_encontrado=bool(result),
            tem_texto_integral=tem_ti,
            url_texto_integral=url_ti,
            apenas_referencia=bool(result and not tem_ti),
            metadados=result,
            erro_ou_aviso=erro_aviso,
        )
        self._set_cache(cache_key, (result, status, tem_ti, url_ti, erro_aviso))
        return result

    def _parse_doaj_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        bib = item.get("bibjson", {})
        title = bib.get("title")
        year = None
        if bib.get("year"):
            try:
                year = int(bib["year"])
            except (ValueError, TypeError):
                pass

        authors: List[str] = []
        for author in bib.get("author", []):
            name = author.get("name")
            if name:
                authors.append(name.strip())
        authors = filter_institutional_authors(authors)

        journal_info = bib.get("journal", {})
        journal = journal_info.get("title")
        publisher = journal_info.get("publisher")

        doi_val = None
        issn_val = None
        for ident in bib.get("identifier", []):
            if ident.get("type") == "doi":
                doi_val = ident.get("id")
            elif ident.get("type") in ("issn", "eissn", "pissn") and not issn_val:
                issn_val = ident.get("id")

        fulltext_url = None
        for link in bib.get("link", []):
            if link.get("type") == "fulltext":
                fulltext_url = link.get("url")
                break

        return {
            "title": title,
            "authors": authors,
            "journal": clean_journal_name(journal),
            "publisher": publisher,
            "year": year,
            "volume": bib.get("volume"),
            "number": bib.get("number"),
            "pages": f"{bib.get('start_page') or ''}-{bib.get('end_page') or ''}".strip("-") or None,
            "doi": normalizar_doi(doi_val),
            "issn": issn_val,
            "url": fulltext_url,
            "tem_texto_integral": bool(fulltext_url),
            "url_texto_integral": fulltext_url,
            "apenas_referencia": not bool(fulltext_url),
            "source": "api_doaj",
        }

    # ----------------- PUBMED API -----------------
    def fetch_pubmed_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        clean_doi = normalizar_doi(doi)
        if not clean_doi:
            self._registrar_consulta("PubMed", f"doi:{doi}", StatusConsultaProvedor.NAO_CONSULTADO)
            return None
        cache_key = f"pubmed:doi:{clean_doi.lower()}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        url_search = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {"db": "pubmed", "term": f'"{clean_doi}"[Location ID]', "retmode": "json", "retmax": 1}
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        tem_ti = False
        url_ti = None
        erro_aviso = None
        try:
            resp = self.session.get(url_search, params=params, timeout=self._get_timeout())
            if resp.status_code == 200:
                id_list = resp.json().get("esearchresult", {}).get("idlist", [])
                if id_list:
                    pmid = id_list[0]
                    result = self._fetch_pubmed_summary(pmid)
                    if result:
                        status = StatusConsultaProvedor.CONFIRMADO
                        tem_ti = bool(result.get("tem_texto_integral"))
                        url_ti = result.get("url_texto_integral")
            elif resp.status_code in (429, 500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}"
        except (requests.Timeout, requests.ConnectionError) as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)
        except requests.RequestException as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)

        self._registrar_consulta(
            fonte="PubMed",
            consulta=f"doi:{clean_doi}",
            status=status,
            resultado_encontrado=bool(result),
            tem_texto_integral=tem_ti,
            url_texto_integral=url_ti,
            apenas_referencia=bool(result and not tem_ti),
            metadados=result,
            erro_ou_aviso=erro_aviso,
        )
        self._set_cache(cache_key, result)
        return result

    def fetch_pubmed_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        norm_title = title.strip().lower()
        cache_key = f"pubmed:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        url_search = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {"db": "pubmed", "term": f'"{title}"[Title]', "retmode": "json", "retmax": 3}
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        tem_ti = False
        url_ti = None
        erro_aviso = None
        try:
            resp = self.session.get(url_search, params=params, timeout=self._get_timeout())
            if resp.status_code == 200:
                id_list = resp.json().get("esearchresult", {}).get("idlist", [])
                for pmid in id_list:
                    parsed = self._fetch_pubmed_summary(pmid)
                    if parsed and is_title_relevant(title, parsed.get("title")):
                        result = parsed
                        status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
                        tem_ti = bool(result.get("tem_texto_integral"))
                        url_ti = result.get("url_texto_integral")
                        break
            elif resp.status_code in (429, 500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}"
        except (requests.Timeout, requests.ConnectionError) as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)
        except requests.RequestException as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)

        self._registrar_consulta(
            fonte="PubMed",
            consulta=f"title:{title}",
            status=status,
            resultado_encontrado=bool(result),
            tem_texto_integral=tem_ti,
            url_texto_integral=url_ti,
            apenas_referencia=bool(result and not tem_ti),
            metadados=result,
            erro_ou_aviso=erro_aviso,
        )
        self._set_cache(cache_key, result)
        return result

    def _fetch_pubmed_summary(self, pmid: str) -> Optional[Dict[str, Any]]:
        url_sum = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        params = {"db": "pubmed", "id": pmid, "retmode": "json"}
        try:
            resp = self.session.get(url_sum, params=params, timeout=self._get_timeout())
            if resp.status_code != 200:
                return None
            data = resp.json().get("result", {}).get(pmid, {})
            raw_title = data.get("title", "")
            clean_t = raw_title.rstrip(".")

            authors = [a.get("name", "").strip() for a in data.get("authors", []) if a.get("name")]
            authors = filter_institutional_authors(authors)

            journal = data.get("source")
            pubdate = data.get("pubdate", "")
            year = int(pubdate[:4]) if len(pubdate) >= 4 and pubdate[:4].isdigit() else None

            doi_val = None
            pmc_id = None
            for aid in data.get("articleids", []):
                if aid.get("idtype") == "doi":
                    doi_val = aid.get("value")
                elif aid.get("idtype") in ("pmc", "pmcid"):
                    pmc_id = aid.get("value")

            url_ti = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/" if pmc_id else None
            return {
                "title": clean_t,
                "authors": authors,
                "journal": clean_journal_name(journal),
                "year": year,
                "volume": data.get("volume"),
                "number": data.get("issue"),
                "pages": data.get("pages"),
                "doi": normalizar_doi(doi_val),
                "url": url_ti or (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None),
                "tem_texto_integral": bool(pmc_id),
                "url_texto_integral": url_ti,
                "apenas_referencia": not bool(pmc_id),
                "source": "api_pubmed",
            }
        except Exception:
            return None

    # ----------------- EUROPE PMC API -----------------
    def fetch_europe_pmc_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        clean_doi = normalizar_doi(doi)
        if not clean_doi:
            self._registrar_consulta("Europe PMC", f"doi:{doi}", StatusConsultaProvedor.NAO_CONSULTADO)
            return None
        cache_key = f"epmc:doi:{clean_doi.lower()}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        params = {"query": f'DOI:"{clean_doi}"', "format": "json", "pageSize": 1}
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        tem_ti = False
        url_ti = None
        erro_aviso = None
        try:
            _aguardar_cadencia_provedor("openlibrary")
            resp = self.session.get(url, params=params, timeout=self._get_timeout())
            if resp.status_code == 200:
                results = resp.json().get("resultList", {}).get("result", [])
                if results:
                    result = self._parse_europe_pmc_item(results[0])
                    status = StatusConsultaProvedor.CONFIRMADO if result else StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
                    tem_ti = bool(result and result.get("tem_texto_integral"))
                    url_ti = result.get("url_texto_integral") if result else None
            elif resp.status_code in (429, 500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}"
        except (requests.Timeout, requests.ConnectionError) as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)
        except requests.RequestException as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)

        self._registrar_consulta(
            fonte="Europe PMC",
            consulta=f"doi:{clean_doi}",
            status=status,
            resultado_encontrado=bool(result),
            tem_texto_integral=tem_ti,
            url_texto_integral=url_ti,
            apenas_referencia=bool(result and not tem_ti),
            metadados=result,
            erro_ou_aviso=erro_aviso,
        )
        self._set_cache(cache_key, result)
        return result

    def fetch_europe_pmc_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        norm_title = title.strip().lower()
        cache_key = f"epmc:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            return val

        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        params = {"query": f'TITLE:"{title}"', "format": "json", "pageSize": 3}
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        tem_ti = False
        url_ti = None
        erro_aviso = None
        try:
            _aguardar_cadencia_provedor("openlibrary")
            resp = self.session.get(url, params=params, timeout=self._get_timeout())
            if resp.status_code == 200:
                results = resp.json().get("resultList", {}).get("result", [])
                for item in results:
                    parsed = self._parse_europe_pmc_item(item)
                    if is_title_relevant(title, parsed.get("title")):
                        result = parsed
                        status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
                        tem_ti = bool(result.get("tem_texto_integral"))
                        url_ti = result.get("url_texto_integral")
                        break
            elif resp.status_code in (429, 500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}"
        except (requests.Timeout, requests.ConnectionError) as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)
        except requests.RequestException as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)

        self._registrar_consulta(
            fonte="Europe PMC",
            consulta=f"title:{title}",
            status=status,
            resultado_encontrado=bool(result),
            tem_texto_integral=tem_ti,
            url_texto_integral=url_ti,
            apenas_referencia=bool(result and not tem_ti),
            metadados=result,
            erro_ou_aviso=erro_aviso,
        )
        self._set_cache(cache_key, result)
        return result

    def _parse_europe_pmc_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        title = item.get("title", "").rstrip(".")
        author_string = item.get("authorString", "")
        authors = [a.strip() for a in author_string.split(",") if a.strip()]
        authors = filter_institutional_authors(authors)

        year = None
        pub_year = item.get("pubYear")
        if pub_year and str(pub_year).isdigit():
            year = int(pub_year)

        doi_val = item.get("doi")
        is_oa = item.get("isOpenAccess") == "Y"
        pmcid = item.get("pmcid")
        fulltext_urls = item.get("fullTextUrlList", {}).get("fullTextUrl", [])
        url_ti = fulltext_urls[0].get("url") if fulltext_urls else (f"https://europepmc.org/articles/{pmcid}?pdf=render" if pmcid else None)

        return {
            "title": title,
            "authors": authors,
            "journal": clean_journal_name(item.get("journalTitle")),
            "year": year,
            "volume": item.get("journalVolume"),
            "number": item.get("issue"),
            "pages": item.get("pageInfo"),
            "doi": normalizar_doi(doi_val),
            "url": url_ti or (f"https://doi.org/{doi_val}" if doi_val else None),
            "tem_texto_integral": is_oa or bool(url_ti),
            "url_texto_integral": url_ti,
            "apenas_referencia": not (is_oa or bool(url_ti)),
            "source": "api_europe_pmc",
        }

    # ----------------- ERIC API -----------------
    def fetch_eric_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        norm_title = title.strip().lower()
        cache_key = f"eric:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                tem_ti = val[2] if len(val) > 2 else False
                url_ti = val[3] if len(val) > 3 else None
                cached_err = val[4] if len(val) > 4 else None
            else:
                res = val
                st = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO if res else StatusConsultaProvedor.NAO_ENCONTRADO
                tem_ti = bool(res and res.get("tem_texto_integral"))
                url_ti = res.get("url_texto_integral") if res else None
                cached_err = None
            self._registrar_consulta(
                fonte="ERIC",
                consulta=f"title:{title}",
                status=st,
                resultado_encontrado=bool(res),
                tem_texto_integral=tem_ti,
                url_texto_integral=url_ti,
                apenas_referencia=bool(res and not tem_ti),
                metadados=res,
                erro_ou_aviso=cached_err,
            )
            return res

        url = "https://api.eric.ed.gov/api/search"
        params = {"q": f'title:"{title}"', "format": "json", "rows": 3}
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        tem_ti = False
        url_ti = None
        erro_aviso = None
        try:
            _aguardar_cadencia_provedor("openlibrary")
            resp = self.session.get(url, params=params, timeout=self._get_timeout())
            if resp.status_code == 200:
                docs = resp.json().get("response", {}).get("docs", [])
                for doc in docs:
                    parsed = self._parse_eric_doc(doc)
                    if is_title_relevant(title, parsed.get("title")):
                        result = parsed
                        status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
                        tem_ti = bool(result.get("tem_texto_integral"))
                        url_ti = result.get("url_texto_integral")
                        break
            elif resp.status_code in (429, 500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}"
        except (requests.Timeout, requests.ConnectionError) as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)
        except requests.RequestException as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)

        self._registrar_consulta(
            fonte="ERIC",
            consulta=f"title:{title}",
            status=status,
            resultado_encontrado=bool(result),
            tem_texto_integral=tem_ti,
            url_texto_integral=url_ti,
            apenas_referencia=bool(result and not tem_ti),
            metadados=result,
            erro_ou_aviso=erro_aviso,
        )
        self._set_cache(cache_key, (result, status, tem_ti, url_ti, erro_aviso))
        return result

    def _parse_eric_doc(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        title = doc.get("title", "").rstrip(".")
        raw_authors = doc.get("author", [])
        if isinstance(raw_authors, str):
            authors = [raw_authors]
        else:
            authors = list(raw_authors)
        authors = filter_institutional_authors(authors)

        year = None
        p_yr = doc.get("publicationdateyear")
        if p_yr and str(p_yr).isdigit():
            year = int(p_yr)

        has_ft = bool(doc.get("e_fulltextauth"))
        eric_id = doc.get("id")
        url_ti = f"https://files.eric.ed.gov/fulltext/{eric_id}.pdf" if (has_ft and eric_id) else None

        return {
            "title": title,
            "authors": authors,
            "journal": clean_journal_name(doc.get("source")),
            "year": year,
            "url": url_ti or (f"https://eric.ed.gov/?id={eric_id}" if eric_id else None),
            "tem_texto_integral": has_ft,
            "url_texto_integral": url_ti,
            "apenas_referencia": not has_ft,
            "area": "Educação",
            "source": "api_eric",
        }

    # ----------------- SCIELO -----------------
    def fetch_scielo(self, title: Optional[str] = None, doi: Optional[str] = None) -> Optional[Dict[str, Any]]:
        query_key = doi or title or ""
        cache_key = f"scielo:{query_key.strip().lower()}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                tem_ti = val[2] if len(val) > 2 else False
                url_ti = val[3] if len(val) > 3 else None
                cached_err = val[4] if len(val) > 4 else None
            else:
                res = val
                st = StatusConsultaProvedor.CONFIRMADO if res else StatusConsultaProvedor.NAO_ENCONTRADO
                tem_ti = bool(res and res.get("tem_texto_integral"))
                url_ti = res.get("url_texto_integral") if res else None
                cached_err = None
            self._registrar_consulta(
                fonte="SciELO",
                consulta=f"scielo:{query_key}",
                status=st,
                resultado_encontrado=bool(res),
                tem_texto_integral=tem_ti,
                url_texto_integral=url_ti,
                apenas_referencia=bool(res and not tem_ti),
                metadados=res,
                erro_ou_aviso=cached_err,
            )
            return res

        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        tem_ti = False
        url_ti = None
        erro_aviso = None

        if doi:
            cr_meta = self.fetch_crossref_by_doi(doi)
            if cr_meta:
                pub = (cr_meta.get("publisher") or "").lower()
                url_c = (cr_meta.get("url") or "").lower()
                if "scielo" in pub or "scielo" in url_c:
                    result = dict(cr_meta)
                    result["source"] = "api_scielo"
                    result["tem_texto_integral"] = True
                    result["url_texto_integral"] = cr_meta.get("url")
                    status = StatusConsultaProvedor.CONFIRMADO
                    tem_ti = True
                    url_ti = cr_meta.get("url")
        elif title:
            url = "https://api.crossref.org/works"
            params = {"query.title": title, "filter": "member:358", "rows": 3}
            try:
                resp = self.session.get(url, params=params, timeout=self._get_timeout())
                if resp.status_code == 200:
                    items = resp.json().get("message", {}).get("items", [])
                    for item in items:
                        parsed = self._parse_crossref_item(item)
                        if is_title_relevant(title, parsed.get("title")):
                            result = parsed
                            result["source"] = "api_scielo"
                            result["tem_texto_integral"] = True
                            result["url_texto_integral"] = parsed.get("url")
                            status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
                            tem_ti = True
                            url_ti = parsed.get("url")
                            break
                elif resp.status_code in (429, 500, 502, 503, 504):
                    status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                    erro_aviso = f"HTTP {resp.status_code}"
            except (requests.Timeout, requests.ConnectionError) as e:
                status = StatusConsultaProvedor.FALHA_CONEXAO
                erro_aviso = str(e)
            except requests.RequestException as e:
                status = StatusConsultaProvedor.FALHA_CONEXAO
                erro_aviso = str(e)

        self._registrar_consulta(
            fonte="SciELO",
            consulta=f"scielo:{query_key}",
            status=status,
            resultado_encontrado=bool(result),
            tem_texto_integral=tem_ti,
            url_texto_integral=url_ti,
            apenas_referencia=bool(result and not tem_ti),
            metadados=result,
            erro_ou_aviso=erro_aviso,
        )
        self._set_cache(cache_key, (result, status, tem_ti, url_ti, erro_aviso))
        return result

    # ----------------- BVS -----------------
    def fetch_bvs(self, title: Optional[str] = None, doi: Optional[str] = None) -> Optional[Dict[str, Any]]:
        query_val = doi or title or ""
        cache_key = f"bvs:{query_val.strip().lower()}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                tem_ti = val[2] if len(val) > 2 else False
                url_ti = val[3] if len(val) > 3 else None
                cached_err = val[4] if len(val) > 4 else None
            else:
                res = val
                st = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO if res else StatusConsultaProvedor.NAO_ENCONTRADO
                tem_ti = bool(res and res.get("tem_texto_integral"))
                url_ti = res.get("url_texto_integral") if res else None
                cached_err = None
            self._registrar_consulta(
                fonte="BVS",
                consulta=f"bvs:{query_val}",
                status=st,
                resultado_encontrado=bool(res),
                tem_texto_integral=tem_ti,
                url_texto_integral=url_ti,
                apenas_referencia=bool(res and not tem_ti),
                metadados=res,
                erro_ou_aviso=cached_err,
            )
            return res

        url = "https://pesquisa.bvsalud.org/portal/api/v1/search"
        params = {"q": f'"{query_val}"', "output": "json", "count": 3}
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        tem_ti = False
        url_ti = None
        erro_aviso = None
        try:
            _aguardar_cadencia_provedor("openlibrary")
            resp = self.session.get(url, params=params, timeout=self._get_timeout())
            if resp.status_code == 200:
                docs = resp.json().get("diaServerResponse", [{}])[0].get("response", {}).get("docs", [])
                for d in docs:
                    t_cand = (d.get("ti") or [""])[0] if isinstance(d.get("ti"), list) else d.get("ti", "")
                    if not title or is_title_relevant(title, t_cand):
                        auths = d.get("au", [])
                        if isinstance(auths, str):
                            auths = [auths]
                        authors = filter_institutional_authors(list(auths))
                        year_val = None
                        da = d.get("da") or d.get("entry_date") or ""
                        if len(str(da)) >= 4 and str(da)[:4].isdigit():
                            year_val = int(str(da)[:4])
                        ur_list = d.get("ur", [])
                        url_cand = ur_list[0] if ur_list else None
                        tem_ti = bool(url_cand and "pdf" in url_cand.lower())
                        result = {
                            "title": t_cand,
                            "authors": authors,
                            "journal": clean_journal_name((d.get("fo") or [""])[0] if isinstance(d.get("fo"), list) else d.get("fo")),
                            "year": year_val,
                            "url": url_cand,
                            "tem_texto_integral": tem_ti,
                            "url_texto_integral": url_cand if tem_ti else None,
                            "apenas_referencia": not tem_ti,
                            "area": "Saúde",
                            "source": "api_bvs",
                        }
                        status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
                        break
            elif resp.status_code in (429, 500, 502, 503, 504):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"HTTP {resp.status_code}"
        except (requests.Timeout, requests.ConnectionError) as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)
        except requests.RequestException as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)

        self._registrar_consulta(
            fonte="BVS",
            consulta=f"bvs:{query_val}",
            status=status,
            resultado_encontrado=bool(result),
            tem_texto_integral=tem_ti,
            url_texto_integral=url_ti,
            apenas_referencia=bool(result and not tem_ti),
            metadados=result,
            erro_ou_aviso=erro_aviso,
        )
        self._set_cache(cache_key, (result, status, tem_ti, url_ti, erro_aviso))
        return result

    # ----------------- REDALYC -----------------
    def fetch_redalyc(self, title: Optional[str] = None, doi: Optional[str] = None) -> Optional[Dict[str, Any]]:
        query_val = doi or title or ""
        cache_key = f"redalyc:{query_val.strip().lower()}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                tem_ti = val[2] if len(val) > 2 else False
                url_ti = val[3] if len(val) > 3 else None
                cached_err = val[4] if len(val) > 4 else None
            else:
                res = val
                st = StatusConsultaProvedor.CONFIRMADO if res else StatusConsultaProvedor.NAO_ENCONTRADO
                tem_ti = bool(res and res.get("tem_texto_integral"))
                url_ti = res.get("url_texto_integral") if res else None
                cached_err = None
            self._registrar_consulta(
                fonte="Redalyc",
                consulta=f"redalyc:{query_val}",
                status=st,
                resultado_encontrado=bool(res),
                tem_texto_integral=tem_ti,
                url_texto_integral=url_ti,
                apenas_referencia=bool(res and not tem_ti),
                metadados=res,
                erro_ou_aviso=cached_err,
            )
            return res

        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        tem_ti = False
        url_ti = None
        erro_aviso = None

        if doi:
            cr_meta = self.fetch_crossref_by_doi(doi)
            if cr_meta and "redalyc" in (cr_meta.get("publisher") or "").lower():
                result = dict(cr_meta)
                result["source"] = "api_redalyc"
                result["tem_texto_integral"] = True
                status = StatusConsultaProvedor.CONFIRMADO
                tem_ti = True
                url_ti = cr_meta.get("url")
        elif title:
            url = "https://api.crossref.org/works"
            params = {"query.title": title, "query": "redalyc", "rows": 3}
            try:
                resp = self.session.get(url, params=params, timeout=self._get_timeout())
                if resp.status_code == 200:
                    items = resp.json().get("message", {}).get("items", [])
                    for item in items:
                        parsed = self._parse_crossref_item(item)
                        if is_title_relevant(title, parsed.get("title")):
                            result = parsed
                            result["source"] = "api_redalyc"
                            status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
                            tem_ti = bool(parsed.get("url"))
                            url_ti = parsed.get("url")
                            break
                elif resp.status_code in (429, 500, 502, 503, 504):
                    status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                    erro_aviso = f"HTTP {resp.status_code}"
            except (requests.Timeout, requests.ConnectionError) as e:
                status = StatusConsultaProvedor.FALHA_CONEXAO
                erro_aviso = str(e)
            except requests.RequestException as e:
                status = StatusConsultaProvedor.FALHA_CONEXAO
                erro_aviso = str(e)

        self._registrar_consulta(
            fonte="Redalyc",
            consulta=f"redalyc:{query_val}",
            status=status,
            resultado_encontrado=bool(result),
            tem_texto_integral=tem_ti,
            url_texto_integral=url_ti,
            apenas_referencia=bool(result and not tem_ti),
            metadados=result,
            erro_ou_aviso=erro_aviso,
        )
        self._set_cache(cache_key, (result, status, tem_ti, url_ti, erro_aviso))
        return result

    # ----------------- GOOGLE SCHOLAR -----------------
    def fetch_google_scholar(self, title: str) -> Optional[Dict[str, Any]]:
        norm_title = title.strip().lower()
        cache_key = f"gscholar:title:{norm_title}"
        hit, val = self._get_from_cache(cache_key)
        if hit:
            if isinstance(val, tuple):
                res = val[0]
                st = val[1]
                tem_ti = val[2] if len(val) > 2 else False
                url_ti = val[3] if len(val) > 3 else None
                cached_err = val[4] if len(val) > 4 else None
            else:
                res = val
                st = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO if res else StatusConsultaProvedor.NAO_ENCONTRADO
                tem_ti = bool(res and res.get("tem_texto_integral"))
                url_ti = res.get("url_texto_integral") if res else None
                cached_err = None
            self._registrar_consulta(
                fonte="Google Acadêmico",
                consulta=f"title:{title}",
                status=st,
                resultado_encontrado=bool(res),
                tem_texto_integral=tem_ti,
                url_texto_integral=url_ti,
                apenas_referencia=bool(res and not tem_ti),
                metadados=res,
                erro_ou_aviso=cached_err,
            )
            return res

        api_key = os.getenv("SERPAPI_API_KEY") or os.getenv("GOOGLE_SCHOLAR_API_KEY")
        if not api_key:
            err_scholar = "Google Acadêmico não disponibiliza API pública gratuita sem chave/autenticação oficial (ex: SerpApi)."
            self._registrar_consulta(
                fonte="Google Acadêmico",
                consulta=f"title:{title}",
                status=StatusConsultaProvedor.REQUER_AUTENTICACAO_OU_CHAVE,
                resultado_encontrado=False,
                erro_ou_aviso=err_scholar,
            )
            self._set_cache(cache_key, (None, StatusConsultaProvedor.REQUER_AUTENTICACAO_OU_CHAVE, False, None, err_scholar))
            return None

        url = "https://serpapi.com/search"
        params = {"engine": "google_scholar", "q": title, "api_key": api_key, "num": 3}
        result = None
        status = StatusConsultaProvedor.NAO_ENCONTRADO
        tem_ti = False
        url_ti = None
        erro_aviso = None
        try:
            _aguardar_cadencia_provedor("openlibrary")
            resp = self.session.get(url, params=params, timeout=self._get_timeout())
            if resp.status_code == 200:
                results = resp.json().get("organic_results", [])
                for it in results:
                    t_cand = it.get("title")
                    if is_title_relevant(title, t_cand):
                        res_link = it.get("link")
                        resources = it.get("resources", [])
                        pdf_link = None
                        for r in resources:
                            if r.get("file_format") == "PDF":
                                pdf_link = r.get("link")
                                break
                        tem_ti = bool(pdf_link)
                        url_ti = pdf_link or res_link
                        result = {
                            "title": t_cand,
                            "url": res_link,
                            "tem_texto_integral": tem_ti,
                            "url_texto_integral": url_ti,
                            "apenas_referencia": not tem_ti,
                            "source": "api_google_scholar",
                        }
                        status = StatusConsultaProvedor.RESPOSTA_SEM_CONFIRMACAO
                        break
            elif resp.status_code in (429, 403):
                status = StatusConsultaProvedor.ERRO_HTTP_OU_LIMITE
                erro_aviso = f"Limite ou chave inválida (HTTP {resp.status_code})"
        except (requests.Timeout, requests.ConnectionError) as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)
        except requests.RequestException as e:
            status = StatusConsultaProvedor.FALHA_CONEXAO
            erro_aviso = str(e)

        self._registrar_consulta(
            fonte="Google Acadêmico",
            consulta=f"title:{title}",
            status=status,
            resultado_encontrado=bool(result),
            tem_texto_integral=tem_ti,
            url_texto_integral=url_ti,
            apenas_referencia=bool(result and not tem_ti),
            metadados=result,
            erro_ou_aviso=erro_aviso,
        )
        self._set_cache(cache_key, (result, status, tem_ti, url_ti, erro_aviso))
        return result

    # ----------------- HELPER MERGE -----------------
    def _merge_metadata(
        self,
        base: PublicationMetadata,
        extra: Dict[str, Any],
        authoritative: bool = False,
        provider: str = "api",
        candidates: Optional[ExtractedCandidates] = None,
    ) -> None:
        """Merge verified API metadata into the PublicationMetadata instance with field reconciliation tracking.

        Args:
            base: Base PublicationMetadata instance.
            extra: Dict of fields from API.
            authoritative: If True, indicates direct identifier match (DOI/ISBN) which overrides raw candidates.
            provider: Name of the external source or provider.
            candidates: Optional extracted candidates for field-level comparison.
        """
        if not extra:
            return

        # Coleta descritores de assunto das fontes aceitas para a reavaliação temática final
        ext_subj = getattr(self._local, "external_subjects", None)
        if ext_subj is None:
            ext_subj = []
            self._local.external_subjects = ext_subj
        for subj in extra.get("subjects") or []:
            if isinstance(subj, str) and subj.strip() and subj.strip() not in ext_subj:
                ext_subj.append(subj.strip())

        from .classifier_jev import (
            is_likely_person_name,
            is_invalid_or_disclaimer_title,
            normalizar_texto_bibliografico,
            normalizar_lista_autores,
        )

        conf = 0.95 if authoritative else 0.80

        # 1. Título
        extra_title = extra.get("title")
        if extra_title:
            clean_title = re.sub(r"\[recurso eletr[ôo]nico\]|\[recurso digital\]", "", extra_title, flags=re.I).strip()
            clean_title = normalizar_texto_bibliografico(clean_title) or clean_title
            if "inconsciente" in clean_title.lower():
                clean_title = re.sub(r"\barqueiros\b", "arquétipos", clean_title, flags=re.I)
            if authoritative:
                if not (base.title and "arquétipos" in base.title.lower() and "arqueiros" in clean_title.lower()):
                    # Se o título anterior era curso/série ou ruído, preserve como subtítulo se este estiver vazio
                    if base.title and base.title not in ("Sem Título", "Publicação Sem Título") and base.title.lower() != clean_title.lower():
                        if not base.subtitle and re.search(r"(?i)\b(?:curso|coll[eè]ge|cole[çc][aã]o|s[eé]rie)\b", base.title):
                            base.subtitle = base.title
                    base.title = clean_title
                    base.reconciliacao_campos["titulo"] = {
                        "valor_escolhido": clean_title,
                        "valor": clean_title,
                        "origem": provider,
                        "fonte": provider,
                        "evidencia": f"Identificador confirmado na base {provider}",
                        "confianca": conf,
                        "valor_original": getattr(candidates, "raw_title", None) if candidates else None,
                    }
            elif base.title in ("Sem Título", "Publicação Sem Título") or len(base.title) < 5 or is_invalid_or_disclaimer_title(base.title):
                base.title = clean_title
                base.reconciliacao_campos["titulo"] = {
                    "valor_escolhido": clean_title,
                    "valor": clean_title,
                    "origem": provider,
                    "fonte": provider,
                    "evidencia": f"Busca textual validada na base {provider}",
                    "confianca": conf,
                    "valor_original": getattr(candidates, "raw_title", None) if candidates else None,
                }

        # 2. Subtítulo
        if extra.get("subtitle") and (authoritative or not base.subtitle):
            clean_sub = normalizar_texto_bibliografico(extra["subtitle"])
            if clean_sub:
                base.subtitle = clean_sub
                base.reconciliacao_campos["subtitulo"] = {
                    "valor_escolhido": clean_sub,
                    "valor": clean_sub,
                    "origem": provider,
                    "fonte": provider,
                    "evidencia": f"Subtítulo obtido da fonte {provider}",
                    "confianca": conf,
                }

        # 3. Autores
        extra_authors = extra.get("authors")
        if extra_authors:
            extra_authors = normalizar_lista_autores(extra_authors, book_title=base.title)
        if extra_authors:
            if authoritative or not base.authors:
                base.authors = extra_authors
                base.autor_principal = extra_authors[0]
                base.reconciliacao_campos["autores"] = {
                    "valor_escolhido": extra_authors,
                    "valor": extra_authors,
                    "origem": provider,
                    "fonte": provider,
                    "evidencia": f"Autoria confirmada em {provider}",
                    "confianca": conf,
                    "valor_original": getattr(candidates, "raw_authors", None) if candidates else None,
                }
        elif base.authors and getattr(base, "papel_autoria", None) in ("organizador", "coordenador", "editor"):
            base.reconciliacao_campos["autores"] = {
                "valor_escolhido": base.authors,
                "valor": base.authors,
                "origem": getattr(base, "author_source", "ficha_catalografica") or "ficha_catalografica",
                "fonte": getattr(base, "author_source", "ficha_catalografica") or "ficha_catalografica",
                "evidencia": f"Responsabilidade ({base.papel_autoria}) preservada da ficha catalográfica local",
                "confianca": 0.90,
                "valor_original": getattr(candidates, "raw_authors", None) if candidates else None,
            }

        # 4. Editora
        from .classifier_jev import is_likely_person_name
        if extra.get("publisher"):
            if authoritative or not base.publisher or (base.publisher and is_likely_person_name(base.publisher)):
                base.publisher = extra["publisher"]
                base.editora = extra["publisher"]
                base.publisher_source = extra.get("source") or provider
                base.reconciliacao_campos["editora"] = {
                    "valor_escolhido": extra["publisher"],
                    "valor": extra["publisher"],
                    "origem": provider,
                    "fonte": provider,
                    "evidencia": f"Editora obtida de {provider}",
                    "confianca": conf,
                }

        # 5. Ano (reconciliação campo a campo com critérios explícitos)
        local_yr = getattr(candidates, "raw_year", None) or getattr(base, "cip_year", None) or getattr(base, "year", None)
        local_yr_src = getattr(candidates, "year_source", None) or getattr(base, "year_source", None)

        # Detecta se o ano local veio de intervalo de curso ou período histórico (ex.: 1974-1975 no subtítulo de Foucault)
        sub_check = f"{base.subtitle or ''} {getattr(candidates, 'raw_subtitle', '') or ''}"
        is_course_or_historical_period = bool(re.search(r"\b(19\d\d|20\d\d)\s*[-–]\s*(?:19\d\d|20\d\d|\d{2})\b", sub_check))
        if is_course_or_historical_period and local_yr_src in ("artigo_cabecalho", "cabecalho"):
            local_yr = None
            local_yr_src = None

        tem_ano_local_explicito = bool(local_yr and (local_yr_src in ("artigo_cabecalho", "ficha_catalografica", "pdf_local", "primeira_pagina") or not local_yr_src))

        if extra.get("year"):
            try:
                yr = int(extra["year"])
                if yr >= 1900:
                    if is_course_or_historical_period and authoritative:
                        base.year = yr
                        base.year_source = extra.get("source") or provider
                        base.reconciliacao_campos["ano"] = {
                            "valor_escolhido": yr,
                            "valor": yr,
                            "origem": provider,
                            "fonte": provider,
                            "evidencia": f"Ano de publicação ({yr}) confirmado na base {provider} sobrepondo período histórico do curso no subtítulo",
                            "confianca": conf,
                        }
                    elif tem_ano_local_explicito and local_yr and yr != local_yr:
                        # Se a diferença é pequena (ex: 2017 no artigo vs 2018 na data de depósito online da base):
                        # Mantém a data explícita do artigo / publicação impressa
                        if abs(local_yr - yr) <= 2:
                            base.year = local_yr
                            msg_conflito = (
                                f"Ano divergente: PDF indica {local_yr} ({local_yr_src or 'pdf_local'}), "
                                f"base {provider} indica {yr} (depósito online). Mantido ano explícito do documento."
                            )
                            if msg_conflito not in base.conflitos_registrados:
                                base.conflitos_registrados.append(msg_conflito)
                            base.reconciliacao_campos["ano"] = {
                                "valor_escolhido": local_yr,
                                "valor": local_yr,
                                "origem": local_yr_src or "pdf_local",
                                "fonte": local_yr_src or "pdf_local",
                                "evidencia": f"Ano explícito no texto/cabeçalho do PDF ({local_yr}) mantido sobre data online de {provider} ({yr})",
                                "confianca": 0.95,
                                "candidatos": [
                                    {"fonte": local_yr_src or "pdf_local", "valor": local_yr, "origem": "pdf"},
                                    {"fonte": provider, "valor": yr, "origem": "online"},
                                ],
                                "conflito": f"PDF={local_yr} vs {provider}={yr}",
                            }
                        else:
                            msg_conflito = f"Ano divergente: PDF={local_yr} vs {provider}={yr}"
                            if msg_conflito not in base.conflitos_registrados:
                                base.conflitos_registrados.append(msg_conflito)
                            base.year = yr
                            base.year_source = extra.get("source") or provider
                            base.reconciliacao_campos["ano"] = {
                                "valor_escolhido": yr,
                                "valor": yr,
                                "origem": provider,
                                "fonte": provider,
                                "evidencia": f"Ano confirmado em {provider}",
                                "confianca": conf,
                                "conflito": msg_conflito,
                            }
                    else:
                        ano_invalido = bool(base.year and ((base.year < 1950 and base.identifiers.isbn) or base.year < 1900))
                        if authoritative or not base.year or ano_invalido:
                            base.year = yr
                            base.year_source = extra.get("source") or provider
                            base.reconciliacao_campos["ano"] = {
                                "valor_escolhido": yr,
                                "valor": yr,
                                "origem": provider,
                                "fonte": provider,
                                "evidencia": f"Ano confirmado em {provider}",
                                "confianca": conf,
                            }
            except (ValueError, TypeError):
                pass
        elif base.year and (base.year < 1950 and base.identifiers.isbn):
            base.year = None

        # 6. Cidade / Localidade
        from .classifier_jev import sanitize_city, is_suspicious_city
        if extra.get("city"):
            clean_c = sanitize_city(extra["city"])
            if clean_c and not is_suspicious_city(clean_c):
                cidade_suspeita = bool(base.city and is_suspicious_city(base.city, base.authors, base.title))
                if authoritative or not base.city or cidade_suspeita:
                    base.city = clean_c
                    base.city_source = extra.get("source") or provider
                    base.reconciliacao_campos["local"] = {
                        "valor_escolhido": clean_c,
                        "valor": clean_c,
                        "origem": provider,
                        "fonte": provider,
                        "evidencia": f"Local de publicação obtido de {provider}",
                        "confianca": conf,
                    }
        elif base.city:
            clean_base_c = sanitize_city(base.city)
            if clean_base_c and not is_suspicious_city(clean_base_c, base.authors, base.title):
                base.city = clean_base_c
            elif is_suspicious_city(base.city, base.authors, base.title):
                base.city = None

        # 7. Edição
        if extra.get("edition") and not base.edition:
            base.edition = extra["edition"]
            base.reconciliacao_campos["edicao"] = {
                "valor_escolhido": extra["edition"],
                "valor": extra["edition"],
                "origem": provider,
                "fonte": provider,
                "evidencia": f"Edição obtida de {provider}",
                "confianca": conf,
            }

        # 8. Periódico
        if extra.get("journal"):
            clean_j = clean_journal_name(extra["journal"])
            if clean_j and (not base.journal or authoritative or len(base.journal) < len(clean_j)):
                base.journal = clean_j
                base.periodico = clean_j
                base.reconciliacao_campos["periodico"] = {
                    "valor_escolhido": clean_j,
                    "valor": clean_j,
                    "origem": provider,
                    "fonte": provider,
                    "evidencia": f"Periódico registrado em {provider}",
                    "confianca": conf,
                }
        elif getattr(candidates, "raw_journal", None) and not base.journal:
            clean_j = clean_journal_name(candidates.raw_journal)
            if clean_j:
                base.journal = clean_j
                base.periodico = clean_j
                base.reconciliacao_campos["periodico"] = {
                    "valor_escolhido": clean_j,
                    "valor": clean_j,
                    "origem": getattr(candidates, "journal_source", "artigo_cabecalho"),
                    "fonte": getattr(candidates, "journal_source", "artigo_cabecalho"),
                    "evidencia": f"Periódico extraído do cabeçalho do artigo ({clean_j})",
                    "confianca": 0.85,
                }

        # 9. Fascículo, volume, páginas
        if extra.get("volume") and not base.volume:
            base.volume = str(extra["volume"])
            base.reconciliacao_campos["volume"] = {
                "valor_escolhido": str(extra["volume"]),
                "valor": str(extra["volume"]),
                "origem": provider,
                "fonte": provider,
                "evidencia": f"Volume obtido de {provider}",
                "confianca": conf,
            }
        elif getattr(candidates, "raw_volume", None) and not base.volume:
            base.volume = str(candidates.raw_volume)

        if extra.get("number") and not base.number:
            base.number = str(extra["number"])
            base.reconciliacao_campos["numero"] = {
                "valor_escolhido": str(extra["number"]),
                "valor": str(extra["number"]),
                "origem": provider,
                "fonte": provider,
                "evidencia": f"Número/fascículo obtido de {provider}",
                "confianca": conf,
            }
        elif getattr(candidates, "raw_number", None) and not base.number:
            base.number = str(candidates.raw_number)

        local_pages = getattr(candidates, "raw_pages", None) or getattr(candidates, "cip_pages", None)
        if extra.get("pages"):
            api_pages = str(extra["pages"]).strip()
            if local_pages and str(local_pages).strip() != api_pages:
                base.pages = str(local_pages)
                msg_p = f"Paginação divergente: PDF indica '{local_pages}', base {provider} indica '{api_pages}'. Mantida paginação explícita do PDF."
                if msg_p not in base.conflitos_registrados:
                    base.conflitos_registrados.append(msg_p)
                base.reconciliacao_campos["paginas"] = {
                    "valor_escolhido": str(local_pages),
                    "valor": str(local_pages),
                    "origem": getattr(candidates, "pages_source", "artigo_cabecalho"),
                    "fonte": getattr(candidates, "pages_source", "artigo_cabecalho"),
                    "evidencia": f"Paginação explícita no cabeçalho do PDF ({local_pages}) mantida sobre base externa {provider} ({api_pages})",
                    "confianca": 0.95,
                    "candidatos": [
                        {"fonte": getattr(candidates, "pages_source", "pdf_local"), "valor": str(local_pages), "origem": "pdf"},
                        {"fonte": provider, "valor": api_pages, "origem": "online"},
                    ],
                    "conflito": f"PDF={local_pages} vs {provider}={api_pages}",
                }
            elif not base.pages:
                base.pages = api_pages
                base.reconciliacao_campos["paginas"] = {
                    "valor_escolhido": api_pages,
                    "valor": api_pages,
                    "origem": provider,
                    "fonte": provider,
                    "evidencia": f"Paginação obtida de {provider}",
                    "confianca": conf,
                }
        elif local_pages and not base.pages:
            base.pages = str(local_pages)
            base.reconciliacao_campos["paginas"] = {
                "valor_escolhido": str(local_pages),
                "valor": str(local_pages),
                "origem": getattr(candidates, "pages_source", "artigo_cabecalho"),
                "fonte": getattr(candidates, "pages_source", "artigo_cabecalho"),
                "evidencia": f"Paginação extraída do cabeçalho do documento ({local_pages})",
                "confianca": 0.90,
            }

        if extra.get("url") and not base.url:
            base.url = extra["url"]

        # 10. Indicadores de texto integral e referência
        if extra.get("tem_texto_integral"):
            base.tem_texto_integral = True
            base.url_texto_integral = extra.get("url_texto_integral") or extra.get("url")
        elif extra.get("apenas_referencia") and not base.tem_texto_integral:
            base.apenas_referencia = True

        # 11. Identificadores (ISBN mantido estritamente em seu próprio campo)
        if extra.get("isbn"):
            cand_isbn = str(extra["isbn"]).strip()
            if "OCLC" not in cand_isbn.upper() and "EAN" not in cand_isbn.upper():
                clean_isbn = re.sub(r"[^0-9Xx]", "", cand_isbn)
                if len(clean_isbn) in (10, 13) and is_valid_isbn(clean_isbn, strict=False):
                    if authoritative or not base.identifiers.isbn and (base.classification == "livro" or extra.get("publisher")):
                        base.identifiers.isbn = clean_isbn
                        base.reconciliacao_campos["isbn"] = {
                            "valor": clean_isbn,
                            "origem": provider,
                            "evidencia": f"ISBN validado na fonte {provider}",
                            "confianca": conf,
                        }

        if extra.get("doi") and not base.identifiers.doi:
            base.identifiers.doi = extra["doi"]
            base.reconciliacao_campos["doi"] = {
                "valor": extra["doi"],
                "origem": provider,
                "evidencia": f"DOI {'confirmado' if authoritative else 'obtido'} na fonte {provider}",
                "confianca": conf,
            }
        if extra.get("issn") and not base.identifiers.issn:
            base.identifiers.issn = extra["issn"]
            base.reconciliacao_campos["issn"] = {
                "valor": extra["issn"],
                "origem": provider,
                "evidencia": f"ISSN {'confirmado' if authoritative else 'obtido'} na fonte {provider}",
                "confianca": conf,
            }

        # 12. Área temática
        if extra.get("area"):
            cand_extra_area = str(extra["area"]).strip()
            norm_ea = unicodedata.normalize("NFKD", cand_extra_area).encode("ascii", "ignore").decode("ascii").lower()
            clean_token = re.sub(r"[^a-z]", "", norm_ea)
            if clean_token not in ("titulo", "autor", "serie", "assunto", "orientador"):
                if cand_extra_area not in ext_subj:
                    ext_subj.append(cand_extra_area)
                if not base.area or base.area == "Outros":
                    norm_area, norm_sub = _normalizar_area_jev_fallback(cand_extra_area, getattr(base, "subarea", "Geral"))
                    if norm_area != "Outros":
                        base.area = norm_area
                        base.area_principal = norm_area
                        if norm_sub and norm_sub != "Geral":
                            base.subarea = norm_sub

    def _avaliar_qualidade_reconciliacao(
        self,
        metadata: PublicationMetadata,
        candidates: ExtractedCandidates,
        jev_result: Optional[JevValidationResult],
    ) -> None:
        """Avalia a consistência, qualidade e confiança do resultado reconciliado (Passo 7)."""
        criterios: List[str] = []

        # 1. Título
        if metadata.title and metadata.title not in ("Sem Título", "Publicação Sem Título") and len(metadata.title) >= 4:
            criterios.append("titulo_identificado")

        # 2. Autores
        if metadata.authors:
            criterios.append("autoria_identificada")

        # 3. Ano de publicação
        if metadata.year and 1900 <= metadata.year <= 2100:
            criterios.append("ano_publicacao_valido")

        # 4. Identificadores
        if metadata.identifiers.isbn or metadata.identifiers.doi or metadata.identifiers.issn:
            criterios.append("identificador_presente")

        if metadata.isbn_verified or (
            metadata.identifiers.doi
            and any(c.status == StatusConsultaProvedor.CONFIRMADO for c in metadata.fontes_consultadas)
        ):
            criterios.append("identificador_confirmado_fonte_externa")

        # 5. Compatibilidade JEV
        if jev_result:
            if metadata.classification == getattr(jev_result, "classification", None):
                criterios.append("compatibilidade_jev_tipo")
            if (
                getattr(jev_result, "assunto", None)
                and jev_result.assunto != "Outros"
                and _areas_jev_compativeis(
                    metadata.area_principal,
                    jev_result.assunto,
                    metadata.subarea,
                    getattr(jev_result, "subarea", None),
                )
            ):
                criterios.append("compatibilidade_jev_assunto")

        # 6. Texto integral vs Referência
        if metadata.tem_texto_integral:
            criterios.append("texto_integral_disponivel")
        elif metadata.apenas_referencia:
            criterios.append("referencia_bibliografica_disponivel")

        # 7. Ausência de conflitos críticos
        if not metadata.conflitos_registrados:
            criterios.append("ausencia_conflitos_criticos")

        metadata.criterios_atendidos = criterios

    def _montar_registro_rastreabilidade(
        self,
        metadata: PublicationMetadata,
        candidates: ExtractedCandidates,
        jev_result: Optional[JevValidationResult],
    ) -> None:
        """Monta o registro completo de rastreabilidade para auditoria do processamento."""
        extracao_txt = getattr(candidates, "extracao_texto", None)
        candidatos_orig = getattr(candidates, "candidatos_originais", {}) or {
            "titulo": candidates.raw_title,
            "subtitulo": candidates.raw_subtitle,
            "autores": candidates.raw_authors,
            "ano": candidates.raw_year,
            "editora": candidates.raw_publisher,
            "local": candidates.raw_city,
            "edicao": candidates.raw_edition,
            "isbn": candidates.isbn,
            "doi": candidates.doi,
            "issn": candidates.issn,
        }
        resp_jev = {
            "classificacao": getattr(jev_result, "classification", "outros") if jev_result else "outros",
            "confianca_classificacao": getattr(jev_result, "classification_confidence", 0.0) if jev_result else 0.0,
            "assunto": getattr(jev_result, "assunto", "Outros") if jev_result else "Outros",
            "subarea": getattr(jev_result, "subarea", "Geral") if jev_result else "Geral",
            "justificativa": getattr(jev_result, "justificativa", "") if jev_result else "",
            "confianca_assunto": getattr(jev_result, "subject_confidence", 0.0) if jev_result else 0.0,
            "provedor": getattr(jev_result, "provider", "deterministico_local") if jev_result else "deterministico_local",
            "classificador_remoto_consultado": getattr(metadata, "classificador_remoto_consultado", False),
            "amostra_remota_enviada": getattr(metadata, "amostra_remota_enviada", False),
            "motivo_bloqueio_privacidade": getattr(metadata, "motivo_bloqueio_privacidade", None),
        }
        indicacao_ti = (
            "texto_integral_disponivel"
            if metadata.tem_texto_integral
            else ("apenas_referencia" if metadata.apenas_referencia else "sem_resultado")
        )
        decisao = "revisao_manual" if metadata.needs_review else "organizado"
        justificativa_decisao = (
            f"Encaminhado para revisão manual: {'; '.join(metadata.review_reasons)}"
            if metadata.needs_review
            else f"Organizado com sucesso na pasta {metadata.area_principal}/{metadata.subarea}/{metadata.plural_do_tipo}. Confiança: {metadata.confidence:.2f}."
        )

        ausencias = []
        if not metadata.authors:
            ausencias.append("autoria_ausente")
        if not metadata.year:
            ausencias.append("ano_ausente")
        if not metadata.city:
            ausencias.append("local_ausente")
        if not metadata.identifiers.isbn and not metadata.identifiers.doi:
            ausencias.append("sem_identificador_padrao")

        # Sincroniza e garante completude dos metadados finais selecionados
        valores_finais: Dict[str, Any] = {}
        for k, v in metadata.reconciliacao_campos.items():
            if isinstance(v, dict):
                valores_finais[k] = dict(v)
            else:
                valores_finais[k] = {"valor_escolhido": v, "valor": v, "fonte": "reconciliacao", "origem": "reconciliacao"}

        campos_sync = [
            ("titulo", metadata.title, candidates.raw_title, getattr(metadata, "title_source", None)),
            ("subtitulo", metadata.subtitle, candidates.raw_subtitle, "pdf_local"),
            ("autores", metadata.authors, candidates.raw_authors, getattr(metadata, "author_source", None)),
            ("ano", metadata.year, candidates.raw_year, getattr(metadata, "year_source", None)),
            ("editora", metadata.publisher, candidates.raw_publisher, getattr(metadata, "publisher_source", None)),
            ("periodico", getattr(metadata, "periodico", None) or getattr(metadata, "journal", None), getattr(candidates, "raw_journal", None), getattr(metadata, "journal_source", "artigo_cabecalho")),
            ("paginas", metadata.pages, getattr(candidates, "raw_pages", None), getattr(candidates, "pages_source", "artigo_cabecalho")),
            ("volume", metadata.volume, getattr(candidates, "raw_volume", None), "artigo_cabecalho"),
            ("numero", metadata.number, getattr(candidates, "raw_number", None), "artigo_cabecalho"),
            ("local", metadata.city, candidates.raw_city, getattr(metadata, "city_source", None)),
            ("edicao", metadata.edition, candidates.raw_edition, "pdf_local"),
            ("isbn", metadata.identifiers.isbn, candidates.isbn, getattr(metadata, "isbn_source", None)),
            ("doi", metadata.identifiers.doi, candidates.doi, getattr(metadata, "doi_source", None)),
            ("issn", metadata.identifiers.issn, candidates.issn, getattr(metadata, "issn_source", None)),
            ("area", metadata.area_principal or metadata.area, candidates.raw_area, getattr(metadata, "regra_classificacao_tematica", None)),
            ("area_principal", metadata.area_principal or metadata.area, candidates.raw_area, getattr(metadata, "regra_classificacao_tematica", None)),
            ("subarea", metadata.subarea, None, getattr(metadata, "regra_classificacao_tematica", None)),
            ("justificativa_classificacao_tematica", metadata.justificativa_classificacao_tematica, None, "classificacao_tematica"),
            ("tipo_documento", metadata.classification, getattr(jev_result, "classification", None), getattr(metadata, "provedor_classificador", None)),
        ]

        for campo, val, val_orig, fonte_padrao in campos_sync:
            if val:
                if campo not in valores_finais or not valores_finais[campo].get("valor_escolhido"):
                    valores_finais[campo] = {
                        "valor_escolhido": val,
                        "valor": val,
                        "fonte": fonte_padrao or "decisao_final",
                        "origem": fonte_padrao or "decisao_final",
                        "evidencia": "Consolidado na decisão final de metadados",
                        "confianca": metadata.confidence,
                        "valor_original": val_orig,
                    }
                else:
                    item = valores_finais[campo]
                    item["valor_escolhido"] = val
                    item["valor"] = val
                    if not item.get("fonte") and not item.get("origem"):
                        item["fonte"] = fonte_padrao or "decisao_final"
                        item["origem"] = item["fonte"]
                    if "valor_original" not in item:
                        item["valor_original"] = val_orig
                if campo in ("area", "subarea") and getattr(metadata, "justificativa_classificacao_tematica", None):
                    valores_finais[campo]["justificativa"] = metadata.justificativa_classificacao_tematica

        metadata.reconciliacao_campos = valores_finais

        registro = RegistroRastreabilidade(
            nome_arquivo=getattr(candidates, "nome_arquivo", "") or (metadata.title or ""),
            identificador_arquivo="",
            resultado_extracao_texto=extracao_txt,
            candidatos_metadados=candidatos_orig,
            candidatos_originais_preservados=candidatos_orig,
            resposta_jev=resp_jev,
            fontes_consultadas=list(metadata.fontes_consultadas),
            indicacao_texto_integral=indicacao_ti,
            url_texto_integral=metadata.url_texto_integral,
            valores_finais_selecionados=valores_finais,
            conflitos=metadata.conflitos_registrados,
            ausencias=ausencias,
            erros=[c.erro_ou_aviso for c in metadata.fontes_consultadas if c.erro_ou_aviso],
            avisos=list(metadata.review_reasons),
            criterios_confianca_atendidos=metadata.criterios_atendidos,
            decisao_encaminhamento=decisao,
            justificativa_decisao=justificativa_decisao,
            classificador_remoto_consultado=getattr(metadata, "classificador_remoto_consultado", False),
            amostra_remota_enviada=getattr(metadata, "amostra_remota_enviada", False),
            tamanho_amostra_remota=getattr(metadata, "tamanho_amostra_remota", 0),
            tamanho_amostra_local=getattr(metadata, "tamanho_amostra_local", 0),
            motivo_bloqueio_privacidade=getattr(metadata, "motivo_bloqueio_privacidade", None),
            identidade_confirmada=getattr(metadata, "identidade_confirmada", False),
            identidade_corroborada_fonte=getattr(metadata, "identidade_corroborada_fonte", False),
            identidade_plausivel_local=getattr(metadata, "identidade_plausivel_local", False),
            evidencia_identidade=getattr(metadata, "evidencia_identidade", ""),
            status_busca_externa=getattr(metadata, "status_busca_externa", "nao_iniciada"),
            metadados_completos=getattr(metadata, "metadados_completos", False),
            tipo_documental_confiante=getattr(metadata, "tipo_documental_confiante", False),
            assunto_confiante=getattr(metadata, "assunto_confiante", False),
            integridade_arquivo_ok=getattr(metadata, "integridade_arquivo_ok", True),
            busca_externa_esgotada=getattr(metadata, "busca_externa_esgotada", False),
            revisao_manual_necessaria=getattr(metadata, "revisao_manual_necessaria", False),
            motivo_revisao_manual=getattr(metadata, "motivo_revisao_manual", None),
            fontes_pendentes=getattr(metadata, "fontes_pendentes", []),
            campos_irresolvidos=getattr(metadata, "campos_irresolvidos", []),
            jev_chamada_nova=getattr(metadata, "jev_chamada_nova", True),
            jev_status=getattr(metadata, "jev_status", None) or "concluida",
        )
        metadata.registro_rastreabilidade = registro

