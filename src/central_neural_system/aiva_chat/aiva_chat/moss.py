"""Thin shim: the painting engine lives in ~/AGi/imagegen (pip: grace-imagegen)."""
from imagegen import MossClient, MossPaintError, paint_size

__all__ = ['MossClient', 'MossPaintError', 'paint_size']
