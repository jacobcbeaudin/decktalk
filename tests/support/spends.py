"""One price, built one way for every test that hands a run, a result or an event something to spend."""

from __future__ import annotations

from decktalk.results import Billing, Layer, Spend, SpendState

PRICE = 0.30
"""Dollars per thousand characters, which is what every price here is quoted at."""

PRICE_KEY = "elevenlabs.price_per_1000_characters"
"""The key that states that rate, which a refusal names when nobody has stated it."""


def a_spend(
    dollars: float = 0.12,
    ceiling: float = 0.2,
    *,
    state: SpendState = SpendState.ESTIMATE,
    layer: Layer = Layer.PROJECT,
    sections: tuple[int, ...] = (1,),
    billing: Billing = Billing.PER_CHARACTER,
) -> Spend:
    """A price as a stage states one, for as many whole characters as `dollars` buys at `PRICE`."""
    return Spend(
        state=state,
        sections=sections,
        characters=int(dollars / PRICE * 1000),
        dollars=dollars,
        ceiling_dollars=ceiling,
        billing=billing,
        price_per_1000_characters=PRICE if billing is Billing.PER_CHARACTER else 0.0,
        price_key=PRICE_KEY if billing is Billing.PER_CHARACTER else None,
        price_layer=layer,
    )
