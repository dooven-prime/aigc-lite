"""Versioned adapters for external research release catalogues."""

from .frontier import FrontierRegistryAdapter
from .openai_math import OpenAIMathReleaseAdapter

__all__ = ["FrontierRegistryAdapter", "OpenAIMathReleaseAdapter"]
