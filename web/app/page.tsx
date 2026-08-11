"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  askQuestion,
  checkHealth,
  deleteDocument,
  listDocuments,
  uploadDocument,
  type Citation,
  type DocumentSummary,
} from "@/lib/api";
import { DocumentList } from "@/components/DocumentList";
import { Chat, type ChatMessage } from "@/components/Chat";
import { SourceViewer } from "@/components/SourceViewer";

const POLL_MS = 2000;

export default function Home() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [health, setHealth] = useState<"checking" | "ok" | "error">("checking");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const [activeCitation, setActiveCitation] = useState<Citation | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshDocuments = useCallback(() => {
    listDocuments()
      .then(setDocuments)
      .catch(() => {
        /* transient — next poll or user action will retry */
      });
  }, []);

  // Cold-start warmer: pings the API the moment the page loads, per
  // ARCHITECTURE.md §6, while the user is still looking at the upload pane.
  useEffect(() => {
    checkHealth()
      .then((h) => setHealth(h.status === "ok" ? "ok" : "error"))
      .catch(() => setHealth("error"));
    refreshDocuments();
  }, [refreshDocuments]);

  // Poll while anything is still ingesting; stop once every doc has settled.
  useEffect(() => {
    const anyInFlight = documents.some((d) => d.status === "parsing" || d.status === "embedding");
    if (anyInFlight && !pollRef.current) {
      pollRef.current = setInterval(refreshDocuments, POLL_MS);
    } else if (!anyInFlight && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [documents, refreshDocuments]);

  const handleUpload = useCallback(
    (files: FileList) => {
      Array.from(files).forEach((file) => {
        uploadDocument(file)
          .then(refreshDocuments)
          .catch((e) => {
            console.error("upload failed", e);
            refreshDocuments();
          });
      });
    },
    [refreshDocuments],
  );

  const handleDelete = useCallback(
    (id: string) => {
      setDocuments((docs) => docs.filter((d) => d.id !== id));
      deleteDocument(id)
        .catch((e) => console.error("delete failed", e))
        .finally(refreshDocuments);
    },
    [refreshDocuments],
  );

  const handleAsk = useCallback((question: string) => {
    const id = crypto.randomUUID();
    setMessages((prev) => [...prev, { id, question }]);
    setBusy(true);

    askQuestion(question, (event) => {
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id !== id) return m;
          switch (event.type) {
            case "stage":
              return { ...m, stage: event.stage };
            case "sources":
              return { ...m, sources: event.sources };
            case "answer":
              return { ...m, answer: event.answer };
            case "done":
              return { ...m, done: event.done };
            case "error":
              return { ...m, error: event.message };
            default:
              return m;
          }
        }),
      );
    })
      .catch((e) => {
        setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, error: String(e) } : m)));
      })
      .finally(() => setBusy(false));
  }, []);

  return (
    <div className="shell">
      <header className="shell-header">
        <h1 className="shell-title">Altovo — Document Q&amp;A</h1>
        <div className="health-badge">
          <span className={`health-dot ${health === "checking" ? "" : health}`} />
          {health === "checking" ? "Checking API…" : health === "ok" ? "API online" : "API unreachable"}
        </div>
      </header>

      <DocumentList documents={documents} onUpload={handleUpload} onDelete={handleDelete} />
      <Chat messages={messages} busy={busy} onAsk={handleAsk} onSelectCitation={setActiveCitation} />
      <SourceViewer citation={activeCitation} />
    </div>
  );
}
