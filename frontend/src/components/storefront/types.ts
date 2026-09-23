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
const categories: Record<string, string> = {
  kabel_provod: "Кабель и провод", nizkovoltnaya_apparatura: "Низковольтная аппаратура",
  rozetki_vyklyuchateli_korobki: "Розетки и выключатели", svetilniki_lampy: "Светильники и лампы",
};
const attributeLabels: Record<string, string> = {
  rated_current: "Номинальный ток", poles: "Количество полюсов", voltage: "Напряжение",
  breaking_capacity: "Отключающая способность", device_type: "Тип устройства", series: "Серия",
  cross_section: "Сечение", conductor_material: "Материал жилы", cable_type: "Тип кабеля",
  insulation: "Изоляция", fire_class: "Пожарный класс", power: "Мощность",
  luminous_flux: "Световой поток", color_temperature: "Цветовая температура", color: "Цвет",
  ip: "Степень защиты", mounting: "Монтаж", leakage_current: "Ток утечки",
  residual_type: "Тип УЗО", curve: "Характеристика срабатывания", trip_unit: "Расцепитель",
};
const warningLabels: Record<string, string> = {
  price_missing: "Цена отсутствует в источнике.", category_unknown: "Категория товара не определена.",
  unsafe_image_url: "Изображение недоступно.", unsafe_product_url: "Ссылка на товар недоступна.",
  unsafe_certificate_url: "Ссылка на сертификат недоступна.",
  catalog_coverage_is_partial_or_unknown: "Показана доступная часть каталога.",
};
export const displayCategory = (value: string) => categories[value] || (value.includes("_") ? value.replaceAll("_", " ") : value);
export const displayAttribute = (key: string) => attributeLabels[key] || (key.includes("_") ? key.replaceAll("_", " ") : key);
export const displayWarnings = (warnings: string[]) => warnings.filter(code => code !== "synthetic_demo_data").map(code => warningLabels[code] || "Некоторые данные товара требуют проверки.");
export function attributeValue(value: string | null, unit: string | null): string {
  if (!value) return "Неизвестно";
  const cleaned = value.trim();
  if (!unit || cleaned.toLocaleLowerCase("ru-RU").endsWith(unit.trim().toLocaleLowerCase("ru-RU"))) return cleaned;
  return `${cleaned} ${unit}`;
}
export function safeImage(url: string | null | undefined): string | null {
  if (!url) return null;
  if (url.startsWith("/") && !url.startsWith("//")) return url;
  try { const parsed = new URL(url); return parsed.protocol === "https:" && (parsed.hostname === "ekt.kz" || parsed.hostname.endsWith(".ekt.kz")) ? url : null; }
  catch { return null; }
}
export function safeCertificate(url: string): string | null {
  if (url.startsWith("/api/v1/certificates/") && !url.startsWith("//")) return url;
  try { const parsed = new URL(url); return parsed.protocol === "https:" && (parsed.hostname === "ekt.kz" || parsed.hostname.endsWith(".ekt.kz")) ? url : null; }
  catch { return null; }
}
