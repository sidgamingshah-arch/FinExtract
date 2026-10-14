/** Responsive layout: a small useMediaQuery hook and the one breakpoint a screen stacks at.
 * Narrow: 1024px wide or less — a tablet, or a desktop window halved. */
import { useEffect, useState } from "react";

export const NARROW = "(max-width: 1024px)";

function matches(q: string): boolean {
  try { return window.matchMedia(q).matches; } catch { return false; }
}

export function useMediaQuery(query: string): boolean {
  const [on, setOn] = useState(() => matches(query));
  useEffect(() => {
    let mql: MediaQueryList;
    try { mql = window.matchMedia(query); } catch { return; }
    const f = () => setOn(mql.matches);
    f();
    mql.addEventListener("change", f);
    return () => mql.removeEventListener("change", f);
  }, [query]);
  return on;
}

/** ≤ 1024 px */
export const useNarrow = () => useMediaQuery(NARROW);
