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

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import (
    PublicationType,
    ExtractedCandidates,
    JevValidationResult,
    PublicationMetadata,
    Identifiers,
)
from src.classifier_jev import (
    JevClassifier,
    extract_native_sample_text,
    extract_candidate_metadata,
)
from src.metadata_api import MetadataEnricher
from src.abnt_formatter import ABNTFormatter
from src.converter import MarkdownConverter, generate_standardized_filename

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
    print(f"\n{BOLD}{BLUE}▶ [{step_num}] {title}{RESET}")


def inspect_pdf_structure(pdf_path: str) -> Dict[str, Any]:
    """Inspect PDF page count, native text availability, and image density."""
    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    sample_pages = min(5, total_pages)
    total_chars = 0
    total_images = 0

    for i in range(sample_pages):
        text = doc[i].get_text("text") or ""
        total_chars += len(text.strip())
        total_images += len(doc[i].get_images())

    is_scanned = total_chars < 50 and total_images > 0
    return {
        "total_pages": total_pages,
        "sample_pages": sample_pages,
        "sample_chars": total_chars,
        "sample_images": total_images,
        "is_scanned": is_scanned,
    }


def explain_jev_classification(
    pdf_path: str,
    combined_text: str,
    candidates: ExtractedCandidates,
    total_pages: int,
    api_key: Optional[str] = None,
) -> JevValidationResult:
    """Run Jev classifier while providing transparent diagnostic reporting."""
    actual_api_key = api_key or os.getenv("TYPESAFE_API_KEY")
    classifier = JevClassifier(api_key=actual_api_key)

    if actual_api_key:
        print(f"  {GREEN}✓ Chave TYPESAFE_API_KEY detectada.{RESET}")
        print(f"  Enviando requisição para API TypeSafe AI com primitivas Choice e Noul...")
        print(f"  - Parâmetros enviados:")
        print(f"    • Amostra de texto: {len(combined_text[:3000])} caracteres")
        print(f"    • Total de páginas: {total_pages}")
        print(f"    • Candidatos extraídos: Título={candidates.raw_title!r}, Autores={candidates.raw_authors!r}")
        res = classifier.classify_and_validate(pdf_path)
        print(f"  {GREEN}✓ Resposta da API TypeSafe:{RESET}")
        print(f"    • Categoria escolhida: {BOLD}{res.classification}{RESET} (Confiança: {res.classification_confidence:.2f})")
        print(f"    • Probabilidades dos campos: {res.probabilities}")
        return res
    else:
        print(f"  {YELLOW}ℹ Nenhuma TYPESAFE_API_KEY configurada no .env.{RESET}")
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

        res = classifier.classify_and_validate(pdf_path)
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
) -> bool:
    """Process a single PDF through the complete interactive validation pipeline."""
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
    print_step(2, "Extraindo metadados preliminares das 10 primeiras e 10 últimas páginas")
    combined_text, pages_text = extract_native_sample_text(str(pdf_file), head_pages=10, tail_pages=10)
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
    )

    # Step 5: External API Enrichment
    print_step(5, "Enriquecendo metadados via APIs Públicas (Crossref, Google Books, OpenLibrary, OpenAlex)")
    enricher = MetadataEnricher()
    metadata = enricher.enrich(jev_result)
    if metadata.source_apis:
        print(f"  {GREEN}✓ APIs consultadas com sucesso:{RESET} {', '.join(metadata.source_apis)}")
    else:
        print(f"  {YELLOW}ℹ Nenhuma API externa retornou novos dados adicionais (mantidos os dados do documento).{RESET}")

    # Format ABNT
    abnt_ref = ABNTFormatter.format(metadata)

    # Step 6: Display classified metadata
    print_step(6, "Metadados Consolidados e Referência ABNT")
    print(f"  ┌{'─' * 70}┐")
    print(f"  │ {BOLD}Categoria:{RESET} {GREEN}{metadata.classification:<58}{RESET}│")
    print(f"  │ {BOLD}Título:{RESET} {metadata.title[:60]:<61}│")
    if metadata.subtitle:
        print(f"  │ {BOLD}Subtítulo:{RESET} {metadata.subtitle[:57]:<58}│")
    authors_str = ", ".join(metadata.authors) if metadata.authors else "Sem autor"
    print(f"  │ {BOLD}Autor(es):{RESET} {authors_str[:58]:<59}│")
    print(f"  │ {BOLD}Editora:{RESET} {(metadata.publisher or 'N/A')[:59]:<60}│")
    print(f"  │ {BOLD}Ano:{RESET} {str(metadata.year or 'N/A'):<63}│")
    print(f"  │ {BOLD}Cidade:{RESET} {(metadata.city or 'N/A')[:60]:<61}│")
    print(f"  └{'─' * 70}┘")
    print(f"\n  {BOLD}Referência ABNT NBR 6023:2018 gerada:{RESET}")
    print(f"  {CYAN}{abnt_ref}{RESET}")

    # Step 7: Confirmation & Correction
    metadata = prompt_user_confirmation(metadata, interactive=interactive)
    # Re-format ABNT in case of edits
    abnt_ref = ABNTFormatter.format(metadata)

    # Step 8: Assembly & Organization
    print_step(7, "Gerando Markdown com metadados e renomeando arquivo padronizado")
    markdown_content = MarkdownConverter.assemble_markdown(
        metadata=metadata,
        abnt_reference=abnt_ref,
        pdf_path=str(pdf_file),
    )

    dest_class_dir = Path(output_base_dir).resolve() / metadata.classification
    dest_class_dir.mkdir(parents=True, exist_ok=True)

    standard_stem = generate_standardized_filename(metadata, fallback_name=pdf_file.name)
    dest_md = dest_class_dir / f"{standard_stem}.md"
    dest_pdf = dest_class_dir / f"{standard_stem}.pdf"

    dest_md.write_text(markdown_content, encoding="utf-8")
    print(f"  {GREEN}✓ Markdown com frontmatter e ABNT gravado em:{RESET} {dest_md.name}")

    if move_original:
        if pdf_file.resolve() != dest_pdf.resolve():
            shutil.move(str(pdf_file), str(dest_pdf))
            print(f"  {GREEN}✓ PDF movido e padronizado como:{RESET} {dest_pdf.name}")
    else:
        if pdf_file.resolve() != dest_pdf.resolve():
            shutil.copy2(str(pdf_file), str(dest_pdf))
            print(f"  {GREEN}✓ PDF copiado e padronizado como:{RESET} {dest_pdf.name}")

    return True


def run_interactive_validator(
    input_dir: str = "/Users/dario/Desktop/origem",
    output_dir: str = "/Users/dario/Desktop/destino",
    interactive: bool = True,
    move_original: bool = False,
):
    """Scan input folder and process all PDFs interactively."""
    print_banner()
    in_path = Path(input_dir).resolve()
    out_path = Path(output_dir).resolve()

    print(f"📁 Pasta de Origem : {BOLD}{in_path}{RESET}")
    print(f"🎯 Pasta de Destino: {BOLD}{out_path}{RESET}")
    print(f"📦 Mover original  : {'Sim' if move_original else 'Não (Copiando)'}")
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
        "--input", "-i",
        default="/Users/dario/Desktop/origem",
        help="Pasta com PDFs de origem (padrão: /Users/dario/Desktop/origem)",
    )
    parser.add_argument(
        "--output", "-o",
        default="/Users/dario/Desktop/destino",
        help="Pasta de destino (padrão: /Users/dario/Desktop/destino)",
    )
    parser.add_argument(
        "--move",
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

    args = parser.parse_args()
    return run_interactive_validator(
        input_dir=args.input,
        output_dir=args.output,
        interactive=not args.non_interactive,
        move_original=args.move,
    )


if __name__ == "__main__":
    sys.exit(main())
