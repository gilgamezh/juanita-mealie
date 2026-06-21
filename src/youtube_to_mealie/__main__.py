# SPDX-License-Identifier: GPL-3.0-or-later
"""Enable `python -m youtube_to_mealie`."""
from __future__ import annotations

from youtube_to_mealie.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
