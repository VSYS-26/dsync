"""Console entry point delegating to the dsync CLI."""

from dsync.cli import cli


def main() -> None:
    """Run the dsync CLI."""
    cli()


if __name__ == "__main__":
    main()
