import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path: string[] }> };
const incomingHeaders = ["cookie", "content-type", "x-csrf-token", "idempotency-key", "accept"];
const outgoingHeaders = ["content-type", "set-cookie", "x-request-id", "retry-after"];
const maxBodyBytes = 32768;

async function boundedBody(request: NextRequest): Promise<Uint8Array<ArrayBuffer>> {
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && length > maxBodyBytes) throw new Error("body_too_large");
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array(0);
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBodyBytes) { await reader.cancel(); throw new Error("body_too_large"); }
    chunks.push(value);
  }
  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  return body;
}

async function proxy(request: NextRequest, context: Context): Promise<Response> {
  const { path } = await context.params;
  const browserOrigin = request.headers.get("origin");
  if (request.method === "POST" && browserOrigin !== request.nextUrl.origin) {
    return Response.json({ error: { code: "origin_forbidden", message: "Источник запроса не разрешён.", retryable: false }, meta: { request_id: "frontend", warnings: [] } }, { status: 403 });
  }
  // The destination comes only from server configuration. Never accept an arbitrary URL from the browser.
  const base = process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000";
  let target: URL;
  try {
    target = new URL(base);
    if (!["http:", "https:"].includes(target.protocol) || target.username || target.password || target.pathname !== "/") {
      throw new Error("Invalid backend origin");
    }
  } catch {
    return Response.json({ error: { code: "proxy_configuration", message: "Сервер API настроен неверно.", retryable: false }, meta: { request_id: "frontend", warnings: [] } }, { status: 503 });
  }
  target.pathname = `/api/v1/${path.map(encodeURIComponent).join("/")}`;
  target.search = request.nextUrl.search;

  const headers = new Headers();
  for (const name of incomingHeaders) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (browserOrigin) headers.set("origin", browserOrigin);
  try {
    const body = request.method === "POST" ? await boundedBody(request) : undefined;
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(9000),
    });
    const responseHeaders = new Headers({ "cache-control": "no-store" });
    for (const name of outgoingHeaders) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch (error) {
    if (error instanceof Error && error.message === "body_too_large") {
      return Response.json({ error: { code: "body_too_large", message: "Запрос слишком большой.", retryable: false }, meta: { request_id: "frontend", warnings: [] } }, { status: 413 });
    }
    return Response.json({ error: { code: "backend_unavailable", message: "Не удалось связаться с API. Проверьте, запущен ли сервер.", retryable: true }, meta: { request_id: "frontend", warnings: [] } }, { status: 503, headers: { "cache-control": "no-store" } });
  }
}

export { proxy as GET, proxy as POST };
