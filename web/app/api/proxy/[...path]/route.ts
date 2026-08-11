import { NextRequest } from "next/server";

// Node runtime, not edge: we stream SSE through untouched and edge adds
// buffering behaviour we don't want between us and FastAPI.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

/**
 * Thin pass-through to the FastAPI service.
 *
 * The browser never talks to the API directly. That kills CORS entirely,
 * keeps the API origin and provider keys server-side, and gives the user a
 * single domain. The upstream body is piped straight back, so SSE still
 * works — we never buffer or re-serialise it.
 */
async function proxy(req: NextRequest, path: string[]) {
  const target = new URL(`${API_BASE_URL}/${path.join("/")}`);
  target.search = req.nextUrl.search;

  const headers = new Headers(req.headers);
  // Let fetch recompute these for the upstream connection.
  headers.delete("host");
  headers.delete("connection");
  headers.delete("content-length");

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: req.method === "GET" || req.method === "HEAD" ? undefined : req.body,
      // Required by Node's fetch when sending a stream body (file uploads).
      // @ts-expect-error -- not in the DOM lib types
      duplex: "half",
      cache: "no-store",
    });
  } catch {
    return Response.json(
      { detail: "The API service is unreachable. Is it running?" },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers(upstream.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");
  // Defeat proxy buffering so SSE stage events arrive when they're sent.
  if (responseHeaders.get("content-type")?.includes("text/event-stream")) {
    responseHeaders.set("Cache-Control", "no-cache, no-transform");
    responseHeaders.set("X-Accel-Buffering", "no");
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
export async function DELETE(req: NextRequest, ctx: Ctx) {
  return proxy(req, (await ctx.params).path);
}
