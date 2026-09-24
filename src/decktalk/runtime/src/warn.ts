/*! Everything the page could not honour, in the five fields the contract publishes.
 *
 * A page never throws at its author. It records what it could not do, echoes it to the console for
 * whoever has the page open, and carries on drawing, because a deck that stopped at the first
 * misspelled attribute would cost a recording rather than save one. The recorder reads the list back
 * through the probe's one report call and turns each row into a finding.
 *
 * The sentence a row prints belongs to the contract and never to this module, so a code's wording
 * changes in one file and reaches the console, the finding and the documentation at once.
 */

import { type Code, message, type PageWarning } from "./contract.ts";

/** What a message's `{field}` placeholders are filled from, which is whatever the caller knows. */
export type Fields = Readonly<Record<string, string | number>>;

/** The prefix the console echo carries, so a page with several scripts says which one spoke. */
const CONSOLE_PREFIX = "decktalk";

/** Every distinct row the page has reported, in the order it first reported them. */
let recorded: PageWarning[] = [];

/**
 * Record one thing the page could not honour, once.
 *
 * The positional shape is the one the probe calls across the seam, `(code, slide, cue)`, and the
 * fields fill the sentence. A row naming an attribute passes it as `attr`, which fills the sentence
 * and becomes the published field of the same name, so the two can never disagree.
 */
export function warn(code: Code, slide: string | null = null, cue: string | null = null, fields: Fields = {}): void {
  // The place is part of almost every sentence, so it fills the template as well as the published
  // field, and a caller that knows a better word for it passes one and wins.
  const filled = { ...(slide === null ? {} : { slide }), ...(cue === null ? {} : { cue }), ...fields };
  const row: PageWarning = {
    code,
    message: message(code, filled),
    slide,
    cue,
    attr: fields.attr === undefined ? null : String(fields.attr),
  };
  if (recorded.some((seen) => same(seen, row))) return;
  recorded.push(row);
  console.warn(`${CONSOLE_PREFIX}: ${row.message}`);
}

/** Whether two rows say the same thing about the same place, which is what makes one of them a repeat. */
function same(a: PageWarning, b: PageWarning): boolean {
  return a.code === b.code && a.slide === b.slide && a.cue === b.cue && a.attr === b.attr;
}

/** Every distinct warning the page reported, which is the list the probe's report answers with. */
export function warnings(): readonly PageWarning[] {
  return recorded;
}

/** Forget every row, which only a test that drives several pages through one module needs. */
export function forget(): void {
  recorded = [];
}
