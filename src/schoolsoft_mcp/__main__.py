"""Console entry point: ``python -m schoolsoft_mcp`` or ``schoolsoft-mcp``."""

from .server import run


def main() -> None:
    run()


if __name__ == "__main__":
    main()
