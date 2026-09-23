import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path: string[] }> };
const incomingHeaders = ["cookie", "content-type", "x-csrf-token", "idempotency-key", "accept"];
const outgoingHeaders = ["content-type", "content-disposition", "x-content-type-options", "content-security-policy", "x-request-id", "retry-after"];

async function boundedBody(request: NextRequest, maxBodyBytes: number): Promise<Uint8Array<ArrayBuffer>> {
  const length = Number(request.headers.get("content-length"));
  if (Number.isFinite(length) && length > maxBodyBytes) throw new Error("body_too_large");
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array(0);
  const chunks: Uint8Array[] = [];
  let total = 0;
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; void reader.cancel(); }, 15000);
  let complete = false;
  try {
  while (true) {
    const { done, value } = await reader.read();
    if (done) { complete = true; break; }
    total += value.byteLength;
    if (total > maxBodyBytes) { await reader.cancel(); throw new Error("body_too_large"); }
    chunks.push(value);
  }
  } finally { clearTimeout(timeout); }
  if (timedOut || !complete || (request.headers.has("content-length") && total !== length)) throw new Error("body_incomplete");
  const body = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  return body;
}

async function proxy(request: NextRequest, context: Context): Promise<Response> {
  const { path } = await context.params;
  if (!path?.length || path.some(segment => segment === "." || segment === ".." || /[\\/\\\\\x00]/.test(segment))) {
    return Response.json({ error: { code: "invalid_path", message: "Некорректный путь API.", retryable: false } }, { status: 400 });
  }
  const upload = request.method === "POST" && path.join("/") === "assets/upload";
  const browserOrigin = request.headers.get("origin");
  // Next may normalize nextUrl to localhost even when the browser uses 127.0.0.1.
  // Host is supplied by the browser; never trust X-Forwarded-Host from the client.
  const publicOrigin = process.env.FRONTEND_ORIGIN || `${request.nextUrl.protocol}//${request.headers.get("host") || request.nextUrl.host}`;
  if (request.method === "POST" && browserOrigin !== publicOrigin) {
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
    const body = request.method === "POST" ? await boundedBody(request, upload ? 10485760 : 32768) : undefined;
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(upload ? 45000 : 12000),
    });
    const responseHeaders = new Headers({ "cache-control": "no-store", "x-content-type-options": "nosniff" });
    for (const name of outgoingHeaders) {
      const value = upstream.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    for (const cookie of upstream.headers.getSetCookie()) responseHeaders.append("set-cookie", cookie);
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch (error) {
    if (error instanceof Error && error.message === "body_too_large") {
      return Response.json({ error: { code: "body_too_large", message: "Запрос слишком большой.", retryable: false }, meta: { request_id: "frontend", warnings: [] } }, { status: 413 });
    }
    if (error instanceof Error && error.message === "body_incomplete") {
      return Response.json({ error: { code: "body_incomplete", message: "Файл или запрос получен не полностью. Повторите загрузку.", retryable: false }, meta: { request_id: "frontend", warnings: [] } }, { status: 400 });
    }
    return Response.json({ error: { code: "backend_unavailable", message: "Не удалось связаться с API. Проверьте, запущен ли сервер.", retryable: true }, meta: { request_id: "frontend", warnings: [] } }, { status: 503, headers: { "cache-control": "no-store" } });
  }
}

export { proxy as GET, proxy as POST };
