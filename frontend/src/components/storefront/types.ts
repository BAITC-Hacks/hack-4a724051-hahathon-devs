import type { Product } from "@/lib/api";

export type StoreProduct = Product & {
  brand?: string | null;
  image_url?: string | null;
  unit?: string | null;
  min_order?: number | null;
  certificates?: { title: string; url: string; number?: string | null; valid_until?: string | null }[];
};
export type Category = { path: string[]; name: string; count: number; children: Category[] };
export type CatalogPage = { items: StoreProduct[]; total: number; page: number; page_size: number; pages: number; coverage: "partial" | "complete" | "unknown"; brands: string[]; warnings: string[] };
export type BrowseParams = { query?: string; category?: string; brand?: string; stock_only?: boolean; min_price?: string; max_price?: string; sort?: string; page?: number; page_size?: number };

export const money = (amount: string | null, currency = "KZT") => {
  if (amount === null) return "Цена по запросу";
  const number = Number(amount);
  return Number.isFinite(number) ? `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(number)} ₸` : `${amount} ${currency}`;
};
export const canAdd = (product: StoreProduct) => product.stock_status === "available" && product.price_amount !== null;
export const pathKey = (category: Category) => category.path.join("/");
export function safeImage(url: string | null | undefined): string | null {
  if (!url) return null;
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  try { const parsed = new URL(url); return parsed.protocol === "https:" && (parsed.hostname === "ekt.kz" || parsed.hostname.endsWith(".ekt.kz")) ? url : null; }
  catch { return null; }
}
