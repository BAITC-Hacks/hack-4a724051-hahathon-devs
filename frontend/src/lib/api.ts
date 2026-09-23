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
  brand: string | null;
  image_url: string | null;
  unit: string | null;
  min_order: number | null;
  certificates: { title: string; url: string; number: string | null; valid_until: string | null }[];
  category_path: string[] | null;
  price_amount: string | null;
  price_currency: string | null;
  stock_status: "available" | "out_of_stock" | "unknown";
  sellable_quantity: string | null;
  attributes: Attribute[];
  sources: Source[];
  warnings: string[];
};
export type SearchResult = { items: Product[]; coverage: "partial" | "complete" | "unknown"; warnings: string[]; reasons: Record<string, string> };
export type Category = { path: string[]; name: string; count: number; children: Category[] };
export type CategoriesPage = { items: Category[]; coverage: "partial" };
export type CatalogPage = Omit<SearchResult, "reasons"> & { total: number; page: number; page_size: number; pages: number; brands: string[] };
export type BrowseParams = { query?: string; category?: string; brand?: string; stock_only?: boolean; min_price?: string | number; max_price?: string | number; sort?: "relevance" | "price_asc" | "price_desc" | "name"; page?: number; page_size?: number };
export type Asset = { id: string; name: string; content_type: string; size_bytes: number; status: "ready" | "partial" | "quarantined"; warnings: string[]; created_at: number };
export type TurnOptions = { asset_ids?: string[]; language?: "auto" | "ru" | "kk" | "en"; allow_external_analysis?: boolean; page_product_id?: number | null };
export type Conversation = { id: string; created_at: number };
export type Message = { id: string; role: "user" | "assistant"; text: string; created_at: number };
export type AssistantOutput = { message: string; products: Product[]; unknowns: string[]; mode: "catalog_only" | "unavailable" | "grounded" | "action"; language: "ru" | "kk" | "en"; proposal: Proposal | null; sources: string[]; alternative_reasons: Record<string, string>; warnings: string[] };
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
let expiresAt = 0;
let conversationPromise: Promise<Conversation> | null = null;
let memoryConversationId: string | null = null;

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
  const response = await fetch(`${apiRoot}${path}`, { method: options.method || "GET", credentials: "same-origin", headers, body: options.body === undefined ? undefined : JSON.stringify(options.body), signal: options.signal || AbortSignal.timeout(15000), cache: "no-store" });
  if (response.status === 401) { csrf = null; expiresAt = 0; }
  return parse<T>(response);
}

export async function ensureSession(): Promise<Session> {
  if (csrf && expiresAt > Date.now() / 1000 + 5) return { csrf_token: csrf, expires_at: expiresAt };
  if (!sessionPromise) {
    sessionPromise = request<Session>("/session", { method: "POST" }).then(session => { csrf = session.csrf_token; expiresAt = session.expires_at; return session; }).finally(() => { sessionPromise = null; });
  }
  return sessionPromise;
}

export function newKey(): string { return crypto.randomUUID().replaceAll("-", ""); }
export const api = {
  categories: () => request<CategoriesPage>("/catalog/categories"),
  browse: (params: BrowseParams = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") query.set(key, String(value));
    return request<CatalogPage>(`/catalog/products?${query}`);
  },
  capabilities: () => request<Capabilities>("/capabilities"),
  search: (query: string) => request<SearchResult>(`/products?query=${encodeURIComponent(query)}&limit=12`),
  product: (id: number) => request<Product>(`/products/${id}`),
  alternatives: (id: number) => request<SearchResult>(`/products/${id}/alternatives`),
  createConversation: () => request<Conversation>("/conversations", { method: "POST", write: true }),
  messages: (id: string) => request<Message[]>(`/conversations/${id}/messages`),
  submitTurn: (id: string, text: string, key: string, options: TurnOptions = {}) => request<TurnSubmission>(`/conversations/${id}/turns`, { method: "POST", write: true, key, body: { text, asset_ids: [], language: "auto", ...options } }),
  turns: (id: string) => request<Turn[]>(`/conversations/${id}/turns`),
  turn: (id: string, signal?: AbortSignal) => request<Turn>(`/turns/${id}`, { signal }),
  cancelTurn: (id: string) => request<Turn>(`/turns/${id}/cancel`, { method: "POST", write: true }),
  cart: () => request<Cart>("/cart"),
  propose: (items: { product_id: number; quantity: number }[], key: string) => request<Proposal>("/cart/proposals", { method: "POST", write: true, key, body: { items } }),
  proposal: (id: string) => request<Proposal>(`/proposals/${id}`),
  confirm: (id: string, version: number, key: string) => request<Proposal>(`/proposals/${id}/confirm`, { method: "POST", write: true, key, body: { version } }),
  reject: (id: string) => request<Proposal>(`/proposals/${id}/reject`, { method: "POST", write: true }),
  asset: (id: string) => request<Asset>(`/assets/${id}`),
  deleteAsset: async (id: string): Promise<void> => { await request(`/assets/${id}/delete`, { method: "POST", write: true }); },
  upload: async (file: File, signal?: AbortSignal): Promise<Asset> => {
    await ensureSession();
    const mime: Record<string, string> = {pdf:"application/pdf",xlsx:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",docx:"application/vnd.openxmlformats-officedocument.wordprocessingml.document",jpg:"image/jpeg",jpeg:"image/jpeg",png:"image/png",txt:"text/plain",csv:"text/csv"};
    const type = mime[file.name.split(".").pop()?.toLowerCase() || ""];
    if (!type || file.size > 10485760 || !file.size) throw new ApiError("invalid_file", "Поддерживаются PDF, DOCX, XLSX, JPG, PNG, TXT, CSV до 10 МиБ.", 422, false);
    const response = await fetch(`${apiRoot}/assets/upload?filename=${encodeURIComponent(file.name)}`, {method:"POST",credentials:"same-origin",body:file,headers:{"content-type":type,"x-csrf-token":csrf!},signal:signal || AbortSignal.timeout(60000)});
    if (response.status === 401) { csrf = null; expiresAt = 0; }
    return parse<Asset>(response);
  },
};

export async function ensureConversation(): Promise<Conversation> {
  if (!conversationPromise) conversationPromise = (async () => {
    await ensureSession();
    let id = memoryConversationId;
    try { id = sessionStorage.getItem("ekt_conversation_id") || id; } catch { /* Use memory when storage is disabled. */ }
    if (id) {
      try { await api.turns(id); return {id, created_at:0}; }
      catch (error) { if (!(error instanceof ApiError) || ![401,404].includes(error.status)) throw error; }
    }
    const conversation = await api.createConversation();
    memoryConversationId = conversation.id;
    try { sessionStorage.setItem("ekt_conversation_id", conversation.id); } catch { /* This tab can still chat without persistent storage. */ }
    return conversation;
  })().finally(() => { conversationPromise = null; });
  return conversationPromise;
}

export async function endSession(): Promise<void> {
  // Server-side revocation: the cookie alone is not the session.
  try { await request<{ ended: boolean }>("/session/logout", { method: "POST", write: true }); }
  finally {
    csrf = null;
    try { sessionStorage.removeItem("ekt_conversation_id"); } catch { /* Storage may be disabled. */ }
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message + (error.requestId ? ` (запрос ${error.requestId})` : "");
  return "Не удалось связаться с сервером. Проверьте подключение и повторите запрос.";
}
