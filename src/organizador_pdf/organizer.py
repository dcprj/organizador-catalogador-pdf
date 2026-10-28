"""Sanitização de nomes, criação de diretórios e gravação dos arquivos organizados.

Suporta:
- Nomenclatura padronizada visual: SOBRENOME, Nome - Título (Ano)
- Nomenclatura formal: <TIPO> - <TITULO> - <SUBTITULO> - <AUTOR> - <ANO> - <EDITORA>
- Estrutura hierárquica: <destino>/<área>/<subárea>/<tipo>/ ou <destino>/revisao_manual/...
- Simulação dry-run
- Copiar ou mover
- Proteção contra caminhos longos (MAX_PATH) e nomes reservados
- Classe PipelineOrganizer para compatibilidade completa
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional
import yaml

from .abnt_formatter import ABNTFormatter
from .classifier_jev import JevClassifier
from .converter import (
    MarkdownConverter,
    converter_pdf,
    generate_standardized_filename,
    listar_pdfs,
)
from .estado import EstadoManager
from .metadata_api import MetadataEnricher
from .models import Metadados, PipelineResult, PublicationMetadata, TipoPublicacao

logger = logging.getLogger(__name__)

CARACTERES_INVALIDOS = r'[\\/:*?"<>|]'

NOMES_RESERVADOS_WINDOWS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

MAX_CARACTERES_SEGMENTO = 120
MAX_CARACTERES_NOME_ARQUIVO = 180
MAX_CARACTERES_CAMINHO = 240
MIN_CARACTERES_NOME_TRUNCADO = 20
SEM_VALOR = "Sem informação"
PASTA_REVISAO_MANUAL = "revisao_manual"

_LOCK_ESCRITA = threading.Lock()


class ErroDeOrganizacao(RuntimeError):
    """Falha ao gravar os arquivos no destino."""


@dataclass
class ResultadoDaOrganizacao:
    """Caminhos planejados (dry-run) ou efetivamente gravados."""

    diretorio: Path
    pdf_destino: Path
    markdown_destino: Optional[Path] = None
    simulado: bool = False


def sanitizar(
    texto: Optional[str],
    *,
    max_caracteres: int = MAX_CARACTERES_SEGMENTO,
    finalizar: bool = True,
) -> str:
    """Converte um texto livre em um segmento de caminho seguro."""
    if not texto:
        return ""

    limpo = unicodedata.normalize("NFC", str(texto))
    limpo = re.sub(CARACTERES_INVALIDOS, " ", limpo)
    limpo = "".join(" " if unicodedata.category(c)[0] == "C" else c for c in limpo)
    limpo = re.sub(r"\s+", " ", limpo).strip()
    if finalizar:
        limpo = limpo.rstrip(" .")

    if len(limpo) > max_caracteres:
        limpo = limpo[:max_caracteres].rstrip(" .,;:-")

    if limpo.upper() in NOMES_RESERVADOS_WINDOWS:
        limpo = f"_{limpo}"

    return limpo


sanitizar_segmento = sanitizar
sanitizar_nome_arquivo = sanitizar


def montar_nome_arquivo(metadados: Metadados) -> str:
    """Monta `<TIPO> - <TITULO> - <SUBTITULO> - <AUTOR> - <ANO> - <EDITORA>`."""
    segmentos = [
        metadados.tipo_publicacao.value,
        metadados.titulo,
        metadados.subtitulo,
        metadados.autor_para_nome,
        str(metadados.ano) if metadados.ano else None,
        metadados.editora_ou_periodico,
    ]
    partes = [sanitizar(segmento, finalizar=False) for segmento in segmentos]
    nome = " - ".join(parte for parte in partes if parte)
    nome = nome.rstrip(" .")

    if not nome:
        nome = SEM_VALOR
    if len(nome) > MAX_CARACTERES_NOME_ARQUIVO:
        nome = nome[:MAX_CARACTERES_NOME_ARQUIVO].rstrip(" -.,;:")
    return nome


def montar_diretorio(
    destino: Path,
    metadados: Metadados,
    *,
    subpasta_markdown: Optional[str] = None,
    revisao_manual: bool = False,
    estrutura: str = "cnpq",
) -> tuple[Path, Path]:
    """Devolve (diretório do PDF, diretório do Markdown).

    Suporta estrutura 'cnpq' (hierárquica por Área/Subárea/Tipo) ou 'plana' (direto por Tipo).
    """
    raiz = destino / PASTA_REVISAO_MANUAL if revisao_manual else destino

    if estrutura == "plana":
        tipo_plana = sanitizar(getattr(metadados, "classification", "") or metadados.tipo_publicacao.value).lower()
        diretorio_pdf = raiz / (tipo_plana or "outros")
    else:
        tipo = sanitizar(metadados.plural_do_tipo) or "Outros"
        area = sanitizar(metadados.area_principal) or SEM_VALOR
        subarea = sanitizar(metadados.subarea) or area
        diretorio_pdf = raiz / area / subarea / tipo

    diretorio_md = diretorio_pdf
    if subpasta_markdown:
        diretorio_md = diretorio_pdf / (sanitizar(subpasta_markdown) or "Markdown")
    return diretorio_pdf, diretorio_md


def _truncar_para_caminho_seguro(nome: str, diretorio: Path, extensao: str) -> str:
    """Encurta `nome` se `diretorio/nome{extensao}` ultrapassar `MAX_CARACTERES_CAMINHO`."""
    espaco_fixo = len(str(diretorio)) + 1 + len(extensao)
    orcamento = MAX_CARACTERES_CAMINHO - espaco_fixo
    if orcamento >= len(nome):
        return nome
    if orcamento < MIN_CARACTERES_NOME_TRUNCADO:
        raise ErroDeOrganizacao(
            f"o caminho de destino é longo demais ({diretorio}) — nem um nome "
            f"de arquivo mínimo cabe dentro do limite seguro do Windows "
            f"({MAX_CARACTERES_CAMINHO} caracteres). Escolha um --destino mais raso."
        )
    return nome[:orcamento].rstrip(" -.,;:")


def caminho_disponivel(caminho: Path) -> Path:
    """Acrescenta ` (2)`, ` (3)`… se o caminho já existir."""
    if not caminho.exists():
        return caminho
    contador = 2
    while True:
        candidato = caminho.with_name(f"{caminho.stem} ({contador}){caminho.suffix}")
        if not candidato.exists():
            return candidato
        contador += 1


garantir_caminho_unico = caminho_disponivel


def _slug(texto: str) -> str:
    """Converte um texto para formato de tag sem acento (ex.: Psicologia -> psicologia)."""
    nfkd = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    slug = re.sub(r"[^\w\s-]", "", sem_acento.lower())
    return re.sub(r"[\s_-]+", "-", slug).strip("-")


def gerar_markdown(
    metadados: Metadados,
    conteudo: str = "",
    *,
    arquivo_origem: Optional[Path] = None,
    total_paginas: Optional[int] = None,
    provedor_extracao: Optional[str] = None,
    extraido_via_fallback: bool = False,
    **kwargs: Any,
) -> str:
    """Monta o `.md` com YAML frontmatter e ABNT NBR 6023."""
    slug_area = _slug(metadados.area_principal) or "outros"
    slug_subarea = _slug(metadados.subarea) or slug_area
    slug_tipo = _slug(metadados.tipo_publicacao.value) or "outros"

    tags = [
        f"area/{slug_area}",
        f"subarea/{slug_subarea}",
        f"tipo/{slug_tipo}",
    ]
    for t in metadados.tags:
        if t not in tags:
            tags.append(t)

    is_artigo = metadados.tipo_publicacao.value in ("artigo", "artigo_cientifico", "revista")
    editora_val = getattr(metadados, "editora", None)
    periodico_val = getattr(metadados, "periodico", None) or getattr(metadados, "journal", None)
    if not editora_val and not is_artigo:
        editora_val = metadados.editora_ou_periodico

    frontmatter: dict[str, object] = {
        "versao_esquema": "2.0",
        "tipo_documento": metadados.tipo_publicacao.value,
        "titulo": metadados.titulo,
        "subtitulo": metadados.subtitulo,
        "autores": metadados.autores,
        "autor_principal": metadados.autor_principal or metadados.autor_para_nome,
        "papel_autoria": getattr(metadados, "papel_autoria", "autor"),
        "editora": editora_val,
        "periodico": periodico_val,
        "ano": metadados.ano,
        "local": metadados.local,
        "area_principal": metadados.area_principal,
        "subarea": metadados.subarea,
        "identificadores": {
            "isbn": metadados.identificadores.isbn or "",
            "doi": metadados.identificadores.doi or "",
            "issn": metadados.identificadores.issn or "",
        },
        "referencia_abnt": metadados.referencia_abnt,
        "tags": tags,
        "arquivo_origem": arquivo_origem.name if arquivo_origem else None,
        "total_paginas": total_paginas,
        "catalogado_em": date.today().isoformat(),
        "provedor_extracao": provedor_extracao,
        "provedor_classificador": getattr(metadados, "provedor_classificador", None),
        "doi_source": getattr(metadados, "doi_source", None),
        "extraido_via_fallback": extraido_via_fallback,
        "confidence": metadados.confidence,
        "confidence_classification": getattr(metadados, "confidence_classification", metadados.confidence),
        "confidence_metadata": getattr(metadados, "confidence_metadata", metadados.confidence),
        "title_source": getattr(metadados, "title_source", None),
        "author_source": getattr(metadados, "author_source", None),
        "year_source": getattr(metadados, "year_source", None),
        "city_source": getattr(metadados, "city_source", None),
        "publisher_source": getattr(metadados, "publisher_source", None),
        "reimpressao": getattr(metadados, "reimpressao", None),
        "isbn_source": getattr(metadados, "isbn_source", None),
        "isbn_verified": getattr(metadados, "isbn_verified", False),
        "status_por_provedor": getattr(metadados, "status_por_provedor", {}),
        "regra_classificacao_tematica": getattr(metadados, "regra_classificacao_tematica", None),
        "needs_review": metadados.needs_review,
        "review_reasons": metadados.review_reasons,
        "source_apis": metadados.source_apis,
    }
    if getattr(metadados, "volume", None):
        frontmatter["volume"] = metadados.volume
    if getattr(metadados, "number", None):
        frontmatter["numero"] = metadados.number
    if getattr(metadados, "pages", None):
        frontmatter["paginas"] = metadados.pages
    if getattr(metadados, "justificativa_classificacao_tematica", None):
        frontmatter["justificativa_classificacao_tematica"] = metadados.justificativa_classificacao_tematica

    yaml_texto = yaml.safe_dump(
        frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).rstrip()

    referencia = metadados.referencia_abnt.strip() or SEM_VALOR

    partes_md = [
        f"---\n{yaml_texto}\n---\n\n",
        f"# {metadados.titulo}\n\n",
        "## Referência Bibliográfica (ABNT)\n\n",
        f"> {referencia}\n",
    ]
    if conteudo and conteudo.strip():
        partes_md.append(f"\n---\n\n## Conteúdo\n\n{conteudo.strip()}\n")

    return "".join(partes_md)


def organizar(
    metadados: Metadados,
    *,
    pdf_origem: Path,
    destino: Path,
    markdown: Optional[str] = None,
    subpasta_markdown: Optional[str] = None,
    mover: bool = False,
    dry_run: bool = False,
    revisao_manual: bool = False,
    estrutura: str = "cnpq",
) -> ResultadoDaOrganizacao:
    """Organiza o PDF renomeado e o Markdown no destino (ou apenas simula)."""
    diretorio_pdf, diretorio_md = montar_diretorio(
        destino,
        metadados,
        subpasta_markdown=subpasta_markdown,
        revisao_manual=revisao_manual,
        estrutura=estrutura,
    )

    # Usa nome padronizado legível
    nome = generate_standardized_filename(metadados, fallback_name=pdf_origem.name)
    nome = _truncar_para_caminho_seguro(nome, diretorio_md, ".pdf")
    destino_pdf = diretorio_pdf / f"{nome}.pdf"
    destino_md = diretorio_md / f"{nome}.md" if markdown is not None else None

    if dry_run:
        return ResultadoDaOrganizacao(
            diretorio=diretorio_pdf,
            pdf_destino=destino_pdf,
            markdown_destino=destino_md,
            simulado=True,
        )

    try:
        diretorio_pdf.mkdir(parents=True, exist_ok=True)
        if diretorio_md:
            diretorio_md.mkdir(parents=True, exist_ok=True)

        with _LOCK_ESCRITA:
            destino_pdf = caminho_disponivel(destino_pdf)
            if destino_md:
                destino_md = diretorio_md / f"{destino_pdf.stem}.md"

            # 1. Se houver Markdown, grava via arquivo temporário atômico
            md_gravado = False
            if markdown is not None and destino_md is not None:
                tmp_md = destino_md.with_name(f"{destino_md.name}.{os.getpid()}.tmp")
                try:
                    tmp_md.write_text(markdown, encoding="utf-8")
                    os.replace(tmp_md, destino_md)
                    md_gravado = True
                except Exception as exc:
                    if tmp_md.exists():
                        tmp_md.unlink(missing_ok=True)
                    raise ErroDeOrganizacao(f"Falha ao gravar Markdown em {destino_md}: {exc}") from exc

            # 2. Copia ou move o PDF original com salvaguarda de rollback
            try:
                if mover:
                    shutil.move(str(pdf_origem), str(destino_pdf))
                else:
                    shutil.copy2(str(pdf_origem), str(destino_pdf))
            except Exception as exc:
                # Rollback do Markdown se a movimentação/cópia do PDF falhar
                if md_gravado and destino_md and destino_md.exists():
                    destino_md.unlink(missing_ok=True)
                raise ErroDeOrganizacao(f"Falha ao transferir PDF de {pdf_origem} para {destino_pdf}: {exc}") from exc

            # 3. Grava o registro estruturado de auditoria e rastreabilidade (Passo 8)
            auditoria_gravada = False
            destino_auditoria = destino_pdf.parent / f"{destino_pdf.stem}.auditoria.json"
            if not metadados.registro_rastreabilidade:
                from .models import RegistroRastreabilidade
                metadados.registro_rastreabilidade = RegistroRastreabilidade(
                    nome_arquivo=destino_pdf.name,
                    identificador_arquivo="",
                    decisao_encaminhamento="revisao_manual" if revisao_manual else "organizado",
                    justificativa_decisao=(
                        f"Encaminhado para revisão manual: {'; '.join(metadados.review_reasons)}"
                        if metadados.needs_review
                        else f"Organizado em {diretorio_pdf}"
                    ),
                    avisos=list(metadados.review_reasons),
                )

            reg = metadados.registro_rastreabilidade
            if pdf_origem:
                reg.nome_arquivo = Path(pdf_origem).name

            if not reg.identificador_arquivo:
                caminho_hash = pdf_origem if (pdf_origem and Path(pdf_origem).exists()) else destino_pdf
                if caminho_hash and Path(caminho_hash).exists():
                    import hashlib
                    with open(caminho_hash, "rb") as f_hash:
                        reg.identificador_arquivo = hashlib.sha256(f_hash.read()).hexdigest()

            reg.caminho_final = str(destino_pdf)
            reg.decisao_encaminhamento = (
                "revisao_manual" if revisao_manual else "organizado"
            )
            reg.classificador_remoto_consultado = getattr(metadados, "classificador_remoto_consultado", False)
            reg.amostra_remota_enviada = getattr(metadados, "amostra_remota_enviada", False)
            reg.tamanho_amostra_remota = getattr(metadados, "tamanho_amostra_remota", 0)
            reg.tamanho_amostra_local = getattr(metadados, "tamanho_amostra_local", 0)
            reg.motivo_bloqueio_privacidade = getattr(metadados, "motivo_bloqueio_privacidade", None)
            reg.identidade_confirmada = getattr(metadados, "identidade_confirmada", False)
            reg.identidade_corroborada_fonte = getattr(metadados, "identidade_corroborada_fonte", False)
            reg.identidade_plausivel_local = getattr(metadados, "identidade_plausivel_local", False)
            reg.evidencia_identidade = getattr(metadados, "evidencia_identidade", "")
            reg.status_busca_externa = getattr(metadados, "status_busca_externa", "nao_iniciada")
            reg.metadados_completos = getattr(metadados, "metadados_completos", False)
            reg.tipo_documental_confiante = getattr(metadados, "tipo_documental_confiante", False)
            reg.assunto_confiante = getattr(metadados, "assunto_confiante", False)
            reg.integridade_arquivo_ok = getattr(metadados, "integridade_arquivo_ok", True)
            reg.busca_externa_esgotada = getattr(metadados, "busca_externa_esgotada", False)
            reg.revisao_manual_necessaria = getattr(metadados, "revisao_manual_necessaria", revisao_manual)
            reg.motivo_revisao_manual = getattr(metadados, "motivo_revisao_manual", None)
            reg.fontes_pendentes = getattr(metadados, "fontes_pendentes", [])
            reg.campos_irresolvidos = getattr(metadados, "campos_irresolvidos", [])
            reg.jev_chamada_nova = getattr(metadados, "jev_chamada_nova", True)
            reg.jev_status = getattr(metadados, "jev_status", "concluida")

            # Sincroniza e garante completude dos valores finais selecionados na auditoria
            vf = reg.valores_finais_selecionados
            campos_finais = {
                "titulo": metadados.titulo or metadados.title,
                "subtitulo": metadados.subtitulo or metadados.subtitle,
                "autores": metadados.autores or metadados.authors,
                "ano": metadados.ano or metadados.year,
                "editora": metadados.editora or metadados.publisher,
                "periodico": metadados.periodico or metadados.journal,
                "local": metadados.local or metadados.city,
                "edicao": metadados.edition,
                "isbn": metadados.identificadores.isbn,
                "doi": metadados.identificadores.doi,
                "issn": metadados.identificadores.issn,
                "area": metadados.area_principal,
                "subarea": metadados.subarea,
                "tipo_documento": metadados.classification,
                "referencia_abnt": metadados.referencia_abnt,
            }
            for campo, val in campos_finais.items():
                if val:
                    if campo not in vf or not vf[campo].get("valor_escolhido"):
                        vf[campo] = {
                            "valor_escolhido": val,
                            "valor": val,
                            "fonte": "decisao_final",
                            "origem": "decisao_final",
                            "evidencia": "Consolidado na gravação da auditoria",
                            "confianca": metadados.confidence,
                        }
                    else:
                        vf[campo]["valor_escolhido"] = val
                        vf[campo]["valor"] = val

            import json
            try:
                tmp_auditoria = destino_auditoria.with_name(f"{destino_auditoria.name}.{os.getpid()}.tmp")
                tmp_auditoria.write_text(
                    json.dumps(metadados.registro_rastreabilidade.model_dump(), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(tmp_auditoria, destino_auditoria)
                auditoria_gravada = True
            except Exception as exc:
                if tmp_auditoria.exists():
                    tmp_auditoria.unlink(missing_ok=True)
                logger.warning("Falha ao gravar arquivo de auditoria em %s: %s", destino_auditoria, exc)

    except ErroDeOrganizacao:
        raise
    except Exception as exc:
        raise ErroDeOrganizacao(f"Falha ao organizar {pdf_origem.name} em {destino_pdf}: {exc}") from exc

    return ResultadoDaOrganizacao(
        diretorio=diretorio_pdf,
        pdf_destino=destino_pdf,
        markdown_destino=destino_md,
        simulado=False,
    )

class PipelineOrganizer:
    """Orquestrador completo de pipeline para PDFs."""

    def __init__(
        self,
        classifier: Optional[JevClassifier] = None,
        enricher: Optional[MetadataEnricher] = None,
        enriquecimento_online: bool = True,
        online: Optional[bool] = None,
        max_paginas: int = 10,
        max_caracteres: int = 30000,
        estrutura: str = "cnpq",
        typesafe_api_key: Optional[str] = None,
        classificador_modo: str = "jev_remoto",
        **kwargs,
    ):
        if kwargs:
            import warnings
            for arg in kwargs:
                warnings.warn(
                    f"O parâmetro '{arg}' em PipelineOrganizer foi descontinuado e não tem mais efeito.",
                    DeprecationWarning,
                    stacklevel=2,
                )
        self.max_paginas = max_paginas
        self.max_caracteres = max_caracteres
        self.estrutura = estrutura
        rede = enriquecimento_online if online is None else online
        self.classifier = classifier or JevClassifier(api_key=typesafe_api_key, modo=classificador_modo)
        self.enricher = enricher or MetadataEnricher(online=rede)

    def process_pdf(
        self,
        pdf_path: str,
        output_base_dir: str,
        move_original: bool = True,
        dry_run: bool = False,
        quarantine: bool = True,
    ) -> PipelineResult:
        """Processa um único PDF através do pipeline determinístico."""
        orig_p = Path(pdf_path).resolve()
        out_base = Path(output_base_dir).resolve()

        if not orig_p.exists():
            return PipelineResult(
                original_pdf=str(orig_p),
                target_pdf="",
                output_markdown="",
                classification="outros",
                metadata=PublicationMetadata(title=orig_p.stem),
                abnt_reference="",
                success=False,
                error_message=f"Arquivo não encontrado: {orig_p}",
            )

        try:
            # 1. Classificação Jev e extração de candidatos respeitando os limites
            jev_res = self.classifier.classify_and_validate(
                str(orig_p),
                max_paginas=self.max_paginas,
                max_caracteres=self.max_caracteres,
            )

            # 2. Enriquecimento via APIs públicas
            meta = self.enricher.enrich(jev_res)

            # 3. Formatação ABNT NBR 6023
            abnt_ref = ABNTFormatter.format(meta)
            meta.referencia_abnt = abnt_ref

            # 4. Geração do Markdown Companion
            md_content = MarkdownConverter.assemble_markdown(
                metadata=meta,
                abnt_reference=abnt_ref,
                pdf_path=str(orig_p),
            )

            # 5. Organização das pastas via função canônica organizar()
            precisa_revisao = bool(quarantine and meta.needs_review)
            res_org = organizar(
                metadados=meta,
                pdf_origem=orig_p,
                destino=out_base,
                markdown=md_content,
                mover=move_original,
                dry_run=dry_run,
                revisao_manual=precisa_revisao,
                estrutura=self.estrutura,
            )
            target_pdf = res_org.pdf_destino
            target_md = res_org.markdown_destino or target_pdf.with_suffix(".md")

            return PipelineResult(
                original_pdf=str(orig_p),
                target_pdf=str(target_pdf),
                output_markdown=str(target_md),
                classification=meta.classification,
                metadata=meta,
                abnt_reference=abnt_ref,
                success=True,
                is_dry_run=dry_run,
                needs_review=meta.needs_review,
            )
        except Exception as e:
            from .converter import ErroPdfEscaneado
            is_scanned = isinstance(e, ErroPdfEscaneado) or "digitalizado/escaneado" in str(e).lower() or "ocr" in str(e).lower()
            if is_scanned:
                logger.info("PDF sem camada de texto nativa: %s (%s)", orig_p.name, e)
                return PipelineResult(
                    original_pdf=str(orig_p),
                    target_pdf="",
                    output_markdown="",
                    classification="outros",
                    metadata=PublicationMetadata(
                        title=orig_p.stem,
                        needs_review=True,
                        review_reasons=["PDF digitalizado sem texto nativo (requer OCR prévio)"],
                    ),
                    abnt_reference="",
                    success=False,
                    is_dry_run=dry_run,
                    needs_review=True,
                    requires_ocr=True,
                    error_message=str(e),
                )
            logger.error("Erro ao processar %s: %s", orig_p.name, e, exc_info=True)
            return PipelineResult(
                original_pdf=str(orig_p),
                target_pdf="",
                output_markdown="",
                classification="outros",
                metadata=PublicationMetadata(title=orig_p.stem),
                abnt_reference="",
                success=False,
                is_dry_run=dry_run,
                error_message=str(e),
            )

    def process_directory(
        self,
        input_dir: str,
        output_dir: str,
        move_original: bool = True,
        dry_run: bool = False,
        resume: bool = False,
        quarantine: bool = True,
        recursive: bool = True,
    ) -> List[PipelineResult]:
        """Processa um lote de PDFs em um diretório com suporte a retomada e busca recursiva."""
        in_path = Path(input_dir).resolve()
        out_path = Path(output_dir).resolve()

        if not in_path.exists():
            logger.error("Diretório de entrada não existe: %s", in_path)
            return []

        # Gerenciador de estado
        state_mgr = EstadoManager(base_dir=out_path)
        pattern = "**/*.pdf" if recursive else "*.pdf"
        all_pdfs = [p for p in in_path.glob(pattern) if p.is_file() and not p.name.startswith(".")]
        all_pdfs = sorted(all_pdfs)

        results: List[PipelineResult] = []
        for pdf_path in all_pdfs:
            abs_str = str(pdf_path.resolve())
            if resume and state_mgr.is_completed(abs_str):
                logger.info("Pulando arquivo já processado (--resume): %s", pdf_path.name)
                continue

            res = self.process_pdf(
                pdf_path=str(pdf_path),
                output_base_dir=str(out_path),
                move_original=move_original,
                dry_run=dry_run,
                quarantine=quarantine,
            )
            results.append(res)

            if not dry_run and res.success:
                state_mgr.save_checkpoint(
                    original_pdf=res.original_pdf,
                    target_pdf=res.target_pdf,
                    classification=res.classification,
                    success=True,
                )

        return results
