"use client";

import { useEffect, useState } from "react";

type Health = { status: string; checks: Record<string, string> };

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Doubles as the cold-start warmer once this is deployed.
  useEffect(() => {
    fetch("/api/proxy/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <main style={{ padding: "3rem", fontFamily: "ui-sans-serif, system-ui", lineHeight: 1.6 }}>
      <h1 style={{ fontSize: "1.5rem", marginBottom: "0.5rem" }}>Altovo — Document Q&amp;A</h1>
      <p style={{ color: "#666", marginBottom: "2rem" }}>
        Scaffold. Browser → Next proxy → FastAPI → Neon round trip:
      </p>
      <pre
        style={{
          background: "#f4f4f5",
          padding: "1rem",
          borderRadius: 8,
          fontSize: "0.85rem",
          overflowX: "auto",
        }}
      >
        {error ?? (health ? JSON.stringify(health, null, 2) : "checking…")}
      </pre>
    </main>
  );
}
