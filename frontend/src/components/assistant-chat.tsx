"use client";

import { ChangeEvent, FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  api, ApiError, ensureConversation, errorMessage, newKey,
  type Asset, type AssistantOutput, type Capabilities, type Message,
  type Product, type Proposal, type Turn,
} from "@/lib/api";
import "./assistant-chat.css";

type Language = "auto" | "ru" | "kk" | "en";
type PendingRequest = {
  conversationId: string;
  key: string;
  text: string;
  asset_ids: string[];
  language: Language;
  allow_external_analysis: boolean;
  page_product_id?: number;
};
type Uploading = { id: string; name: string };
type Props = {
  open: boolean;
  onClose: () => void;
  pageProductId?: number;
  onCartChanged: () => void;
  capabilities: Capabilities | null;
};

const STORAGE = {
  conversation: "ekt_conversation_id",
  pending: "ekt_assistant_pending_turn",
  assets: "ekt_assistant_assets",
  selected: "ekt_assistant_selected_assets",
  confirm: "ekt_assistant_confirm_keys",
};
const TERMINAL_TURN = new Set<Turn["status"]>(["completed", "failed", "cancelled"]);
const TERMINAL_PROPOSAL = new Set<Proposal["status"]>(["applied", "rejected", "expired", "stale", "failed"]);
const ALLOWED_EXTENSIONS = new Set(["pdf", "docx", "xlsx", "jpg", "jpeg", "png", "txt", "csv"]);
const MAX_FILE_BYTES = 10 * 1024 * 1024;

function storageGet(key: string): string | null {
  try { return window.sessionStorage.getItem(key); } catch { return null; }
}
function storageSet(key: string, value: string | null) {
  try {
    if (value === null) window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, value);
  } catch { /* Private browsing may disable storage. */ }
}
function storedJson<T>(key: string, fallback: T): T {
  try { const value = storageGet(key); return value ? JSON.parse(value) as T : fallback; }
  catch { return fallback; }
}
function clock(seconds: number) {
  return new Date(seconds * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}
function dateTime(seconds: number) {
  return new Date(seconds * 1000).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
}
function money(amount: string | null, currency: string | null) {
  return amount === null ? "Цена не указана" : `${amount} ${currency || ""}`.trim();
}
function sameOriginCertificate(url: string): string | null {
  if (typeof window === "undefined" || !url.startsWith("/") || url.startsWith("//")) return null;
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.origin === window.location.origin && parsed.pathname.startsWith("/api/v1/certificates/")
      ? parsed.pathname + parsed.search : null;
  } catch { return null; }
}
function proposalStatus(status: Proposal["status"]) {
  return ({
    proposed: "Ожидает подтверждения", queued: "В очереди", executing: "Добавляем в корзину",
    applied: "Добавлено", rejected: "Отклонено", expired: "Срок истёк",
    stale: "Данные изменились", failed: "Ошибка", outcome_unknown: "Проверяем результат",
  })[status];
}
function assetStatus(status: Asset["status"]) {
  return ({ ready: "Готов", partial: "Частично прочитан", quarantined: "Проверка не пройдена" })[status];
}

function ProductMini({ product, demo }: { product: Product; demo: boolean }) {
  const certificates = (product as Product & { certificates?: { title: string; url: string }[] }).certificates || [];
  return <article className="assistant-chat-product">
    <div className="assistant-chat-product-top"><span>АРТ. {product.article_original}</span>{demo && <b>ДЕМО</b>}</div>
    <strong>{product.name}</strong>
    <div className="assistant-chat-product-price">{money(product.price_amount, product.price_currency)}</div>
    <p>{product.stock_status === "available" ? `Остаток: ${product.sellable_quantity ?? "не указан"}` : product.stock_status === "out_of_stock" ? "Нет в наличии" : "Остаток неизвестен"}</p>
    {product.attributes.slice(0, 4).map((attribute, index) => <p key={`${attribute.key}-${index}`}>
      {attribute.key}: {attribute.status === "conflict" ? "данные расходятся" : attribute.value || "неизвестно"}{attribute.unit && attribute.value ? ` ${attribute.unit}` : ""}
    </p>)}
    {certificates.map((certificate, index) => {
      const href = sameOriginCertificate(certificate.url);
      return href ? <a key={index} href={href} target="_blank" rel="noopener noreferrer">Сертификат: {certificate.title}</a>
        : <span key={index}>Сертификат: {certificate.title}</span>;
    })}
    {product.warnings.length > 0 && <p className="assistant-chat-warning">{product.warnings.join(" · ")}</p>}
  </article>;
}

export default function AssistantChat({ open, onClose, pageProductId, onCartChanged, capabilities }: Props) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [lastOutput, setLastOutput] = useState<AssistantOutput | null>(null);
  const [currentTurn, setCurrentTurn] = useState<Turn | null>(null);
  const [currentText, setCurrentText] = useState("");
  const [pending, setPending] = useState<PendingRequest | null>(null);
  const [draft, setDraft] = useState("");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [uploading, setUploading] = useState<Uploading[]>([]);
  const [language, setLanguage] = useState<Language>("auto");
  const [consent, setConsent] = useState(false);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [booting, setBooting] = useState(false);
  const [sending, setSending] = useState(false);
  const [proposalBusy, setProposalBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const initialized = useRef(false);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const fileInput = useRef<HTMLInputElement>(null);
  const textInput = useRef<HTMLTextAreaElement>(null);
  const panel = useRef<HTMLElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const uploadControllers = useRef(new Map<string, AbortController>());

  const refresh = useCallback(async (id: string) => {
    const [latestMessages, turns] = await Promise.all([api.messages(id), api.turns(id)]);
    setMessages(latestMessages);
    const newestCompleted = [...turns].reverse().find(turn => turn.status === "completed" && turn.output);
    if (newestCompleted?.output) {
      setLastOutput(newestCompleted.output);
      if (newestCompleted.output.proposal) setProposal(newestCompleted.output.proposal);
    }
    const active = [...turns].reverse().find(turn => !TERMINAL_TURN.has(turn.status));
    setCurrentTurn(active || null);
    return active || null;
  }, []);

  useEffect(() => {
    if (!open || initialized.current) return;
    initialized.current = true;
    let cancelled = false;
    const boot = async () => {
      setBooting(true); setError("");
      try {
        const previousId = storageGet(STORAGE.conversation);
        const conversation = await ensureConversation();
        const id = conversation.id;
        const active = await refresh(id);
        if (previousId !== id) {
          setMessages([]); setLastOutput(null); setProposal(null);
          storageSet(STORAGE.pending, null); storageSet(STORAGE.assets, null); storageSet(STORAGE.selected, null);
        }
        if (cancelled) return;
        setConversationId(id);
        const saved = storedJson<PendingRequest | null>(STORAGE.pending, null);
        if (saved?.conversationId === id && !active) { setPending(saved); setDraft(saved.text); }
        else if (active) storageSet(STORAGE.pending, null);
        const savedAssets = storedJson<string[]>(STORAGE.assets, []).slice(0, 3);
        const retrieved = await Promise.all(savedAssets.map(assetId => api.asset(assetId).catch(() => null)));
        if (!cancelled) {
          setAssets(retrieved.filter((asset): asset is Asset => asset !== null));
          setSelectedIds(storedJson<string[]>(STORAGE.selected, []).filter(assetId => retrieved.some(asset => asset?.id === assetId)));
        }
      } catch (caught) {
        if (!cancelled) { setError(errorMessage(caught)); initialized.current = false; }
      } finally { if (!cancelled) setBooting(false); }
    };
    void boot();
    return () => { cancelled = true; initialized.current = false; };
  }, [open, refresh]);

  useEffect(() => { if (conversationId) storageSet(STORAGE.assets, JSON.stringify(assets.map(asset => asset.id))); }, [assets, conversationId]);
  useEffect(() => { if (conversationId) storageSet(STORAGE.selected, JSON.stringify(selectedIds)); }, [selectedIds, conversationId]);
  useEffect(() => { scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: "smooth" }); }, [messages, currentTurn?.status, pending, lastOutput]);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    window.setTimeout(() => textInput.current?.focus(), 0);
    const keydown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") { closeRef.current(); return; }
      if (event.key !== "Tab" || !panel.current) return;
      const focusable = Array.from(panel.current.querySelectorAll<HTMLElement>("button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]"));
      if (!focusable.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.removeEventListener("keydown", keydown); previous?.focus(); };
  }, [open]);

  useEffect(() => {
    if (!currentTurn || TERMINAL_TURN.has(currentTurn.status) || !conversationId) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const latest = await api.turn(currentTurn.id);
        if (cancelled) return;
        setCurrentTurn(latest);
        if (TERMINAL_TURN.has(latest.status)) {
          window.clearInterval(timer);
          setCurrentText("");
          if (latest.output) {
            setLastOutput(latest.output);
            if (latest.output.proposal) setProposal(latest.output.proposal);
          }
          if (latest.status === "failed") setError(`Ответ не завершён${latest.error_code ? `: ${latest.error_code}` : ""}.`);
          await refresh(conversationId);
        }
      } catch (caught) { if (!cancelled) setError(errorMessage(caught)); }
    }, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [currentTurn?.id, currentTurn?.status, conversationId, refresh]);

  useEffect(() => {
    if (!proposal || proposal.status === "proposed" || TERMINAL_PROPOSAL.has(proposal.status)) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const latest = await api.proposal(proposal.id);
        if (cancelled) return;
        setProposal(latest);
        if (TERMINAL_PROPOSAL.has(latest.status)) {
          window.clearInterval(timer);
          if (latest.status === "applied") onCartChanged();
        }
      } catch (caught) { if (!cancelled) setError(errorMessage(caught)); }
    }, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [proposal?.id, proposal?.status, onCartChanged]);

  async function addFiles(event: ChangeEvent<HTMLInputElement>) {
    const chosen = Array.from(event.target.files || []);
    event.target.value = "";
    const remaining = Math.max(0, 3 - assets.length - uploading.length);
    if (chosen.length > remaining) setError("К одному сообщению можно прикрепить не более трёх файлов.");
    for (const file of chosen.slice(0, remaining)) {
      const extension = file.name.split(".").pop()?.toLowerCase() || "";
      if (!ALLOWED_EXTENSIONS.has(extension) || file.size > MAX_FILE_BYTES || file.size === 0) {
        setError(`Не удалось прикрепить «${file.name}»: проверьте формат и размер до 10 МБ.`);
        continue;
      }
      const temporaryId = newKey();
      const controller = new AbortController();
      uploadControllers.current.set(temporaryId, controller);
      setUploading(items => [...items, { id: temporaryId, name: file.name }]);
      try {
        const uploaded = await api.upload(file, controller.signal);
        setAssets(items => [...items, uploaded]);
        if (uploaded.status !== "quarantined") setSelectedIds(ids => [...ids, uploaded.id]);
        else setError(`Файл «${file.name}» не прошёл проверку и не будет отправлен.`);
      } catch (caught) {
        if (!controller.signal.aborted) setError(errorMessage(caught));
      } finally {
        uploadControllers.current.delete(temporaryId);
        setUploading(items => items.filter(item => item.id !== temporaryId));
      }
    }
  }

  async function removeAsset(asset: Asset) {
    try {
      await api.deleteAsset(asset.id);
      setAssets(items => items.filter(item => item.id !== asset.id));
      setSelectedIds(ids => ids.filter(id => id !== asset.id));
    } catch (caught) { setError(errorMessage(caught)); }
  }

  async function submit(request: PendingRequest) {
    setSending(true); setError("");
    try {
      const result = await api.submitTurn(request.conversationId, request.text, request.key, {
        asset_ids: request.asset_ids, language: request.language,
        allow_external_analysis: request.allow_external_analysis,
        page_product_id: request.page_product_id,
      });
      storageSet(STORAGE.pending, null);
      setPending(null);
      setCurrentTurn(result.turn);
      setCurrentText(request.text);
      setDraft(""); setSelectedIds([]); setConsent(false);
      if (TERMINAL_TURN.has(result.turn.status)) {
        setCurrentText("");
        if (result.turn.output) {
          setLastOutput(result.turn.output);
          if (result.turn.output.proposal) setProposal(result.turn.output.proposal);
        }
        await refresh(request.conversationId);
      }
    } catch (caught) {
      setError(errorMessage(caught));
      const definitive = caught instanceof ApiError && [400, 401, 403, 404, 413, 415, 422].includes(caught.status);
      if (definitive) { storageSet(STORAGE.pending, null); setPending(null); }
    } finally { setSending(false); }
  }

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!conversationId || sending || currentTurn || pending || uploading.length) return;
    const selected = assets.filter(asset => selectedIds.includes(asset.id));
    if (selected.some(asset => asset.status === "quarantined")) return;
    const text = draft.trim() || (selected.length ? "Проанализируй прикреплённые файлы и помоги подобрать позиции." : "");
    if (!text) return;
    const request: PendingRequest = {
      conversationId, key: newKey(), text, asset_ids: selected.map(asset => asset.id), language,
      allow_external_analysis: consent && capabilities?.llm === "openai" && selected.length > 0,
      ...(pageProductId ? { page_product_id: pageProductId } : {}),
    };
    storageSet(STORAGE.pending, JSON.stringify(request));
    setPending(request);
    void submit(request);
  }

  function editPending() {
    if (!pending || sending) return;
    setDraft(pending.text);
    storageSet(STORAGE.pending, null);
    setPending(null);
  }

  async function cancel() {
    if (!currentTurn || !conversationId) return;
    try {
      const latest = await api.cancelTurn(currentTurn.id);
      setCurrentTurn(latest);
      setCurrentText("");
      await refresh(conversationId);
    } catch (caught) { setError(errorMessage(caught)); }
  }

  async function confirm() {
    if (!proposal || proposal.status !== "proposed" || proposalBusy) return;
    setProposalBusy(true); setError("");
    const keys = storedJson<Record<string, string>>(STORAGE.confirm, {});
    const key = keys[proposal.id] || newKey();
    storageSet(STORAGE.confirm, JSON.stringify({ ...keys, [proposal.id]: key }));
    try {
      const latest = await api.confirm(proposal.id, proposal.version, key);
      setProposal(latest);
      if (latest.status === "applied") onCartChanged();
    } catch (caught) {
      setError(errorMessage(caught));
      try {
        const latest = await api.proposal(proposal.id);
        setProposal(latest);
        if (latest.status === "applied") onCartChanged();
      } catch { /* Same key is retained for an uncertain result. */ }
    } finally { setProposalBusy(false); }
  }

  async function reject() {
    if (!proposal || proposal.status !== "proposed" || proposalBusy) return;
    setProposalBusy(true); setError("");
    try { setProposal(await api.reject(proposal.id)); }
    catch (caught) { setError(errorMessage(caught)); }
    finally { setProposalBusy(false); }
  }

  function composeKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  if (!open) return null;
  const selected = assets.filter(asset => selectedIds.includes(asset.id));
  const uploadReady = capabilities?.upload === "ready";
  const llmReady = capabilities?.llm === "openai";
  const demo = capabilities?.catalog === "synthetic_demo" || capabilities?.cart === "synthetic_demo";
  const canSend = Boolean(conversationId && !sending && !pending && !currentTurn && !uploading.length
    && (draft.trim() || selected.length) && !selected.some(asset => asset.status === "quarantined"));

  return <div className="assistant-chat-overlay" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="assistant-chat-panel" ref={panel} role="dialog" aria-modal="true" aria-label="Помощник ЭКТ">
      <header className="assistant-chat-header">
        <div className="assistant-chat-avatar" aria-hidden="true">Э</div>
        <div><strong>Помощник ЭКТ</strong><span>{llmReady ? "Помогаю с подбором" : "По данным каталога"}</span></div>
        <button type="button" className="assistant-chat-close" onClick={onClose} aria-label="Закрыть помощника">×</button>
      </header>

      <div className="assistant-chat-scroll" ref={scroll} aria-live="polite" aria-relevant="additions text">
        {booting && <p className="assistant-chat-system">Восстанавливаем диалог…</p>}
        {messages.length === 0 && !booting && <div className="assistant-chat-welcome">
          <div className="assistant-chat-welcome-icon">✳</div>
          <strong>Что нужно подобрать?</strong>
          <p>Опишите товар, артикул или прикрепите список. Покажу только данные из доступных источников.</p>
        </div>}
        {messages.map(message => <div key={message.id} className={`assistant-chat-message assistant-chat-${message.role}`}>
          <div className="assistant-chat-bubble">{message.text}</div>
          <time dateTime={new Date(message.created_at * 1000).toISOString()}>{message.role === "user" ? "Вы" : "Помощник"} · {clock(message.created_at)}</time>
        </div>)}
        {pending && <div className="assistant-chat-message assistant-chat-user">
          <div className="assistant-chat-bubble">{pending.text}</div>
          <time>{sending ? "Отправляем…" : "Ответ не подтверждён сервером"}</time>
        </div>}
        {currentTurn && !TERMINAL_TURN.has(currentTurn.status) && <div className="assistant-chat-message assistant-chat-assistant">
          {currentText && <p className="assistant-chat-current-text">Ваш запрос: {currentText}</p>}
          <div className="assistant-chat-bubble assistant-chat-typing"><span aria-hidden="true">● ● ●</span> {currentTurn.status === "queued" ? "В очереди" : "Проверяем данные"}</div>
          <button type="button" className="assistant-chat-link" onClick={() => void cancel()}>Остановить ответ</button>
        </div>}
        {lastOutput && <div className="assistant-chat-facts">
          <div className="assistant-chat-facts-title">Данные последнего ответа {demo && <span>ДЕМО</span>}</div>
          {lastOutput.products.map(product => <ProductMini key={product.id} product={product} demo={demo}/>)}
          {lastOutput.unknowns.length > 0 && <p>Нужно уточнить: {lastOutput.unknowns.join(" · ")}</p>}
          {lastOutput.warnings?.length > 0 && <p className="assistant-chat-warning">{lastOutput.warnings.join(" · ")}</p>}
        </div>}
        {proposal && <div className="assistant-chat-proposal">
          <div className="assistant-chat-proposal-heading"><strong>Предложение для корзины</strong><span>{proposalStatus(proposal.status)}</span></div>
          {proposal.mode === "demo" && <p className="assistant-chat-demo">Демонстрационная корзина. Реальный заказ не создаётся.</p>}
          <div className="assistant-chat-proposal-lines">{proposal.items.map(line => <div key={line.product_id}>
            <span>{line.name}<small>{line.article_original} · {line.quantity} × {money(line.unit_price_amount, proposal.currency)}</small></span>
            <strong>{money(line.line_total_amount, proposal.currency)}</strong>
          </div>)}</div>
          <div className="assistant-chat-proposal-total"><span>Итого</span><strong>{money(proposal.total_amount, proposal.currency)}</strong></div>
          <p className="assistant-chat-proposal-meta">Версия {proposal.version} · корзина {proposal.cart_version} · до {dateTime(proposal.expires_at)}</p>
          {proposal.status === "proposed" && <div className="assistant-chat-proposal-actions">
            <button type="button" onClick={() => void confirm()} disabled={proposalBusy}>Подтвердить добавление</button>
            <button type="button" onClick={() => void reject()} disabled={proposalBusy}>Отклонить</button>
          </div>}
          {proposal.status === "outcome_unknown" && <button type="button" className="assistant-chat-link" onClick={() => void api.proposal(proposal.id).then(setProposal).catch(caught => setError(errorMessage(caught)))}>Проверить результат</button>}
          {(["expired", "stale", "failed"] as Proposal["status"][]).includes(proposal.status) && <p className="assistant-chat-warning">Проверьте данные и создайте новое предложение.</p>}
          {proposal.status === "applied" && proposal.cart_url?.startsWith("/") && !proposal.cart_url.startsWith("//") && <a href={proposal.cart_url}>Открыть корзину →</a>}
        </div>}
        {error && <div className="assistant-chat-alert" role="alert">{error}</div>}
        {notice && <div className="assistant-chat-notice" role="status">{notice}</div>}
      </div>

      <div className="assistant-chat-composer">
        {assets.length > 0 && <div className="assistant-chat-assets" aria-label="Загруженные файлы">{assets.map(asset => <div className="assistant-chat-asset" key={asset.id}>
          <label><input type="checkbox" checked={selectedIds.includes(asset.id)} disabled={asset.status === "quarantined" || Boolean(pending)} onChange={event => setSelectedIds(ids => event.target.checked ? [...ids, asset.id] : ids.filter(id => id !== asset.id))}/>
            <span className="assistant-chat-asset-name">{asset.name}</span><small>{assetStatus(asset.status)}</small></label>
          <button type="button" onClick={() => void removeAsset(asset)} disabled={Boolean(pending)} aria-label={`Удалить файл ${asset.name}`}>×</button>
          {asset.warnings.length > 0 && <p>{asset.warnings.join(" · ")}</p>}
        </div>)}</div>}
        {uploading.map(item => <div className="assistant-chat-uploading" key={item.id}>{item.name} · загружаем…</div>)}
        {llmReady && selected.length > 0 && <label className="assistant-chat-consent"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} disabled={Boolean(pending)}/><span>Разрешаю передать содержимое выбранных файлов в OpenAI для анализа. Без разрешения файлы обрабатываются локально.</span></label>}
        <form onSubmit={send}>
          <label className="assistant-chat-sr-only" htmlFor="assistant-chat-text">Сообщение помощнику</label>
          <textarea id="assistant-chat-text" ref={textInput} rows={2} maxLength={8000} value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={composeKey} placeholder="Напишите, что нужно найти…" disabled={!conversationId || Boolean(pending) || Boolean(currentTurn)}/>
          <div className="assistant-chat-controls">
            <div className="assistant-chat-controls-left">
              <input ref={fileInput} className="assistant-chat-sr-only" type="file" multiple accept=".pdf,.docx,.xlsx,.jpg,.jpeg,.png,.txt,.csv" onChange={event => void addFiles(event)}/>
              <button type="button" className="assistant-chat-attach" onClick={() => fileInput.current?.click()} disabled={!uploadReady || assets.length + uploading.length >= 3 || Boolean(pending)} title={uploadReady ? "Прикрепить до трёх файлов" : "Загрузка файлов не подключена"} aria-label="Прикрепить файлы">⌁</button>
              <label className="assistant-chat-language">Язык <select value={language} onChange={event => setLanguage(event.target.value as Language)} disabled={Boolean(pending)}><option value="auto">Авто</option><option value="ru">Русский</option><option value="kk">Қазақша</option><option value="en">English</option></select></label>
            </div>
            <button className="assistant-chat-send" type="submit" disabled={!canSend} aria-label="Отправить сообщение">↑</button>
          </div>
        </form>
        {pending && !sending && <div className="assistant-chat-retry"><span>Неизвестно, дошёл ли запрос. Повтор использует тот же ключ.</span><button type="button" onClick={() => void submit(pending)}>Повторить</button><button type="button" onClick={editPending}>Редактировать</button></div>}
        <p className="assistant-chat-disclaimer">{demo ? "Демо-данные не являются реальным предложением ЭКТ. " : ""}Проверяйте характеристики и цену перед заказом.</p>
      </div>
    </section>
  </div>;
}
