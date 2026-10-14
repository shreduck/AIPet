#!/usr/bin/env python3
"""Stable hook entry for Windows, WSL and macOS; runtime is deployed alongside it."""
import sys

sys.dont_write_bytecode = True


def main():
    from aipet_hook_runtime.cli import main as run
    return run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # Hook failures never block the host; only explicit decisions go to stdout.
    sys.exit(0)
