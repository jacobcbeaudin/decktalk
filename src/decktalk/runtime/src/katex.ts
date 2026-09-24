/*! Typesetting, the wait for the typesetter, and the readable text an equation falls back to.
 *
 * KaTeX is the page's own dependency and never the runtime's, so everything here is written for a
 * page that loads it late, loads it from somewhere slow, or never loads it at all. An element that
 * cannot be typeset keeps the text its author wrote inside it, which is why the contract asks for
 * that text in the first place: it is the equation's readable fallback, it is what the transcript
 * prints, and it is what a screen reader reads.
 */

import { MILLISECONDS } from "./contract.ts";
import { ATTR, flagged, written } from "./scene.ts";
import { warn } from "./warn.ts";

/** How long a page that wants KaTeX waits for it before it is drawn without it. */
const KATEX_SECONDS = 5;

/** How often the wait looks for the typesetter, which is often enough to be invisible to a person. */
const LOOK_EVERY_MS = 100;

/** The class KaTeX leaves on something it refused, which is how a page finds out that it refused. */
const ERROR_CLASS = "katex-error";

/** What KaTeX is, as far as this module is concerned, which is one call with two options. */
type Katex = { render(tex: string, into: Element, options: { throwOnError: boolean; displayMode: boolean }): void };

declare global {
  interface Window {
    katex?: Katex;
  }
}

/** The text each typeset element was written with, kept here because the element no longer carries it. */
const readable = new WeakMap<Element, string>();

/** Whether a page is waiting for a typesetter that has not arrived, so it waits once and not once a slide. */
let waiting = false;

/**
 * The text an element reads as, which is what its author wrote inside it rather than what KaTeX drew.
 *
 * The transcript takes this, so a line of it is a sentence a person can hear read aloud instead of a
 * typeset element's own text content, which is the markup of the drawn equation.
 */
export function fallback(el: Element): string {
  return (readable.get(el) ?? el.textContent ?? "").trim().replace(/\s+/g, " ");
}

/**
 * Whether the page has any equation at all, which decides whether it waits for a typesetter.
 *
 * A template's content is a document of its own, so a deck that writes every slide as markup has
 * every one of its equations out of reach of a plain query and is looked for template by template.
 */
function wants(): boolean {
  if (document.querySelector(`[${ATTR.tex}]`) || document.querySelector('script[src*="katex"]')) return true;
  return [...document.querySelectorAll("template")].some((tpl) => tpl.content.querySelector(`[${ATTR.tex}]`) !== null);
}

/**
 * Typeset every equation in one subtree, and say what happened when one of them cannot be.
 *
 * The author's own text is kept before KaTeX replaces it, because an equation with no readable text
 * is an equation no transcript and no screen reader can carry, and the contract asks for it.
 */
export function typeset(root: ParentNode, slideId: string | null = null): void {
  const equations = [...root.querySelectorAll(`[${ATTR.tex}]`)];
  if (!equations.length) return;
  if (!window.katex) {
    for (const el of equations) readable.set(el, el.textContent ?? "");
    watch(slideId);
    return;
  }
  for (const el of equations) {
    if (readable.has(el) && el.querySelector(".katex")) continue;
    const tex = written(el, ATTR.tex) ?? "";
    readable.set(el, readable.get(el) ?? el.textContent ?? "");
    window.katex.render(tex, el, { throwOnError: false, displayMode: flagged(el, ATTR.texDisplay) });
    // With `throwOnError` off, KaTeX draws what it refused in red rather than raising, so the page
    // finds out by looking for what it left behind.
    if (el.querySelector(`.${ERROR_CLASS}`)) {
      el.textContent = readable.get(el) ?? "";
      warn("PAGE_KATEX_ERROR", slideId, null, { value: tex, attr: ATTR.tex });
    }
  }
}

/**
 * Wait for a typesetter that was not there when a slide mounted, and say so if it never comes.
 *
 * A later slide of a recording mounts long after the readiness chain has finished, so this is the
 * one path by which an equation on slide nine reports a missing typesetter at all.
 */
function watch(slideId: string | null): void {
  if (waiting) return;
  waiting = true;
  setTimeout(() => {
    if (!window.katex) warn("PAGE_KATEX_MISSING", slideId, null, { attr: ATTR.tex });
  }, KATEX_SECONDS * MILLISECONDS);
}

/**
 * Settle once the typesetter is there, or once the page has waited long enough to be drawn without it.
 *
 * This is one link of the readiness chain, so it never rejects: a page with no equation and no KaTeX
 * script is ready as it stands, and a page that waited in vain is ready with a warning.
 */
export function ready(root: ParentNode | null): Promise<void> {
  if (!wants() || window.katex) return Promise.resolve();
  return new Promise((resolve) => {
    const started = performance.now();
    const look = () => {
      if (window.katex) {
        if (root) typeset(root);
        resolve();
        return;
      }
      if (performance.now() - started > KATEX_SECONDS * MILLISECONDS) {
        warn("PAGE_KATEX_MISSING", null, null, { attr: ATTR.tex });
        resolve();
        return;
      }
      setTimeout(look, LOOK_EVERY_MS);
    };
    look();
  });
}
