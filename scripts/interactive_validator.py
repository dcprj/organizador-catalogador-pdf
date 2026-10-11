#!/usr/bin/env python3
"""Shim de compatibilidade para scripts/interactive_validator.py.

Delega a execução para `organizador_pdf.interactive_validator`.
Mantém total compatibilidade com chamadas CLI legadas e scripts externos.
"""

import sys
from pathlib import Path

# Garante que o diretório src/ esteja acessível no PYTHONPATH
SRC_PATH = Path(__file__).resolve().parent.parent / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from organizador_pdf.interactive_validator import (
    main,
    run_interactive_validator,
    validate_and_process_pdf,
    explain_jev_classification,
    inspect_pdf_structure,
    prompt_user_confirmation,
)

# Alias de compatibilidade
inspect_pdf_pages = inspect_pdf_structure

if __name__ == "__main__":
    sys.exit(main())
