"use client";

import { useCallback, useRef, useState } from "react";
import type { DocumentSummary } from "@/lib/api";

const ACCEPTED = ".pdf,.txt,.md,.markdown";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

const STATUS_LABEL: Record<DocumentSummary["status"], string> = {
  parsing: "Parsing…",
  embedding: "Embedding…",
  ready: "Ready",
  failed: "Failed",
};

export function DocumentList({
  documents,
  onUpload,
  onDelete,
}: {
  documents: DocumentSummary[];
  onUpload: (files: FileList) => void;
  onDelete: (id: string) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      if (e.dataTransfer.files.length) onUpload(e.dataTransfer.files);
    },
    [onUpload],
  );

  return (
    <div className="pane documents-pane">
      <h2 className="pane-title">Documents</h2>

      <div
        className={`dropzone${dragging ? " dropzone-active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
      >
        <p>Drop files here or click to upload</p>
        <p className="dropzone-hint">PDF · TXT · Markdown</p>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED}
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files?.length) onUpload(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      <ul className="document-list">
        {documents.length === 0 && <li className="document-empty">No documents yet.</li>}
        {documents.map((doc) => (
          <li key={doc.id} className="document-row">
            <div className="document-row-main">
              <span className={`status-dot status-${doc.status}`} aria-hidden />
              <div className="document-row-text">
                <div className="document-filename" title={doc.filename}>
                  {doc.filename}
                </div>
                <div className="document-meta">
                  {STATUS_LABEL[doc.status]} · {formatBytes(doc.byte_size)}
                  {doc.status === "ready" ? ` · ${doc.chunk_count} chunks` : ""}
                </div>
                {doc.status === "failed" && doc.error && (
                  <div className="document-error" title={doc.error}>
                    {doc.error}
                  </div>
                )}
              </div>
            </div>
            <button
              type="button"
              className="document-delete"
              onClick={() => onDelete(doc.id)}
              aria-label={`Delete ${doc.filename}`}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
