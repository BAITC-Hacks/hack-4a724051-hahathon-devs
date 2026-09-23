"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, AssistantOutput, Capabilities, Cart, Conversation, errorMessage, Message, newKey, Product, Proposal, SearchResult, Turn, ensureSession } from "@/lib/api";

type DraftLine = { product: Product; quantity: number };
type PendingTurn = { text: string; key: string };

function money(amount: string | null, currency = "KZT") {
  if (amount === null) return "Цена уточняется";
  const number = Number(amount);
  return Number.isFinite(number) ? `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 }).format(number)} ${currency}` : `${amount} ${currency}`;
}
function dateTime(seconds: number) { return new Date(seconds * 1000).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }); }
function isReady(capabilities: Capabilities | null, name: string) { return capabilities?.[name] === "ready" || capabilities?.[name] === "synthetic_demo"; }
function statusText(status: Proposal["status"]) {
  return ({ proposed: "Ждёт подтверждения", queued: "В очереди", executing: "Добавляем в корзину", applied: "Добавлено в корзину", rejected: "Отклонено", expired: "Срок истёк", stale: "Данные изменились", failed: "Не удалось добавить", outcome_unknown: "Результат уточняется" })[status];
}
const terminalProposal = new Set<Proposal["status"]>(["applied", "rejected", "expired", "stale", "failed"]);
const terminalTurn = new Set<Turn["status"]>(["completed", "failed", "cancelled"]);

function ProductCard({ product, onAdd, canAdd, onAlternatives }: { product: Product; onAdd: (product: Product) => void; canAdd: boolean; onAlternatives: (product: Product) => void }) {
  const conflicts = product.attributes.filter(attribute => attribute.status === "conflict");
  const unknowns = product.attributes.filter(attribute => attribute.status === "unknown");
  const available = product.stock_status === "available";
  return <article className="product-card">
    <div className="product-card-top"><span className="article">АРТ. {product.article_original}</span><span className={`stock ${available ? "positive" : product.stock_status === "out_of_stock" ? "negative" : "neutral"}`}>{available ? "В наличии" : product.stock_status === "out_of_stock" ? "Нет в наличии" : "Остаток неизвестен"}</span></div>
    <h3>{product.name}</h3>
    {product.category_path?.length ? <p className="category">{product.category_path.join(" / ")}</p> : null}
    <div className="attributes">{product.attributes.filter(attribute => attribute.status !== "unknown").slice(0, 4).map(attribute => <span key={attribute.key} className={attribute.status === "conflict" ? "attribute conflict" : "attribute"}><b>{attribute.key}</b> {attribute.value || "—"}{attribute.unit ? ` ${attribute.unit}` : ""}</span>)}</div>
    {conflicts.length > 0 && <p className="caution">Есть расхождения в характеристиках: {conflicts.map(item => item.key).join(", ")}. Проверьте перед заказом.</p>}
    {unknowns.length > 0 && <p className="muted small">Не указано: {unknowns.map(item => item.key).join(", ")}.</p>}
    {product.warnings.length > 0 && <p className="caution">{product.warnings.join(" ")}</p>}
    <div className="product-card-bottom"><div><strong className="price">{money(product.price_amount, product.price_currency || "KZT")}</strong>{available && product.sellable_quantity && <span className="muted small">Доступно: {product.sellable_quantity}</span>}</div><div className="card-actions"><button className="text-button" onClick={() => onAlternatives(product)} type="button">Аналоги</button><button className="small-button" onClick={() => onAdd(product)} disabled={!canAdd || !available || product.price_amount === null} title={!canAdd ? "Корзина недоступна" : !available ? "Нет подтверждённого остатка" : product.price_amount === null ? "Цена неизвестна" : undefined} type="button">В подборку <span aria-hidden="true">↗</span></button></div></div>
  </article>;
}

export default function Home() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [assistantOutput, setAssistantOutput] = useState<AssistantOutput | null>(null);
  const [query, setQuery] = useState("");
  const [searched, setSearched] = useState("");
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [searchBusy, setSearchBusy] = useState(false);
  const [chatText, setChatText] = useState("");
  const [pendingTurn, setPendingTurn] = useState<PendingTurn | null>(null);
  const [turn, setTurn] = useState<Turn | null>(null);
  const [turnBusy, setTurnBusy] = useState(false);
  const [draft, setDraft] = useState<DraftLine[]>([]);
  const [cart, setCart] = useState<Cart | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [proposalBusy, setProposalBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [bootBusy, setBootBusy] = useState(true);
  const [mobilePanel, setMobilePanel] = useState<"catalog" | "assistant" | "cart">("catalog");
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null);
  const [alternatives, setAlternatives] = useState<SearchResult | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const draftKey = useRef<string | null>(null);
  const confirmKey = useRef<string | null>(null);
  const bootFlight = useRef<Promise<void> | null>(null);

  const boot = useCallback(() => {
    if (bootFlight.current) return bootFlight.current;
    const run = async () => {
    setBootBusy(true); setError("");
    try {
      await ensureSession();
      const caps = await api.capabilities();
      setCapabilities(caps);
      const savedId = window.sessionStorage.getItem("ekt_conversation_id");
      let convo: Conversation;
      if (savedId) {
        try {
          const previousMessages = await api.messages(savedId);
          setMessages(previousMessages);
          convo = { id: savedId, created_at: 0 };
        } catch (caught) {
          if (!(caught instanceof ApiError) || ![401, 404].includes(caught.status)) throw caught;
          convo = await api.createConversation();
          window.sessionStorage.setItem("ekt_conversation_id", convo.id);
        }
      } else {
        convo = await api.createConversation();
        window.sessionStorage.setItem("ekt_conversation_id", convo.id);
      }
      setConversation(convo);
      if (isReady(caps, "cart")) setCart(await api.cart());
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBootBusy(false); }
    };
    const promise = run().finally(() => { bootFlight.current = null; });
    bootFlight.current = promise;
    return promise;
  }, []);
  useEffect(() => { void boot(); }, [boot]);

  useEffect(() => {
    if (!turn || terminalTurn.has(turn.status)) return;
    let mounted = true;
    const timer = window.setInterval(async () => {
      try {
        const latest = await api.turn(turn.id);
        if (!mounted) return;
        setTurn(latest);
        if (terminalTurn.has(latest.status)) {
          window.clearInterval(timer);
          setPendingTurn(null);
          if (latest.output) setAssistantOutput(latest.output);
          if (latest.status === "failed") setError(`Помощник не смог завершить ответ${latest.error_code ? ` (${latest.error_code})` : ""}.`);
          if (conversation) setMessages(await api.messages(conversation.id));
        }
      } catch (caught) { if (mounted) setError(errorMessage(caught)); }
    }, 1500);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [turn?.id, turn?.status, conversation]);

  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }); }, [messages, turn?.status]);

  useEffect(() => {
    if (!proposal || terminalProposal.has(proposal.status) || proposal.status === "proposed") return;
    let mounted = true;
    const timer = window.setInterval(async () => {
      try {
        const latest = await api.proposal(proposal.id);
        if (!mounted) return;
        setProposal(latest);
        if (terminalProposal.has(latest.status)) {
          window.clearInterval(timer);
          if (latest.status === "applied") { setDraft([]); setCart(await api.cart()); setNotice("Позиции добавлены в корзину."); }
          else setNotice(`Предложение: ${statusText(latest.status).toLowerCase()}.`);
        }
      } catch (caught) { if (mounted) setError(errorMessage(caught)); }
    }, 1500);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [proposal?.id, proposal?.status]);

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const term = query.trim();
    if (!term || !isReady(capabilities, "catalog")) return;
    setSearchBusy(true); setError(""); setSearched(term); setSelectedProduct(null); setAlternatives(null);
    try { setSearchResult(await api.search(term)); } catch (caught) { setSearchResult(null); setError(errorMessage(caught)); }
    finally { setSearchBusy(false); }
  }

  async function submitTurn(text: string, key: string) {
    if (!conversation) return;
    setTurnBusy(true); setTurn(null); setError("");
    try {
      const submitted = await api.submitTurn(conversation.id, text, key);
      setTurn(submitted.turn);
      setChatText("");
      if (terminalTurn.has(submitted.turn.status)) {
        setPendingTurn(null);
        if (submitted.turn.output) setAssistantOutput(submitted.turn.output);
        setMessages(await api.messages(conversation.id));
      }
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setTurnBusy(false); }
  }
  function sendChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = chatText.trim();
    if (!text || !conversation || pendingTurn || turnBusy) return;
    const pending = { text, key: newKey() };
    setPendingTurn(pending);
    void submitTurn(text, pending.key);
  }
  async function cancelTurn() {
    if (!turn) return;
    try { const cancelled = await api.cancelTurn(turn.id); setTurn(cancelled); setPendingTurn(null); if (conversation) setMessages(await api.messages(conversation.id)); }
    catch (caught) { setError(errorMessage(caught)); }
  }
  function add(product: Product) {
    setDraft(lines => { const match = lines.find(line => line.product.id === product.id); return match ? lines.map(line => line.product.id === product.id ? { ...line, quantity: Math.min(100000, line.quantity + 1) } : line) : [...lines, { product, quantity: 1 }]; });
    draftKey.current = null; confirmKey.current = null;
    setProposal(null);
    setMobilePanel("cart");
    setNotice(`${product.name} — в подборке. Корзина изменится после подтверждения предложения.`);
  }
  function updateQuantity(id: number, value: number) {
    setDraft(lines => lines.map(line => line.product.id === id ? { ...line, quantity: Math.max(1, Math.min(100000, Math.floor(value) || 1)) } : line));
    draftKey.current = null; confirmKey.current = null; setProposal(null);
  }
  async function showAlternatives(product: Product) {
    if (!isReady(capabilities, "catalog")) return;
    setSelectedProduct(product); setAlternatives(null); setError("");
    try { setAlternatives(await api.alternatives(product.id)); }
    catch (caught) { setError(errorMessage(caught)); }
  }
  async function makeProposal() {
    if (!draft.length || !isReady(capabilities, "cart")) return;
    setProposalBusy(true); setError(""); setNotice("");
    const key = draftKey.current || newKey(); draftKey.current = key;
    try { const created = await api.propose(draft.map(line => ({ product_id: line.product.id, quantity: line.quantity })), key); if (created.id !== proposal?.id) confirmKey.current = null; setProposal(created); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setProposalBusy(false); }
  }
  async function confirmProposal() {
    if (!proposal || proposal.status !== "proposed") return;
    setProposalBusy(true); setError("");
    const key = confirmKey.current || newKey(); confirmKey.current = key;
    try {
      const latest = await api.confirm(proposal.id, proposal.version, key);
      setProposal(latest);
      if (latest.status === "applied") { setDraft([]); setCart(await api.cart()); setNotice("Позиции добавлены в корзину."); }
    }
    catch (caught) {
      setError(errorMessage(caught));
      // On an unknown network outcome, read status before offering another confirmation.
      try { setProposal(await api.proposal(proposal.id)); } catch { /* Keep the same key for a deliberate retry. */ }
    } finally { setProposalBusy(false); }
  }
  async function rejectProposal() {
    if (!proposal || proposal.status !== "proposed") return;
    setProposalBusy(true); setError("");
    try { setProposal(await api.reject(proposal.id)); setNotice("Предложение отклонено. Корзина не менялась."); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setProposalBusy(false); }
  }

  const catalogReady = isReady(capabilities, "catalog");
  const cartReady = isReady(capabilities, "cart");
  const chatReady = isReady(capabilities, "chat");
  const shownProducts = selectedProduct ? alternatives?.items || [] : searchResult?.items || [];
  const activeTurn = turn && !terminalTurn.has(turn.status);
  return <main className="app-shell">
    <header className="topbar"><div className="brand"><div className="brand-mark">Э</div><div><strong>ЭКТ</strong><span>умная комплектация</span></div></div><div className="topbar-center">Рабочее пространство <span className="topbar-dot"/> Подбор оборудования</div><div className="topbar-right"><span className="live-dot"/> {bootBusy ? "Подключение…" : error && !capabilities ? "Нет соединения" : "Рабочая сессия"}</div></header>
    <nav className="mobile-tabs" aria-label="Разделы"><button onClick={() => setMobilePanel("catalog")} className={mobilePanel === "catalog" ? "active" : ""}>Каталог</button><button onClick={() => setMobilePanel("assistant")} className={mobilePanel === "assistant" ? "active" : ""}>Помощник</button><button onClick={() => setMobilePanel("cart")} className={mobilePanel === "cart" ? "active" : ""}>Подборка {draft.length ? <span>{draft.length}</span> : null}</button></nav>
    {(error || notice) && <div className={`global-notice ${error ? "error-notice" : "success-notice"}`} role={error ? "alert" : "status"}><span>{error || notice}</span><button type="button" onClick={() => { setError(""); setNotice(""); }} aria-label="Закрыть уведомление">×</button></div>}
    <div className="workspace">
      <section className={`catalog-panel ${mobilePanel === "catalog" ? "mobile-visible" : ""}`} aria-label="Каталог">
        <div className="panel-heading"><div><span className="eyebrow">ЗАКУПКИ / КАТАЛОГ</span><h1>Найдите нужное.<br/><em>Проверьте детали.</em></h1><p>Поиск по наименованию или артикулу. Показываем только данные, которые вернул каталог.</p></div><div className="heading-decoration" aria-hidden="true">01<span>/03</span></div></div>
        <form className="search-form" onSubmit={search}><span className="search-icon" aria-hidden="true">⌕</span><input aria-label="Поиск по каталогу" placeholder="Например, автоматический выключатель 16А" value={query} onChange={event => setQuery(event.target.value)} maxLength={256} disabled={!catalogReady || bootBusy}/><button type="submit" disabled={!catalogReady || searchBusy || !query.trim()}>{searchBusy ? "Ищем…" : "Найти"} <span aria-hidden="true">→</span></button></form>
        {capabilities && !catalogReady && <div className="empty-state"><div className="empty-icon">⌁</div><h2>Каталог пока не подключён</h2><p>Для поиска нужна интеграция с источником товаров. Статус API: {capabilities.catalog || "неизвестен"}.</p></div>}
        {!capabilities && !bootBusy && <div className="empty-state"><h2>Не удалось открыть рабочее пространство</h2><p>Запустите API и попробуйте ещё раз.</p><button className="outline-button" onClick={() => void boot()} type="button">Повторить подключение</button></div>}
        {capabilities?.catalog === "synthetic_demo" && <p className="coverage-note">Демо-режим: каталог и корзина используют тестовые данные. Заказ не отправляется на сайт ЭКТ.</p>}
        {catalogReady && !searched && <div className="intro-grid"><div className="intro-card dark"><span className="intro-number">01</span><h2>Точный подбор</h2><p>Введите артикул или параметры. Если характеристика неизвестна, мы так и покажем.</p></div><div className="intro-card"><span className="intro-number">02</span><h2>Проверка наличия</h2><p>Карточка покажет статус остатка, цену и возможные расхождения.</p></div><div className="intro-card"><span className="intro-number">03</span><h2>Подтверждение</h2><p>Сначала предложение с суммой и составом, затем отдельное добавление в корзину.</p></div></div>}
        {searched && catalogReady && <div className="results-heading"><div><span className="eyebrow">РЕЗУЛЬТАТ ПОИСКА</span><h2>{selectedProduct ? `Аналоги: ${selectedProduct.article_original}` : `«${searched}»`}</h2></div><div className="results-count">{shownProducts.length} позиций</div></div>}
        {selectedProduct && <button className="back-button" type="button" onClick={() => { setSelectedProduct(null); setAlternatives(null); }}>← Вернуться к результатам</button>}
        {searchResult && !selectedProduct && searchResult.coverage !== "complete" && <p className="coverage-note">Выдача может быть неполной. Уточните запрос, если нужной позиции нет.</p>}
        {searchResult?.warnings.map((warning, index) => <p className="coverage-note" key={index}>{warning}</p>)}
        {alternatives?.warnings.map((warning, index) => <p className="coverage-note" key={index}>{warning}</p>)}
        {searched && !searchBusy && shownProducts.length === 0 && (selectedProduct ? alternatives : searchResult) && <div className="empty-state"><h2>{selectedProduct ? "Аналоги не найдены" : "Ничего не найдено"}</h2><p>Попробуйте другой артикул или более общий запрос.</p></div>}
        <div className="product-grid">{shownProducts.map(product => <ProductCard key={product.id} product={product} onAdd={add} canAdd={cartReady} onAlternatives={showAlternatives}/>)}</div>
      </section>
      <aside className={`assistant-panel ${mobilePanel === "assistant" ? "mobile-visible" : ""}`} aria-label="Помощник"><div className="side-heading"><div className="assistant-avatar">✳</div><div><strong>Помощник ЭКТ</strong><span>{chatReady ? "Поиск по каталогу" : "Недоступен"}</span></div><span className="side-status"/></div><div className="assistant-intro"><span className="eyebrow">НАВИГАЦИЯ ПО АССОРТИМЕНТУ</span><h2>Какую задачу<br/>решаем сегодня?</h2><p>Опишите товар, артикул или параметры. Помощник проверит каталог и укажет, каких данных не хватает.</p></div><div className="messages" ref={scrollRef} aria-live="polite">{messages.length === 0 && <div className="assistant-hint">Например: «Найди кабель ВВГнг 3×2,5»</div>}{messages.map(message => <div key={message.id} className={`message ${message.role}`}><span className="message-author">{message.role === "user" ? "Вы" : "ЭКТ / ПОМОЩНИК"}</span><p>{message.text}</p></div>)}{pendingTurn && <div className="message user pending"><span className="message-author">ВЫ · {turn?.status === "running" ? "ОБРАБАТЫВАЕТСЯ" : "ОЖИДАНИЕ"}</span><p>{pendingTurn.text}</p></div>}{activeTurn && <div className="typing">Проверяем каталог <span className="typing-dots">● ● ●</span></div>}{assistantOutput && <div className="assistant-facts"><span className="eyebrow">ПОСЛЕДНИЙ ОТВЕТ</span><span className="mode-pill">{assistantOutput.mode === "catalog_only" ? "По данным каталога" : "Сервис недоступен"}</span>{assistantOutput.unknowns.length > 0 && <p>Нужно уточнить: {assistantOutput.unknowns.join(", ")}.</p>}{assistantOutput.products.length > 0 && <div className="fact-products">{assistantOutput.products.slice(0, 3).map(product => <button type="button" key={product.id} onClick={() => { setQuery(product.article_original); setSearched(product.article_original); setSearchResult({ items: assistantOutput.products, coverage: "partial", warnings: [] }); setMobilePanel("catalog"); }}><strong>{product.name}</strong><span>{product.article_original} · {money(product.price_amount, product.price_currency || "KZT")}</span></button>)}</div>}</div>}</div><div className="chat-compose"><form onSubmit={sendChat}><label className="sr-only" htmlFor="chat-text">Сообщение помощнику</label><textarea id="chat-text" placeholder="Напишите, что нужно найти…" value={chatText} onChange={event => setChatText(event.target.value)} maxLength={8000} disabled={!conversation || !chatReady || Boolean(pendingTurn)} rows={2} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }}/><div className="compose-bottom"><button className="upload-disabled" type="button" disabled title="Загрузка документов пока не подключена">⌁ Прикрепить файл</button><button type="submit" disabled={!conversation || !chatReady || Boolean(pendingTurn) || !chatText.trim()} aria-label="Отправить сообщение">↑</button></div></form>{pendingTurn && !turn && !turnBusy && <button className="inline-action" onClick={() => void submitTurn(pendingTurn.text, pendingTurn.key)} type="button">Повторить отправку с тем же номером запроса</button>}{activeTurn && <button className="inline-action" onClick={() => void cancelTurn()} type="button">Остановить ответ</button>}<p className="compose-disclaimer">{capabilities?.llm === "disabled" ? "Без генеративной модели. Ответы основаны на доступных данных каталога." : "Проверяйте характеристики и цену перед заказом."}</p></div></aside>
      <aside className={`cart-panel ${mobilePanel === "cart" ? "mobile-visible" : ""}`} aria-label="Подборка и корзина"><div className="cart-heading"><div><span className="eyebrow">ВАША ЗАЯВКА</span><h2>Подборка</h2></div><span className="cart-count">{draft.length.toString().padStart(2, "0")}</span></div><div className="cart-content">{!cartReady && capabilities && <div className="cart-empty"><div className="cart-empty-icon">□</div><h3>Корзина не подключена</h3><p>Формирование предложения станет доступно после интеграции с корзиной. Статус API: {capabilities.cart || "неизвестен"}.</p></div>}{cartReady && draft.length === 0 && <div className="cart-empty"><div className="cart-empty-icon">□</div><h3>Пока пусто</h3><p>Добавьте товары из каталога. Сначала вы проверите предложение, потом подтвердите добавление.</p></div>}{draft.length > 0 && <><p className="draft-caption">КАНДИДАТЫ В КОРЗИНУ</p><div className="draft-list">{draft.map(line => <div className="draft-item" key={line.product.id}><button className="remove-item" type="button" onClick={() => { setDraft(items => items.filter(item => item.product.id !== line.product.id)); setProposal(null); draftKey.current = null; }} aria-label={`Убрать ${line.product.name}`}>×</button><span className="article">{line.product.article_original}</span><strong>{line.product.name}</strong><span className="muted small">{money(line.product.price_amount, line.product.price_currency || "KZT")} / шт.</span><div className="quantity-row"><label htmlFor={`quantity-${line.product.id}`}>Количество</label><input id={`quantity-${line.product.id}`} type="number" min="1" max="100000" value={line.quantity} onChange={event => updateQuantity(line.product.id, Number(event.target.value))}/></div></div>)}</div><button className="primary-button full" onClick={() => void makeProposal()} disabled={proposalBusy || !cartReady} type="button">{proposalBusy ? "Проверяем…" : proposal?.status === "proposed" ? "Обновить предложение" : "Проверить предложение"} <span aria-hidden="true">→</span></button></>}{proposal && <div className="proposal-box"><div className="proposal-head"><span className="eyebrow">ПРЕДЛОЖЕНИЕ #{proposal.id.slice(0, 8)}</span><span className={`proposal-status ${proposal.status}`}>{statusText(proposal.status)}</span></div><p className="muted small">{proposal.mode === "demo" ? "Демонстрационная корзина" : "Реальная корзина"} · Версия корзины {proposal.cart_version}</p><div className="proposal-lines">{proposal.items.map(line => <div key={line.product_id}><span>{line.name}<small>{line.quantity} × {money(line.unit_price_amount, proposal.currency)}</small></span><strong>{money(line.line_total_amount, proposal.currency)}</strong></div>)}</div><div className="proposal-total"><span>Итого</span><strong>{money(proposal.total_amount, proposal.currency)}</strong></div><p className="expires">Действует до {dateTime(proposal.expires_at)} · версия {proposal.version}</p>{proposal.status === "proposed" && <div className="proposal-actions"><button className="primary-button" onClick={() => void confirmProposal()} disabled={proposalBusy} type="button">Подтвердить добавление</button><button className="outline-button" onClick={() => void rejectProposal()} disabled={proposalBusy} type="button">Отклонить</button></div>}{proposal.status === "outcome_unknown" && <button className="outline-button" onClick={() => void api.proposal(proposal.id).then(setProposal).catch(caught => setError(errorMessage(caught)))} type="button">Проверить статус</button>}{proposal.status === "applied" && proposal.cart_url?.startsWith("/") && !proposal.cart_url.startsWith("//") && <a className="cart-link" href={proposal.cart_url}>Открыть корзину →</a>}{["expired", "stale", "failed"].includes(proposal.status) && <p className="caution">Проверьте данные и создайте новое предложение.</p>}</div>}{cart && <div className="current-cart"><span className="eyebrow">ТЕКУЩАЯ КОРЗИНА · {cart.mode === "demo" ? "ДЕМО" : "РЕАЛЬНАЯ"}</span><p>Версия {cart.version} · {cart.items.length} позиций</p>{cart.items.map(line => <div className="current-line" key={line.product_id}><span>{line.name} × {line.quantity}</span><strong>{money(line.line_total_amount, cart.currency)}</strong></div>)}<div className="current-total"><span>Итого</span><strong>{money(cart.total_amount, cart.currency)}</strong></div></div>}</div><div className="cart-footer"><span className="secure-symbol">◎</span> Добавление только после вашего подтверждения</div></aside>
    </div>
  </main>;
}

