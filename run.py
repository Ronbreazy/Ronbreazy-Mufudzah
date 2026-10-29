#!/usr/bin/env python3
"""
run.py
------
Starts the ZimLegal backend.

Usage:
    python3 run.py

No installation step is required -- everything used here is part of
Python's standard library. Requires Python 3.8 or newer.
"""

from app.config import HOST, PORT
from app.server import run_server

if __name__ == "__main__":
    run_server(HOST, PORT)
