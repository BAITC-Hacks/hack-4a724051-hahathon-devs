export type Meta = { request_id: string; warnings: string[] };
export type Envelope<T> = { data: T; meta: Meta };
export type Capabilities = Record<string, string>;
export type Session = { csrf_token: string; expires_at: number };
export type Source = { ref: string; path: string; fetched_at: string };
export type Attribute = { key: string; value: string | null; unit: string | null; status: "observed" | "verified" | "conflict" | "unknown"; sources: Source[] };
export type Product = {
  id: number;
  article_original: string;
  name: string;
  category_path: string[] | null;
  price_amount: string | null;
  price_currency: string | null;
  stock_status: "available" | "out_of_stock" | "unknown";
  sellable_quantity: string | null;
  attributes: Attribute[];
  sources: Source[];
  warnings: string[];
};
export type SearchResult = { items: Product[]; coverage: "partial" | "complete" | "unknown"; warnings: string[] };
export type Conversation = { id: string; created_at: number };
export type Message = { id: string; role: "user" | "assistant"; text: string; created_at: number };
export type AssistantOutput = { message: string; products: Product[]; unknowns: string[]; mode: "catalog_only" | "unavailable" };
export type Turn = { id: string; conversation_id: string; status: "queued" | "running" | "completed" | "failed" | "cancelled"; output: AssistantOutput | null; error_code: string | null; created_at: number };
export type TurnSubmission = { turn: Turn; created: boolean };
export type CartLine = { product_id: number; quantity: number; article_original: string; name: string; unit_price_amount: string; line_total_amount: string };
export type Cart = { mode: "demo" | "real"; version: number; items: CartLine[]; total_amount: string; currency: string };
export type Proposal = { id: string; version: number; status: "proposed" | "queued" | "executing" | "applied" | "rejected" | "expired" | "stale" | "failed" | "outcome_unknown"; mode: "demo" | "real"; items: CartLine[]; total_amount: string; currency: string; cart_version: number; expires_at: number; receipt_id: string | null; cart_url: string | null };

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number, public retryable: boolean, public requestId?: string) {
    super(message);
    this.name = "ApiError";
  }
}

const apiRoot = "/api/v1";
let csrf: string | null = null;
let sessionPromise: Promise<Session> | null = null;

async function parse<T>(response: Response): Promise<T> {
  let payload: unknown;
  try { payload = await response.json(); } catch { throw new ApiError("invalid_response", "Сервер вернул некорректный ответ.", response.status, false); }
  if (!payload || typeof payload !== "object") throw new ApiError("invalid_response", "Сервер вернул некорректный ответ.", response.status, false);
  const body = payload as { data?: T; error?: { code?: string; message?: string; retryable?: boolean }; meta?: Meta };
  if (!response.ok || body.error) {
    throw new ApiError(body.error?.code || "request_failed", body.error?.message || "Не удалось выполнить запрос.", response.status, Boolean(body.error?.retryable), body.meta?.request_id);
  }
  if (body.data === undefined) throw new ApiError("invalid_response", "В ответе API нет данных.", response.status, false, body.meta?.request_id);
  return body.data;
}

async function request<T>(path: string, options: { method?: "GET" | "POST"; body?: unknown; write?: boolean; key?: string; signal?: AbortSignal } = {}): Promise<T> {
  if (options.write) await ensureSession();
  const headers = new Headers({ accept: "application/json" });
  if (options.body !== undefined) headers.set("content-type", "application/json");
  if (options.write && csrf) headers.set("x-csrf-token", csrf);
  if (options.key) headers.set("idempotency-key", options.key);
  const response = await fetch(`${apiRoot}${path}`, { method: options.method || "GET", credentials: "same-origin", headers, body: options.body === undefined ? undefined : JSON.stringify(options.body), signal: options.signal, cache: "no-store" });
  return parse<T>(response);
}

export async function ensureSession(): Promise<Session> {
  if (csrf) return { csrf_token: csrf, expires_at: 0 };
  if (!sessionPromise) {
    sessionPromise = request<Session>("/session", { method: "POST" }).then(session => { csrf = session.csrf_token; return session; }).finally(() => { sessionPromise = null; });
  }
  return sessionPromise;
}

export function newKey(): string { return crypto.randomUUID().replaceAll("-", ""); }
export const api = {
  capabilities: () => request<Capabilities>("/capabilities"),
  search: (query: string) => request<SearchResult>(`/products?query=${encodeURIComponent(query)}&limit=12`),
  product: (id: number) => request<Product>(`/products/${id}`),
  alternatives: (id: number) => request<SearchResult>(`/products/${id}/alternatives`),
  createConversation: () => request<Conversation>("/conversations", { method: "POST", write: true }),
  messages: (id: string) => request<Message[]>(`/conversations/${id}/messages`),
  submitTurn: (id: string, text: string, key: string) => request<TurnSubmission>(`/conversations/${id}/turns`, { method: "POST", write: true, key, body: { text, asset_ids: [], language: "ru" } }),
  turn: (id: string, signal?: AbortSignal) => request<Turn>(`/turns/${id}`, { signal }),
  cancelTurn: (id: string) => request<Turn>(`/turns/${id}/cancel`, { method: "POST", write: true }),
  cart: () => request<Cart>("/cart"),
  propose: (items: { product_id: number; quantity: number }[], key: string) => request<Proposal>("/cart/proposals", { method: "POST", write: true, key, body: { items } }),
  proposal: (id: string) => request<Proposal>(`/proposals/${id}`),
  confirm: (id: string, version: number, key: string) => request<Proposal>(`/proposals/${id}/confirm`, { method: "POST", write: true, key, body: { version } }),
  reject: (id: string) => request<Proposal>(`/proposals/${id}/reject`, { method: "POST", write: true }),
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message + (error.requestId ? ` (запрос ${error.requestId})` : "");
  return "Не удалось связаться с сервером. Проверьте подключение и повторите запрос.";
}
