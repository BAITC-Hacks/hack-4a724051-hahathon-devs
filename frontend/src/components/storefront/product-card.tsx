"use client";

import { Icon, type IconName } from "./icons";
import { canAdd, money, safeImage, type StoreProduct } from "./types";

export function categoryIcon(name: string): IconName {
  const lower = name.toLowerCase();
  if (lower.includes("кабел") || lower.includes("провод")) return "cable";
  if (lower.includes("свет") || lower.includes("ламп")) return "lamp";
  if (lower.includes("розет") || lower.includes("выключат")) return "socket";
  if (lower.includes("щит") || lower.includes("шкаф")) return "panel";
  if (lower.includes("инструмент") || lower.includes("монтаж")) return "tool";
  if (lower.includes("камер") || lower.includes("видео")) return "camera";
  if (lower.includes("защит") || lower.includes("автомат")) return "shield";
  if (lower.includes("оборуд") || lower.includes("автоматиз")) return "power";
  return "bolt";
}

export function ProductVisual({ product, large = false }: { product: StoreProduct; large?: boolean }) {
  const image = safeImage(product.image_url);
  if (image) return <div className={`product-visual ${large ? "large" : ""}`}><img src={image} alt={product.name} loading="lazy"/></div>;
  return <div className={`product-visual product-visual-fallback ${large ? "large" : ""}`} role="img" aria-label="Схематичная иллюстрация категории">
    <svg className="visual-circuit" viewBox="0 0 320 240" aria-hidden="true"><path d="M-12 183h75l28-28h47l33-33h70l25-25h62"/><path d="M-10 207h111l29-30h72l34-35h84"/><path d="M-10 155h46l36-35h59l36-36h52l27-28h80"/><circle cx="80" cy="155" r="4"/><circle cx="167" cy="84" r="4"/><circle cx="235" cy="142" r="4"/></svg>
    <div className="visual-glyph"><Icon name={categoryIcon(product.category_path?.[0] || product.name)} size={large ? 112 : 78}/></div>
    <span className="visual-caption">ELECTROKOMPLEKT · КАТАЛОГ</span>
  </div>;
}

export function ProductCard({ product, onOpen, onAdd, onFavorite, onCompare, favorite, compared, mode = "grid", cartReady }: {
  product: StoreProduct; onOpen: () => void; onAdd: () => void; onFavorite: () => void; onCompare: () => void; favorite: boolean; compared: boolean; mode?: "grid" | "list"; cartReady: boolean;
}) {
  const stock = product.stock_status === "available" ? "В наличии" : product.stock_status === "out_of_stock" ? "Нет в наличии" : "Остаток уточняется";
  return <article className={`store-product ${mode}`}>
    <div className="store-product-image"><button className="image-open" type="button" onClick={onOpen} aria-label={`Открыть ${product.name}`}><ProductVisual product={product}/></button><div className="product-float-actions"><button type="button" className={favorite ? "selected" : ""} onClick={onFavorite} aria-label={favorite ? "Убрать из избранного" : "В избранное"} title="Избранное"><Icon name="heart" size={17}/></button><button type="button" className={compared ? "selected" : ""} onClick={onCompare} aria-label={compared ? "Убрать из сравнения" : "Сравнить"} title="Сравнение"><Icon name="compare" size={17}/></button></div></div>
    <div className="store-product-info"><div className="product-meta"><span>{product.brand || product.category_path?.[0] || "Каталог ЭКТ"}</span><span className="product-article">{product.article_original}</span></div><button className="product-title" type="button" onClick={onOpen}>{product.name}</button><div className="product-specs">{product.attributes.filter(item => item.value && item.status !== "unknown").slice(0, mode === "list" ? 4 : 2).map(item => <span key={item.key}><b>{item.key}</b> {item.value}{item.unit ? ` ${item.unit}` : ""}</span>)}</div><div className="product-purchase"><div><strong>{money(product.price_amount, product.price_currency || "KZT")}</strong><span className={`stock-state ${product.stock_status}`}>● {stock}</span></div><button className="add-cart-button" type="button" disabled={!cartReady || !canAdd(product)} onClick={onAdd} title={!cartReady ? "Корзина пока недоступна" : !canAdd(product) ? "Цена или остаток не подтверждены" : "Добавить в подборку"}><Icon name="cart" size={18}/><span>В подборку</span></button></div></div>
  </article>;
}

export function ProductDetail({ product, onClose, onAdd, cartReady, onFavorite, favorite }: { product: StoreProduct; onClose: () => void; onAdd: () => void; cartReady: boolean; onFavorite: () => void; favorite: boolean }) {
  return <div className="overlay" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section className="detail-modal" role="dialog" aria-modal="true" aria-label={product.name}><button className="modal-close" type="button" onClick={onClose} aria-label="Закрыть"><Icon name="close"/></button><div className="detail-visual"><ProductVisual product={product} large/></div><div className="detail-copy"><span className="micro-label">КАТАЛОГ / {product.category_path?.join(" / ") || "ТОВАР"}</span><h2>{product.name}</h2><p className="detail-code">Артикул: {product.article_original}{product.brand ? ` · ${product.brand}` : ""}</p><div className={`detail-stock ${product.stock_status}`}>{product.stock_status === "available" ? "● В наличии" : product.stock_status === "out_of_stock" ? "● Нет в наличии" : "● Остаток неизвестен"}{product.sellable_quantity ? ` · ${product.sellable_quantity} ${product.unit || "шт."}` : ""}</div><div className="detail-attrs">{product.attributes.map((attribute, index) => <div key={`${attribute.key}-${index}`}><span>{attribute.key}</span><strong>{attribute.value || "Неизвестно"}{attribute.unit ? ` ${attribute.unit}` : ""}{attribute.status === "conflict" ? " · данные расходятся" : ""}</strong></div>)}</div>{product.warnings.length > 0 && <p className="detail-warning">{product.warnings.join(" ")}</p>}{product.certificates && product.certificates.length > 0 && <div className="detail-certs"><span className="micro-label">ДОКУМЕНТЫ</span>{product.certificates.map((certificate, index) => certificate.url.startsWith("/") && !certificate.url.startsWith("//") ? <a href={certificate.url} key={index} target="_blank" rel="noreferrer">{certificate.title}{certificate.number ? ` №${certificate.number}` : ""} <Icon name="external" size={14}/></a> : null)}</div>}<div className="detail-bottom"><strong>{money(product.price_amount, product.price_currency || "KZT")}</strong><button type="button" onClick={onFavorite} className="detail-favorite" aria-label={favorite ? "Убрать из избранного" : "В избранное"}><Icon name="heart"/></button><button className="main-button" type="button" disabled={!cartReady || !canAdd(product)} onClick={onAdd}><Icon name="cart" size={17}/> В подборку</button></div><p className="micro-note">Корзина изменится только после проверки предложения и вашего подтверждения.</p></div></section></div>;
}
