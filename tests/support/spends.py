"""One price, built one way for every test that hands a run, a result or an event something to spend."""

from __future__ import annotations

from decktalk.results import Layer, Spend, SpendState

PRICE = 0.30
"""Dollars per thousand characters, which is what every price here is quoted at."""


def a_spend(
    dollars: float = 0.12,
    ceiling: float = 0.2,
    *,
    state: SpendState = SpendState.ESTIMATE,
    layer: Layer = Layer.PROJECT,
    sections: tuple[int, ...] = (1,),
) -> Spend:
    """A price as a stage states one, for as many whole characters as `dollars` buys at `PRICE`."""
    return Spend(
        state=state,
        sections=sections,
        characters=int(dollars / PRICE * 1000),
        dollars=dollars,
        ceiling_dollars=ceiling,
        price_per_1000_characters=PRICE,
        price_layer=layer,
    )
