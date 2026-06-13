"""Module entry point so ``python -m dsync`` works (used by the daemon ExecStart)."""

from dsync.main import main

if __name__ == "__main__":
    main()
