"use client";

import { useState } from "react";
import type { AnswerPayload, Citation, DoneEvent, RetrievedSourceWire } from "@/lib/api";
import { renderAnswer } from "./renderAnswer";

export type ChatMessage = {
  id: string;
  question: string;
  stage?: "retrieving" | "generating";
  sources?: RetrievedSourceWire[];
  answer?: AnswerPayload;
  done?: DoneEvent;
  error?: string;
};

const STAGE_LABEL: Record<string, string> = {
  retrieving: "Searching your documents…",
  generating: "Reading sources and writing an answer…",
};

function SourceScores({ sources }: { sources: RetrievedSourceWire[] }) {
  if (!sources.length) return null;
  return (
    <details className="source-scores">
      <summary>{sources.length} source{sources.length === 1 ? "" : "s"} retrieved</summary>
      <ul>
        {sources.map((s) => (
          <li key={s.chunk_id}>
            <span className="source-score-ordinal">[{s.ordinal}]</span> {s.filename}
            {s.page_start != null ? ` (p.${s.page_start}${s.page_end !== s.page_start ? `–${s.page_end}` : ""})` : ""}
            <span className="source-score-value"> · score {s.score.toFixed(3)}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function MessageAnswer({
  message,
  onSelectCitation,
}: {
  message: ChatMessage;
  onSelectCitation: (citation: Citation) => void;
}) {
  if (message.error) {
    return <div className="answer-card answer-error">⚠ {message.error}</div>;
  }

  if (!message.answer) {
    return (
      <div className="answer-card answer-pending">
        <span className="spinner" aria-hidden />
        {STAGE_LABEL[message.stage ?? "retrieving"]}
      </div>
    );
  }

  const { answer } = message;
  return (
    <div className={`answer-card${answer.abstained ? " answer-abstained" : ""}`}>
      {answer.abstained && <div className="answer-badge">Couldn&apos;t find this in your documents</div>}
      <p className="answer-text">{renderAnswer(answer.text, answer.citations, onSelectCitation)}</p>
      {answer.assumption && <p className="answer-assumption">Assumption: {answer.assumption}</p>}
      {message.sources && <SourceScores sources={message.sources} />}
      {message.done && (
        <p className="answer-meta">
          {message.done.latency_ms}ms
          {message.done.prompt_tokens > 0 ? ` · ${message.done.prompt_tokens + message.done.output_tokens} tokens` : ""}
        </p>
      )}
    </div>
  );
}

export function Chat({
  messages,
  busy,
  onAsk,
  onSelectCitation,
}: {
  messages: ChatMessage[];
  busy: boolean;
  onAsk: (question: string) => void;
  onSelectCitation: (citation: Citation) => void;
}) {
  const [draft, setDraft] = useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const question = draft.trim();
    if (!question || busy) return;
    onAsk(question);
    setDraft("");
  };

  return (
    <div className="pane chat-pane">
      <div className="chat-log">
        {messages.length === 0 && (
          <div className="chat-empty">
            Upload a document on the left, then ask a question about it.
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className="chat-turn">
            <div className="question-bubble">{m.question}</div>
            <MessageAnswer message={m} onSelectCitation={onSelectCitation} />
          </div>
        ))}
      </div>

      <form className="chat-input-row" onSubmit={submit}>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask a question about your documents…"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          Ask
        </button>
      </form>
    </div>
  );
}
