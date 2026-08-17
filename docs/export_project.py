import os
from pathlib import Path
from typing import Dict, List, Set

# Projekt-Root: Elternverzeichnis von docs/ (stabil, unabhaengig vom Arbeitsverzeichnis)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Ausgabeordner: immer docs/exports/
EXPORT_DIR: Path = Path(__file__).resolve().parent / "exports"
FULL_EXPORT: Path = EXPORT_DIR / "PyBack_export.md"

# Dateiendungen, die in den Export aufgenommen werden
ALLOWED_EXTENSIONS: Set[str] = {
    '.py', '.js', '.ui', '.sql', '.json', '.yaml', '.yml', '.toml', '.md',
}

# Ordner, die ignoriert werden sollen
IGNORE_DIRS: Set[str] = {
    '.git', '.idea', '__pycache__', 'venv', 'env', 'build', 'dist', '.venv',
    'node_modules', '.pytest_cache', 'docs', 'exports', 'test',
}

# Dateien, die ignoriert werden sollen (z. B. Exportdateien selbst)
IGNORE_FILES: Set[str] = {'PyBack_export.md'}


def should_ignore_dir(dir_path: Path) -> bool:
    """Prüft, ob ein Verzeichnis ignoriert werden soll."""
    return any(part in IGNORE_DIRS for part in dir_path.parts)


def export_project() -> None:
    """Durchläuft das Projekt, sammelt relevante Dateien und erstellt einen Markdown-Export."""
    # Sicherstellen, dass das Export-Verzeichnis existiert
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    collected_files: List[Path] = []

    # Projekt rekursiv durchlaufen
    for root, dirs, files in os.walk(PROJECT_ROOT):
        root_path = Path(root)

        # Ignorierte Ordner aus der Suche ausschließen (verhindert auch den Einstieg in diese Ordner)
        dirs[:] = [d for d in dirs if not should_ignore_dir(root_path / d)]

        # Wenn sich das aktuelle Verzeichnis in einem ignorierten Pfad befindet, überspringen
        if should_ignore_dir(root_path):
            continue

        for file in files:
            if file in IGNORE_FILES:
                continue

            file_path = root_path / file

            # Prüfen ob Endung erlaubt ist
            if file_path.suffix.lower() in ALLOWED_EXTENSIONS:
                collected_files.append(file_path)

    # Sortieren für einen konsistenten Export
    collected_files.sort()

    # Markdown-Export generieren
    with open(FULL_EXPORT, 'w', encoding='utf-8') as out:
        out.write(f"# Projekt-Export: PyBack\n\n")
        out.write(f"* **Projekt-Root:** `{PROJECT_ROOT}`\n")
        out.write(f"* **Enthaltene Dateien:** {len(collected_files)}\n\n")
        out.write("---\n\n")

        for file_path in collected_files:
            # Relativen Pfad für die Darstellung berechnen
            rel_path = file_path.relative_to(PROJECT_ROOT)
            extension = file_path.suffix.lstrip('.').lower()

            # Markdown-Sprachbezeichner für Codeblocks anpassen
            lang_map = {
                'py': 'python',
                'js': 'javascript',
                'json': 'json',
                'yaml': 'yaml',
                'yml': 'yaml',
                'toml': 'toml',
                'sql': 'sql',
                'md': 'markdown',
                'ui': 'xml'  # Qt .ui Dateien sind XML-basiert
            }
            lang = lang_map.get(extension, '')

            out.write(f"## Datei: `{rel_path}`\n\n")
            out.write(f"```{lang}\n")

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    out.write(f.read())
            except Exception as e:
                out.write(f"# Fehler beim Lesen der Datei: {e}\n")

            out.write("\n```\n\n")
            out.write("---\n\n")

    print(f"Export erfolgreich erstellt unter: {FULL_EXPORT}")


if __name__ == '__main__':
    export_project()