#!/usr/bin/env python3
"""CLI Entrypoint for the PDF Extraction, Classification, and Markdown Conversion Pipeline."""

import sys
import argparse
import logging
from pathlib import Path
from dotenv import load_dotenv

# Garante que 'src' esteja acessível mesmo se o pacote não foi instalado com pip install -e .
SRC_PATH = Path(__file__).resolve().parent / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from organizador_pdf.organizer import PipelineOrganizer

load_dotenv()


def setup_logging(verbose: bool = False) -> None:
    """Configure structured logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Pipeline determinístico para extração de metadados, classificação (CIP/Jev) e organização de PDFs com ABNT NBR 6023.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos de Uso:
  # Modo Automático em lote (copiando os originais sem apagar):
  python main.py --input ./documentos_origem --output ./documentos_organizados --keep-original

  # Simulação prévia sem alterar arquivos no disco:
  python main.py --input ./documentos_origem --output ./documentos_organizados --dry-run

  # Busca recursiva em subpastas e retomada de lote interrompido:
  python main.py --input ./documentos_origem --output ./documentos_organizados -r --resume

  # Modo Interativo (permite revisar/ajustar metadados na tela):
  python main.py --input ./documentos_origem --output ./documentos_organizados --interactive

Classificações Suportadas:
  - artigo
  - livro
  - tese
  - revista
  - apostila
  - outros
        """,
    )

    parser.add_argument(
        "--input",
        "--origem",
        "-i",
        dest="input",
        required=True,
        type=str,
        help="Caminho da pasta de origem contendo os arquivos PDF para processamento.",
    )
    parser.add_argument(
        "--output",
        "--destino",
        "-o",
        dest="output",
        required=True,
        type=str,
        help="Caminho da pasta de destino onde os arquivos serão organizados por classificação.",
    )
    parser.add_argument(
        "--keep-original",
        action="store_true",
        default=False,
        help="Se informado, copia os PDFs em vez de movê-los da pasta de origem.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Executa em modo de simulação, exibindo nomes padronizados e destinos sem modificar o disco.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Retoma a execução do lote a partir do checkpoint salvo (.organizador_pdf_estado.json), pulando arquivos já concluídos.",
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        default=False,
        help="Busca recursivamente arquivos PDF em subpastas da pasta de origem.",
    )
    parser.add_argument(
        "--no-quarantine",
        action="store_true",
        default=False,
        help="Desativa o direcionamento para revisao_manual/ para documentos com baixa confiança ou metadados incertos.",
    )
    parser.add_argument(
        "--offline",
        "--no-online",
        dest="offline",
        action="store_true",
        default=False,
        help="Desativa consultas externas a APIs públicas (Crossref, Google Books, Brasil API), rodando 100%% offline.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Habilita logs detalhados de depuração (DEBUG).",
    )

    parser.add_argument(
        "--interactive",
        "--validate",
        dest="interactive",
        action="store_true",
        default=False,
        help="Executa em modo interativo de validação passo a passo com confirmação de metadados em tempo real.",
    )

    return parser


def main() -> int:
    """Main CLI execution flow."""
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)
    logger = logging.getLogger("CLI")

    input_dir = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        logger.error("A pasta de origem especificada não existe: %s", input_dir)
        return 1

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    if args.interactive:
        from scripts.interactive_validator import run_interactive_validator
        return run_interactive_validator(
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            interactive=True,
            move_original=not args.keep_original,
        )

    print("\n" + "=" * 75)
    print("🚀 PIPELINE DE PROCESSAMENTO E CONVERSÃO DE PDF PARA MARKDOWN")
    print("=" * 75)
    print(f"📂 Origem     : {input_dir}")
    print(f"🎯 Destino    : {output_dir}")
    print(f"📦 Mover      : {'Não (copiando)' if args.keep_original else 'Sim (movendo original)'}")
    print(f"🔍 Recursivo  : {'Sim (--recursive)' if args.recursive else 'Não (somente raiz)'}")
    print(f"🔄 Retomada   : {'Ativa (--resume)' if args.resume else 'Padrão'}")
    print(f"🛡️  Quarentena : {'Desativada (--no-quarantine)' if args.no_quarantine else 'Ativa (revisao_manual/)'}")
    print(f"🌐 Rede        : {'100% Offline (--offline)' if args.offline else 'Online (APIs públicas ativas)'}")
    if args.dry_run:
        print(f"⚠️  MODO      : SIMULAÇÃO / DRY-RUN (Nenhum arquivo será gravado ou movido)")
    print("=" * 75 + "\n")

    organizer = PipelineOrganizer(online=not args.offline)
    results = organizer.process_directory(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        move_original=not args.keep_original,
        dry_run=args.dry_run,
        recursive=args.recursive,
        resume=args.resume,
        quarantine=not args.no_quarantine,
    )

    if not results:
        print("⚠️ Nenhum arquivo PDF pendente para processamento na pasta de origem.\n")
        return 0

    successful = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    quarantined = [r for r in successful if r.needs_review and not args.no_quarantine]

    print("\n" + "=" * 75)
    print(f"📊 RESUMO DE EXECUÇÃO {'[SIMULAÇÃO DRY-RUN]' if args.dry_run else ''}")
    print("=" * 75)
    print(f"Total de PDFs processados: {len(results)}")
    print(f"  ✅ Concluídos com sucesso : {len(successful)}")
    if quarantined:
        print(f"  🔍 Em Quarentena (Revisão): {len(quarantined)}")
    print(f"  ❌ Falhas                 : {len(failed)}")

    class_counts = {}
    for r in successful:
        class_counts[r.classification] = class_counts.get(r.classification, 0) + 1

    print("\n📂 Distribuição por Categoria:")
    for cat, count in sorted(class_counts.items()):
        print(f"  - {cat:20s}: {count}")

    if successful:
        print("\n📄 Amostra dos Documentos Gerados / Destinos:")
        for r in successful[:5]:
            status_tag = "[QUARENTENA] " if r.needs_review and not args.no_quarantine else ""
            print(f"  • {status_tag}[{r.classification}] {Path(r.target_pdf).name}")
            print(f"    Destino: {Path(r.target_pdf).parent}")
            if r.metadata and r.metadata.area:
                print(f"    Área   : {r.metadata.area} | Tags: {r.metadata.tags}")
            print(f"    ABNT   : {r.abnt_reference[:100]}...")

    if quarantined:
        print("\n⚠️ Documentos Enviados para Revisão Manual (revisao_manual/):")
        for r in quarantined:
            motivos = "; ".join(r.metadata.review_reasons) if r.metadata else "Critério de quarentena atingido"
            print(f"  • {Path(r.target_pdf).name}")
            print(f"    Motivo: {motivos}")

    if failed:
        print("\n❌ Erros Encontrados:")
        for r in failed:
            print(f"  • {Path(r.original_pdf).name}: {r.error_message}")

    print("=" * 75 + "\n")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
