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

from organizador_pdf.estado import EstadoManager, DEFAULT_STATE_FILENAME
from organizador_pdf.models import PublicationMetadata, Identifiers
from organizador_pdf.organizer import PipelineOrganizer
from organizador_pdf.converter import MarkdownConverter
from organizador_pdf.classifier_jev import parse_page_cip


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
    assert "--interactive" in proc.stdout


def test_metadata_enricher_offline_mode():
    """Verify that MetadataEnricher in offline mode does not make external requests."""
    from organizador_pdf.metadata_api import MetadataEnricher
    from organizador_pdf.models import ExtractedCandidates, JevValidationResult

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
    """Verify that organizador-pdf CLI Typer includes expected options."""
    from typer.testing import CliRunner
    from organizador_pdf.cli import app

    runner = CliRunner(env={"COLUMNS": "160"})
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "--input" in res.output
    assert "--output" in res.output
    assert "--origem" in res.output
    assert "--destino" in res.output
    assert "--resume" in res.output
    assert "--quarantine" in res.output
    assert "--interactive" in res.output


def test_resume_preserva_parametros(tmp_path: Path):
    """Verify that EstadoDeExecucao saves and restores execution parameters."""
    from organizador_pdf.estado import EstadoDeExecucao, ParametrosSalvos

    params = ParametrosSalvos(
        origem=str(tmp_path / "origem"),
        destino=str(tmp_path / "destino"),
        quarantine=False,
    )
    estado = EstadoDeExecucao(parametros=params)
    estado.salvar()

    recarregado = EstadoDeExecucao.carregar()
    assert recarregado is not None
    assert recarregado.parametros.quarantine is False


def test_jev_classifier_always_uses_jev(monkeypatch, sample_pdf_generator):
    """Verify that JevClassifier uses TYPESAFE_API_KEY if present, and local fallback if not."""
    from organizador_pdf.classifier_jev import JevClassifier

    # 1. With API key in environment
    monkeypatch.setenv("TYPESAFE_API_KEY", "dummy-secret-key")
    classifier_with_key = JevClassifier()
    assert classifier_with_key.api_key == "dummy-secret-key"

    # 2. Without API key
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    classifier_no_key = JevClassifier()
    assert classifier_no_key.api_key is None

    # 3. Classify with fallback
    pages = ["Documento de teste com conteúdo básico para classificação Jev local."]
    pdf_path = sample_pdf_generator("doc_jev.pdf", "Jev Doc", pages)
    res = classifier_no_key.classify_and_validate(str(pdf_path))
    assert res.classification is not None
    assert res.classification_confidence >= 0.0
    assert res.probabilities is not None


def test_scripts_interactive_validator_shim_import():
    """Verify that scripts/interactive_validator.py works as a backwards-compatible shim."""
    import scripts.interactive_validator as shim

    assert hasattr(shim, "run_interactive_validator")
    assert hasattr(shim, "main")
    assert hasattr(shim, "validate_and_process_pdf")


def test_main_propagates_config_limits(tmp_path: Path, monkeypatch):
    """Verify that main.py respects max_paginas and max_caracteres from Config."""
    from main import build_parser, Config
    from organizador_pdf.organizer import PipelineOrganizer

    monkeypatch.setenv("ORGPDF_MAX_PAGINAS", "8")
    monkeypatch.setenv("ORGPDF_MAX_CARACTERES", "12000")
    config = Config.do_ambiente()
    assert config.max_paginas == 8
    assert config.max_caracteres == 12000

    organizer = PipelineOrganizer(
        max_paginas=config.max_paginas,
        max_caracteres=config.max_caracteres,
    )
    assert organizer.max_paginas == 8
    assert organizer.max_caracteres == 12000


def test_resume_preserva_execucao_cli(tmp_path: Path, sample_pdf_generator):
    """Test full pipeline resume via Typer CLI."""
    from typer.testing import CliRunner
    from organizador_pdf.cli import app
    from organizador_pdf.estado import EstadoDeExecucao, ParametrosSalvos

    origem = tmp_path / "origem"
    destino = tmp_path / "destino"
    origem.mkdir()
    destino.mkdir()

    pages = ["Documento de teste para retomada do lote."]
    pdf1 = sample_pdf_generator("pdf1.pdf", "PDF 1", pages)
    pdf1.rename(origem / "pdf1.pdf")

    # Manually seed an interrupted state
    estado = EstadoDeExecucao(
        parametros=ParametrosSalvos(
            origem=str(origem),
            destino=str(destino),
            dry_run=False,
            recursive=True,
            mover=False,
            subpasta_markdown=None,
            paralelo=1,
            quarantine=True,
        ),
        concluidos=[],
    )
    estado.salvar()

    runner = CliRunner()
    res = runner.invoke(app, ["--resume"])
    assert res.exit_code == 0


def test_pipeline_organizer_limits_and_verificar_online(tmp_path: Path, sample_pdf_generator):
    """Verify that PipelineOrganizer passes max_paginas, max_caracteres, and online setting."""
    from organizador_pdf.organizer import PipelineOrganizer

    pages = ["A" * 500, "B" * 500, "C" * 500]
    pdf_path = sample_pdf_generator("limites.pdf", "Doc Limites", pages)

    out_dir = tmp_path / "out_limites"
    out_dir.mkdir()

    organizer = PipelineOrganizer(
        max_paginas=2,
        max_caracteres=200,
        enriquecimento_online=False,
    )
    assert organizer.max_paginas == 2
    assert organizer.max_caracteres == 200
    assert organizer.enricher.online is False

    result = organizer.process_pdf(str(pdf_path), str(out_dir), dry_run=True)
    assert result.success is True


def test_deprecation_warnings_on_old_kwargs():
    """Verify that passing deprecated kwargs raises DeprecationWarning."""
    import pytest
    from organizador_pdf.classifier_jev import JevClassifier
    from organizador_pdf.organizer import PipelineOrganizer

    with pytest.deprecated_call():
        JevClassifier(permitir_rede=False)

    with pytest.deprecated_call():
        PipelineOrganizer(sem_enriquecimento_online=True)


def test_classify_and_validate_receives_limits_from_interactive_validator(tmp_path: Path, sample_pdf_generator, monkeypatch):
    """Verify that validate_and_process_pdf forwards custom max_paginas and max_caracteres to classify_and_validate."""
    import organizador_pdf.classifier_jev as cj
    from organizador_pdf.interactive_validator import validate_and_process_pdf

    pages = ["Conteúdo para teste do validador interativo com limites customizados."]
    pdf_path = sample_pdf_generator("interativo_limites.pdf", "Interativo Limites", pages)
    out_dir = tmp_path / "out_interativo"
    out_dir.mkdir()

    calls = []
    orig = cj.JevClassifier.classify_and_validate

    def spy(self, pdf_path, max_paginas=10, max_caracteres=30000, **kwargs):
        calls.append({"pdf_path": pdf_path, "max_paginas": max_paginas, "max_caracteres": max_caracteres})
        return orig(self, pdf_path, max_paginas=max_paginas, max_caracteres=max_caracteres, **kwargs)

    monkeypatch.setattr(cj.JevClassifier, "classify_and_validate", spy)

    validate_and_process_pdf(
        pdf_path=str(pdf_path),
        output_base_dir=str(out_dir),
        interactive=False,
        verificar_online=False,
        max_paginas=4,
        max_caracteres=2500,
    )

    assert len(calls) == 1
    assert calls[0]["max_paginas"] == 4
    assert calls[0]["max_caracteres"] == 2500


def test_classify_and_validate_receives_limits_from_cli_pipeline(tmp_path: Path, sample_pdf_generator, monkeypatch):
    """Verify that Typer CLI pipeline forwards custom limits to classify_and_validate."""
    from typer.testing import CliRunner
    from organizador_pdf.cli import app
    import organizador_pdf.classifier_jev as cj

    in_dir = tmp_path / "cli_in"
    out_dir = tmp_path / "cli_out"
    in_dir.mkdir()
    out_dir.mkdir()

    pages = ["Conteúdo do PDF para teste CLI."]
    pdf = sample_pdf_generator("cli_doc.pdf", "CLI Doc", pages)
    pdf.rename(in_dir / "cli_doc.pdf")

    monkeypatch.setenv("ORGPDF_MAX_PAGINAS", "5")
    monkeypatch.setenv("ORGPDF_MAX_CARACTERES", "10000")
    monkeypatch.setenv("ORGPDF_VERIFICAR_ONLINE", "false")

    calls = []
    orig = cj.JevClassifier.classify_and_validate

    def spy(self, pdf_path, max_paginas=10, max_caracteres=30000, **kwargs):
        calls.append({"pdf_path": pdf_path, "max_paginas": max_paginas, "max_caracteres": max_caracteres})
        return orig(self, pdf_path, max_paginas=max_paginas, max_caracteres=max_caracteres, **kwargs)

    monkeypatch.setattr(cj.JevClassifier, "classify_and_validate", spy)

    runner = CliRunner()
    result = runner.invoke(app, ["-i", str(in_dir), "-o", str(out_dir), "--dry-run"])
    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0]["max_paginas"] == 5
    assert calls[0]["max_caracteres"] == 10000


def test_classify_and_validate_receives_limits_from_main(tmp_path: Path, sample_pdf_generator, monkeypatch):
    """Verify that main.py forwards custom limits to classify_and_validate."""
    import main
    import organizador_pdf.classifier_jev as cj

    in_dir = tmp_path / "main_in"
    out_dir = tmp_path / "main_out"
    in_dir.mkdir()
    out_dir.mkdir()

    pages = ["Conteúdo para teste via main.py."]
    pdf = sample_pdf_generator("main_doc.pdf", "Main Doc", pages)
    pdf.rename(in_dir / "main_doc.pdf")

    monkeypatch.setenv("ORGPDF_MAX_PAGINAS", "7")
    monkeypatch.setenv("ORGPDF_MAX_CARACTERES", "14000")
    monkeypatch.setenv("ORGPDF_VERIFICAR_ONLINE", "false")
    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "--input", str(in_dir), "--output", str(out_dir), "--dry-run"],
    )

    calls = []
    orig = cj.JevClassifier.classify_and_validate

    def spy(self, pdf_path, max_paginas=10, max_caracteres=30000, **kwargs):
        calls.append({"pdf_path": pdf_path, "max_paginas": max_paginas, "max_caracteres": max_caracteres})
        return orig(self, pdf_path, max_paginas=max_paginas, max_caracteres=max_caracteres, **kwargs)

    monkeypatch.setattr(cj.JevClassifier, "classify_and_validate", spy)

    ret = main.main()
    assert ret == 0
    assert len(calls) == 1
    assert calls[0]["max_paginas"] == 7
    assert calls[0]["max_caracteres"] == 14000


def test_sampling_limits_affect_actual_text_extraction(tmp_path: Path, sample_pdf_generator, monkeypatch):
    """Verify that max_paginas and max_caracteres effectively limit text extraction and candidate analysis."""
    import organizador_pdf.classifier_jev as cj

    # 12 pages of text with distinct markers
    pages = [f"Página {i}: " + ("x" * 200) for i in range(1, 13)]
    pdf_path = sample_pdf_generator("doc_12_paginas.pdf", "Doc 12 Paginas", pages)

    calls_extract = []
    orig_extract = cj.extract_native_sample_text

    def spy_extract(pdf, head_pages=10, tail_pages=10, **kwargs):
        calls_extract.append({"head_pages": head_pages, "tail_pages": tail_pages})
        return orig_extract(pdf, head_pages=head_pages, tail_pages=tail_pages, **kwargs)

    monkeypatch.setattr(cj, "extract_native_sample_text", spy_extract)

    classifier = cj.JevClassifier()
    # Call with max_paginas=2 and max_caracteres=300
    res = classifier.classify_and_validate(str(pdf_path), max_paginas=2, max_caracteres=300)

    assert len(calls_extract) == 1
    assert calls_extract[0]["head_pages"] == 2
    assert calls_extract[0]["tail_pages"] == 2
    assert res.classification in ["artigo", "livro", "tese", "revista", "apostila", "outros"]


def test_interactive_validator_supports_estrutura_cnpq_and_plana(tmp_path: Path, sample_pdf_generator):
    """Verify that validate_and_process_pdf honors estrutura='cnpq' and 'plana'."""
    from organizador_pdf.interactive_validator import validate_and_process_pdf

    pages = ["Conteúdo para teste de estrutura cnpq e plana."]
    pdf_path = sample_pdf_generator("estrutura_test.pdf", "Estrutura Test", pages)

    out_cnpq = tmp_path / "out_cnpq"
    out_cnpq.mkdir()
    ok_cnpq = validate_and_process_pdf(
        pdf_path=str(pdf_path),
        output_base_dir=str(out_cnpq),
        interactive=False,
        estrutura="cnpq",
        quarantine=False,
        verificar_online=False,
    )
    assert ok_cnpq is True
    # Deve conter estrutura hierárquica (subpastas além de apenas tipo)
    cnpq_pdfs = list(out_cnpq.glob("*/*/*/*.pdf"))
    assert len(cnpq_pdfs) == 1

    out_plana = tmp_path / "out_plana"
    out_plana.mkdir()
    ok_plana = validate_and_process_pdf(
        pdf_path=str(pdf_path),
        output_base_dir=str(out_plana),
        interactive=False,
        estrutura="plana",
        quarantine=False,
        verificar_online=False,
    )
    assert ok_plana is True
    # Estrutura plana: <out_plana>/<tipo>/arquivo.pdf
    plana_pdfs = list(out_plana.glob("*/*.pdf"))
    assert len(plana_pdfs) == 1


def test_interactive_validator_collision_avoidance(tmp_path: Path, sample_pdf_generator):
    """Verify that validate_and_process_pdf avoids overwriting existing files via caminho_disponivel."""
    from organizador_pdf.interactive_validator import validate_and_process_pdf

    pages = ["Conteúdo idêntico para testar colisão no validador interativo."]
    pdf_path = sample_pdf_generator("colisao_test.pdf", "Colisao Test", pages)
    out_dir = tmp_path / "out_colisao"
    out_dir.mkdir()

    # Primeira execução
    validate_and_process_pdf(
        pdf_path=str(pdf_path),
        output_base_dir=str(out_dir),
        interactive=False,
        estrutura="plana",
        quarantine=False,
        verificar_online=False,
    )

    # Segunda execução do mesmo arquivo
    validate_and_process_pdf(
        pdf_path=str(pdf_path),
        output_base_dir=str(out_dir),
        interactive=False,
        estrutura="plana",
        quarantine=False,
        verificar_online=False,
    )

    # Devem existir dois PDFs: o original e a versão com sufixo (2)
    todos_pdfs = list(out_dir.glob("*/*.pdf"))
    assert len(todos_pdfs) == 2
    nomes = [p.name for p in todos_pdfs]
    assert any("(2).pdf" in n for n in nomes)

    todos_mds = list(out_dir.glob("*/*.md"))
    assert len(todos_mds) == 2
    nomes_md = [m.name for m in todos_mds]
    assert any("(2).md" in n for n in nomes_md)


def test_low_confidence_and_quarantine_policy(tmp_path: Path, sample_pdf_generator):
    """Verify that ambiguous PDFs without identifiers receive confidence < 0.50 and are quarantined."""
    from organizador_pdf.classifier_jev import JevClassifier
    from organizador_pdf.metadata_api import MetadataEnricher
    from organizador_pdf.pipeline import Pipeline, OpcoesDoPipeline

    # Ambiguous document without clear keywords or identifiers
    pages = ["Texto sem marcadores bibliográficos claros ou identificadores públicos."]
    pdf_path = sample_pdf_generator("ambiguo.pdf", "Ambiguo Test", pages)

    classifier = JevClassifier()
    res = classifier.classify_and_validate(str(pdf_path))
    assert res.classification_confidence < 0.50
    assert "calibrated_scores" in res.raw_jev_data

    enricher = MetadataEnricher(online=False)
    meta = enricher.enrich(res)
    assert meta.needs_review is True
    assert len(meta.review_reasons) > 0
    assert meta.confidence < 0.50

    # Test via pipeline quarantine
    out_dir = tmp_path / "out_quarantine"
    out_dir.mkdir()
    pipeline = Pipeline(
        opcoes=OpcoesDoPipeline(
            destino=out_dir, mover=False, quarantine=True, enriquecimento_online=False
        )
    )
    result = pipeline.processar_arquivo(pdf_path)
    assert result.ok is True
    assert result.metadados.needs_review is True
    assert result.pdf_destino is not None
    assert "revisao_manual" in result.pdf_destino.parts


def test_pre_extracted_text_used_without_reopening_pdf(tmp_path: Path, sample_pdf_generator, monkeypatch):
    """Verify that classify_and_validate uses texto_pre_extraido without re-opening the PDF."""
    import pymupdf
    from organizador_pdf.classifier_jev import JevClassifier

    pages = [f"Conteúdo da página {i}" for i in range(1, 5)]
    pdf_path = sample_pdf_generator("pre_extraido.pdf", "Pre Extraido", pages)

    open_calls = []
    orig_open = pymupdf.open

    def spy_open(*args, **kwargs):
        open_calls.append(args)
        return orig_open(*args, **kwargs)

    monkeypatch.setattr(pymupdf, "open", spy_open)

    classifier = JevClassifier()
    # Pass pre-extracted text and total_paginas
    res = classifier.classify_and_validate(
        str(pdf_path),
        texto_pre_extraido="Texto pré-extraído com abstract e introdução",
        total_paginas=4,
    )
    assert res.classification in ["artigo", "livro", "tese", "revista", "apostila", "outros"]
    # pymupdf.open should NOT have been called because text and page count were provided!
    assert len(open_calls) == 0


def test_extract_native_sample_text_with_count_head_and_tail(sample_pdf_generator):
    """Verify that extract_native_sample_text_with_count captures both head, tail, and doc length."""
    from organizador_pdf.classifier_jev import extract_native_sample_text_with_count

    pages = [f"Página de teste número {i}" for i in range(1, 26)]
    pdf_path = sample_pdf_generator("head_tail_count.pdf", "Head Tail Count", pages)

    combined_text, pages_list, total = extract_native_sample_text_with_count(
        str(pdf_path), head_pages=5, tail_pages=5
    )
    assert total == 25
    assert len(pages_list) == 10
    assert "Página de teste número 1" in combined_text
    assert "Página de teste número 5" in combined_text
    assert "Página de teste número 25" in combined_text
    assert "Página de teste número 12" not in combined_text


def test_metadata_enricher_thread_local_sessions():
    """Verify that worker threads get isolated requests.Session objects with retry adapters."""
    import threading
    from organizador_pdf.metadata_api import MetadataEnricher

    enricher = MetadataEnricher()
    sessions = []

    def worker():
        sessions.append(enricher.session)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Different threads must have distinct session instances
    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert enricher.session is not sessions[0]
    assert enricher.session is not sessions[1]

    # Main thread session also exists and has retry adapters mounted
    main_session = enricher.session
    assert "https://" in main_session.adapters
    assert "http://" in main_session.adapters


def test_metadata_enricher_in_memory_cache():
    """Verify that in-memory cache avoids duplicate network requests for identical queries."""
    from unittest.mock import MagicMock
    from organizador_pdf.metadata_api import MetadataEnricher

    enricher = MetadataEnricher()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {
            "title": ["Paper Title"],
            "DOI": "10.1016/j.test.2024",
        }
    }
    enricher.session = MagicMock()
    enricher.session.get.return_value = mock_resp

    # First call - queries API and stores in cache
    res1 = enricher.fetch_crossref_by_doi("10.1016/j.test.2024")
    assert res1 is not None
    assert res1["title"] == "Paper Title"
    assert enricher.session.get.call_count == 1

    # Second call for the same DOI - must return cached result without calling .get
    res2 = enricher.fetch_crossref_by_doi("10.1016/j.test.2024")
    assert res2 is not None
    assert res2["title"] == "Paper Title"
    assert enricher.session.get.call_count == 1

    # After clearing cache, next call must query again
    enricher.clear_cache()
    res3 = enricher.fetch_crossref_by_doi("10.1016/j.test.2024")
    assert res3 is not None
    assert enricher.session.get.call_count == 2


def test_metadata_enricher_timeout_tuple():
    """Verify that separate connect and read timeouts are configured."""
    from organizador_pdf.metadata_api import MetadataEnricher

    enricher = MetadataEnricher(timeout=8.0)
    assert enricher._get_timeout() == (3.0, 8.0)

    enricher_custom = MetadataEnricher(timeout=(1.5, 4.0))
    assert enricher_custom._get_timeout() == (1.5, 4.0)


def test_verificar_identificadores_skips_when_source_apis_already_verified():
    """Verify that verificar_identificadores skips external network calls when source_apis already verified the ID."""
    from unittest.mock import MagicMock
    from organizador_pdf.models import Metadados, Identificadores
    from organizador_pdf.verificacao import verificar_identificadores

    mock_client = MagicMock()

    # Case 1: DOI already enriched from Crossref
    meta_verified = Metadados(
        titulo="Deep Learning",
        identificadores=Identificadores(doi="10.1000/182"),
        source_apis=["Crossref (DOI)"],
    )
    res, aviso = verificar_identificadores(meta_verified, cliente=mock_client)
    assert aviso is None
    # Client must NOT be called
    assert mock_client.get.call_count == 0

    # Case 2: ISBN already enriched from CBL
    meta_verified_isbn = Metadados(
        titulo="Clean Code",
        identificadores=Identificadores(isbn="9780132350884"),
        source_apis=["Brasil API / CBL (ISBN)"],
    )
    res, aviso = verificar_identificadores(meta_verified_isbn, cliente=mock_client)
    assert aviso is None
    assert mock_client.get.call_count == 0

    # Case 3: Identifier present but NOT in source_apis -> must query client
    meta_unverified = Metadados(
        titulo="Deep Learning",
        identificadores=Identificadores(doi="10.1000/182"),
        source_apis=[],
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {"title": ["Deep Learning"], "author": []}
    }
    mock_client.get.return_value = mock_resp
    res, aviso = verificar_identificadores(meta_unverified, cliente=mock_client)
    assert aviso is None
    assert mock_client.get.call_count == 1


def test_cli_cabecalho_dynamic_banner():
    """Verify that _cabecalho produces dynamic, truthful output for online/offline and quarantine."""
    from pathlib import Path
    from organizador_pdf.cli import _cabecalho

    _cabecalho(
        origem=Path("/fake/origem"),
        destino=Path("/fake/destino"),
        pdfs=[Path("doc.pdf")],
        dry_run=True,
        mover=False,
        paralelo=2,
        estrutura="plana",
        quarantine=False,
        enriquecimento_online=False,
        max_paginas=5,
        max_caracteres=15000,
    )


# ==============================================================================
# FASE 5: SEGURANÇA, VALIDAÇÃO DE ENTRADA, LOCK DE ESTADO E PRIVACIDADE
# ==============================================================================


def test_is_valid_isbn_checksum():
    """Valida o cálculo de checksum estrito e verificação flexível de ISBN-10 e ISBN-13."""
    from organizador_pdf.models import is_valid_isbn

    # ISBN-10 válido numérico
    assert is_valid_isbn("0-306-40615-2", strict=True)
    # ISBN-10 válido com dígito X
    assert is_valid_isbn("0-8044-2957-X", strict=True)
    # ISBN-13 válido (978 e 979)
    assert is_valid_isbn("978-0-306-40615-7", strict=True)
    assert is_valid_isbn("9780132350884", strict=True)
    assert is_valid_isbn("979-10-90636-07-1", strict=True)

    # ISBN com checksum incorreto
    assert not is_valid_isbn("9780132350885", strict=True)
    assert is_valid_isbn("9780132350885", strict=False)  # Formato aceito em modo não-estrito

    # Formatos ilegais
    assert not is_valid_isbn("", strict=False)
    assert not is_valid_isbn(None, strict=False)
    assert not is_valid_isbn("12345", strict=False)
    assert not is_valid_isbn("976-0-306-40615-7", strict=False)  # Não inicia com 978/979


def test_is_valid_issn_checksum():
    """Valida o cálculo de checksum estrito e formato de ISSN-8."""
    from organizador_pdf.models import is_valid_issn

    # ISSNs válidos
    assert is_valid_issn("0378-5955", strict=True)
    assert is_valid_issn("2434-561X", strict=True)

    # Checksum incorreto
    assert not is_valid_issn("0378-5956", strict=True)
    assert is_valid_issn("0378-5956", strict=False)

    # Inválidos
    assert not is_valid_issn("", strict=False)
    assert not is_valid_issn(None, strict=False)
    assert not is_valid_issn("1234", strict=False)


def test_normalizar_doi():
    """Valida a remoção de prefixos comuns e limpeza de espaços em DOIs."""
    from organizador_pdf.models import normalizar_doi

    assert normalizar_doi("https://doi.org/10.1000/182") == "10.1000/182"
    assert normalizar_doi("http://doi.org/10.1000/182") == "10.1000/182"
    assert normalizar_doi("http://dx.doi.org/10.1000/182") == "10.1000/182"
    assert normalizar_doi("doi: 10.1000/182") == "10.1000/182"
    assert normalizar_doi("  10.1000/182  ") == "10.1000/182"
    assert normalizar_doi(None) is None
    assert normalizar_doi("") is None


def test_lock_arquivo_estado_concurrency():
    """Verifica que lock_arquivo_estado impede acesso simultâneo quando bloqueado."""
    import time
    from organizador_pdf.estado import lock_arquivo_estado, _HAS_FCNTL
    import pytest

    if not _HAS_FCNTL:
        pytest.skip("fcntl não suportado neste sistema operacional")

    with lock_arquivo_estado(timeout=1.0):
        # Uma segunda tentativa concorrente com timeout curto deve expirar
        with pytest.raises(TimeoutError, match="Não foi possível obter o lock do arquivo de estado"):
            with lock_arquivo_estado(timeout=0.1):
                pass


def test_erro_pdf_escaneado(tmp_path):
    """Verifica que PDFs sem camada de texto disparam ErroPdfEscaneado."""
    import pymupdf
    from organizador_pdf.converter import converter_pdf, ErroPdfEscaneado, ErroDeConversao
    import pytest

    pdf_sem_texto = tmp_path / "escaneado.pdf"
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)  # Página em branco
    doc.save(pdf_sem_texto)
    doc.close()

    with pytest.raises(ErroPdfEscaneado, match="Nenhum texto nativo extraível"):
        converter_pdf(pdf_sem_texto)

    assert issubclass(ErroPdfEscaneado, ErroDeConversao)


def test_max_file_size_mb_limit(monkeypatch, tmp_path):
    """Verifica proteção contra arquivos que excedem ORGPDF_MAX_FILE_SIZE_MB."""
    import pymupdf
    from organizador_pdf.converter import converter_pdf, ErroDeConversao
    import pytest

    pdf_file = tmp_path / "grande.pdf"
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 50), "Texto de teste suficiente.")
    doc.save(pdf_file)
    doc.close()

    # Define o limite como 0 MB (qualquer arquivo > 0 bytes falha)
    monkeypatch.setenv("ORGPDF_MAX_FILE_SIZE_MB", "0")

    with pytest.raises(ErroDeConversao, match="Arquivo excede o tamanho máximo permitido"):
        converter_pdf(pdf_file)


def test_config_total_offline():
    """Verifica o cálculo da propriedade Config.total_offline."""
    from organizador_pdf.config import Config

    # Modo 100% offline
    c_off = Config(verificar_online=False, typesafe_api_key=None)
    assert c_off.total_offline is True

    # Com verificação online ativada
    c_on = Config(verificar_online=True, typesafe_api_key=None)
    assert c_on.total_offline is False

    # Com verificação online desligada mas com chave typesafe
    c_typesafe = Config(verificar_online=False, typesafe_api_key="ts_test_key")
    assert c_typesafe.total_offline is False


def test_metadata_enricher_doi_quoting_and_normalization():
    """Verifica normalização de prefixo e encoding seguro de caracteres em DOIs."""
    from unittest.mock import MagicMock
    from organizador_pdf.metadata_api import MetadataEnricher

    enricher = MetadataEnricher(timeout=5.0)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": {"title": ["Test Paper"], "DOI": "10.1000/abc<123>"}}
    enricher.session = MagicMock()
    enricher.session.get.return_value = mock_resp

    # Passa DOI com prefixo https://doi.org/ e caracteres especiais
    res = enricher.fetch_crossref_by_doi("https://doi.org/10.1000/abc<123>")
    assert res is not None
    # Verifica que a URL foi codificada com urllib.parse.quote (substituindo < e > por %3C e %3E)
    args, kwargs = enricher.session.get.call_args
    assert "https://api.crossref.org/works/10.1000/abc%3C123%3E" in args[0]


def test_cli_rejects_invalid_estrutura(tmp_path):
    """Verifica que a CLI rejeita valor inválido para --estrutura."""
    from typer.testing import CliRunner
    from organizador_pdf.cli import app

    runner = CliRunner()
    origem = tmp_path / "origem"
    destino = tmp_path / "destino"
    origem.mkdir()
    destino.mkdir()

    res = runner.invoke(app, ["-i", str(origem), "-o", str(destino), "--estrutura", "invalida"])
    assert res.exit_code == 2
    assert "Opção de estrutura inválida" in res.output


def test_cli_rejects_directory_conflicts(tmp_path):
    """Verifica que a CLI impede destino igual à origem ou destino aninhado em modo recursivo."""
    from typer.testing import CliRunner
    from organizador_pdf.cli import app

    runner = CliRunner()
    origem = tmp_path / "origem"
    origem.mkdir()

    # Destino igual à origem
    res_same = runner.invoke(app, ["-i", str(origem), "-o", str(origem)])
    assert res_same.exit_code == 2
    assert "não pode ser idêntico" in res_same.output

    # Destino aninhado na origem com busca recursiva (-r ativado por padrão)
    destino_aninhado = origem / "saida"
    destino_aninhado.mkdir()
    res_nested = runner.invoke(app, ["-i", str(origem), "-o", str(destino_aninhado)])
    assert res_nested.exit_code == 2
    assert "Conflito de diretórios" in res_nested.output


# ==============================================================================
# FASE 6: RECOMENDAÇÕES DO RELATÓRIO TÉCNICO (JEV REMOTO, DOI, CIP, TIPOS)
# ==============================================================================


def test_classificador_modo_remoto_missing_key_fails_fast(monkeypatch, tmp_path):
    """Verifica que o modo jev_remoto falha rápido se a chave TYPESAFE_API_KEY estiver ausente."""
    import pytest
    from organizador_pdf.config import Config, ErroDeConfiguracao
    from organizador_pdf.classifier_jev import JevClassifier

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("ORGPDF_TYPESAFE_API_KEY", raising=False)
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("", encoding="utf-8")

    # Config.do_ambiente deve disparar ErroDeConfiguracao quando remoto sem chave
    with pytest.raises(ErroDeConfiguracao, match="remoto"):
        Config.do_ambiente(classificador="remoto", env_file=empty_env)

    # Instanciação direta do JevClassifier em modo jev_remoto sem chave
    with pytest.raises(ErroDeConfiguracao, match="remoto"):
        JevClassifier(api_key=None, modo="jev_remoto")


def test_classificador_modo_remoto_missing_sdk_or_api_error_no_silent_fallback(monkeypatch):
    """Verifica que o modo jev_remoto NÃO faz fallback silencioso para heurísticas locais."""
    import pytest
    import sys
    from organizador_pdf.classifier_jev import JevClassifier, ErroDeClassificacaoRemota

    # Simula pacote typesafe_sdk ausente
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)

    classifier = JevClassifier(api_key="ts_test_key", modo="jev_remoto")

    with pytest.raises(ErroDeClassificacaoRemota, match=r"typesafe-sdk.*instalado"):
        classifier.classify_and_validate(
            pdf_path="arquivo_inexistente.pdf",
            texto_pre_extraido="Texto do documento para teste.",
            total_paginas=5,
        )


def test_classificador_modo_remoto_api_call_failure_raises(monkeypatch):
    """Verifica que falha de rede/API no modo jev_remoto levanta ErroDeClassificacaoRemota sem fallback local."""
    import pytest
    from unittest.mock import MagicMock
    import sys
    from organizador_pdf.classifier_jev import JevClassifier, ErroDeClassificacaoRemota

    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.system_one.side_effect = ConnectionError("API TypeSafe AI indisponível (503 Service Unavailable)")
    mock_sdk.TypeSafeClient.return_value = mock_client
    monkeypatch.setitem(sys.modules, "typesafe_sdk", mock_sdk)

    classifier = JevClassifier(api_key="ts_test_key", modo="jev_remoto")

    with pytest.raises(ErroDeClassificacaoRemota, match="Falha na chamada ao serviço remoto TypeSafe AI"):
        classifier.classify_and_validate(
            pdf_path="doc.pdf",
            texto_pre_extraido="Texto de teste para chamada remota.",
            total_paginas=3,
        )


def test_jev_classifier_idempotent_caching(monkeypatch, tmp_path):
    """Verifica que chamadas repetidas usam o cache por sha256 sem reexecutar chamada externa cobrada."""
    from unittest.mock import MagicMock
    import sys
    from organizador_pdf.classifier_jev import JevClassifier

    mock_choice = MagicMock()
    mock_choice.choice = "artigo"
    mock_choice.confidence = 0.98

    mock_resp = MagicMock()
    mock_resp.choices = {"classification": mock_choice}
    mock_resp.nouls = {}

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.system_one.return_value = mock_resp

    mock_sdk = MagicMock()
    mock_sdk.TypeSafeClient.return_value = mock_client
    monkeypatch.setitem(sys.modules, "typesafe_sdk", mock_sdk)

    cache_file = tmp_path / "cache_jev.json"
    classifier = JevClassifier(api_key="ts_test_key", modo="auto", cache_file=cache_file)

    sample = "Texto de amostra com Abstract e Introdução científica para classificação."

    # Primeira execução: chama a API remota e grava em cache
    res1 = classifier.classify_and_validate(pdf_path="artigo.pdf", texto_pre_extraido=sample, total_paginas=8)
    assert res1.classification == "artigo"
    assert res1.provider == "typesafe"
    assert mock_client.system_one.call_count == 1
    assert cache_file.exists()

    # Segunda execução com mesmo texto e total de páginas: DEVE vir do cache sem chamar o SDK
    res2 = classifier.classify_and_validate(pdf_path="artigo.pdf", texto_pre_extraido=sample, total_paginas=8)
    assert res2.classification == "artigo"
    assert res2.provider == "typesafe"
    assert res2.raw_jev_data.get("cached") is True
    # Call count deve continuar exatamente 1!
    assert mock_client.system_one.call_count == 1


def test_doi_in_references_and_citations_rejected_ambrosio_case():
    """Verifica que DOI localizado em referências ou citação não é atribuído a tese/dissertação."""
    from organizador_pdf.classifier_jev import extract_candidate_metadata

    # Simulação do caso Ambrosio: dissertação de mestrado citando artigo de terceiro com DOI nas referências
    texto_dissertacao = (
        "UNIVERSIDADE DE SÃO PAULO\n"
        "FACULDADE DE FILOSOFIA, LETRAS E CIÊNCIAS HUMANAS\n"
        "Dissertação apresentada ao Programa de Pós-Graduação em História Social\n"
        "Autor: Carlos Ambrosio\n"
        "Orientador: Prof. Dr. Silva\n\n"
        "RESUMO\n"
        "Este trabalho investiga os movimentos sociais do século XX.\n\n"
        "REFERÊNCIAS\n"
        "SILVA, J. Estudo sobre movimentos. Revista de História, v. 10, n. 2, 2015. DOI: 10.1590/0102-01882015000200008\n"
        "SANTOS, M. História social. In: Anais do Simpósio, 2018. https://doi.org/10.1016/j.soc.2018.01.002\n"
    )

    candidates = extract_candidate_metadata(texto_dissertacao, total_pages=150)
    # O DOI citado nas referências NÃO deve ser associado como DOI da dissertação!
    assert candidates.doi is None


def test_thematic_area_rejects_titulo_or_autor():
    """Verifica que marcadores AACR2 como 'titulo' ou 'autor' não são aceitos como área temática."""
    from organizador_pdf.classifier_jev import parse_page_cip
    from organizador_pdf.metadata_api import MetadataEnricher
    from organizador_pdf.models import PublicationMetadata, Identifiers

    ficha_falsa = (
        "Dados Internacionais de Catalogação na Publicação (CIP)\n"
        "Silva, João\n"
        "Psicologia Social / João Silva. - São Paulo: Editora X, 2020.\n"
        "1. Titulo. 2. Autor.\n"
        "CDD 150\n"
    )
    res = parse_page_cip(ficha_falsa)
    assert res is not None
    # "area" não deve ser "Titulo" ou "Autor"
    assert res.get("area") is None

    # Teste de fusão em MetadataEnricher._merge_metadata
    enricher = MetadataEnricher(online=False)
    meta = PublicationMetadata(
        title="Psicologia Social",
        identifiers=Identifiers(),
    )
    enricher._merge_metadata(meta, {"area": "1. Titulo."})
    assert meta.area in (None, "Outros")
    assert meta.area != "1. Titulo."

    enricher._merge_metadata(meta, {"area": "Psicologia Social"})
    assert meta.area == "Psicologia Social"


def test_page_markers_in_title_cleaned_and_flagged_for_review():
    """Verifica que marcadores de página são removidos e títulos contaminados ativam revisão manual."""
    from organizador_pdf.classifier_jev import extract_candidate_metadata
    from organizador_pdf.metadata_api import MetadataEnricher
    from organizador_pdf.models import JevValidationResult, ExtractedCandidates

    # Texto inicial contendo marcador de página
    texto = (
        "--- [Página 1] ---\n"
        "[PAGE BREAK]\n"
        "Teoria Geral da Neuropsicologia\n"
        "Maria de Andrade\n"
    )
    candidates = extract_candidate_metadata(texto, total_pages=50)
    assert candidates.raw_title is not None
    assert "--- [Página 1] ---" not in candidates.raw_title
    assert "[page break]" not in candidates.raw_title.lower()
    assert candidates.raw_title == "Teoria Geral da Neuropsicologia"

    # Se um título ainda contiver marcador de página, MetadataEnricher deve marcar needs_review
    jev_res = JevValidationResult(
        classification="livro",
        classification_confidence=0.9,
        candidates=ExtractedCandidates(raw_title="--- [Página 1] --- Teoria Geral", sample_text=texto),
    )
    enricher = MetadataEnricher(online=False)
    enriched = enricher.enrich(jev_res)
    assert enriched.needs_review is True
    assert any("marcador de página" in r for r in enriched.review_reasons)


def test_extended_publication_types_support():
    """Verifica suporte aos novos tipos documentais: capitulo_livro, relatorio, trabalho_evento."""
    from organizador_pdf.models import TipoPublicacao, PLURAL_POR_TIPO
    from organizador_pdf.classifier_jev import JevClassifier

    assert TipoPublicacao.CAPITULO_LIVRO.value == "Capítulo de Livro"
    assert TipoPublicacao.RELATORIO.value == "Relatório"
    assert TipoPublicacao.TRABALHO_EVENTO.value == "Trabalho em Evento"

    assert "Capítulos de Livro" in PLURAL_POR_TIPO.values()
    assert "Relatórios" in PLURAL_POR_TIPO.values()
    assert "Trabalhos em Eventos" in PLURAL_POR_TIPO.values()

    classifier = JevClassifier()
    # Texto de relatório técnico
    sample_relatorio = (
        "MINISTÉRIO DA SAÚDE\n"
        "RELATÓRIO TÉCNICO DE GESTÃO E VIGILÂNCIA EPIDEMIOLÓGICA\n"
        "Relatório institucional anual de monitoramento\n"
        "Brasília, DF, 2024\n"
    )
    res = classifier.classify_and_validate(pdf_path="relatorio.pdf", texto_pre_extraido=sample_relatorio, total_paginas=40)
    assert res.classification in ("relatorio", "outros", "apostila")


def test_pipeline_revisao_manual_sync_with_markdown(tmp_path: Path, sample_pdf_generator):
    """Verifica que quando há divergência/aviso, o Markdown gerado contém needs_review e review_reasons."""
    from organizador_pdf.pipeline import Pipeline, OpcoesDoPipeline
    from organizador_pdf.config import Config

    # Gera PDF onde título no documento diverge do nome do arquivo
    pages = [
        "Tratado de Filosofia da Mente Contemporânea\n"
        "Autor: Epicteto de Hierápolis\n"
        "Ano: 2021\n"
    ]
    # Nome do arquivo completamente divergente para forçar aviso de divergência
    pdf_path = sample_pdf_generator("calculo_diferencial_avancado.pdf", "Calculo Diferencial", pages)

    out_dir = tmp_path / "out_sync"
    out_dir.mkdir()

    cfg = Config(verificar_online=False, classificador="local")
    pipeline = Pipeline(
        config=cfg,
        opcoes=OpcoesDoPipeline(destino=out_dir, mover=False, quarantine=True, enriquecimento_online=False),
    )

    resultado = pipeline.processar_arquivo(pdf_path)
    assert resultado.ok is True
    assert resultado.aviso is not None
    assert resultado.metadados.needs_review is True

    # O Markdown gravado DEVE ter needs_review: true e review_reasons preenchido
    assert resultado.markdown_destino is not None
    assert resultado.markdown_destino.exists()
    conteudo_md = resultado.markdown_destino.read_text(encoding="utf-8")
    assert "needs_review: true" in conteudo_md
    assert "review_reasons:" in conteudo_md
    assert "provedor_classificador:" in conteudo_md


# ==============================================================================
# FASE 7: CRITÉRIOS DE ACEITE DO RELATÓRIO DE RETESTE (4 OUT 2026, 15h27 BRT)
# ==============================================================================


def test_cida_bento_disclaimer_rejected_as_title(sample_pdf_generator):
    """Verifica que 'dominio publico e propriedade intelectual...' é rejeitado como título."""
    from organizador_pdf.classifier_jev import extract_candidate_metadata

    sample_pages = [
        "A presente obra é disponibilizada pela equipe eLivros respeitando o domínio público e propriedade intelectual de forma...",
        "BENTO, Cida\nO Pacto da Branquitude\nSão Paulo: Companhia das Letras, 2022.\nISBN 978-85-359-3350-5",
    ]
    pdf = sample_pdf_generator("BENTO, Cida - O Pacto da Branquitude.pdf", "Pacto da Branquitude", sample_pages)
    sample_text = "\n\n".join(sample_pages)

    candidates = extract_candidate_metadata(sample_text, pdf_path=str(pdf), total_pages=150)
    assert candidates.raw_title is not None
    assert "dominio publico" not in candidates.raw_title.lower()
    assert "propriedade intelectual" not in candidates.raw_title.lower()
    assert "Pacto da Branquitude" in candidates.raw_title


def test_beer_franco_issn_rejected_as_title(sample_pdf_generator):
    """Verifica que cabeçalho numérico com ISSN nunca é gravado como título do artigo."""
    from organizador_pdf.classifier_jev import extract_candidate_metadata, is_invalid_or_disclaimer_title

    assert is_invalid_or_disclaimer_title("ISSN 0123-8884") is True
    assert is_invalid_or_disclaimer_title("DOI: 10.1590/1234") is True
    assert is_invalid_or_disclaimer_title("ISBN 978-85-359-3350-5") is True

    sample_pages = [
        "ISSN 0123-8884\nRevista Latinoamericana de Psicopatologia Fundamental\n\nDA INDISSOCIABILIDADE ENTRE CLÍNICA E POLÍTICA\nBEER, P. A.; FRANCO, R.",
    ]
    pdf = sample_pdf_generator("BEER e FRANCO-DA_INDISSOCIABILIDADE.pdf", "Artigo", sample_pages)
    sample_text = sample_pages[0]

    candidates = extract_candidate_metadata(sample_text, pdf_path=str(pdf), total_pages=15)
    assert candidates.raw_title != "ISSN 0123-8884"
    assert "ISSN" not in candidates.raw_title
    assert "indissociabilidade" in candidates.raw_title.lower()


def test_neuropsicologia_page_and_cdd_cleaned_from_title():
    """Verifica que prefixos residuais de página e códigos de catalogação CDD/CDU são limpos."""
    from organizador_pdf.classifier_jev import sanitize_title_candidate

    raw_polluted = "Página 2] 150.195 - Neuropsicologia: Teoria e Prática"
    cleaned = sanitize_title_candidate(raw_polluted)
    assert cleaned == "Neuropsicologia: Teoria e Prática"

    raw_cdd = "CDD 150 - Neuropsicologia Contemporânea"
    assert sanitize_title_candidate(raw_cdd) == "Neuropsicologia Contemporânea"

    raw_cutter = "N494 - Neuropsicologia e Funções Executivas"
    assert sanitize_title_candidate(raw_cutter) == "Neuropsicologia e Funções Executivas"


def test_angela_ales_bello_editorial_credits_truncated():
    """Verifica que blocos maciços de ficha/créditos de tradução são truncados do título."""
    from organizador_pdf.classifier_jev import sanitize_title_candidate

    polluted = "Introdução à Fenomenologia Tradução de Jacinta Turolo Garcia Revisão técnica de Maria Silva Capa: Design Studio"
    cleaned = sanitize_title_candidate(polluted)
    assert cleaned == "Introdução à Fenomenologia"


def test_bell_hooks_single_word_fallback_and_outros_quarantine(sample_pdf_generator):
    """Verifica que título unipalavra 'criar' dá lugar ao nome do arquivo e 'Outros' aciona quarentena."""
    from organizador_pdf.classifier_jev import extract_candidate_metadata
    from organizador_pdf.metadata_api import MetadataEnricher
    from organizador_pdf.models import JevValidationResult

    sample_pages = [
        "criar\n\nTexto de abertura sobre pensamento crítico feminista e descolonização.",
    ]
    pdf = sample_pdf_generator("hooks, bell - Ensinando a Transgredir.pdf", "Ensinando a Transgredir", sample_pages)

    candidates = extract_candidate_metadata(sample_pages[0], pdf_path=str(pdf), total_pages=80)
    # Título candidato não deve ser a palavra isolada 'criar' quando o arquivo possui título substantivo
    assert candidates.raw_title == "Ensinando a Transgredir"
    assert candidates.title_source == "nome_arquivo"

    # Quando um item termina classificado como 'outros', deve ser marcado para revisão
    jev_outros = JevValidationResult(
        classification="outros",
        classification_confidence=0.95,
        candidates=candidates,
    )
    enricher = MetadataEnricher(online=False)
    meta = enricher.enrich(jev_outros)
    assert meta.needs_review is True
    assert any("outros" in r.lower() for r in meta.review_reasons)


def test_separate_classification_and_metadata_confidence():
    """Verifica que alta confiança de tipo não anula baixa confiança de metadados nem suprime revisão."""
    from organizador_pdf.metadata_api import MetadataEnricher
    from organizador_pdf.models import JevValidationResult, ExtractedCandidates

    # Documento onde o classificador diz ser 'livro' com 99% de certeza, mas o título é 'Sem Título'
    jev_res = JevValidationResult(
        classification="livro",
        classification_confidence=0.99,
        candidates=ExtractedCandidates(raw_title="Sem Título", raw_authors=[]),
    )
    enricher = MetadataEnricher(online=False)
    meta = enricher.enrich(jev_res)

    assert meta.confidence_classification == 0.99
    assert meta.confidence_metadata < 0.50
    assert meta.needs_review is True
    assert any("sem título" in r.lower() for r in meta.review_reasons)


def test_scanned_pdf_andrade_case_returns_pendente_ocr(tmp_path):
    """Verifica que PDF sem camada de texto (como ANDRADE) resulta em Situacao.PENDENTE_OCR."""
    import pymupdf
    from organizador_pdf.pipeline import Pipeline, OpcoesDoPipeline
    from organizador_pdf.models import Situacao
    from organizador_pdf.config import Config

    # Cria um PDF com página em branco (sem nenhuma fonte ou camada de texto)
    pdf_path = tmp_path / "ANDRADE, Érico - Negritude sem identidade.pdf"
    doc = pymupdf.open()
    doc.new_page()  # Página vazia / escaneada
    doc.save(str(pdf_path))
    doc.close()

    out_dir = tmp_path / "out_andrade"
    out_dir.mkdir()

    pipeline = Pipeline(
        config=Config(verificar_online=False, classificador="local"),
        opcoes=OpcoesDoPipeline(destino=out_dir),
    )
    res = pipeline.processar_arquivo(pdf_path)

    assert res.situacao == Situacao.PENDENTE_OCR
    assert res.requer_ocr is True
    assert "digitalizado" in (res.aviso or "").lower() or "ocr" in (res.aviso or "").lower()
    # Não gravou nada no destino
    assert res.pdf_destino is None


def test_persistence_failure_idempotent_retry_avoids_duplicate_paid_call(tmp_path, monkeypatch):
    """Verifica que se a API remota responde e a gravação local falha, a retomada reutiliza o cache sem nova chamada paga."""
    from unittest.mock import MagicMock
    import sys
    from organizador_pdf.classifier_jev import JevClassifier

    mock_choice = MagicMock()
    mock_choice.choice = "livro"
    mock_choice.confidence = 0.95

    mock_resp = MagicMock()
    mock_resp.choices = {"classification": mock_choice}
    mock_resp.nouls = {}

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.system_one.return_value = mock_resp

    mock_sdk = MagicMock()
    mock_sdk.TypeSafeClient.return_value = mock_client
    monkeypatch.setitem(sys.modules, "typesafe_sdk", mock_sdk)

    cache_file = tmp_path / "idempotent_jev_cache.json"
    classifier = JevClassifier(api_key="ts_test_key", modo="jev_remoto", cache_file=cache_file)

    sample_doc = "Capítulo 1. Fundamentos epistemológicos da psicanálise freudiana."

    # 1. Primeira chamada: consulta API remota e persiste cache
    res1 = classifier.classify_and_validate(pdf_path="livro_epist.pdf", texto_pre_extraido=sample_doc, total_paginas=200)
    assert res1.classification == "livro"
    assert res1.provider == "typesafe"
    assert mock_client.system_one.call_count == 1

    # 2. Simula falha de persistência no disco (ex.: erro de I/O na pasta de destino)
    # Na retomada / reexecução da mesma análise, o cache DEVE ser reutilizado:
    res2 = classifier.classify_and_validate(pdf_path="livro_epist.pdf", texto_pre_extraido=sample_doc, total_paginas=200)
    assert res2.classification == "livro"
    assert res2.provider == "typesafe"
    assert res2.raw_jev_data.get("cached") is True
    # mock_client.system_one NUNCA deve ser chamado uma segunda vez!
    assert mock_client.system_one.call_count == 1


def test_cli_cabecalho_truthful_remote_mode_and_network_status(monkeypatch):
    """Verifica que o cabeçalho da CLI reflete fielmente o modo remoto exclusivo e a separação de rede."""
    import io
    from rich.console import Console
    from organizador_pdf.cli import _cabecalho
    import organizador_pdf.cli as cli_mod

    buffer = io.StringIO()
    test_console = Console(file=buffer, force_terminal=False, color_system=None, width=140)
    monkeypatch.setattr(cli_mod, "saida", test_console)
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts_test_key")

    from pathlib import Path
    orig = Path("/tmp/origem")
    dest = Path("/tmp/destino")

    # Modo remoto exclusivo com consultas bibliográficas desativadas:
    # NÃO deve ser rotulado como "Modo Offline" genérico!
    _cabecalho(
        origem=orig,
        destino=dest,
        pdfs=[Path("doc.pdf")],
        dry_run=False,
        mover=False,
        quarantine=True,
        enriquecimento_online=False,
        classificador="remoto",
    )

    output = buffer.getvalue()
    assert "JEV remoto — TypeSafe AI" in output
    assert "fallback local desativado" in output
    assert "Transmissão JEV:" in output
    assert "Ativa" in output
    assert "Bases Bibliog.:" in output
    assert "Desativadas" in output
    assert "Modo Offline" not in output  # Não pode chamar de Modo Offline se o JEV remoto está ativo!










