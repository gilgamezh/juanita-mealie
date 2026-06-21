# SPDX-License-Identifier: GPL-3.0-or-later
"""Enable `python -m juanita`."""
from __future__ import annotations

from juanita.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
