"use client";

import type { Citation } from "@/lib/api";

export function CitationChip({
  citation,
  onSelect,
}: {
  citation: Citation;
  onSelect: (citation: Citation) => void;
}) {
  const pages =
    citation.page_start == null
      ? null
      : citation.page_start === citation.page_end
        ? `p.${citation.page_start}`
        : `pp.${citation.page_start}–${citation.page_end}`;

  return (
    <button
      type="button"
      className="citation-chip"
      onClick={() => onSelect(citation)}
      title={`${citation.filename}${pages ? ` — ${pages}` : ""}`}
    >
      {citation.marker}
    </button>
  );
}
