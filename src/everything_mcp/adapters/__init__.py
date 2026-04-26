"""Backend adapter selection for Everything_Mew."""

from .base import EverythingAdapter
from .selection import NullAdapter, select_adapter

__all__ = ["EverythingAdapter", "NullAdapter", "select_adapter"]
