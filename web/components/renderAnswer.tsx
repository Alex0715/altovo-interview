import type { ReactNode } from "react";
import type { Citation } from "@/lib/api";
import { CitationChip } from "./CitationChip";

const MARKER_RE = /(\[\d+\])/g;

/**
 * Splits answer prose on inline [n] markers and swaps validated ones for
 * clickable chips. A marker the server dropped (out of range, or claimed
 * in citations but never in prose, or vice versa) just renders as plain
 * text — a dead marker, not a broken link (ARCHITECTURE.md §5).
 */
export function renderAnswer(
  text: string,
  citations: Citation[],
  onSelectCitation: (citation: Citation) => void,
): ReactNode[] {
  const byMarker = new Map(citations.map((c) => [c.marker, c]));
  const parts = text.split(MARKER_RE);

  return parts.map((part, i) => {
    const match = part.match(/^\[(\d+)\]$/);
    if (match) {
      const citation = byMarker.get(Number(match[1]));
      if (citation) {
        return <CitationChip key={i} citation={citation} onSelect={onSelectCitation} />;
      }
    }
    return <span key={i}>{part}</span>;
  });
}
