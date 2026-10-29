"""Market data providers.

The agent never assumes a single vendor. Every provider returns the same
``MarketState`` and any provider that cannot deliver *its* part raises
``ProviderUnavailable``, so the resolver can fall through and the run report can
state exactly which source produced the numbers.
"""

from __future__ import annotations

from ..config import RunConfig
from ..schemas import MarketState
from .base import MarketDataProvider, ProviderUnavailable  # noqa: F401

AUTO_ORDER = ("nse", "yahoo", "synthetic", "fixture")


def build(name: str) -> MarketDataProvider:
    from .fixture import FixtureProvider
    from .nse import NseProvider
    from .synthetic import SyntheticProvider
    from .yahoo import YahooProvider

    registry = {
        "nse": NseProvider,
        "yahoo": YahooProvider,
        "synthetic": SyntheticProvider,
        "fixture": FixtureProvider,
    }
    if name not in registry:
        raise ValueError(f"unknown provider {name!r}; choose from {sorted(registry)}")
    return registry[name]()


def get_market_state(config: RunConfig) -> MarketState:
    """Resolve a market state using the requested provider, falling back in AUTO mode."""
    names = AUTO_ORDER if config.provider == "auto" else (config.provider,)
    attempts: list[str] = []
    last_error: Exception | None = None

    for name in names:
        provider = build(name)
        try:
            state = provider.fetch(config)
        except ProviderUnavailable as exc:
            attempts.append(f"{name}: unavailable ({exc})")
            last_error = exc
            continue
        except Exception as exc:  # noqa: BLE001 - a provider blow-up must not kill the loop
            attempts.append(f"{name}: error ({type(exc).__name__}: {exc})")
            last_error = exc
            continue
        if attempts:
            state.warnings.append("provider fallback: " + " | ".join(attempts))
        return state

    raise ProviderUnavailable(
        "no market data provider succeeded: " + " | ".join(attempts) if attempts else str(last_error)
    )
