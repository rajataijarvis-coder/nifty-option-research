"""Provider contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import RunConfig
from ..schemas import MarketState


class ProviderUnavailable(RuntimeError):
    """Raised when a provider cannot deliver a usable market state.

    The resolver treats this as "try the next source", never as a hard failure of
    the run, so a blocked network degrades the research instead of killing it.
    """


class MarketDataProvider(ABC):
    name: str = "base"

    @abstractmethod
    def fetch(self, config: RunConfig) -> MarketState:
        """Return a market state or raise ``ProviderUnavailable``."""
