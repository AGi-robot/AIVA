"""Thin shim: the painting engine lives in ~/AGi/capability/imaging (pip: grace-imagegen)."""
from imagegen import MossClient, MossPaintError, paint_size

__all__ = ['MossClient', 'MossPaintError', 'paint_size']
