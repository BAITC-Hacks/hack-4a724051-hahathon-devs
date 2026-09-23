"use client";
import { Icon } from "./icons";
import { attributeValue, money, type StoreProduct } from "./types";
import { useDialog } from "./use-dialog";

export function CompareModal({ open, products, onClose, onRemove }: { open: boolean; products: StoreProduct[]; onClose: () => void; onRemove: (id: number) => void }) {
  const dialogRef = useDialog(open, onClose);
  if (!open) return null;
  const keys = Array.from(new Set(products.flatMap(product => product.attributes.map(attribute => attribute.key)))).slice(0, 20);
  return <div className="overlay" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section className="compare-modal" role="dialog" aria-modal="true" aria-label="Сравнение товаров"><div className="modal-header"><div><span className="micro-label">ВЫБРАННЫЕ ТОВАРЫ</span><h2>Сравнение</h2></div><button onClick={onClose} aria-label="Закрыть"><Icon name="close"/></button></div>{products.length === 0 ? <div className="empty-block">Добавьте до четырёх товаров через значок сравнения на карточках.</div> : <div className="comparison-table"><table><thead><tr><th>Характеристика</th>{products.map(product => <th key={product.id}>{product.name}<button onClick={() => onRemove(product.id)} aria-label={`Убрать ${product.name}`}><Icon name="close" size={15}/></button></th>)}</tr></thead><tbody><tr><th>Артикул</th>{products.map(product => <td key={product.id}>{product.article_original}</td>)}</tr><tr><th>Цена</th>{products.map(product => <td key={product.id}>{money(product.price_amount, product.price_currency || "KZT")}</td>)}</tr><tr><th>Наличие</th>{products.map(product => <td key={product.id}>{product.stock_status === "available" ? "В наличии" : product.stock_status === "out_of_stock" ? "Нет в наличии" : "Неизвестно"}</td>)}</tr>{keys.map(key => <tr key={key}><th>{key}</th>{products.map(product => <td key={product.id}>{product.attributes.find(attribute => attribute.key === key)?.value || "—"}</td>)}</tr>)}</tbody></table></div>}</section></div>;
}
