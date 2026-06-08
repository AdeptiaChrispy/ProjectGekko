"""Entrypoint for `python -m gekko` — routes through the Typer CLI.

Per RESEARCH.md Open Question 6: the Typer `app` is also the console-script
entry declared in pyproject.toml ([project.scripts] gekko = "gekko.cli:app").
"""

from gekko.cli import app

if __name__ == "__main__":
    app()
