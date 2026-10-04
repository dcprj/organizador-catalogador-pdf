"""Unit and integration tests for the 5 implemented improvements:
1. EstadoManager and --resume
2. --dry-run simulation mode
3. Quarantine routing to revisao_manual/ and --no-quarantine
4. Thematic area extraction and Obsidian tags
5. --recursive PDF file discovery
"""

import sys
import subprocess
from pathlib import Path
import pytest

from src.estado import EstadoManager, DEFAULT_STATE_FILENAME
from src.models import PublicationMetadata, Identifiers
from src.organizer import PipelineOrganizer
from src.converter import MarkdownConverter
from src.classifier_jev import parse_page_cip


def test_estado_manager_lifecycle(tmp_path: Path):
    """Test EstadoManager initialization, saving checkpoint, query, and cleanup."""
    state_dir = tmp_path / "output_dest"
    state_dir.mkdir()
    mgr = EstadoManager(base_dir=state_dir)

    test_pdf = str(tmp_path / "doc1.pdf")
    assert not mgr.is_completed(test_pdf)
    assert mgr.get_completed_count() == 0

    mgr.save_checkpoint(
        original_pdf=test_pdf,
        target_pdf=str(state_dir / "livro" / "doc1.pdf"),
        classification="livro",
        success=True,
    )

    assert mgr.is_completed(test_pdf)
    assert mgr.get_completed_count() == 1

    # Reload from file to ensure persistence
    mgr2 = EstadoManager(base_dir=state_dir)
    assert mgr2.is_completed(test_pdf)
    assert mgr2.get_completed_count() == 1

    mgr2.clear()
    assert not (state_dir / DEFAULT_STATE_FILENAME).exists()


def test_dry_run_simulation(tmp_path: Path, sample_pdf_generator):
    """Test that dry-run mode simulates renaming without modifying or creating files."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    pages = [
        "Apostila de Introdução à Algoritmos\n"
        "Material didático do curso\n"
        "Notas de aula e exercícios práticos.\n"
    ]
    pdf_path = sample_pdf_generator("algoritmos.pdf", "Algoritmos", pages)
    source_pdf = input_dir / "algoritmos.pdf"
    pdf_path.rename(source_pdf)

    organizer = PipelineOrganizer()
    result = organizer.process_pdf(
        pdf_path=str(source_pdf),
        output_base_dir=str(output_dir),
        move_original=True,
        dry_run=True,
    )

    assert result.success is True
    assert result.is_dry_run is True
    # The original PDF must still exist in the input directory
    assert source_pdf.exists()
    # The output markdown and pdf should NOT exist on disk
    assert not Path(result.output_markdown).exists()
    assert not Path(result.target_pdf).exists()


def test_recursive_discovery(tmp_path: Path, sample_pdf_generator):
    """Test recursive vs non-recursive file search."""
    input_dir = tmp_path / "in_root"
    sub_dir = input_dir / "subfolder" / "deep"
    sub_dir.mkdir(parents=True)
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    pages = ["Documento de teste com conteúdo básico."]
    pdf1 = sample_pdf_generator("root_doc.pdf", "Root Doc", pages)
    pdf1.rename(input_dir / "root_doc.pdf")

    pdf2 = sample_pdf_generator("nested_doc.pdf", "Nested Doc", pages)
    pdf2.rename(sub_dir / "nested_doc.pdf")

    organizer = PipelineOrganizer()

    # Non-recursive: should only find root_doc.pdf
    res_shallow = organizer.process_directory(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        move_original=False,
        dry_run=True,
        recursive=False,
    )
    assert len(res_shallow) == 1
    assert Path(res_shallow[0].original_pdf).name == "root_doc.pdf"

    # Recursive: should find both root_doc.pdf and nested_doc.pdf
    res_recursive = organizer.process_directory(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        move_original=False,
        dry_run=True,
        recursive=True,
    )
    assert len(res_recursive) == 2


def test_resume_skips_already_processed_files(tmp_path: Path, sample_pdf_generator):
    """Test that --resume skips files that were already processed in checkpoint."""
    input_dir = tmp_path / "batch_in"
    output_dir = tmp_path / "batch_out"
    input_dir.mkdir()
    output_dir.mkdir()

    pages = ["Apostila de Redes\nMaterial didático.\n"]
    pdf1 = sample_pdf_generator("doc1.pdf", "Doc 1", pages)
    pdf1.rename(input_dir / "doc1.pdf")
    pdf2 = sample_pdf_generator("doc2.pdf", "Doc 2", pages)
    pdf2.rename(input_dir / "doc2.pdf")

    organizer = PipelineOrganizer()

    # Process first time (both processed)
    results1 = organizer.process_directory(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        move_original=False,
        resume=False,
    )
    assert len(results1) == 2

    # Second run with resume=True: both should be skipped
    results2 = organizer.process_directory(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        move_original=False,
        resume=True,
    )
    assert len(results2) == 0


def test_quarantine_revisao_manual_routing(tmp_path: Path, sample_pdf_generator):
    """Test that documents flagged for review route to revisao_manual/ unless disabled."""
    from unittest.mock import MagicMock

    input_dir = tmp_path / "quar_in"
    output_dir = tmp_path / "quar_out"
    input_dir.mkdir()
    output_dir.mkdir()

    pages = ["Texto qualquer de teste."]
    pdf = sample_pdf_generator("incerto.pdf", "Incerto", pages)
    source_pdf = input_dir / "incerto.pdf"
    pdf.rename(source_pdf)

    # Mock enricher to return metadata with needs_review=True
    mock_enricher = MagicMock()
    flagged_meta = PublicationMetadata(
        title="Documento Sem Identificadores",
        classification="outros",
        needs_review=True,
        review_reasons=["Baixa confiança e sem confirmação em base pública"],
    )
    mock_enricher.enrich.return_value = flagged_meta

    organizer = PipelineOrganizer(enricher=mock_enricher)

    # 1. With quarantine enabled (default)
    res_quarantine = organizer.process_pdf(
        pdf_path=str(source_pdf),
        output_base_dir=str(output_dir),
        move_original=False,
        quarantine=True,
    )
    assert res_quarantine.needs_review is True
    target_q_path = Path(res_quarantine.target_pdf)
    assert target_q_path.parent.name == "outros"
    assert target_q_path.parent.parent.name == "revisao_manual"
    assert (output_dir / "revisao_manual" / "outros").exists()

    # 2. With quarantine disabled (--no-quarantine)
    res_no_quar = organizer.process_pdf(
        pdf_path=str(source_pdf),
        output_base_dir=str(output_dir),
        move_original=False,
        quarantine=False,
    )
    assert res_no_quar.needs_review is True
    target_nq_path = Path(res_no_quar.target_pdf)
    assert target_nq_path.parent.name == "outros"
    assert target_nq_path.parent.parent.name == "quar_out"
    assert (output_dir / "outros").exists()


def test_thematic_area_and_obsidian_tags():
    """Test thematic area parsing from CIP and Obsidian hierarchical tags formatting."""
    cip_sample = (
        "Dados Internacionais de Catalogação na Publicação (CIP)\n"
        "Fuentes, Daniel\n"
        "Neuropsicologia: teoria e prática / Daniel Fuentes... [et al.]. - Porto Alegre : Artmed, 2014.\n"
        "1. Neuropsicologia. 2. Avaliação neuropsicológica. I. Título.\n"
        "CDD 616.8\n"
    )
    cip_data = parse_page_cip(cip_sample)
    assert cip_data.get("area") == "Neuropsicologia"

    metadata = PublicationMetadata(
        title="Neuropsicologia: teoria e prática",
        authors=["Fuentes, Daniel"],
        year=2014,
        area="Neuropsicologia",
        classification="livro",
        identifiers=Identifiers(isbn="9788582710548"),
    )

    md = MarkdownConverter.assemble_markdown(
        metadata=metadata,
        abnt_reference="FUENTES, Daniel. Neuropsicologia...",
    )

    assert "area: Neuropsicologia" in md
    assert "tags:" in md
    assert "- tipo/livro" in md
    assert "- ano/2014" in md
    assert "- area/neuropsicologia" in md


def test_cli_new_flags_help():
    """Verify that all new CLI arguments are present in main.py --help."""
    venv_python = Path(sys.executable)
    cmd = [str(venv_python), "main.py", "--help"]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )

    assert proc.returncode == 0
    assert "--dry-run" in proc.stdout
    assert "--resume" in proc.stdout
    assert "--recursive" in proc.stdout
    assert "--no-quarantine" in proc.stdout
    assert "--sem-enriquecimento-online" in proc.stdout


def test_metadata_enricher_offline_mode():
    """Verify that MetadataEnricher in offline mode does not make external requests."""
    from src.metadata_api import MetadataEnricher
    from src.models import ExtractedCandidates, JevValidationResult

    enricher = MetadataEnricher(online=False)
    assert not enricher.online

    dummy_jev = JevValidationResult(
        classification="livro",
        confidence=0.98,
        reasoning="Test",
        candidates=ExtractedCandidates(
            raw_title="Título Offline",
            raw_authors=["Autor Teste"],
            raw_year=2024,
            isbn="978-85-326-1234-5",
        ),
        probabilities={"isbn": 1.0},
    )

    result = enricher.enrich(dummy_jev)
    assert result.title == "Título Offline"
    assert result.authors == ["Autor Teste"]
    assert result.year == 2024


def test_cli_typer_flags_help():
    """Verify that organizador-pdf CLI Typer includes --sem-enriquecimento-online and --input/--output."""
    from typer.testing import CliRunner
    from src.organizador_pdf.cli import app

    runner = CliRunner(env={"COLUMNS": "160"})
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "--sem-enriquecimento-online" in res.output
    assert "--input" in res.output
    assert "--output" in res.output


def test_resume_preserva_quarantine_e_enriquecimento(tmp_path: Path):
    """Verify that EstadoDeExecucao saves and restores quarantine and enriquecimento_online."""
    from src.organizador_pdf.estado import EstadoDeExecucao, ParametrosSalvos

    params = ParametrosSalvos(
        origem=str(tmp_path / "origem"),
        destino=str(tmp_path / "destino"),
        quarantine=False,
        enriquecimento_online=False,
    )
    estado = EstadoDeExecucao(parametros=params)
    estado.salvar()

    recarregado = EstadoDeExecucao.carregar()
    assert recarregado is not None
    assert recarregado.parametros.quarantine is False
    assert recarregado.parametros.enriquecimento_online is False


def test_jev_classifier_blocks_network_when_permitir_rede_false(monkeypatch):
    """Verify that JevClassifier zeroes API key when permitir_rede is False."""
    from src.organizador_pdf.classifier_jev import JevClassifier

    monkeypatch.setenv("TYPESAFE_API_KEY", "dummy-secret-key")
    classifier = JevClassifier(permitir_rede=False)
    assert classifier.api_key is None


def test_strict_zero_network_when_sem_enriquecimento_online(tmp_path: Path, monkeypatch, sample_pdf_generator):
    """Ensure that with sem_enriquecimento_online=True or permitir_rede=False, zero network requests occur."""
    import requests
    from src.organizador_pdf.metadata_api import MetadataEnricher
    from src.organizador_pdf.classifier_jev import JevClassifier
    from src.organizador_pdf.interactive_validator import validate_and_process_pdf
    from src.models import ExtractedCandidates, JevValidationResult

    # Strictly block any network call via requests
    def mock_fail(*args, **kwargs):
        raise RuntimeError("VIOLAÇÃO DE REDE: Chamada de rede externa detectada quando permitir_rede=False!")

    monkeypatch.setattr(requests.Session, "send", mock_fail)
    monkeypatch.setattr(requests.Session, "request", mock_fail)
    monkeypatch.setattr(requests, "get", mock_fail)
    monkeypatch.setattr(requests, "post", mock_fail)
    monkeypatch.setenv("TYPESAFE_API_KEY", "dummy-secret-key")

    # 1. MetadataEnricher test
    enricher = MetadataEnricher(online=False)
    dummy_jev = JevValidationResult(
        classification="livro",
        confidence=0.98,
        reasoning="Test",
        candidates=ExtractedCandidates(
            raw_title="Título Offline",
            raw_authors=["Autor Teste"],
            raw_year=2024,
            isbn="978-85-326-1234-5",
            doi="10.1000/182",
        ),
        probabilities={"isbn": 1.0},
    )
    result = enricher.enrich(dummy_jev)
    assert result.title == "Título Offline"
    assert result.source_apis == []

    # 2. JevClassifier test
    pages = ["Documento de teste com conteúdo básico e ISBN 978-85-326-1234-5."]
    pdf_path = sample_pdf_generator("doc_offline.pdf", "Offline Doc", pages)
    classifier = JevClassifier(permitir_rede=False)
    res = classifier.classify_and_validate(str(pdf_path))
    assert res.classification is not None

    # 3. Interactive validator process test (non-interactive, sem_enriquecimento_online=True)
    out_dir = tmp_path / "saida_offline"
    out_dir.mkdir()
    ok = validate_and_process_pdf(
        pdf_path=str(pdf_path),
        output_base_dir=str(out_dir),
        interactive=False,
        move_original=False,
        sem_enriquecimento_online=True,
    )
    assert ok is True


def test_scripts_interactive_validator_shim_import():
    """Verify that scripts/interactive_validator.py works as a backwards-compatible shim."""
    import scripts.interactive_validator as shim

    assert hasattr(shim, "run_interactive_validator")
    assert hasattr(shim, "main")
    assert hasattr(shim, "validate_and_process_pdf")


def test_main_respects_config_verificar_online(tmp_path: Path, monkeypatch):
    """Verify that main.py respects ORGPDF_VERIFICAR_ONLINE=false from the environment."""
    from main import build_parser, Config

    monkeypatch.setenv("ORGPDF_VERIFICAR_ONLINE", "false")
    config = Config.do_ambiente()
    assert config.verificar_online is False

    parser = build_parser()
    args = parser.parse_args(["--input", str(tmp_path), "--output", str(tmp_path)])
    enriquecimento_online = (not args.sem_enriquecimento_online) and config.verificar_online
    assert enriquecimento_online is False

