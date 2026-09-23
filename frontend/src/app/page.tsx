"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import AssistantChat from "@/components/assistant-chat";
import { Icon } from "@/components/storefront/icons";
import { ProductCard, ProductDetail, categoryIcon } from "@/components/storefront/product-card";
import { StoreHeader } from "@/components/storefront/header";
import { CartDrawer } from "@/components/storefront/cart-drawer";
import { CompareModal } from "@/components/storefront/compare-modal";
import { type BrowseParams, type CatalogPage, type Category, type StoreProduct, displayWarnings, money, pathKey } from "@/components/storefront/types";
import { api, type Capabilities, type Cart, type Proposal, ensureSession, errorMessage, newKey } from "@/lib/api";

type CategoriesPage = { items: Category[]; coverage: "partial" | "complete" | "unknown" };
type StoreApi = typeof api & { categories: () => Promise<CategoriesPage>; browse: (params: BrowseParams) => Promise<CatalogPage> };
const storeApi = api as StoreApi;
const initialParams: BrowseParams = { query: "", category: "", brand: "", stock_only: false, sort: "relevance", page: 1, page_size: 12 };
const terminal = new Set<Proposal["status"]>(["applied", "rejected", "expired", "stale", "failed"]);
const ready = (caps: Capabilities | null, key: string) => caps?.[key] === "ready" || caps?.[key] === "synthetic_demo";
const proposalStatus = (value: Proposal["status"]) => ({ proposed: "Ожидает подтверждения", queued: "В очереди", executing: "Добавляем в корзину", applied: "Добавлено", rejected: "Отклонено", expired: "Срок истёк", stale: "Данные изменились", failed: "Ошибка", outcome_unknown: "Статус уточняется" })[value];
const flatten = (categories: Category[]): Category[] => categories.flatMap(item => [item, ...flatten(item.children || [])]);

function HeroArt() { return <svg className="hero-art" viewBox="0 0 540 350" fill="none" aria-hidden="true"><defs><linearGradient id="heroFill" x1="0" y1="0" x2="1" y2="1"><stop stopColor="#fff"/><stop offset="1" stopColor="#bad8fb"/></linearGradient><filter id="heroShadow"><feDropShadow dx="0" dy="20" stdDeviation="22" floodColor="#2867b5" floodOpacity=".2"/></filter></defs><path d="M0 286h127l38-38h71l48-48h77l48-48h130M14 321h177l42-42h91l48-48h160M78 108h127l38 38h77l47 47h151" stroke="#93bde8" strokeWidth="2" opacity=".45"/><g filter="url(#heroShadow)" transform="translate(135 42) rotate(-10 150 150)"><rect x="55" y="70" width="286" height="190" rx="24" fill="url(#heroFill)" stroke="#c4ddf7" strokeWidth="3"/><rect x="72" y="88" width="252" height="155" rx="17" fill="#f5fbff" stroke="#cfe4fb"/><rect x="92" y="112" width="84" height="112" rx="10" fill="#dcecff" stroke="#a8ccee"/><rect x="188" y="112" width="55" height="112" rx="9" fill="#e8f3ff" stroke="#b5d2f2"/><rect x="255" y="112" width="50" height="112" rx="9" fill="#e8f3ff" stroke="#b5d2f2"/><path d="M115 148h38m-38 13h38m-38 13h38m-38 13h38" stroke="#83b2e2" strokeWidth="5" strokeLinecap="round"/><circle cx="215" cy="144" r="12" fill="#fff" stroke="#8ab8e9" strokeWidth="4"/><path d="M215 136v16" stroke="#8ab8e9" strokeWidth="3"/><circle cx="280" cy="147" r="10" fill="#2e76c5"/><path d="M271 190h18" stroke="#8ab8e9" strokeWidth="5" strokeLinecap="round"/><path d="M92 71v-23m21 23v-23m21 23v-23m21 23v-23m21 23v-23m21 23v-23m21 23v-23m21 23v-23m21 23v-23m21 23v-23" stroke="#94bae5" strokeWidth="7" strokeLinecap="round"/></g><circle cx="415" cy="66" r="8" fill="#fff" opacity=".8"/><circle cx="96" cy="61" r="5" fill="#fff" opacity=".8"/></svg>; }

export default function Home() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [categoryCoverage, setCategoryCoverage] = useState("unknown");
  const [page, setPage] = useState<CatalogPage | null>(null);
  const [params, setParams] = useState<BrowseParams>(initialParams);
  const [queryInput, setQueryInput] = useState("");
  const [minInput, setMinInput] = useState("");
  const [maxInput, setMaxInput] = useState("");
  const [section, setSection] = useState<"home" | "catalog" | "favorites">("home");
  const [layout, setLayout] = useState<"grid" | "list">("grid");
  const [menuOpen, setMenuOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [cartOpen, setCartOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantProductId, setAssistantProductId] = useState<number | undefined>(undefined);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [detail, setDetail] = useState<StoreProduct | null>(null);
  const [favorites, setFavorites] = useState<number[]>([]);
  const [comparison, setComparison] = useState<number[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [savedLoaded, setSavedLoaded] = useState(false);
  const [savedProducts, setSavedProducts] = useState<StoreProduct[]>([]);
  const [compareProducts, setCompareProducts] = useState<StoreProduct[]>([]);
  const [draft, setDraft] = useState<{ product: StoreProduct; quantity: number }[]>([]);
  const [cart, setCart] = useState<Cart | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [cartBusy, setCartBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const catalogRef = useRef<HTMLElement>(null);
  const requestId = useRef(0);
  const bootFlight = useRef<Promise<void> | null>(null);
  const proposeKey = useRef<string | null>(null);
  const confirmKey = useRef<string | null>(null);

  const categoryList = useMemo(() => flatten(categories), [categories]);
  const selectedCategory = categoryList.find(category => pathKey(category) === params.category);
  const cartReady = ready(caps, "cart");
  const catalogReady = ready(caps, "catalog");
  const loadCart = useCallback(async () => { try { setCart(await api.cart()); } catch (caught) { setError(errorMessage(caught)); } }, []);
  const browse = useCallback(async (next: BrowseParams) => {
    const current = ++requestId.current;
    setCatalogLoading(true);
    try { const result = await storeApi.browse(next); if (current === requestId.current) setPage(result); }
    catch (caught) { if (current === requestId.current) { setPage(null); setError(errorMessage(caught)); } }
    finally { if (current === requestId.current) setCatalogLoading(false); }
  }, []);
  const boot = useCallback(() => {
    if (bootFlight.current) return bootFlight.current;
    const run = async () => {
      setLoading(true); setError("");
      try {
        await ensureSession();
        const capabilities = await api.capabilities();
        setCaps(capabilities);
        if (ready(capabilities, "catalog")) {
          const [tree, firstPage] = await Promise.all([storeApi.categories(), storeApi.browse(initialParams)]);
          setCategories(tree.items); setCategoryCoverage(tree.coverage); setPage(firstPage);
        }
        if (ready(capabilities, "cart")) setCart(await api.cart());
      } catch (caught) { setError(errorMessage(caught)); }
      finally { setLoading(false); }
    };
    const promise = run().finally(() => { bootFlight.current = null; });
    bootFlight.current = promise;
    return promise;
  }, []);
  useEffect(() => { void boot(); }, [boot]);
  useEffect(() => { if (!loading && catalogReady && section === "catalog") void browse(params); }, [params, section, loading, catalogReady, browse]);
  useEffect(() => { try { const ids = JSON.parse(localStorage.getItem("ekt_favorites") || "[]"); const compared = JSON.parse(localStorage.getItem("ekt_comparison") || "[]"); if (Array.isArray(ids)) setFavorites(ids.filter((id): id is number => Number.isSafeInteger(id) && id > 0).slice(0, 100)); if (Array.isArray(compared)) setComparison(compared.filter((id): id is number => Number.isSafeInteger(id) && id > 0).slice(0, 4)); } catch { /* Ignore malformed browser storage. */ } setSavedLoaded(true); }, []);
  useEffect(() => { if (savedLoaded) { try { localStorage.setItem("ekt_favorites", JSON.stringify(favorites)); } catch { /* Storage may be disabled. */ } } }, [favorites, savedLoaded]);
  useEffect(() => { if (savedLoaded) { try { localStorage.setItem("ekt_comparison", JSON.stringify(comparison)); } catch { /* Storage may be disabled. */ } } }, [comparison, savedLoaded]);
  useEffect(() => { if (section !== "favorites" || !favorites.length) { setSavedProducts([]); return; } let active = true; void Promise.allSettled(favorites.map(id => api.product(id))).then(results => { if (active) setSavedProducts(results.flatMap(result => result.status === "fulfilled" ? [result.value as StoreProduct] : [])); }); return () => { active = false; }; }, [favorites, section]);
  useEffect(() => { if (!compareOpen || !comparison.length) { setCompareProducts([]); return; } let active = true; void Promise.allSettled(comparison.map(id => api.product(id))).then(results => { if (active) setCompareProducts(results.flatMap(result => result.status === "fulfilled" ? [result.value as StoreProduct] : [])); }); return () => { active = false; }; }, [comparison, compareOpen]);
  useEffect(() => { if (detailId === null) { setDetail(null); return; } let active = true; void api.product(detailId).then(product => { if (active) setDetail(product as StoreProduct); }).catch(caught => { if (active) setError(errorMessage(caught)); }); return () => { active = false; }; }, [detailId]);
  useEffect(() => {
    if (!proposal || terminal.has(proposal.status) || proposal.status === "proposed") return;
    let active = true;
    const timer = window.setInterval(async () => { try { const latest = await api.proposal(proposal.id); if (!active) return; setProposal(latest); if (terminal.has(latest.status)) { clearInterval(timer); if (latest.status === "applied") { setDraft([]); await loadCart(); setNotice("Товары добавлены в корзину."); } else setNotice(`Предложение: ${proposalStatus(latest.status).toLowerCase()}.`); } } catch (caught) { if (active) setError(errorMessage(caught)); } }, 1500);
    return () => { active = false; clearInterval(timer); };
  }, [proposal?.id, proposal?.status, loadCart]);

  function openCatalog(next: Partial<BrowseParams> = {}) { setParams(previous => ({ ...previous, ...next, page: 1 })); setSection("catalog"); setMenuOpen(false); setFiltersOpen(false); window.setTimeout(() => catalogRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0); }
  function submitSearch(event: FormEvent<HTMLFormElement>) { event.preventDefault(); openCatalog({ query: queryInput.trim(), category: "" }); }
  function toggleSaved(id: number, target: "favorite" | "compare") { if (target === "favorite") setFavorites(items => items.includes(id) ? items.filter(item => item !== id) : [...items, id]); else setComparison(items => items.includes(id) ? items.filter(item => item !== id) : items.length >= 4 ? items : [...items, id]); }
  function addDraft(product: StoreProduct) { setDraft(items => { const present = items.find(item => item.product.id === product.id); return present ? items.map(item => item.product.id === product.id ? { ...item, quantity: Math.min(100000, item.quantity + 1) } : item) : [...items, { product, quantity: Math.max(1, product.min_order || 1) }]; }); setProposal(null); proposeKey.current = null; confirmKey.current = null; setCartOpen(true); setDetailId(null); }
  function updateDraft(id: number, quantity: number) { setDraft(items => items.map(item => item.product.id === id ? { ...item, quantity: Math.max(item.product.min_order || 1, Math.min(100000, Math.floor(quantity) || 1)) } : item)); setProposal(null); proposeKey.current = null; confirmKey.current = null; }
  async function propose() { if (!draft.length) return; setCartBusy(true); setError(""); const key = proposeKey.current || newKey(); proposeKey.current = key; try { setProposal(await api.propose(draft.map(item => ({ product_id: item.product.id, quantity: item.quantity })), key)); confirmKey.current = null; } catch (caught) { setError(errorMessage(caught)); } finally { setCartBusy(false); } }
  async function confirm() { if (!proposal || proposal.status !== "proposed") return; setCartBusy(true); setError(""); const key = confirmKey.current || newKey(); confirmKey.current = key; try { const result = await api.confirm(proposal.id, proposal.version, key); setProposal(result); if (result.status === "applied") { setDraft([]); await loadCart(); setNotice("Товары добавлены в корзину."); } } catch (caught) { setError(errorMessage(caught)); try { setProposal(await api.proposal(proposal.id)); } catch { /* Preserve key for explicit retry. */ } } finally { setCartBusy(false); } }
  async function reject() { if (!proposal || proposal.status !== "proposed") return; setCartBusy(true); try { setProposal(await api.reject(proposal.id)); setNotice("Предложение отклонено. Корзина не менялась."); } catch (caught) { setError(errorMessage(caught)); } finally { setCartBusy(false); } }
  function renderCard(product: StoreProduct) { return <ProductCard key={product.id} product={product} mode={layout} cartReady={cartReady} onOpen={() => setDetailId(product.id)} onAdd={() => addDraft(product)} favorite={favorites.includes(product.id)} compared={comparison.includes(product.id)} onFavorite={() => toggleSaved(product.id, "favorite")} onCompare={() => toggleSaved(product.id, "compare")}/>; }
  const displayProducts = section === "favorites" ? savedProducts : page?.items || [];

  return <div className="storefront">
    <StoreHeader section={section} categories={categories} menuOpen={menuOpen} setMenuOpen={setMenuOpen} query={queryInput} setQuery={setQueryInput} onSearch={submitSearch} onHome={() => { setSection("home"); setMenuOpen(false); window.scrollTo({ top: 0, behavior: "smooth" }); }} onCatalog={() => openCatalog({ query: "", category: "" })} onCategory={category => openCatalog({ category: pathKey(category), query: "" })} onFavorites={() => { setSection("favorites"); setMenuOpen(false); }} onCompare={() => setCompareOpen(true)} onCart={() => setCartOpen(true)} onAssistant={() => { setAssistantProductId(undefined); setAssistantOpen(true); }} favorites={favorites.length} comparison={comparison.length} cartCount={cart?.items.length || draft.length}/>
    {error && <div className="container"><div className="alert error" role="alert">{error}<button onClick={() => setError("")} aria-label="Закрыть"><Icon name="close" size={16}/></button></div></div>}
    {notice && <div className="container"><div className="alert success" role="status">{notice}<button onClick={() => setNotice("")} aria-label="Закрыть"><Icon name="close" size={16}/></button></div></div>}
    <main>
      {section === "home" && <>
        <section className="hero"><div className="container hero-inner"><div className="hero-copy"><span className="hero-kicker"><Icon name="spark" size={14}/> ПОДБОР ЭЛЕКТРОТЕХНИКИ</span><h1>Всё для ваших<br/><em>электропроектов</em></h1><p>Находите оборудование по каталогу, проверяйте характеристики и собирайте подборку с прозрачным подтверждением.</p><div className="hero-actions"><button className="main-button" onClick={() => openCatalog({ query: "", category: "" })}>Смотреть каталог <Icon name="arrow" size={18}/></button><button className="ghost-button" onClick={() => { setAssistantProductId(undefined); setAssistantOpen(true); }}><Icon name="spark" size={17}/> Спросить помощника</button></div></div><HeroArt/></div></section>
        <div className="container"><div className="trust-strip"><div><Icon name="box"/><span>Каталог по категориям</span></div><div><Icon name="check"/><span>Проверяем цену и остаток</span></div><div><Icon name="shield"/><span>Подтверждение перед корзиной</span></div></div>
          {caps?.catalog === "synthetic_demo" && <p className="demo-banner">Демо-режим: каталог и корзина используют тестовые данные. Заказ не отправляется на ekt.kz.</p>}
          <section className="home-section"><div className="section-heading"><div><span className="micro-label">КАТАЛОГ ЭКТ</span><h2>Категории товаров</h2><p>Выберите направление, чтобы увидеть доступные товары.</p></div><button className="text-link" onClick={() => openCatalog({ query: "", category: "" })}>Все категории <Icon name="arrow" size={17}/></button></div><div className="category-tiles">{categories.slice(0, 12).map((category, index) => <button type="button" className={`category-tile category-tone-${index % 4}`} key={pathKey(category)} onClick={() => openCatalog({ category: pathKey(category), query: "" })}><span className="category-tile-icon"><Icon name={categoryIcon(category.name)} size={34}/></span><strong>{category.name}</strong><span className="category-bottom"><small>{category.count} товаров</small><Icon name="arrow" size={17}/></span></button>)}</div>{categories.length === 0 && !loading && <div className="empty-block">Категории пока недоступны. <button onClick={() => void boot()} className="text-link">Повторить</button></div>}{categoryCoverage !== "complete" && categories.length > 0 && <p className="data-note">Категории отражают доступную часть каталога.</p>}</section>
          <section className="home-section products-section"><div className="section-heading"><div><span className="micro-label">ИЗ КАТАЛОГА</span><h2>Товары для вашего проекта</h2><p>Данные из подключённого источника.</p></div><button className="text-link" onClick={() => openCatalog({ query: "", category: "" })}>Перейти в каталог <Icon name="arrow" size={17}/></button></div><div className="products-grid">{(page?.items || []).slice(0, 4).map(renderCard)}</div>{!catalogReady && !loading && <div className="empty-block">Каталог пока не подключён.</div>}</section>
        </div>
      </>}
      {section !== "home" && <section className="container catalog-section" ref={catalogRef}><div className="breadcrumbs"><button type="button" onClick={() => setSection("home")}>Главная</button><span>/</span><span>{section === "favorites" ? "Избранное" : selectedCategory?.name || (params.query ? "Результаты поиска" : "Каталог")}</span></div>{caps?.catalog === "synthetic_demo" && <p className="demo-banner">Демо-каталог: данные тестовые. Заказ на ekt.kz не оформляется.</p>}<div className="catalog-title-row"><div><span className="micro-label">ЭЛЕКТРООБОРУДОВАНИЕ</span><h1>{section === "favorites" ? "Избранное" : selectedCategory?.name || (params.query ? `Поиск: «${params.query}»` : "Каталог товаров")}</h1><p>{section === "favorites" ? `${savedProducts.length} сохранённых товаров` : page ? `${page.total} товаров${page.coverage !== "complete" ? " · выдача может быть неполной" : ""}` : "Загрузка каталога"}</p></div>{section === "catalog" && <button className="mobile-filter-toggle" type="button" onClick={() => setFiltersOpen(!filtersOpen)}><Icon name="filter" size={18}/> Фильтры</button>}</div><div className="catalog-layout">
        {section === "catalog" && <aside className={`filters ${filtersOpen ? "open" : ""}`}><div className="filter-heading"><strong>Фильтры</strong><button onClick={() => setFiltersOpen(false)} aria-label="Закрыть"><Icon name="close" size={17}/></button></div><div className="filter-group"><h3>Категории</h3><button className={!params.category ? "active" : ""} onClick={() => openCatalog({ category: "" })}>Все категории</button>{categories.map(category => <div key={pathKey(category)}><button className={params.category === pathKey(category) ? "active" : ""} onClick={() => openCatalog({ category: pathKey(category) })}>{category.name}<small>{category.count}</small></button>{params.category === pathKey(category) && category.children?.map(child => <button className="sub-category" key={pathKey(child)} onClick={() => openCatalog({ category: pathKey(child) })}>{child.name}<small>{child.count}</small></button>)}</div>)}</div><div className="filter-group"><h3>Производитель</h3><select value={params.brand || ""} onChange={event => setParams(previous => ({ ...previous, brand: event.target.value, page: 1 }))}><option value="">Все производители</option>{(page?.brands || []).map(brand => <option key={brand} value={brand}>{brand}</option>)}</select></div><div className="filter-group"><h3>Цена, ₸</h3><form className="price-filter" onSubmit={event => { event.preventDefault(); setParams(previous => ({ ...previous, min_price: minInput || undefined, max_price: maxInput || undefined, page: 1 })); }}><div><input type="number" min="0" placeholder="От" value={minInput} onChange={event => setMinInput(event.target.value)} aria-label="Цена от"/><input type="number" min="0" placeholder="До" value={maxInput} onChange={event => setMaxInput(event.target.value)} aria-label="Цена до"/></div><button type="submit">Применить</button></form></div><div className="filter-group"><label className="stock-filter"><input type="checkbox" checked={Boolean(params.stock_only)} onChange={event => setParams(previous => ({ ...previous, stock_only: event.target.checked, page: 1 }))}/><span>Только в наличии</span></label></div><button className="filter-reset" onClick={() => { setParams({ ...initialParams, query: params.query }); setMinInput(""); setMaxInput(""); }} type="button">Сбросить фильтры</button></aside>}
        <div className="catalog-main">{section === "catalog" && <div className="catalog-toolbar"><span>{page ? `${page.total} позиций` : "Загрузка..."}</span><div><label htmlFor="sort-select">Сортировать</label><select id="sort-select" value={params.sort || "relevance"} onChange={event => setParams(previous => ({ ...previous, sort: event.target.value, page: 1 }))}><option value="relevance">По релевантности</option><option value="price_asc">Сначала дешевле</option><option value="price_desc">Сначала дороже</option><option value="name">По названию</option></select><div className="view-switch"><button aria-label="Плитка" className={layout === "grid" ? "active" : ""} onClick={() => setLayout("grid")}><Icon name="grid" size={17}/></button><button aria-label="Список" className={layout === "list" ? "active" : ""} onClick={() => setLayout("list")}><Icon name="list" size={17}/></button></div></div></div>}{section === "catalog" && displayWarnings(page?.warnings || []).map((warning, index) => <p className="data-note" key={index}>{warning}</p>)}{catalogLoading && <p className="loading-note">Обновляем товары…</p>}<div className={`products-grid ${layout === "list" ? "list-mode" : ""}`}>{displayProducts.map(renderCard)}</div>{!catalogLoading && displayProducts.length === 0 && <div className="empty-block"><Icon name={section === "favorites" ? "heart" : "search"} size={34}/><h3>{section === "favorites" ? "Пока нет избранных товаров" : "Товары не найдены"}</h3><p>{section === "favorites" ? "Сохраните товары из каталога, чтобы быстро вернуться к ним." : "Измените запрос или фильтры."}</p></div>}{section === "catalog" && page && page.pages > 1 && <div className="pagination"><button disabled={page.page <= 1} onClick={() => setParams(previous => ({ ...previous, page: Math.max(1, (previous.page || 1) - 1) }))}>← Назад</button><span>Страница {page.page} из {page.pages}</span><button disabled={page.page >= page.pages} onClick={() => setParams(previous => ({ ...previous, page: (previous.page || 1) + 1 }))}>Вперёд →</button></div>}</div>
      </div></section>}
    </main>
    <footer className="store-footer"><div className="container footer-content"><div><b>ЭЛЕКТРОКОМПЛЕКТ</b><p>Демонстрационная витрина подбора электротехники.</p></div><div><a href="https://ekt.kz/" target="_blank" rel="noreferrer">Официальный сайт ЭКТ</a><a href="https://ekt.kz/about/contacts/" target="_blank" rel="noreferrer">Контакты и магазины</a></div><span>Цены, наличие и состав корзины показываются по данным API.</span></div></footer>
    <button className="assistant-fab" onClick={() => { setAssistantProductId(undefined); setAssistantOpen(true); }} aria-label="Открыть помощника"><Icon name="spark" size={22}/><span>Помощник по подбору</span></button>
    <AssistantChat open={assistantOpen} onClose={() => { setAssistantOpen(false); setAssistantProductId(undefined); }} pageProductId={assistantProductId} onCartChanged={() => void loadCart()} capabilities={caps}/>
    {detail && <ProductDetail product={detail} onClose={() => setDetailId(null)} onAsk={() => { setAssistantProductId(detail.id); setDetailId(null); setAssistantOpen(true); }} cartReady={cartReady} onAdd={() => addDraft(detail)} onFavorite={() => toggleSaved(detail.id, "favorite")} favorite={favorites.includes(detail.id)}/>}
    <CompareModal open={compareOpen} products={compareProducts} onClose={() => setCompareOpen(false)} onRemove={id => toggleSaved(id, "compare")}/>
    <CartDrawer open={cartOpen} onClose={() => setCartOpen(false)} ready={cartReady} capability={caps?.cart} draft={draft} cart={cart} proposal={proposal} busy={cartBusy} onQuantity={updateDraft} onRemove={id => { setDraft(items => items.filter(item => item.product.id !== id)); setProposal(null); proposeKey.current = null; confirmKey.current = null; }} onPropose={() => void propose()} onConfirm={() => void confirm()} onReject={() => void reject()} onCheck={() => { if (proposal) void api.proposal(proposal.id).then(setProposal).catch(caught => setError(errorMessage(caught))); }}/>
  </div>;
}

