"""Typed relational iterators and deliberately scoped optimizer rewrites."""

from .engine import Table, execute, validate, optimize

__all__ = ["Table", "execute", "validate", "optimize"]
