"""Launcher used by PyInstaller and for running from the source folder."""
import sys
from nexus.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
