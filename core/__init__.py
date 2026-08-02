"""MetoX core package.

Segmentation has one implementation source. These re-exports preserve the
legacy ``from core import segment_products`` import without duplicating rules.
"""
from .segmentation import (
    _apply_matrix,
    ensure_segmentation_columns,
    segment_products,
)

__all__ = ["_apply_matrix", "ensure_segmentation_columns", "segment_products"]
