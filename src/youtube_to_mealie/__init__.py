# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn YouTube cooking videos into Mealie recipes."""
from __future__ import annotations

from youtube_to_mealie.cli import (
    Ingredient,
    Mealie,
    Recipe,
    extract_recipe,
    fetch_video,
    load_text_file,
    main,
    push_to_mealie,
)

__version__ = "0.1.0"

__all__ = [
    "Ingredient",
    "Mealie",
    "Recipe",
    "extract_recipe",
    "fetch_video",
    "load_text_file",
    "main",
    "push_to_mealie",
    "__version__",
]
