# The claims ledger

A claim is anything in the video that a viewer could check. DeckTalk does not read the ledger, so it
is an ordinary Markdown file in the project, such as `claims.md`, that travels with the script.

## What counts as a claim

A number, a customer or partner name, a comparison with another product, a superlative such as
"first", "only" or "fastest", an integration, a price, a date, a version, a benchmark, a quotation,
and any feature said to exist today.

## The columns

| Column | What it holds |
| --- | --- |
| `claim` | The claim as the video states it, in one sentence. |
| `where` | The section number, and the page and slide when the claim is on screen. |
| `source` | Where the value came from, such as a dashboard, a release note or a person. |
| `checked` | The date it was last confirmed, as YYYY-MM-DD. |
| `by` | Who confirmed it. |
| `status` | `confirmed`, `unconfirmed` or `stale`. |

## The rules

1. Every claim in `script.md` and every claim in the text of a page gets a row. A claim that is both
   spoken and shown gets one row that names both places.
2. A claim with no source is `unconfirmed`. Write it in the script as a capitalised placeholder such
   as `[CUSTOMER_COUNT]`, so the voice refuses to say it.
3. Never invent a source. Never round a number up. A number that is "about" something is written as
   "about" in the script and in the ledger.
4. A feature that has not shipped is labelled as coming soon in the script, or it is removed. Nothing
   on screen shows a feature that does not exist in the product the video names.
5. The spoken number and the number on screen say the same thing. "Forty two customers" in the
   script goes with `42` on the slide.
6. A row older than the date the user sets is `stale`. Seven days is a sensible default for an
   investor update, and a release is the natural expiry for a tutorial.
7. Show the user every `unconfirmed` and every `stale` row and wait. Fill a placeholder only with a
   value the user gives.

## Before a voiced build

`decktalk check` reports every placeholder still in the script and fails on it, which is the gate a
voiced run has to pass. A claim that is still open means the run does not start.

## Keeping the ledger honest

- A source note belongs in the ledger and never in the script, because a bracketed note inside a
  paragraph changes the text the voice receives.
- When a claim changes, the sentence that says it changes, which re-voices that section. Group the
  claims of one topic into one section so the cost of a correction stays small.
- Record the date and the product version beside any screenshot that shows a claim, so a later
  revision can tell which pictures went stale with it.
