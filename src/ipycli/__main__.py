"""Enable ``python -m ipycli`` — used to spawn detached background workers."""

from .cli import app

if __name__ == "__main__":
    app()
