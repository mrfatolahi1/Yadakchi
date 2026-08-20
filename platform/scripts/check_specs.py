#!/usr/bin/env python3
"""Thin entry point: fail if any distributed spec copy has drifted.

Equivalent to `sync_specs.py --check`; it exists so CI and the Makefile can
name the check directly.
"""

from __future__ import annotations

from sync_specs import check

if __name__ == "__main__":
    raise SystemExit(check())
