#!/usr/bin/env python3
"""Interactive Validation and Diagnostic Tool for PDF Classification Pipeline.

Provides real-time visibility into each step of the pipeline:
1. File discovery and page/text inspection (native text vs scanned)
2. Metadata candidate extraction
3. Classification reasoning (TypeSafe Jev API parameters / calibrated System One scoring)
4. External API enrichment (Crossref, Google Books, OpenLibrary, Brasil API, OpenAlex)
5. Consolidated metadata display & ABNT reference formatting
6. Interactive field-by-field confirmation and correction input
7. Markdown assembly and organized file delivery to destination
"""

import os
import sys
import shutil
import argparse
from pathlib import Path
from typing import List, Optional, Dict, Any
import pymupdf
from dotenv import load_dotenv

from .models import (
    PublicationType,
    ExtractedCandidates,
    JevValidationResult,
    PublicationMetadata,
    Identifiers,
)
from .classifier_jev import (
    JevClassifier,
    extract_native_sample_text,
    extract_candidate_metadata,
)
from .metadata_api import MetadataEnricher
from .abnt_formatter import ABNTFormatter
from .converter import MarkdownConverter, generate_standardized_filename
from .config import Config

load_dotenv()

# ANSI Color Codes
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def print_banner():
    print(f"\n{BOLD}{CYAN}{'=' * 75}{RESET}")
    print(f"{BOLD}{CYAN}🔍 VALIDATRON - DIAGNÓSTICO E VALIDAÇÃO INTERATIVA DE CLASSIFICAÇÃO{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 75}{RESET}\n")


def print_step(step_num: int, title: str):
    print(f"\n{BOLD}{BLUE}[Etapa {step_num}]{RESET} {BOLD}{title}{RESET}")


def inspect_pdf_structure(pdf_path: str) -> Dict[str, Any]:
    """Inspect PDF physical structure, pages, and native text."""
    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    sample_text = ""
    image_count = 0

    for i in range(min(5, total_pages)):
        page = doc[i]
        sample_text += page.get_text("text") or ""
        image_count += len(page.get_images())

    doc.close()
    is_scanned = len(sample_text.strip()) < 50
    return {
        "total_pages": total_pages,
        "sample_chars": len(sample_text),
        "is_scanned": is_scanned,
        "sample_images": image_count,
    }


def explain_jev_classification(
    pdf_path: str,
    combined_text: str,
    candidates: ExtractedCandidates,
    total_pages: int,
    api_key: Optional[str] = None,
    max_paginas: Optional[int] = None,
    max_caracteres: Optional[int] = None,
    **kwargs,
) -> JevValidationResult:
    """Run Jev classifier while providing transparent diagnostic reporting."""
    if kwargs:
        import warnings
        for arg in kwargs:
            warnings.warn(
                f"O parâmetro '{arg}' em explain_jev_classification foi descontinuado e não tem mais efeito.",
                DeprecationWarning,
                stacklevel=2,
            )
    actual_api_key = api_key or os.getenv("TYPESAFE_API_KEY")
    classifier = JevClassifier(api_key=actual_api_key)

    classify_kwargs: Dict[str, Any] = {}
    if max_paginas is not None:
        classify_kwargs["max_paginas"] = max_paginas
    if max_caracteres is not None:
        classify_kwargs["max_caracteres"] = max_caracteres

    if actual_api_key:
        print(f"  {GREEN}✓ Chave TYPESAFE_API_KEY detectada.{RESET}")
        print(f"  Enviando requisição para API TypeSafe AI com primitivas Choice e Noul...")
        print(f"  - Parâmetros enviados:")
        print(f"    • Amostra de texto: {len(combined_text[:3000])} caracteres")
        print(f"    • Total de páginas: {total_pages}")
        print(f"    • Candidatos extraídos: Título={candidates.raw_title!r}, Autores={candidates.raw_authors!r}")
        res = classifier.classify_and_validate(pdf_path, **classify_kwargs)
        print(f"  {GREEN}✓ Resposta da API TypeSafe:{RESET}")
        print(f"    • Categoria escolhida: {BOLD}{res.classification}{RESET} (Confiança: {res.classification_confidence:.2f})")
        print(f"    • Probabilidades dos campos: {res.probabilities}")
        return res
    else:
        print(f"  {YELLOW}ℹ Nenhuma TYPESAFE_API_KEY configurada no ambiente.{RESET}")
        print(f"  Executando motor calibrado local de regras probabilísticas (System One Heuristics)...")

        # Heuristic explanation
        lower_text = combined_text.lower()
        thesis_markers = [
            "tese apresentada", "dissertação apresentada",
            "requisito para a obtenção do título de doutor",
            "requisito para obtenção do título de doutor",
            "requisito para obtenção do título de mestre",
            "requisito para a obtenção do título de mestre",
            "programa de pós-graduação stricto sensu",
            "programa de pós-graduação",
        ]
        courseware_markers = [
            "disciplina na modalidade a distância", "modalidade a distância",
            "unisulvirtual", "ead", "livro didático", "material didático", "apostila"
        ]

        found_thesis = [m for m in thesis_markers if m in lower_text]
        found_courseware = [m for m in courseware_markers if m in lower_text]

        print(f"  - Análise estrutural:")
        print(f"    • Total de páginas: {total_pages}")
        if found_thesis:
            print(f"    • {GREEN}Marcador de Tese/Dissertação detectado:{RESET} {found_thesis} (Indica 'outros' / tese ou dissertação acadêmica)")
        if found_courseware:
            print(f"    • {GREEN}Marcador de Apostila/EAD detectado:{RESET} {found_courseware} (Indica 'apostila')")
        if candidates.isbn:
            print(f"    • {GREEN}ISBN detectado:{RESET} {candidates.isbn} (Indica 'livro')")
        if candidates.doi:
            print(f"    • {GREEN}DOI detectado:{RESET} {candidates.doi} (Indica 'artigo_cientifico')")
        if candidates.issn:
            print(f"    • {GREEN}ISSN detectado:{RESET} {candidates.issn} (Indica 'revista')")

        res = classifier.classify_and_validate(pdf_path, **classify_kwargs)
        scores = res.raw_jev_data.get("calibrated_scores", {})
        print(f"  - Pontuação detalhada calculada pelo Jev:")
        for cat, score in scores.items():
            bar = "■" * max(0, int(score))
            print(f"    • {cat:18s}: {score:5.1f} {DIM}{bar}{RESET}")

        print(f"  {GREEN}✓ Classificação Jev:{RESET} {BOLD}{res.classification}{RESET} (Confiança: {res.classification_confidence:.2f})")
        return res


def prompt_user_confirmation(
    metadata: PublicationMetadata,
    interactive: bool = True,
) -> PublicationMetadata:
    """Prompt user to confirm or edit metadata fields."""
    if not interactive:
        return metadata

    print(f"\n{BOLD}{YELLOW}❓ PASSO DE VALIDAÇÃO: Deseja confirmar os metadados classificados?{RESET}")
    print(f"   [Pressione {BOLD}Enter{RESET} para CONFIRMAR ou digite {BOLD}'c'{RESET} para CORRIGIR algum campo]")
    choice = input(f"   Sua escolha [Enter=Confirmar / c=Corrigir]: ").strip().lower()

    if choice != "c":
        print(f"   {GREEN}✓ Metadados confirmados com sucesso!{RESET}")
        return metadata

    print(f"\n{BOLD}{CYAN}✏️  MODO DE EDIÇÃO MANUAL (pressione Enter para manter o valor atual):{RESET}")

    # 1. Classification
    valid_classes = ["artigo", "livro", "tese", "revista", "apostila", "outros"]
    print(f"\n   Opções de categoria: {', '.join(valid_classes)}")
    new_class = input(f"   • Categoria [{metadata.classification}]: ").strip()
    if new_class and new_class in valid_classes:
        metadata.classification = new_class  # type: ignore

    # 2. Title
    new_title = input(f"   • Título [{metadata.title}]: ").strip()
    if new_title:
        metadata.title = new_title

    # 3. Subtitle
    new_subtitle = input(f"   • Subtítulo [{metadata.subtitle or ''}]: ").strip()
    if new_subtitle:
        metadata.subtitle = new_subtitle

    # 4. Authors
    current_authors = ", ".join(metadata.authors) if metadata.authors else ""
    new_authors = input(f"   • Autores (separados por vírgula) [{current_authors}]: ").strip()
    if new_authors:
        metadata.authors = [a.strip() for a in new_authors.split(",") if a.strip()]

    # 5. Publisher
    new_pub = input(f"   • Editora / Instituição [{metadata.publisher or ''}]: ").strip()
    if new_pub:
        metadata.publisher = new_pub

    # 6. Year
    new_year = input(f"   • Ano [{metadata.year or ''}]: ").strip()
    if new_year:
        try:
            metadata.year = int(new_year)
        except ValueError:
            pass

    # 7. City
    new_city = input(f"   • Cidade [{metadata.city or ''}]: ").strip()
    if new_city:
        metadata.city = new_city

    # 8. Identifiers
    new_doi = input(f"   • DOI [{metadata.identifiers.doi or ''}]: ").strip()
    if new_doi:
        metadata.identifiers.doi = new_doi
    new_isbn = input(f"   • ISBN [{metadata.identifiers.isbn or ''}]: ").strip()
    if new_isbn:
        metadata.identifiers.isbn = new_isbn

    print(f"\n   {GREEN}✓ Metadados atualizados pelo usuário!{RESET}")
    return metadata


def validate_and_process_pdf(
    pdf_path: str,
    output_base_dir: str,
    interactive: bool = True,
    move_original: bool = False,
    api_key: Optional[str] = None,
    verificar_online: Optional[bool] = None,
    max_paginas: Optional[int] = None,
    max_caracteres: Optional[int] = None,
    **kwargs,
) -> bool:
    """Process a single PDF through the complete interactive validation pipeline."""
    if kwargs:
        import warnings
        for arg in kwargs:
            warnings.warn(
                f"O parâmetro '{arg}' em validate_and_process_pdf foi descontinuado e não tem mais efeito.",
                DeprecationWarning,
                stacklevel=2,
            )
    config = Config.do_ambiente()
    if verificar_online is None:
        verificar_online = config.verificar_online
    if max_paginas is None:
        max_paginas = config.max_paginas
    if max_caracteres is None:
        max_caracteres = config.max_caracteres

    pdf_file = Path(pdf_path).resolve()
    print(f"\n{BOLD}{'─' * 75}{RESET}")
    print(f"{BOLD}📄 ARQUIVO: {CYAN}{pdf_file.name}{RESET}")
    print(f"   Caminho: {DIM}{pdf_file}{RESET}")
    print(f"{BOLD}{'─' * 75}{RESET}")

    # Step 1: Inspection
    print_step(1, f"Processando o arquivo: {pdf_file.name}")
    info = inspect_pdf_structure(str(pdf_file))
    print(f"  • Total de páginas: {BOLD}{info['total_pages']}{RESET}")
    print(f"  • Caracteres nas primeiras páginas: {info['sample_chars']}")
    print(f"  • Imagens nas primeiras páginas: {info['sample_images']}")
    if info["is_scanned"]:
        print(f"  {YELLOW}⚠️  ALERTA: Documento digitalizado (scanned). Sem camada de texto nativo selecionável.{RESET}")
    else:
        print(f"  {GREEN}✓ Camada de texto nativo detectada.{RESET}")

    # Step 2: Native text extraction & candidates
    print_step(2, f"Extraindo metadados preliminares das {max_paginas} primeiras e últimas páginas")
    combined_text, pages_text = extract_native_sample_text(str(pdf_file), head_pages=max_paginas, tail_pages=max_paginas)
    if max_caracteres and len(combined_text) > max_caracteres:
        combined_text = combined_text[:max_caracteres]
    candidates = extract_candidate_metadata(combined_text, pdf_path=str(pdf_file), total_pages=info["total_pages"])

    # Step 3: Display raw candidates
    print_step(3, "Exibindo os metadados extraídos preliminares (Candidatos)")
    print(f"  • Título candidato   : {BOLD}{candidates.raw_title or '(não detectado)'}{RESET}")
    print(f"  • Subtítulo candidato: {candidates.raw_subtitle or '(não detectado)'}")
    print(f"  • Autores candidatos : {candidates.raw_authors or '(não detectado)'}")
    print(f"  • Editora candidata  : {candidates.raw_publisher or '(não detectado)'}")
    print(f"  • Ano candidato      : {candidates.raw_year or '(não detectado)'}")
    print(f"  • Cidade candidata   : {candidates.raw_city or '(não detectado)'}")
    print(f"  • DOI                : {candidates.doi or '(não detectado)'}")
    print(f"  • ISBN               : {candidates.isbn or '(não detectado)'}")
    print(f"  • ISSN               : {candidates.issn or '(não detectado)'}")

    # Step 4: Classification
    print_step(4, "Processando a classificação com Jev (System One)")
    jev_result = explain_jev_classification(
        pdf_path=str(pdf_file),
        combined_text=combined_text,
        candidates=candidates,
        total_pages=info["total_pages"],
        api_key=api_key,
        max_paginas=max_paginas,
        max_caracteres=max_caracteres,
    )

    # Step 5: External API Enrichment
    if verificar_online:
        print_step(5, "Enriquecendo metadados via APIs Públicas (Crossref, Google Books, OpenLibrary, OpenAlex)")
        enricher = MetadataEnricher(online=True)
        metadata = enricher.enrich(jev_result)
        if metadata.source_apis:
            print(f"  {GREEN}✓ APIs consultadas com sucesso:{RESET} {', '.join(metadata.source_apis)}")
        else:
            print(f"  {YELLOW}ℹ Nenhuma API externa retornou novos dados adicionais (mantidos os dados do documento).{RESET}")
    else:
        print_step(5, "Enriquecimento bibliográfico externo desativado (ORGPDF_VERIFICAR_ONLINE=false)")
        enricher = MetadataEnricher(online=False)
        metadata = enricher.enrich(jev_result)
        print(f"  {YELLOW}ℹ Metadados mantidos estritamente a partir do texto do documento.{RESET}")

    # Format ABNT
    abnt_ref = ABNTFormatter.format(metadata)

    # Step 6: Display classified metadata
    print_step(6, "Metadados Consolidados e Referência ABNT")
    print(f"  ┌{'─' * 70}┐")
    print(f"  │ {BOLD}Categoria:{RESET} {GREEN}{metadata.classification:<58}{RESET}│")
    print(f"  │ {BOLD}Título:{RESET} {metadata.title[:60]:<61}│")
    if metadata.subtitle:
        print(f"  │ {BOLD}Subtítulo:{RESET} {metadata.subtitle[:57]:<58}│")
    authors_str = "; ".join(metadata.authors) if metadata.authors else "Sem autor"
    print(f"  │ {BOLD}Autor(es):{RESET} {authors_str[:58]:<59}│")
    print(f"  │ {BOLD}Ano:{RESET} {str(metadata.year or 'Sem ano'):<64}│")
    print(f"  │ {BOLD}Editora:{RESET} {str(metadata.publisher or 'Sem editora')[:59]:<60}│")
    if metadata.area:
        print(f"  │ {BOLD}Área CNPq:{RESET} {metadata.area[:58]:<59}│")
    print(f"  └{'─' * 70}┘")
    print(f"\n  {BOLD}Referência ABNT NBR 6023:{RESET}")
    print(f"  {CYAN}{abnt_ref}{RESET}")

    # Step 7: Confirmation & Editing
    metadata = prompt_user_confirmation(metadata, interactive=interactive)

    # Step 8: Markdown Assembly
    print_step(7, "Montando o Markdown de Acompanhamento (Companion)")
    md_content = MarkdownConverter.assemble_markdown(
        metadata=metadata,
        abnt_reference=abnt_ref,
        pdf_path=str(pdf_file),
    )
    print(f"  {GREEN}✓ Markdown gerado com YAML Frontmatter e citação ABNT completa.{RESET}")

    # Step 9: Organization
    print_step(8, "Organizando e gravando arquivos no destino")
    out_dir = Path(output_base_dir).resolve() / metadata.classification.lower()
    out_dir.mkdir(parents=True, exist_ok=True)

    stem_name = generate_standardized_filename(metadata, fallback_name=pdf_file.name)
    dest_pdf = out_dir / f"{stem_name}.pdf"
    dest_md = out_dir / f"{stem_name}.md"

    # Save Markdown
    dest_md.write_text(md_content, encoding="utf-8")
    print(f"  {GREEN}✓ Markdown gravado em:{RESET} {dest_md.name}")

    # Copy or Move PDF
    if move_original:
        shutil.move(str(pdf_file), str(dest_pdf))
        print(f"  {GREEN}✓ PDF movido e padronizado como:{RESET} {dest_pdf.name}")
    else:
        shutil.copy2(str(pdf_file), str(dest_pdf))
        print(f"  {GREEN}✓ PDF copiado e padronizado como:{RESET} {dest_pdf.name}")

    return True


def run_interactive_validator(
    input_dir: str = "/Users/dario/Desktop/origem",
    output_dir: str = "/Users/dario/Desktop/destino",
    interactive: bool = True,
    move_original: bool = False,
    verificar_online: Optional[bool] = None,
    max_paginas: Optional[int] = None,
    max_caracteres: Optional[int] = None,
    **kwargs,
):
    """Scan input folder and process all PDFs interactively."""
    if kwargs:
        import warnings
        for arg in kwargs:
            warnings.warn(
                f"O parâmetro '{arg}' em run_interactive_validator foi descontinuado e não tem mais efeito.",
                DeprecationWarning,
                stacklevel=2,
            )
    config = Config.do_ambiente()
    if verificar_online is None:
        verificar_online = config.verificar_online
    if max_paginas is None:
        max_paginas = config.max_paginas
    if max_caracteres is None:
        max_caracteres = config.max_caracteres

    print_banner()
    in_path = Path(input_dir).resolve()
    out_path = Path(output_dir).resolve()

    print(f"📁 Pasta de Origem : {BOLD}{in_path}{RESET}")
    print(f"🎯 Pasta de Destino: {BOLD}{out_path}{RESET}")
    print(f"📦 Mover original  : {'Sim' if move_original else 'Não (Copiando)'}")
    print(f"🧠 Classificador   : Jev System One (TypeSafe / Heurísticas Locais)")
    print(f"🌐 APIs Externas   : {'Ativas (Crossref, Google Books, Brasil API, OpenAlex)' if verificar_online else 'Desativadas (ORGPDF_VERIFICAR_ONLINE=false)'}")
    print(f"📄 Amostragem      : até {max_paginas} páginas / {max_caracteres} caracteres")
    print(f"🤝 Modo Interativo : {'Habilitado (solicita confirmação)' if interactive else 'Desabilitado'}\n")

    if not in_path.exists() or not in_path.is_dir():
        print(f"{RED}❌ Erro: Pasta de origem não encontrada: {in_path}{RESET}")
        return 1

    pdf_files = sorted([
        p for p in in_path.glob("*.pdf")
        if not p.name.startswith("._") and not p.name.startswith(".")
    ])

    if not pdf_files:
        print(f"{YELLOW}⚠️  Nenhum arquivo PDF encontrado em {in_path}{RESET}")
        return 0

    print(f"Encontrados {BOLD}{len(pdf_files)}{RESET} arquivo(s) PDF para validação.\n")

    for idx, pdf in enumerate(pdf_files, start=1):
        print(f"\n{BOLD}{MAGENTA}[Arquivo {idx} de {len(pdf_files)}]{RESET}")
        validate_and_process_pdf(
            pdf_path=str(pdf),
            output_base_dir=str(out_path),
            interactive=interactive,
            move_original=move_original,
            verificar_online=verificar_online,
            max_paginas=max_paginas,
            max_caracteres=max_caracteres,
        )

    print(f"\n{BOLD}{GREEN}{'=' * 75}{RESET}")
    print(f"{BOLD}{GREEN}✅ VALIDAÇÃO CONCLUÍDA PARA TODOS OS {len(pdf_files)} ARQUIVOS!{RESET}")
    print(f"{BOLD}{GREEN}{'=' * 75}{RESET}\n")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Validador interativo passo-a-passo para classificação e conversão de PDFs."
    )
    parser.add_argument(
        "--origem", "--input", "-i",
        dest="input",
        default="/Users/dario/Desktop/origem",
        help="Pasta com PDFs de origem (padrão: /Users/dario/Desktop/origem)",
    )
    parser.add_argument(
        "--destino", "--output", "-o",
        dest="output",
        default="/Users/dario/Desktop/destino",
        help="Pasta de destino (padrão: /Users/dario/Desktop/destino)",
    )
    parser.add_argument(
        "--mover", "--move",
        dest="move",
        action="store_true",
        default=False,
        help="Mover PDFs originais em vez de copiar.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        default=False,
        help="Executar sem pausar para confirmação do usuário.",
    )
    parser.add_argument(
        "--max-paginas",
        type=int,
        default=None,
        help="Limite de páginas para amostragem inicial/final (padrão: 10).",
    )
    parser.add_argument(
        "--max-caracteres",
        type=int,
        default=None,
        help="Teto de caracteres da amostra para análise (padrão: 30000).",
    )

    args = parser.parse_args()

    return run_interactive_validator(
        input_dir=args.input,
        output_dir=args.output,
        interactive=not args.non_interactive,
        move_original=args.move,
        max_paginas=args.max_paginas,
        max_caracteres=args.max_caracteres,
    )


if __name__ == "__main__":
    sys.exit(main())
