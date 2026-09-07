#!/usr/bin/env python3
"""Run directly with the system Python, or from a virtual environment."""

from flowdocks.app import main

if __name__ == "__main__":
    raise SystemExit(main())
