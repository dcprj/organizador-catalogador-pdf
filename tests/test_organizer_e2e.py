"""End-to-End integration tests for PipelineOrganizer and CLI."""

import subprocess
import sys
from pathlib import Path
from organizador_pdf.organizer import PipelineOrganizer


def test_pipeline_organizer_e2e(tmp_path: Path, sample_pdf_generator):
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()

    # Create synthetic scientific paper PDF in input directory
    pages = [
        "Uma Nova Abordagem em Aprendizado por Reforço\n"
        "Por: Carlos Silva, Ana Paula Souza\n"
        "doi: 10.1016/j.artint.2024.1001\n"
        "Abstract: Apresentamos resultados teóricos e práticos...",
        "Seção 2: Metodologia e formulação matemática...",
        "Seção 3: Conclusão e trabalhos futuros...",
    ]
    pdf_path = sample_pdf_generator("artigo_reforco.pdf", "Artigo Reforço", pages)
    # Move to input_dir
    source_pdf = input_dir / "artigo_reforco.pdf"
    pdf_path.rename(source_pdf)

    assert source_pdf.exists()

    organizer = PipelineOrganizer()
    results = organizer.process_directory(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        move_original=True,
    )

    assert len(results) == 1
    res = results[0]
    assert res.success is True
    assert res.classification in ("artigo", "artigo_cientifico")

    # Original PDF should have been moved out of input_dir
    assert not source_pdf.exists()

    # Novo default hierárquico: <output_dir>/<Assunto Principal>/<Especificação>/<Plural do Tipo>/
    meta = res.metadata
    target_class_dir = (
        output_dir
        / meta.area_principal
        / (meta.subarea or meta.area_principal)
        / meta.plural_do_tipo
    )
    assert target_class_dir.exists()

    pdf_files = list(target_class_dir.glob("*.pdf"))
    md_files = list(target_class_dir.glob("*.md"))
    assert len(pdf_files) == 1
    assert len(md_files) == 1

    md_content = md_files[0].read_text(encoding="utf-8")
    assert f"tipo_documento: {res.classification}" in md_content or f"classification: {res.classification}" in md_content
    assert "## Referência Bibliográfica" in md_content
    assert "Disponível em: <https://doi.org/10.1016/j.artint.2024.1001>" in md_content


def test_cli_execution_e2e(tmp_path: Path, sample_pdf_generator):
    input_dir = tmp_path / "cli_in"
    output_dir = tmp_path / "cli_out"
    input_dir.mkdir()
    output_dir.mkdir()

    pages = [
        "Apostila de Redes de Computadores\n"
        "Autor: Prof. Carlos Eduardo\n"
        "Material didático do curso\n"
        "Notas de aula e roteiro de laboratório...\n"
    ]
    pdf_path = sample_pdf_generator("redes.pdf", "Apostila Redes", pages)
    source_pdf = input_dir / "redes.pdf"
    pdf_path.rename(source_pdf)

    # Run main.py via subprocess using the virtualenv python
    venv_python = Path(sys.executable)
    cmd = [
        str(venv_python),
        "main.py",
        "--input", str(input_dir),
        "--output", str(output_dir),
    ]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )

    assert proc.returncode == 0
    assert "RESUMO DE EXECUÇÃO" in proc.stdout
    assert "Total de PDFs processados: 1" in proc.stdout

    # Default hierárquico: <destino>/<Assunto>/<Especificação>/Apostilas/ (4 partes com o arquivo)
    pdfs = list(output_dir.rglob("*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].parent.name == "Apostilas"
    assert len(pdfs[0].relative_to(output_dir).parts) == 4
    assert len(list(pdfs[0].parent.glob("*.md"))) == 1
    assert not (output_dir / "apostila").exists()

    # Atalho --plana: <destino>/apostila/
    plana_in = tmp_path / "cli_in_plana"
    plana_out = tmp_path / "cli_out_plana"
    plana_in.mkdir()
    plana_out.mkdir()
    pdf_plana = sample_pdf_generator("redes2.pdf", "Apostila Redes", pages)
    pdf_plana.rename(plana_in / "redes2.pdf")
    proc_plana = subprocess.run(
        [str(venv_python), "main.py", "--input", str(plana_in), "--output", str(plana_out), "--plana"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )
    assert proc_plana.returncode == 0
    apostila_dir = plana_out / "apostila"
    assert apostila_dir.exists()
    assert len(list(apostila_dir.glob("*.pdf"))) == 1
    assert len(list(apostila_dir.glob("*.md"))) == 1
