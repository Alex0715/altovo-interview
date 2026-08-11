"use client";

import { useEffect, useRef, useState } from "react";
import { getDocument, type Citation, type DocumentDetail } from "@/lib/api";

export function SourceViewer({ citation }: { citation: Citation | null }) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const highlightRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!citation) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getDocument(citation.document_id)
      .then((d) => {
        if (!cancelled) setDoc(d);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [citation]);

  useEffect(() => {
    highlightRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [doc, citation]);

  if (!citation) {
    return (
      <div className="pane source-pane">
        <h2 className="pane-title">Source</h2>
        <div className="source-empty">Click a citation to see the exact passage it came from.</div>
      </div>
    );
  }

  const pages =
    citation.page_start == null
      ? null
      : citation.page_start === citation.page_end
        ? `p.${citation.page_start}`
        : `pp.${citation.page_start}–${citation.page_end}`;

  return (
    <div className="pane source-pane">
      <h2 className="pane-title">Source</h2>
      <div className="source-header">
        <div className="source-filename">{citation.filename}</div>
        {pages && <div className="source-pages">{pages}</div>}
      </div>

      {loading && <div className="source-loading">Loading…</div>}
      {error && <div className="source-error">⚠ {error}</div>}

      {doc && doc.full_text != null && (
        <pre className="source-text">
          {doc.full_text.slice(0, citation.char_start)}
          <mark ref={highlightRef}>
            {doc.full_text.slice(citation.char_start, citation.char_end)}
          </mark>
          {doc.full_text.slice(citation.char_end)}
        </pre>
      )}
    </div>
  );
}
