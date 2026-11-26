"""Entry point of jarvis-cli.exe (console commands: chat, doctor, voices, say, ...)."""

import multiprocessing
import sys

from jarvis.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
