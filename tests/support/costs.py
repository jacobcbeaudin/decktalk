"""One price, built one way for every test that hands a run, a result or an event something to spend."""

from __future__ import annotations

from decktalk.pipeline import Stage
from decktalk.results import BillingBasis, Cost, CostState, Layer, StageCost

PRICE = 0.30
"""Dollars per thousand characters, which is what every price here is quoted at."""

PRICE_KEY = "elevenlabs.dollars_per_1000_characters"
"""The key that states that rate, which a refusal names when nobody has stated it."""


def a_cost(
    dollars: float = 0.12,
    ceiling: float = 0.2,
    *,
    state: CostState = CostState.ESTIMATE,
    layer: Layer = Layer.PROJECT,
    sections: tuple[int, ...] = (1,),
    billing: BillingBasis = BillingBasis.PER_CHARACTER,
    stage: Stage = Stage.NARRATE,
) -> Cost:
    """A price as a stage states one, for as many whole characters as `dollars` buys at `PRICE`, with its one row."""
    fields = {
        "state": state,
        "sections": sections,
        "characters": int(dollars / PRICE * 1000),
        "seconds": 0.0,
        "dollars": dollars,
        "ceiling_dollars": ceiling,
        "billing": billing,
        "dollars_per_1000_characters": PRICE if billing is BillingBasis.PER_CHARACTER else 0.0,
        "dollars_per_minute": 0.0,
        "price_key": PRICE_KEY if billing is BillingBasis.PER_CHARACTER else None,
        "averaged": False,
        "price_layer": layer,
    }
    return Cost(**fields, stages=(StageCost(stage=stage, **fields),))
