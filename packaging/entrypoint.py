"""Ponto de entrada usado só pelo PyInstaller (build de binário standalone).

Não faz parte do pacote instalável — existe fora de `src/organizador_pdf`
porque o bootloader do PyInstaller executa este arquivo como script solto
(`__main__`), sem contexto de pacote, então o import precisa ser absoluto.
"""

from organizador_pdf.cli import main

if __name__ == "__main__":
    main()
