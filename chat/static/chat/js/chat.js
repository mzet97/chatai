// Tela principal do chat (redesign).
import { api, csrfToken, newIdempotencyKey } from "./api.js";
import { readSSE } from "./sse.js";
import { renderMarkdown } from "./markdown.js";
import { initTheme } from "./theme.js";

initTheme();

const $ = (id) => document.getElementById(id);
const listNav = $("conv-nav");
const listEl = $("conv-list");
const msgEl = $("messages");
const statusEl = $("status");
const inputEl = $("input");
const composerEl = $("composer");
const sendBtn = $("btn-send");
const stopBtn = $("btn-stop");
const emptyState = $("empty-state");
const scrollPill = $("scroll-pill");
const levelSelect = $("level-select");
const streamToggle = $("stream-toggle");
const streamMenuItem = $("stream-menuitem");

let convUuid = location.pathname.startsWith("/c/") ? location.pathname.split("/")[2] : null;
let currentConv = null;
let levelSaving = false;
let pendingLevel = null; // escolhido antes da primeira conversa existir
let streamSaving = false;
let pendingMode = null; // "streaming" | "complete", antes da primeira conversa existir
let levelSupport = { state: "unknown", reason: "" };
let activeRun = null;
let aborter = null;
let composing = false;
let searchTimer = null;
let lastOpener = null;
const drafts = new Map(); // rascunho por conversa (memória da aba; nunca sai do dispositivo)

function icon(name, cls) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", cls ? "ic " + cls : "ic");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", "#i-" + name);
  svg.append(use);
  return svg;
}

function setStatus(t) {
  statusEl.textContent = t || "";
}

function nearBottom() {
  return msgEl.scrollHeight - msgEl.scrollTop - msgEl.clientHeight < 120;
}

/* ---------- sidebar ---------- */
function groupOf(iso) {
  const d = new Date(iso);
  const now = new Date();
  const days = Math.floor((now - d) / 86400000);
  if (days <= 0 && now.getDate() === d.getDate()) return "Hoje";
  if (days < 7) return "Últimos 7 dias";
  return "Anteriores";
}

async function loadConversations(q = "") {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  let data;
  try {
    data = await api("/api/conversations?" + params.toString());
  } catch (e) {
    setStatus("Não foi possível carregar a lista: " + e.message);
    return;
  }
  listEl.innerHTML = "";
  document.querySelectorAll(".conv-group").forEach((g) => g.remove());
  $("conv-empty").hidden = data.results.length > 0;
  if (data.results.length === 0) {
    const empty = $("conv-empty");
    empty.textContent = "";
    if (!q) {
      empty.textContent = "Nenhuma conversa ainda.";
    } else {
      empty.textContent = "Nada para “" + q + "”. ";
      const clearBtn = document.createElement("button");
      clearBtn.id = "conv-clear-search";
      clearBtn.className = "linklike";
      clearBtn.type = "button";
      clearBtn.textContent = "Limpar busca";
      clearBtn.addEventListener("click", () => {
        $("search").value = "";
        loadConversations("");
        $("search").focus();
      });
      empty.append(clearBtn);
    }
  }
  $("search-clear").hidden = !q;
  let lastGroup = null;
  for (const c of data.results) {
    const g = groupOf(c.updated_at);
    if (g !== lastGroup) {
      lastGroup = g;
      const h = document.createElement("div");
      h.className = "conv-group";
      const h3 = document.createElement("h3");
      h3.textContent = g;
      h3.id = "grp-" + data.results.indexOf(c);
      h.append(h3);
      listNav.insertBefore(h, listEl);
      listEl.setAttribute("aria-labelledby", h3.id);
    }
    listEl.append(convRow(c));
  }
}

function convRow(c) {
  const li = document.createElement("li");
  li.className = "conv-row";
  if (c.uuid === convUuid) li.setAttribute("aria-current", "true");
  const open = document.createElement("button");
  open.type = "button";
  open.className = "conv-open";
  open.textContent = c.title;
  open.title = c.title;
  open.setAttribute("aria-label", "Abrir conversa: " + c.title);
  open.addEventListener("click", () => openConversation(c.uuid));
  const menuBtn = document.createElement("button");
  menuBtn.type = "button";
  menuBtn.className = "icon-btn sm conv-menu-btn";
  menuBtn.setAttribute("aria-label", "Ações de: " + c.title);
  menuBtn.setAttribute("aria-expanded", "false");
  menuBtn.append(icon("dots"));
  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    rowMenu(menuBtn, c);
  });
  li.append(open, menuBtn);
  return li;
}

function rowMenu(anchor, c) {
  closePops();
  const pop = document.createElement("div");
  pop.className = "popover menu rowmenu";
  pop.setAttribute("role", "menu");
  const mk = (label, fn) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.setAttribute("role", "menuitem");
    b.addEventListener("click", () => {
      pop.remove();
      anchor.setAttribute("aria-expanded", "false");
      fn();
    });
    return b;
  };
  pop.append(
    mk("Renomear", () => {
      if (c.uuid !== convUuid) location.href = "/c/" + c.uuid + "/";
      else openConvDialog();
    }),
    mk(c.archived ? "Desarquivar" : "Arquivar", async () => {
      await api(`/api/conversations/${c.uuid}`, { method: "PATCH", body: { archived: !c.archived } });
      await loadConversations($("search").value.trim());
    }),
    mk("Excluir…", () => askDelete(c)),
  );
  document.body.append(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.position = "fixed";
  pop.style.top = Math.min(r.bottom + 4, window.innerHeight - 180) + "px";
  pop.style.left = Math.min(r.left, window.innerWidth - 260) + "px";
  anchor.setAttribute("aria-expanded", "true");
  pop.querySelector("button").focus();
  pop._anchor = anchor;
  openPop = pop;
}

let openPop = null;
function closePops() {
  document.querySelectorAll(".popover.rowmenu").forEach((p) => p.remove());
  for (const id of ["model-pop", "actions-pop", "tools-pop", "sources-pop", "thinking-pop"]) {
    const el = $(id);
    if (el) el.hidden = true;
  }
  $("model-btn").setAttribute("aria-expanded", "false");
  $("tools-btn").setAttribute("aria-expanded", "false");
  $("sources-btn").setAttribute("aria-expanded", "false");
  $("actions-btn").setAttribute("aria-expanded", "false");
  if (openPop && openPop._anchor) openPop._anchor.setAttribute("aria-expanded", "false");
  openPop = null;
}

function openConversation(uuid) {
  if (activeRun) {
    setStatus("Aguarde ou interrompa a geração atual antes de trocar de conversa.");
    return;
  }
  saveDraft();
  location.href = "/c/" + uuid + "/";
}

/* ---------- mensagens ---------- */
function metaLine(m, model) {
  const who = m.role === "user" ? "Você" : "Assistente";
  const when = new Date(m.created_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  let s = who + " · " + when;
  if (model) s += " · " + model;
  if (m.state && m.state !== "ok" && m.state !== "partial") s += " · " + m.state;
  return s;
}

function messageNode(m, model) {
  const turn = document.createElement("article");
  turn.className = "turn";
  turn.dataset.uuid = m.uuid;
  const head = document.createElement("div");
  head.className = "turn-head";
  const role = document.createElement("span");
  role.className = "turn-role";
  role.textContent = m.role === "user" ? "Você" : "Assistente";
  const meta = document.createElement("span");
  meta.className = "turn-meta";
  meta.textContent = new Date(m.created_at).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) + (model ? " · " + model : "");
  head.append(role, meta);
  turn.append(head);
  if (m.role === "user") {
    if (m.images && m.images.length) turn.append(userImagesNode(m.images));
    const b = document.createElement("div");
    b.className = "msg-user";
    b.textContent = m.text;
    turn.append(b);
  } else {
    const body = document.createElement("div");
    body.className = "msg-body";
    body.append(renderMarkdown(m.text));
    turn.append(body);
    turn.append(turnActions(m, body));
    if (m.run_id) {
      turn.dataset.runId = m.run_id;
      addDetailsButton(turn, m.run_id); // histórico: inspeciona a execução real
    }
  }
  return turn;
}

function turnActions(m, body) {
  const bar = document.createElement("div");
  bar.className = "turn-actions";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "action-btn";
  copy.append(icon("copy", "sm"));
  copy.append(document.createTextNode("Copiar resposta"));
  copy.setAttribute("aria-label", "Copiar resposta");
  copy.addEventListener("click", () => {
    navigator.clipboard
      .writeText(m.text || body.textContent || "")
      .then(() => {
        copy.lastChild.textContent = "Copiado";
        setTimeout(() => {
          copy.lastChild.textContent = "Copiar resposta";
        }, 1600);
      })
      .catch(() => setStatus("Não foi possível copiar."));
  });
  bar.append(copy);
  if (["failed", "cancelled", "interrupted"].includes(m.state)) {
    const rb = document.createElement("button");
    rb.type = "button";
    rb.className = "action-btn";
    rb.textContent = "Repetir (pode gerar novo custo)";
    rb.addEventListener("click", retryLast);
    bar.append(rb);
  }
  return bar;
}

function appendMessage(m, model, { scroll = true } = {}) {
  const stick = nearBottom();
  const node = messageNode(m, model);
  let thread = msgEl.querySelector(".thread");
  if (!thread) {
    thread = document.createElement("div");
    thread.className = "thread";
    msgEl.append(thread);
  }
  thread.append(node);
  if (scroll && stick) msgEl.scrollTop = msgEl.scrollHeight;
  return node;
}

/* ---------- conversa ---------- */
function skeleton() {
  msgEl.innerHTML = "";
  const thread = document.createElement("div");
  thread.className = "thread";
  const sk = document.createElement("div");
  sk.className = "skeleton";
  sk.setAttribute("aria-hidden", "true");
  for (const w of ["90%", "100%", "75%"]) {
    const s = document.createElement("span");
    s.style.width = w;
    sk.append(s);
  }
  thread.append(sk);
  msgEl.append(thread);
}

async function loadConversation() {
  msgEl.innerHTML = "";
  restoreDraft();
  if (!convUuid) {
    $("chat-title").textContent = "Nova conversa";
    setModelLabel(null);
    applyLevelState(null);
    applyStreamState(null);
    emptyState.hidden = false;
    $("btn-export-md").href = "#";
    $("btn-export-json").href = "#";
    return;
  }
  emptyState.hidden = true;
  skeleton();
  setStatus("Carregando…");
  const conv = await api(`/api/conversations/${convUuid}`);
  currentConv = conv;
  $("chat-title").textContent = conv.title;
  setModelLabel(conv.preferred_model || null);
  applyLevelState(conv);
  applyStreamState(conv);
  refreshToolsLabel();
  refreshSourcesLabel();
  $("btn-export-md").href = `/api/conversations/${convUuid}/export?format=markdown`;
  $("btn-export-json").href = `/api/conversations/${convUuid}/export?format=json`;
  $("f-title").value = conv.title;
  $("f-model").value = conv.preferred_model || "";
  $("f-system").value = conv.system_prompt || "";
  $("f-maxout").value = conv.max_output_tokens || "";
  $("f-budget").value = conv.input_budget || "";
  $("f-strict").checked = !!conv.strict_mode;
  msgEl.innerHTML = "";
  let page = 1;
  let pages = 1;
  do {
    const data = await api(`/api/conversations/${convUuid}/messages?page=${page}`);
    pages = data.num_pages;
    for (const m of data.results) appendMessage(m, null, { scroll: false });
    page++;
  } while (page <= pages);
  msgEl.scrollTop = msgEl.scrollHeight;
  updateScrollPill();
  loadHistoricalCitations();
  if (conv.has_active_run) {
    setStatus("Há uma geração ativa (outra aba?). Leitura liberada; envio bloqueado.");
    sendBtn.disabled = true;
  } else {
    setStatus("");
    syncSendState();
  }
}

async function refreshKeyBanner() {
  try {
    const s = await api("/api/settings");
    const missing = s.key_origin === "none";
    $("key-banner").hidden = !missing;
    // O banner desloca o histórico: refixa no fim se já estava lá.
    if (nearBottom()) msgEl.scrollTop = msgEl.scrollHeight;
    if (s.base_url && !s.base_url.includes("api.anthropic.com")) {
      try {
        $("privacy-dest").textContent = new URL(s.base_url).host;
      } catch {
        /* mantém padrão */
      }
    }
  } catch {
    /* banner é contextual; falha não bloqueia */
  }
}

/* ---------- compositor ---------- */
function autogrow() {
  inputEl.style.height = "auto";
  const line = parseFloat(getComputedStyle(inputEl).lineHeight) || 26;
  inputEl.style.height = Math.min(inputEl.scrollHeight, line * 8) + "px";
}

function syncSendState() {
  const hasText = inputEl.value.trim().length > 0;
  const hasImages = pendingImages.length > 0;
  sendBtn.disabled = (!hasText && !hasImages) || !!activeRun || uploading > 0 || levelSaving || streamSaving || (currentConv && currentConv.has_active_run);
  stopBtn.hidden = !activeRun;
  const note = $("durable-note");
  if (note) note.hidden = !activeRun; // M2: execução vive no servidor
  syncLevelLock();
  syncStreamLock();
}

/* ---------- anexo visível de imagens (M3; separado do Documento RAG) ---------- */
const attachBtn = $("btn-attach");
const fileInput = $("file-image");
const stripEl = $("attach-strip");
const MAX_ATTACH = 4;
const MAX_ATTACH_BYTES = 5 * 1024 * 1024;
const IMG_OK = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
let pendingImages = []; // {media_type,data,thumbnail,width,height,name}
let uploading = 0;

function userImagesNode(images) {
  const wrap = document.createElement("div");
  wrap.className = "msg-images";
  for (const im of images) {
    if (!im.thumbnail) continue;
    const img = document.createElement("img");
    img.src = im.thumbnail;
    img.alt = `Imagem anexada (${im.width || "?"}×${im.height || "?"})`;
    img.title = `${im.media_type || "imagem"} · ${im.width || "?"}×${im.height || "?"}`;
    img.loading = "lazy";
    wrap.append(img);
  }
  return wrap;
}

function renderStrip() {
  stripEl.innerHTML = "";
  stripEl.hidden = pendingImages.length === 0;
  pendingImages.forEach((item, idx) => {
    const box = document.createElement("div");
    box.className = "attach-thumb";
    const img = document.createElement("img");
    img.src = item.thumbnail;
    img.alt = `Anexo ${idx + 1}: ${item.name}`;
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "attach-rm";
    rm.textContent = "Remover";
    rm.setAttribute("aria-label", `Remover ${item.name}`);
    rm.addEventListener("click", () => {
      pendingImages.splice(idx, 1);
      renderStrip();
      syncSendState();
      inputEl.focus();
    });
    box.append(img, rm);
    stripEl.append(box);
  });
}

function isImageFile(f) {
  if (IMG_OK.has(f.type)) return true;
  return /\.(jpe?g|png|webp|gif)$/i.test(f.name || "");
}

async function uploadImageFile(file) {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch("/api/images", {
    method: "POST",
    headers: { "X-CSRFToken": csrfToken() },
    credentials: "same-origin",
    body: form,
  });
  const body = await resp.json().catch(() => ({}));
  if (resp.status === 403) throw new Error("Sessão expirada ou CSRF inválido. Recarregue a página.");
  if (!resp.ok) throw new Error(body.message || `HTTP ${resp.status}`);
  return { ...body, name: file.name };
}

async function addImageFiles(files) {
  const imgs = [...files].filter(isImageFile);
  if ([...files].length && !imgs.length) {
    setStatus("Somente arquivos de imagem (JPEG, PNG, WebP, GIF).");
    return;
  }
  for (const f of imgs) {
    if (pendingImages.length >= MAX_ATTACH) {
      setStatus(`Máximo de ${MAX_ATTACH} imagens por mensagem.`);
      break;
    }
    if (f.size > MAX_ATTACH_BYTES) {
      setStatus(`“${f.name}” excede 5 MiB e foi ignorado.`);
      continue;
    }
    uploading += 1;
    syncSendState();
    try {
      pendingImages.push(await uploadImageFile(f));
      renderStrip();
      setStatus("");
    } catch (e) {
      setStatus(`Falha ao anexar “${f.name}”: ${e.message}`);
    } finally {
      uploading -= 1;
      syncSendState();
    }
  }
}

function clearAttachments() {
  pendingImages = [];
  renderStrip();
}

/* ---------- modo de entrega (Streaming ligado/desligado; vale na próxima resposta) ---------- */
function currentMode() {
  return (currentConv && currentConv.response_mode) || pendingMode || "streaming";
}

function applyStreamState(conv) {
  const mode = (conv && conv.response_mode) || pendingMode || "streaming";
  const on = mode !== "complete";
  streamToggle.checked = on;
  streamMenuItem.setAttribute("aria-checked", String(on));
  streamMenuItem.textContent = on ? "Streaming: ativado" : "Streaming: desativado";
  $("stream-wrap").title = on
    ? "Exibe a resposta conforme ela é gerada."
    : "A resposta será exibida ao terminar.";
  syncStreamLock();
}

function syncStreamLock() {
  const busy = streamSaving || !!activeRun || (currentConv && currentConv.has_active_run);
  streamToggle.disabled = busy;
  streamMenuItem.setAttribute("aria-disabled", String(busy));
}

async function saveStream(mode, previous) {
  if (!convUuid) {
    pendingMode = mode; // sem conversa: guarda para aplicar na criação
    applyStreamState(null);
    setStatus(mode === "complete"
      ? "Streaming desligado para a próxima conversa. A resposta será exibida ao terminar."
      : "Streaming ativado para a próxima conversa.");
    return;
  }
  streamSaving = true;
  syncSendState();
  try {
    const updated = await api(`/api/conversations/${convUuid}`, {
      method: "PATCH",
      body: { response_mode: mode },
    });
    currentConv = updated;
    pendingMode = null;
    applyStreamState(currentConv);
    setStatus(mode === "complete"
      ? "Streaming desligado. A resposta será exibida ao terminar. Vale nas próximas respostas."
      : "Streaming ativado. Exibe a resposta conforme ela é gerada. Vale nas próximas respostas.");
  } catch (e) {
    // 409 devolve o valor vigente; rascunho intacto (nunca tocamos no input).
    applyStreamState({ response_mode: (e.data && e.data.response_mode) || previous });
    setStatus("Não foi possível salvar o modo: " + (e.data && e.data.message ? e.data.message : e.message));
  } finally {
    streamSaving = false;
    syncSendState();
  }
}

streamToggle.addEventListener("change", () => {
  const previous = currentMode();
  saveStream(streamToggle.checked ? "streaming" : "complete", previous);
});
streamMenuItem.addEventListener("click", () => {
  if (streamMenuItem.getAttribute("aria-disabled") === "true") return;
  const previous = currentMode();
  const next = previous === "complete" ? "streaming" : "complete";
  closePops();
  saveStream(next, previous);
});

/* ---------- nível de variação (Baixo/Médio/Alto; números vivem no backend) ---------- */
function applyLevelState(conv) {
  const level = (conv && conv.temperature_level) || pendingLevel || "medium";
  levelSelect.value = level;
  levelSupport = (conv && conv.temperature_support) || { state: "unknown", reason: "" };
  if (conv && !levelSupport.reason && levelSupport.state !== "supported") {
    levelSupport = { state: "unknown", reason: "Suporte a este ajuste ainda não confirmado." };
  }
  syncLevelLock();
}

function syncLevelLock() {
  const busy = levelSaving || !!activeRun || (currentConv && currentConv.has_active_run);
  // Sem conversa não há modelo avaliado: permite escolher (vira pendingLevel).
  const blocked = !!convUuid && levelSupport.state !== "supported";
  levelSelect.disabled = busy || blocked;
  const reason = busy && !blocked
    ? "Disponível após concluir ou interromper a resposta."
    : (convUuid ? levelSupport.reason : "");
  levelSelect.title = reason || "";
  if (reason) levelSelect.setAttribute("aria-label", "Nível de variação. " + reason);
  else levelSelect.removeAttribute("aria-label");
}

async function saveLevel(level, previous) {
  if (!convUuid) {
    // Sem conversa ainda: guarda para aplicar na criação (antes da geração).
    pendingLevel = level;
    levelSelect.value = level;
    setStatus("Nível de variação vale para a próxima conversa.");
    return;
  }
  levelSaving = true;
  syncSendState();
  try {
    const updated = await api(`/api/conversations/${convUuid}`, {
      method: "PATCH",
      body: { temperature_level: level },
    });
    currentConv = updated;
    pendingLevel = null;
    applyLevelState(currentConv);
    setStatus("Nível de variação: " + levelSelect.selectedOptions[0].textContent + ". Vale nas próximas respostas.");
  } catch (e) {
    // 409 devolve o valor vigente no servidor; rascunho intacto (nunca tocamos no input).
    levelSelect.value = (e.data && e.data.temperature_level) || previous;
    setStatus("Não foi possível salvar o nível: " + (e.data && e.data.message ? e.data.message : e.message));
  } finally {
    levelSaving = false;
    syncSendState();
  }
}

levelSelect.addEventListener("change", () => {
  const previous = (currentConv && currentConv.temperature_level) || pendingLevel || "medium";
  saveLevel(levelSelect.value, previous);
});

function saveDraft() {
  drafts.set(convUuid || null, inputEl.value);
}

function restoreDraft() {
  if (drafts.has(convUuid || null)) inputEl.value = drafts.get(convUuid || null);
  autogrow();
  syncSendState();
}

async function send() {
  const content = inputEl.value.trim();
  const images = pendingImages.map(({ media_type, data }) => ({ media_type, data }));
  if ((!content && !images.length) || activeRun || uploading > 0) return;
  saveDraft();
  // Limpa já: a reserva é assíncrona e o texto digitado depois não pode ser apagado por ela.
  inputEl.value = "";
  autogrow();
  syncSendState();
  if (!convUuid) {
    const conv = await api("/api/conversations", { method: "POST", body: {} });
    convUuid = conv.uuid;
    history.replaceState(null, "", "/c/" + convUuid + "/");
    await loadConversations();
    if (pendingLevel) {
      // Aplica o nível escolhido antes da geração, sem gerar nada extra.
      try {
        currentConv = await api(`/api/conversations/${convUuid}`, {
          method: "PATCH",
          body: { temperature_level: pendingLevel },
        });
        pendingLevel = null;
        applyLevelState(currentConv);
      } catch (e) {
        setStatus("Nível não salvo (" + e.message + "); segue o padrão. Texto mantido.");
      }
    }
    if (pendingMode) {
      // Aplica o modo escolhido antes da geração, sem gerar nada extra.
      try {
        currentConv = await api(`/api/conversations/${convUuid}`, {
          method: "PATCH",
          body: { response_mode: pendingMode },
        });
        pendingMode = null;
        applyStreamState(currentConv);
      } catch (e) {
        setStatus("Modo de entrega não salvo (" + e.message + "); segue o padrão. Texto mantido.");
      }
    }
    if (window.__pendingThinking) {
      // Aplica o pensamento escolhido antes da geração, sem gerar nada extra.
      try {
        currentConv = await api(`/api/conversations/${convUuid}`, {
          method: "PATCH",
          body: window.__pendingThinking,
        });
        window.__pendingThinking = null;
      } catch (e) {
        setStatus("Pensamento não salvo (" + e.message + "); segue o padrão. Texto mantido.");
      }
    }
  }
  const key = newIdempotencyKey();
  let reservation;
  try {
    reservation = await api(`/api/conversations/${convUuid}/messages`, {
      method: "POST",
      body: { content, images, idempotency_key: key },
    });
  } catch (e) {
    setStatus("Erro ao enviar: " + e.message + " Seu texto e anexos foram mantidos.");
    inputEl.value = content;
    saveDraft();
    autogrow();
    syncSendState();
    return;
  }
  drafts.delete(convUuid);
  emptyState.hidden = true;
  const sentImages = pendingImages.map((p) => ({
    thumbnail: p.thumbnail, media_type: p.media_type, width: p.width, height: p.height,
  }));
  clearAttachments();
  appendMessage({
    uuid: reservation.user_message_id,
    role: "user",
    text: content,
    images: sentImages,
    state: "ok",
    created_at: new Date().toISOString(),
  });
  await streamRun(reservation.run_id, reservation.stream_url);
}

function friendlyError(ev) {
  if (ev.type === "cancelled") return "Resposta interrompida.";
  if (ev.code === "unauthorized") return "Não foi possível autenticar. Revise a conexão.";
  if (ev.code === "cancelled" || ev.code === "interrupted") return "Resposta interrompida.";
  if (ev.code === "context_too_large") return ev.message;
  return ev.message || "Falha na geração.";
}

async function streamRun(runId, url, opts = {}) {
  const { method = "GET", resume = null } = opts;
  activeRun = runId;
  aborter = new AbortController();
  syncSendState();

  let S;
  if (resume) {
    S = resume;
    S.terminal = null;
    setStatus("Retomando execução autorizada…");
  } else {
    setStatus("Gerando resposta…");
    const shell = {
      uuid: "run-" + runId,
      role: "assistant",
      text: "",
      state: "partial",
      created_at: new Date().toISOString(),
    };
    const stick0 = nearBottom();
    const node = messageNode(shell);
    node.querySelector(".turn-actions").remove();
    const bodyDiv = node.querySelector(".msg-body");
    const textNode = document.createTextNode("");
    bodyDiv.textContent = "";
    bodyDiv.append(textNode); // streaming: texto puro via textContent, sem innerHTML
    const trail = document.createElement("details");
    trail.className = "trail";
    trail.open = true;
    const summary = document.createElement("summary");
    summary.textContent = "Atividade de ferramentas";
    const items = document.createElement("div");
    items.className = "trail-items";
    trail.append(summary, items);
    node.append(trail);
    let thread = msgEl.querySelector(".thread");
    if (!thread) {
      thread = document.createElement("div");
      thread.className = "thread";
      msgEl.append(thread);
    }
    thread.append(node);
    node.dataset.runId = runId;
    if (stick0) msgEl.scrollTop = msgEl.scrollHeight;
    S = {
      runId, node, bodyDiv, textNode, trail, items, shell,
      acc: "", lastSeq: 0, liveMode: null, terminal: null,
      trailById: {}, keepBusy: false,
    };
    node._toolState = S; // continueRun reaproveita este turno
  }
  const finishTurn = (finalText, state) => {
    S.bodyDiv.textContent = "";
    S.bodyDiv.append(renderMarkdown(finalText));
    S.node.insertAdjacentElement("beforeend", turnActions({ ...S.shell, text: finalText, state }, S.bodyDiv));
    addDetailsButton(S.node, runId);
    if (S.trail) S.trail.open = false;
  };
  S.finishTurn = finishTurn;
  try {
    const resp = await fetch(url, { signal: aborter.signal, credentials: "same-origin", method });
    const ctype = resp.headers.get("content-type") || "";
    if (!resp.ok || !resp.body || !ctype.includes("text/event-stream")) {
      throw new Error(`Stream HTTP ${resp.status}`);
    }
    for await (const ev of readSSE(resp)) {
      handleStreamEvent(runId, ev, S);
    }
    if (!S.terminal) {
      // Fechamento sem evento terminal não é sucesso: consulta o estado salvo.
      try {
        const run = await api(`/api/runs/${runId}`);
        if (run.state === "done") {
          const msgs = await api(`/api/conversations/${convUuid}/messages`);
          const mine = (msgs.results || []).find((m) => m.run_id === runId);
          finishTurn(mine ? mine.text : S.acc, "ok");
          S.terminal = "done";
          setStatus("");
        } else if (run.state === "awaiting_approval") {
          S.terminal = "paused";
          S.keepBusy = true;
          setStatus("Aguardando sua aprovação…");
        } else {
          finishTurn(S.liveMode === "complete" ? "" : S.acc, "failed");
          S.terminal = "error";
          setStatus("Conexão encerrada antes da conclusão. Verifique o estado e repita se precisar.");
        }
      } catch {
        setStatus("Conexão encerrada antes da conclusão. Recarregue para ver o estado salvo.");
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      if (!S.terminal) setStatus("Interrompendo…");
    } else setStatus("Conexão perdida: " + e.message);
  } finally {
    activeRun = null;
    aborter = null;
    if (currentConv && !S.keepBusy) currentConv.has_active_run = false;
    syncSendState();
    updateScrollPill();
    await loadConversations($("search").value.trim());
  }
}

function trailItem(S, key, text) {
  let el = key && S.trailById[key];
  if (!el) {
    el = document.createElement("div");
    el.className = "trail-item";
    S.items.append(el);
    if (key) S.trailById[key] = el;
  }
  el.textContent = text; // textContent: nome/resultado é texto não confiável
  return el;
}

function handleStreamEvent(runId, ev, S) {
  if (!ev || ev.run_id !== runId) return; // outra execução: ignorar
  if (typeof ev.seq === "number" && ev.seq <= S.lastSeq) return; // repetição: ignorar
  if (typeof ev.seq === "number") S.lastSeq = ev.seq;
  if (S.terminal) return; // terminal já aplicado: done não duplica
  const { node, bodyDiv, textNode } = S;
  if (ev.type === "run_started") {
    S.liveMode = ev.mode === "complete" ? "complete" : "streaming";
    if (S.liveMode === "complete" && !ev.resumed) {
      textNode.textContent = "Gerando resposta… a resposta será exibida ao terminar.";
      setStatus("Gerando resposta… aguarde a conclusão.");
    }
  } else if (ev.type === "text_delta") {
    if (S.liveMode === "complete") return; // parcial nunca vaza no modo completo
    S.acc += ev.text || "";
    textNode.textContent = S.acc;
    if (nearBottom()) msgEl.scrollTop = msgEl.scrollHeight;
  } else if (ev.type === "model_step_started") {
    trailItem(S, "step-" + ev.step, `Etapa ${ev.step + 1} do modelo…`);
  } else if (ev.type === "tool_call_requested") {
    trailItem(S, ev.tool_use_id, `Solicitada: ${ev.name}`);
  } else if (ev.type === "tool_approval_required") {
    trailItem(S, ev.tool_use_id, `Aguardando aprovação: ${ev.name}`);
    S.items.append(approvalCard(runId, ev));
    setStatus("Aprovação necessária para continuar.");
  } else if (ev.type === "tool_started") {
    trailItem(S, ev.tool_use_id, `Executando: ${ev.name}…`);
  } else if (ev.type === "tool_finished") {
    trailItem(S, ev.tool_use_id, ev.ok ? `Concluída.` : `Falhou (ver detalhes).`);
  } else if (ev.type === "run_paused") {
    S.terminal = "paused";
    S.keepBusy = true; // servidor mantém active_run: envio segue bloqueado
    if (currentConv) currentConv.has_active_run = true;
    syncSendState();
    setStatus("Aguardando sua aprovação para continuar.");
  } else if (ev.type === "done") {
    S.terminal = "done";
    // Estado canônico: o texto do done (persistido) prevalece; sem re-concatenar.
    S.acc = typeof ev.text === "string" ? ev.text : S.acc;
    S.finishTurn(S.acc, "ok");
    const meta = node.querySelector(".turn-meta");
    meta.textContent += ev.truncated ? " · limite de saída" : "";
    setStatus(ev.truncated ? "A resposta atingiu o limite de saída." : "");
    updateScrollPill();
    refreshInspection(runId, false);
  } else if (ev.type === "usage") {
    const meta = node.querySelector(".turn-meta");
    meta.textContent += ` · ${ev.input_tokens ?? "?"} in / ${ev.output_tokens ?? "?"} out`;
  } else if (ev.type === "citation_delta") {
    // Provisório: conta menções, sem afirmar sustentação validada.
    S.citeCount = (S.citeCount || 0) + 1;
    if (S.liveMode !== "complete") {
      let tag = node.querySelector(".cite-count");
      if (!tag) {
        tag = document.createElement("span");
        tag.className = "turn-meta cite-count";
        node.querySelector(".turn-head").append(tag);
      }
      tag.textContent = ` · fontes mencionadas: ${S.citeCount} (verificando…)`;
    }
  } else if (ev.type === "sources") {
    // Canônico: reconcilia com a mensagem final, sem duplicar.
    const old = node.querySelector(".source-chips");
    if (old) old.remove();
    const provisional = node.querySelector(".cite-count");
    if (provisional) provisional.remove();
    renderSourceChips(node, ev.sources || []);
  } else if (ev.type === "cancelled") {
    S.terminal = "cancelled";
    S.finishTurn(S.acc, "cancelled");
    setStatus("Resposta interrompida.");
  } else if (ev.type === "error") {
    S.terminal = "error";
    // Modo completo: parcial retido no servidor nunca é revelado aqui.
    const shown = S.liveMode === "complete" ? "" : S.acc;
    S.finishTurn(shown, "failed");
    const p = document.createElement("div");
    p.className = "turn-error" + (ev.code === "unauthorized" ? " danger" : "");
    p.textContent = friendlyError(ev);
    bodyDiv.append(p);
    setStatus("Falha: " + friendlyError(ev));
  }
}

// Títulos definidos pela aplicação (nunca HTML do servidor).
const TOOL_TITLES = {
  local__calculate: "Calcular valor",
  local__current_time: "Consultar horário",
  local__create_study_note: "Criar nota de estudo",
  local__list_study_notes: "Listar notas de estudo",
  local__search_knowledge_base: "Buscar nas fontes",
  local__read_knowledge_excerpt: "Ler trecho da fonte",
};

function approvalCard(runId, ev) {
  const card = document.createElement("div");
  card.className = "approval-card";
  card.setAttribute("role", "group");
  card.setAttribute("aria-label", "Aprovação de ferramenta");
  card.tabIndex = -1;
  const title = document.createElement("strong");
  title.textContent = TOOL_TITLES[ev.name] || "Executar ferramenta externa (MCP)";
  const name = document.createElement("code");
  name.textContent = ev.name; // texto, nunca HTML
  const hint = document.createElement("p");
  hint.textContent = "Aprovação única. Decida para retomar a execução.";
  const row = document.createElement("div");
  row.className = "approval-actions";
  const okBtn = document.createElement("button");
  okBtn.type = "button";
  okBtn.className = "btn-approve";
  okBtn.textContent = "Aprovar uma vez";
  const noBtn = document.createElement("button");
  noBtn.type = "button";
  noBtn.className = "btn-deny";
  noBtn.textContent = "Recusar";
  const decide = async (decision) => {
    okBtn.disabled = true; // anti duplo-clique: consumo é único no servidor
    noBtn.disabled = true;
    hint.textContent = decision === "approve" ? "Aprovada. Retomando…" : "Recusada. Retomando…";
    try {
      await api(`/api/runs/${runId}/approvals/${ev.approval_id}/decide`, {
        method: "POST",
        body: { decision, idempotency_key: newIdempotencyKey() },
      });
    } catch (e) {
      hint.textContent = "Decisão falhou: " + e.message;
      okBtn.disabled = false;
      noBtn.disabled = false;
      return;
    }
    card.querySelector(".approval-actions").remove();
    continueRun(runId, card);
  };
  okBtn.addEventListener("click", () => decide("approve"));
  noBtn.addEventListener("click", () => decide("deny"));
  row.append(okBtn, noBtn);
  card.append(title, name, hint, row);
  return card;
}

async function continueRun(runId, card) {
  // Reaproveita o turno atual: o estado S vive no DOM do nó.
  const node = card.closest(".turn");
  const S = node && node._toolState;
  if (!S) {
    setStatus("Não foi possível retomar neste turno. Recarregue a página.");
    return;
  }
  await streamRun(runId, `/api/runs/${runId}/continue`, { method: "POST", resume: S });
}

/* ---------- ferramentas por conversa (M5) ---------- */
function setToolsLabel(count) {
  $("tools-label").textContent = count ? `Ferramentas (${count})` : "Ferramentas";
}

async function refreshToolsLabel() {
  if (!convUuid) return;
  try {
    const prefs = await api(`/api/conversations/${convUuid}/tools`);
    setToolsLabel((prefs.enabled || []).length);
  } catch {
    setToolsLabel(0);
  }
}

async function openToolsPop() {
  const pop = $("tools-pop");
  const willOpen = pop.hidden;
  closePops();
  if (!willOpen) return;
  pop.hidden = false;
  $("tools-btn").setAttribute("aria-expanded", "true");
  if (!convUuid) {
    $("tools-state").textContent =
      "Abra ou crie uma conversa para configurar as ferramentas.";
    $("tools-list").innerHTML = "";
    pop.focus();
    return;
  }
  $("tools-state").textContent = "Carregando…";
  $("tools-list").innerHTML = "";
  try {
    const data = await api(`/api/conversations/${convUuid}/tools/catalog`);
    renderToolsList(data.groups || {}, new Set(data.enabled || []));
    $("tools-state").textContent = "Somente as marcadas entram no ciclo da IA.";
  } catch (e) {
    $("tools-state").textContent = "Erro ao carregar: " + e.message;
  }
  $("tools-pop").focus();
}

function renderToolsList(groups, enabled) {
  const list = $("tools-list");
  list.innerHTML = "";
  const origins = { local: "Neste app", mcp: "Servidores MCP" };
  for (const [origin, tools] of Object.entries(groups)) {
    const h = document.createElement("p");
    h.className = "tools-group";
    h.textContent = origins[origin] || origin;
    list.append(h);
    for (const t of tools) {
      const label = document.createElement("label");
      label.className = "tool-check";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = enabled.has(t.stable_id);
      box.setAttribute("aria-label", t.name);
      box.addEventListener("change", () => toggleTool(t.stable_id, box));
      const name = document.createElement("span");
      name.className = "tool-name";
      name.textContent = t.name; // texto, nunca HTML
      const desc = document.createElement("span");
      desc.className = "tool-desc";
      desc.textContent = (t.approval === "require" ? "pede aprovação · " : "") + (t.description || "");
      label.append(box, name, desc);
      list.append(label);
    }
  }
  if (!list.children.length) {
    $("tools-state").textContent = "Nenhuma ferramenta autorizada para esta conversa.";
  }
}

async function toggleTool(stableId, box) {
  box.disabled = true;
  try {
    const prefs = await api(`/api/conversations/${convUuid}/tools`);
    const set = new Set(prefs.enabled || []);
    if (box.checked) set.add(stableId);
    else set.delete(stableId);
    const saved = await api(`/api/conversations/${convUuid}/tools`, {
      method: "PUT",
      body: { enabled: [...set] },
    });
    setToolsLabel((saved.enabled || []).length);
  } catch (e) {
    box.checked = !box.checked;
    $("tools-state").textContent = "Falha ao salvar: " + e.message;
  } finally {
    box.disabled = false;
  }
}

/* ---------- fontes por conversa (RAG M5) ---------- */
function setSourcesLabel(count) {
  $("sources-label").textContent = count ? `Fontes (${count})` : "Fontes";
}

async function refreshSourcesLabel() {
  if (!convUuid) return;
  try {
    const sel = await api(`/api/rag/conversations/${convUuid}/sources`);
    setSourcesLabel((sel.bases || []).length);
  } catch {
    setSourcesLabel(0);
  }
}

async function openSourcesPop() {
  const pop = $("sources-pop");
  const willOpen = pop.hidden;
  closePops();
  if (!willOpen) return;
  pop.hidden = false;
  $("sources-btn").setAttribute("aria-expanded", "true");
  if (!convUuid) {
    $("sources-state").textContent =
      "Abra ou crie uma conversa para selecionar as fontes.";
    $("sources-list").innerHTML = "";
    pop.focus();
    return;
  }
  $("sources-state").textContent = "Carregando…";
  $("sources-list").innerHTML = "";
  try {
    const [bases, sel] = await Promise.all([
      api("/api/rag/bases"),
      api(`/api/rag/conversations/${convUuid}/sources`),
    ]);
    renderSourcesList(bases.results || [], new Set(sel.bases || []), sel.coverage || {});
    for (const r of document.querySelectorAll('input[name="src-mode"]')) {
      r.checked = r.value === (sel.mode || "always");
      r.onchange = () => saveSources();
    }
    $("sources-state").textContent = "Vale para a próxima resposta.";
  } catch (e) {
    $("sources-state").textContent = "Erro ao carregar: " + e.message;
  }
  $("sources-pop").focus();
}

function renderSourcesList(bases, selected, coverage) {
  const list = $("sources-list");
  list.innerHTML = "";
  for (const b of bases) {
    const label = document.createElement("label");
    label.className = "tool-check";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = selected.has(b.uuid);
    box.dataset.uuid = b.uuid;
    box.setAttribute("aria-label", b.name);
    box.addEventListener("change", () => saveSources());
    const name = document.createElement("span");
    name.className = "tool-name";
    name.textContent = b.name; // texto, nunca HTML
    label.append(box, name);
    const cov = (coverage || {})[b.uuid];
    if (cov && (cov.ready || cov.processing)) {
      const sub = document.createElement("span");
      sub.className = "tool-sub";
      sub.textContent = `${cov.ready} pronto(s)` + (cov.processing ? ` · ${cov.processing} em processamento` : "");
      label.append(sub);
    }
    list.append(label);
  }
  if (!bases.length) {
    $("sources-state").textContent = "Nenhuma base. Envie documentos ou crie em Conhecimento.";
  }
}

async function openUploadFromSources() {
  const { openUploadDialog } = await import("./upload.js");
  $("sources-pop").hidden = true;
  $("sources-btn").setAttribute("aria-expanded", "false");
  await openUploadDialog({
    conversationUuid: convUuid,
    onPublished: async () => { await refreshSourcesLabel(); if (!$("sources-pop").hidden) await openSourcesPop(); },
  });
  await refreshSourcesLabel();
}

async function saveSources() {
  const boxes = [...$("sources-list").querySelectorAll("input[type=checkbox]")];
  const picked = boxes.filter((b) => b.checked).map((b) => b.dataset.uuid);
  const mode = (document.querySelector('input[name="src-mode"]:checked') || {}).value || "always";
  try {
    const saved = await api(`/api/rag/conversations/${convUuid}/sources`, {
      method: "PUT",
      body: { bases: picked, mode },
    });
    setSourcesLabel((saved.bases || []).length);
    $("sources-state").textContent = "Salvo. Vale para a próxima resposta.";
  } catch (e) {
    $("sources-state").textContent = "Falha ao salvar: " + e.message;
  }
}

function formatLocator(loc) {
  if (!loc || typeof loc !== "object") return "";
  if (loc.page) return `página ${loc.page}`;
  const parts = [];
  if (Array.isArray(loc.section) && loc.section.length) parts.push("seção: " + loc.section.join(" › "));
  if (Array.isArray(loc.lines)) parts.push(`linhas ${loc.lines[0]}–${loc.lines[1]}`);
  if (loc.block) parts.push(`bloco ${loc.block}`);
  if (Array.isArray(loc.spans)) parts.push(`${loc.spans.length} trecho(s)`);
  return parts.join(" · ");
}

function renderSourceChips(turnNode, sources) {
  if (!sources.length) return;
  const row = document.createElement("div");
  row.className = "source-chips";
  row.setAttribute("role", "group");
  row.setAttribute("aria-label", "Fontes citadas e verificadas");
  for (const s of sources) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "source-chip" + (s.available === false ? " stale" : "");
    const label = s.available === false ? "fonte removida" : s.doc;
    chip.textContent = `[${s.index}] ${label}`;
    chip.setAttribute("aria-label", `Abrir fonte ${s.index}: ${label}`);
    chip.addEventListener("click", () => openSourceDialog(s));
    row.append(chip);
  }
  turnNode.append(row);
}

function openSourceDialog(s) {
  lastOpener = document.activeElement;
  if (s.available === false) {
    $("source-title").textContent = `Fonte [${s.index}] — removida`;
    $("source-meta").textContent = "conteúdo excluído pelo proprietário";
    $("source-locator").textContent = formatLocator(s.locator) || "localizador registrado";
    $("source-excerpt").textContent =
      "Esta fonte foi removida e não está mais disponível. A referência permanece no histórico sem ressuscitar o conteúdo.";
  } else {
    $("source-title").textContent = `Fonte [${s.index}] — ${s.doc}`;
    $("source-meta").textContent = `${s.base} · versão ${s.version}`;
    $("source-locator").textContent = formatLocator(s.locator) || "localizador registrado";
    $("source-excerpt").textContent = s.excerpt || "";
  }
  $("source-dialog").showModal();
}

async function loadHistoricalCitations() {
  if (!convUuid) return;
  try {
    const data = await api(`/api/rag/conversations/${convUuid}/citations`);
    for (const [msgUuid, sources] of Object.entries(data.citations || {})) {
      const turn = msgEl.querySelector(`article.turn[data-uuid="${msgUuid}"]`);
      if (turn && !turn.querySelector(".source-chips")) renderSourceChips(turn, sources);
    }
  } catch {
    /* sem fontes: histórico segue normal */
  }
}

function addDetailsButton(node, runId) {
  const bar = node.querySelector(".turn-actions");
  if (!bar || bar.querySelector("[data-details]")) return;
  const b = document.createElement("button");
  b.type = "button";
  b.className = "action-btn";
  b.dataset.details = "1";
  b.textContent = "Detalhes";
  b.setAttribute("aria-label", "Detalhes da execução");
  b.addEventListener("click", () => openInspector(runId, true));
  bar.append(b);
}

async function stop() {
  if (!activeRun) return;
  try {
    await api(`/api/runs/${activeRun}/cancel`, { method: "POST" });
  } catch {
    /* segue o abort */
  }
  if (aborter) aborter.abort();
}

async function retryLast() {
  const users = [...msgEl.querySelectorAll(".turn")].filter((n) =>
    n.querySelector(".turn-role")?.textContent === "Você",
  );
  if (!users.length) return;
  const lastUuid = users[users.length - 1].dataset.uuid;
  const data = await api(`/api/conversations/${convUuid}/messages/${lastUuid}/retry`, {
    method: "POST",
    body: { idempotency_key: newIdempotencyKey() },
  });
  await streamRun(data.run_id, data.stream_url);
}

function updateScrollPill() {
  scrollPill.hidden = nearBottom();
}

/* ---------- seletor de modelos ---------- */
function setModelLabel(preferred) {
  $("model-label").textContent = preferred || "Modelo padrão";
  $("model-label").title = preferred || "Usando o modelo padrão da conta";
}

async function openModelPop() {
  const pop = $("model-pop");
  const willOpen = pop.hidden;
  closePops();
  if (!willOpen) return;
  pop.hidden = false;
  $("model-btn").setAttribute("aria-expanded", "true");
  $("model-state").textContent = "Carregando…";
  $("model-list").innerHTML = "";
  try {
    const data = await api("/api/models");
    renderModelList(data.models || [], data.warning || "", data.fetched_at);
  } catch (e) {
    $("model-state").textContent = "Erro ao carregar: " + e.message;
  }
  $("model-search").value = "";
  $("model-search").focus();
}

function renderModelList(models, warning, fetchedAt) {
  const ul = $("model-list");
  ul.innerHTML = "";
  const q = $("model-search").value.trim().toLowerCase();
  const items = models.filter(
    (m) => !q || m.id.toLowerCase().includes(q) || (m.display_name || "").toLowerCase().includes(q),
  );
  const current = currentConv && currentConv.preferred_model;
  if (!items.length) {
    $("model-state").textContent = q ? "Nenhum resultado para a busca." : "Nenhum modelo no catálogo. Use atualizar.";
    return;
  }
  $("model-state").textContent =
    (warning || "") + (fetchedAt ? (warning ? " " : "") + "Catálogo de " + new Date(fetchedAt).toLocaleString("pt-BR") : "");
  for (const m of items) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "option");
    b.setAttribute("aria-selected", String(current === m.id));
    const name = document.createElement("span");
    name.className = "m-name";
    name.textContent = m.display_name || m.id;
    const id = document.createElement("span");
    id.className = "m-id";
    id.textContent = m.id;
    b.append(name, id);
    b.addEventListener("click", () => selectModel(m.id, m.display_name || m.id));
    li.append(b);
    ul.append(li);
  }
}

async function selectModel(id, label) {
  closePops();
  try {
    if (convUuid) {
      const updated = await api(`/api/conversations/${convUuid}`, {
        method: "PATCH",
        body: { preferred_model: id },
      });
      // PATCH já devolve temperature_support atualizado para o novo modelo.
      currentConv = updated;
      setModelLabel(id);
      applyLevelState(currentConv);
    } else {
      await api("/api/settings/save", { method: "POST", body: { default_model: id } });
      setModelLabel(id);
    }
    setStatus("Modelo das próximas mensagens: " + label + ". Respostas anteriores mantêm o rótulo.");
  } catch (e) {
    setStatus("Não foi possível trocar o modelo: " + e.message);
  }
}

/* ---------- inspetor ---------- */
function kv(dl, k, v) {
  const dt = document.createElement("dt");
  dt.textContent = k;
  const dd = document.createElement("dd");
  dd.textContent = v;
  dl.append(dt, dd);
}

const missing = (v) => (v === null || v === undefined || v === "" ? "Não informado" : String(v));

async function openInspector(runId, focus) {
  const panel = $("inspector");
  panel.hidden = false;
  $("inspector-which").textContent = "Carregando execução…";
  if (focus) {
    lastOpener = document.activeElement;
    $("inspector-close").focus();
  }
  try {
    const run = await api(`/api/runs/${runId}`);
    fillInspector(run);
  } catch (e) {
    $("inspector-which").textContent = "Não foi possível carregar: " + e.message;
  }
}

function fillInspector(run) {
  $("inspector-which").textContent = "Resposta (tentativa " + run.attempt + ", " + run.state + ")";
  const sum = $("insp-summary");
  sum.innerHTML = "";
  kv(sum, "Modelo real", missing(run.actual_model));
  kv(sum, "Modelo pedido", missing(run.requested_model));
  kv(sum, "Estado", run.state + (run.truncated ? " · truncada" : ""));
  kv(sum, "Política base", missing((run.snapshot || {}).base_policy_version));
  kv(sum, "Instruções da conversa", missing((run.snapshot || {}).system_prompt));
  const snap = run.snapshot || {};
  const levelNames = { low: "Baixo", medium: "Médio", high: "Alto" };
  if (snap.requested_level) {
    const lvl = levelNames[snap.requested_level] || snap.requested_level;
    kv(sum, "Nível de variação",
      snap.temperature_sent == null
        ? `${lvl} (não aplicado — ${snap.temperature_reason || snap.temperature_state || "omitido"})`
        : `${lvl} (aplicado; valor no modo técnico)`);
  }
  const modeNames = { streaming: "Streaming", complete: "Completa" };
  if (snap.effective_response_mode) {
    const req = modeNames[snap.requested_response_mode] || snap.requested_response_mode || "?";
    const eff = modeNames[snap.effective_response_mode] || snap.effective_response_mode;
    kv(sum, "Entrega", req === eff ? eff : `${req} (efetivo: ${eff})`);
  }
  const rag = snap.rag || {};
  if (rag.status === "ready") {
    const d = rag.diagnosis || {};
    kv(sum, "RAG", `pré-busca · ${rag.evidences} evidência(s) · ${rag.sent_chars} chars (−${rag.dropped})`);
    kv(sum, "Consulta efetiva", missing(rag.effective_query));
    kv(sum, "Recuperação", `${d.method || "?"} · ${d.ms_total ?? "?"} ms · perfil ${d.profile_id ?? "?"}`);
  } else if (rag.status === "abstain") {
    kv(sum, "RAG", `abstenção (${rag.reason || "?"}) — sem chamada paga`);
  }
  const ctx = $("insp-context");
  ctx.innerHTML = "";
  // O backend registra seqs incluídos/omitidos (context_used); o texto completo vive no histórico.
  const used = run.context_used || [];
  const included = used.filter((u) => u.included).map((u) => u.seq);
  const omitted = used.find((u) => u.omitted_turns);
  if (included.length) {
    const p = document.createElement("p");
    p.textContent = `Turnos incluídos nesta resposta: ${included.join(", ")}.`;
    ctx.append(p);
    const hint = document.createElement("p");
    hint.className = "meta";
    hint.textContent = "O texto completo de cada turno está no histórico da conversa.";
    ctx.append(hint);
  }
  if (omitted && omitted.omitted_turns > 0) {
    const p = document.createElement("p");
    p.textContent = `Parte do histórico ficou fora desta resposta (${omitted.omitted_turns} turno(s)). As mensagens continuam salvas.`;
    ctx.append(p);
  }
  if (!included.length && !(omitted && omitted.omitted_turns > 0)) {
    const p = document.createElement("p");
    p.textContent = "Contexto não registrado para esta execução.";
    ctx.append(p);
  }
  const use = $("insp-usage");
  use.innerHTML = "";
  kv(use, "Entrada", missing(run.input_tokens) + " tokens");
  kv(use, "Saída", missing(run.output_tokens) + " tokens");
  kv(use, "Parada", missing(run.stop_reason));
  kv(use, "Request", missing(run.request_id));
  if (run.error_code) kv(use, "Erro", run.error_code + (run.error_message ? " — " + run.error_message : ""));
  renderAgentTrail(run, sum, use);
  $("inspect-body").textContent = JSON.stringify(run, null, 2);
}

/* ---------- trilha do agente/equipe + métricas (M5) ---------- */
function renderAgentTrail(run, sum, use) {
  const snap = run.snapshot || {};
  const agent = snap.agent || {};
  if (agent.applied) {
    const origins = Object.entries(agent.origins || {})
      .map(([k, v]) => `${k}: ${v}`)
      .join(" · ");
    kv(sum, "Agente", `${agent.definition || "?"} (r${agent.version_revision ?? "?"}) · modo ${agent.mode || "?"}`);
    if (origins) kv(sum, "Origens do agente", origins);
  } else if ((agent.mode || "chat") !== "chat") {
    kv(sum, "Agente", `modo ${agent.mode} (perfil indisponível — resposta sem instruções do perfil)`);
  }
  const think = snap.agent_thinking;
  if (think && think.applied) {
    const eff = think.effective || {};
    kv(sum, "Pensamento do agente", `${eff.mode || "?"} · ${eff.level || "?"} · origem ${JSON.stringify(think.origins || {})}`);
  }
  const cache = snap.cache || {};
  if (cache.plan) {
    kv(use, "Cache solicitado", `${cache.plan.mode || "?"} · TTL ${cache.plan.ttl || "?"}`);
    kv(use, "Cache elegível", cache.plan.eligible ? `sim (${cache.plan.diagnosis || "ok"})` : `não (${cache.plan.diagnosis || "?"})`);
  }
  const steps = ((run.tools || {}).steps || []).filter((s) => s.cache_creation_input_tokens || s.cache_read_input_tokens);
  if (steps.length) {
    const wrote = steps.reduce((a, s) => a + (s.cache_creation_input_tokens || 0), 0);
    const read = steps.reduce((a, s) => a + (s.cache_read_input_tokens || 0), 0);
    const total = (run.input_tokens || 0) + wrote + read;
    const frac = total > 0 ? ` (${((100 * read) / total).toFixed(1)}% lida)` : "";
    kv(use, "Cache confirmado", `escrita ${wrote} · leitura ${read}${frac} — só tokens, sem estimativa monetária`);
  } else if (cache.plan && cache.plan.eligible) {
    kv(use, "Cache confirmado", "sem confirmação do provedor (desconhecido ≠ zero)");
  }
  const team = run.team || {};
  if (team.available) {
    kv(sum, "Equipe", `${team.coordinator || "?"} (r${team.revision ?? "?"}) · raiz ${team.root_state || "?"}`);
    const kids = team.children || [];
    kv(sum, "Filhas", kids.length ? kids.map((c) => `${c.agent || "?"}: ${c.state}${c.summary ? ` — ${c.summary}` : ""}`).join(" | ") : "nenhuma delegação concluída");
    const evts = (team.events || []).map((e) => `#${e.seq} ${e.kind}`).join(" · ");
    if (evts) kv(sum, "Trilha", evts);
    const led = team.ledger || {};
    if (led && Object.keys(led).length) kv(use, "Orçamento da árvore", JSON.stringify(led));
    const lim = team.limits || {};
    if (lim.max_child_runs != null) kv(use, "Teto de filhas", `${kids.length}/${lim.max_child_runs}`);
  } else if (agent.mode === "team" || team.reason === "no_delegatables") {
    kv(sum, "Equipe", `indisponível (${team.reason || "sem delegáveis"}): resposta só do coordenador`);
  }
}

function closeInspector(returnFocus) {
  $("inspector").hidden = true;
  if (returnFocus && lastOpener && document.contains(lastOpener)) lastOpener.focus();
}

async function refreshInspection(runId, focus) {
  if ($("inspector").hidden) return;
  await openInspector(runId, focus);
}

/* ---------- diálogos ---------- */
function openConvDialog() {
  if (!convUuid) {
    setStatus("Crie a conversa enviando a primeira mensagem.");
    return;
  }
  lastOpener = document.activeElement;
  $("conv-dialog").showModal();
}

function askDelete(c) {
  lastOpener = document.activeElement;
  $("delete-name").textContent = c.title;
  const dlg = $("delete-dialog");
  dlg.showModal();
  dlg.dataset.uuid = c.uuid;
  dlg.dataset.mine = String(c.uuid === convUuid);
}

/* ---------- eventos ---------- */
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  send();
});
inputEl.addEventListener("input", () => {
  saveDraft();
  autogrow();
  syncSendState();
});
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !composing && !e.isComposing) {
    e.preventDefault();
    send();
  }
});
stopBtn.addEventListener("click", stop);
// Anexo de imagens: seletor nativo + arrastar/soltar + colar (só imagem).
attachBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  addImageFiles([...fileInput.files]);
  fileInput.value = "";
});
["dragenter", "dragover"].forEach((t) => composerEl.addEventListener(t, (e) => {
  if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
  e.preventDefault();
  composerEl.classList.add("over");
}));
["dragleave", "drop"].forEach((t) => composerEl.addEventListener(t, (e) => {
  e.preventDefault();
  composerEl.classList.remove("over");
}));
composerEl.addEventListener("drop", (e) => {
  if (e.dataTransfer && e.dataTransfer.files.length) addImageFiles([...e.dataTransfer.files]);
});
inputEl.addEventListener("paste", (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (files.length) addImageFiles(files);
});
scrollPill.addEventListener("click", () => {
  msgEl.scrollTop = msgEl.scrollHeight;
  updateScrollPill();
});
msgEl.addEventListener("scroll", updateScrollPill, { passive: true });
$("btn-new").addEventListener("click", () => {
  saveDraft();
  location.href = "/";
});
$("search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadConversations(e.target.value.trim()), 250);
});
$("search-clear").addEventListener("click", () => {
  $("search").value = "";
  loadConversations();
});
$("conv-clear-search").addEventListener("click", () => {
  $("search").value = "";
  loadConversations();
  $("search").focus();
});
document.querySelectorAll("[data-suggest]").forEach((b) => {
  b.addEventListener("click", () => {
    inputEl.value = b.dataset.suggest;
    autogrow();
    syncSendState();
    inputEl.focus(); // preenche sem enviar
  });
});

// Sidebar recolher / abrir
const sidePref = () => {
  try {
    return localStorage.getItem("cc-side") || "open";
  } catch {
    return "open";
  }
};
if (sidePref() === "hidden" && window.innerWidth > 1024) document.body.classList.add("side-hidden");
if (window.innerWidth <= 1024) document.body.classList.add("side-hidden");
function setSide(open) {
  document.body.classList.toggle("side-hidden", !open);
  try {
    localStorage.setItem("cc-side", open ? "open" : "hidden");
  } catch {
    /* sem persistência */
  }
  $("scrim").hidden = !open || window.innerWidth > 1024;
}
$("side-collapse").addEventListener("click", () => setSide(false));
$("side-open").addEventListener("click", () => setSide(true));
$("scrim").addEventListener("click", () => setSide(false));

// Seletor + menu
$("model-btn").addEventListener("click", openModelPop);
$("tools-btn").addEventListener("click", openToolsPop);
$("sources-btn").addEventListener("click", openSourcesPop);
$("sources-upload").addEventListener("click", openUploadFromSources);
$("source-close").addEventListener("click", () => {
  $("source-dialog").close();
  if (lastOpener && document.contains(lastOpener)) lastOpener.focus();
});
$("model-search").addEventListener("input", async () => {
  try {
    const data = await api("/api/models");
    renderModelList(data.models || [], data.warning || "", data.fetched_at);
  } catch {
    /* estado já exibido */
  }
});
$("model-refresh").addEventListener("click", async () => {
  $("model-state").textContent = "Atualizando catálogo…";
  try {
    await api("/api/models", { method: "POST" });
    const data = await api("/api/models");
    renderModelList(data.models || [], data.warning || "", data.fetched_at);
  } catch (e) {
    $("model-state").textContent = "Erro ao atualizar: " + e.message;
  }
});
$("actions-btn").addEventListener("click", () => {
  const pop = $("actions-pop");
  const willOpen = pop.hidden;
  closePops();
  if (!willOpen) return;
  pop.hidden = false;
  $("actions-btn").setAttribute("aria-expanded", "true");
  pop.querySelector("button").focus();
});
$("actions-pop").addEventListener("click", (e) => {
  const act = e.target.closest("[data-act]");
  if (!act) return;
  const kind = act.dataset.act;
  closePops();
  if (kind === "more") {
    const on = document.body.classList.toggle("head-expanded");
    try { localStorage.setItem("cc-head-expanded", on ? "1" : "0"); } catch (_) {}
    return;
  }
  if (kind === "tools-panel") {
    $("tools-btn").click();
    return;
  }
  if (kind === "sources-panel") {
    $("sources-btn").click();
    return;
  }
  if (kind === "thinking-panel") {
    $("thinking-btn").click();
    return;
  }
  if (kind === "conv-settings") openConvDialog();
  if (kind === "details") {
    const nodes = [...msgEl.querySelectorAll(".turn")].filter((n) => n.dataset.runId);
    if (nodes.length) openInspector(nodes[nodes.length - 1].dataset.runId, true);
    else setStatus("Nenhuma execução nesta sessão para inspecionar.");
  }
});

$("inspector-close").addEventListener("click", () => closeInspector(true));

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("model-pop").hidden || !$("actions-pop").hidden || !$("tools-pop").hidden || !$("sources-pop").hidden || openPop) {
      closePops();
      $("model-btn").focus();
    } else if (!$("inspector").hidden) closeInspector(true);
  }
});
document.addEventListener("click", (e) => {
  if (
    !$("model-pop").hidden &&
    !$("model-pop").contains(e.target) &&
    !$("model-btn").contains(e.target)
  ) {
    $("model-pop").hidden = true;
    $("model-btn").setAttribute("aria-expanded", "false");
  }
  if (
    !$("actions-pop").hidden &&
    !$("actions-pop").contains(e.target) &&
    !$("actions-btn").contains(e.target)
  ) {
    $("actions-pop").hidden = true;
    $("actions-btn").setAttribute("aria-expanded", "false");
  }
  if (
    !$("tools-pop").hidden &&
    !$("tools-pop").contains(e.target) &&
    !$("tools-btn").contains(e.target)
  ) {
    $("tools-pop").hidden = true;
    $("tools-btn").setAttribute("aria-expanded", "false");
  }
  if (
    !$("sources-pop").hidden &&
    !$("sources-pop").contains(e.target) &&
    !$("sources-btn").contains(e.target)
  ) {
    $("sources-pop").hidden = true;
    $("sources-btn").setAttribute("aria-expanded", "false");
  }
});

// Diálogo da conversa
$("conv-form").addEventListener("submit", async (e) => {
  const action = e.submitter && e.submitter.value;
  if (action === "cancel" || !convUuid) return;
  e.preventDefault();
  if (action === "archive") {
    const cur = await api(`/api/conversations/${convUuid}`);
    const updated = await api(`/api/conversations/${convUuid}`, {
      method: "PATCH",
      body: { archived: !cur.archived },
    });
    $("conv-dialog").close();
    if (lastOpener && document.contains(lastOpener)) lastOpener.focus();
    if (updated.archived) location.href = "/";
    else {
      $("chat-title").textContent = updated.title;
      await loadConversations();
    }
    return;
  }
  const payload = {
    title: $("f-title").value,
    preferred_model: $("f-model").value,
    system_prompt: $("f-system").value,
    max_output_tokens: $("f-maxout").value ? Number($("f-maxout").value) : null,
    input_budget: $("f-budget").value ? Number($("f-budget").value) : null,
    strict_mode: $("f-strict").checked,
  };
  const updated = await api(`/api/conversations/${convUuid}`, { method: "PATCH", body: payload });
  currentConv = updated;
  $("chat-title").textContent = updated.title;
  setModelLabel(updated.preferred_model || null);
  $("conv-dialog").close();
  if (lastOpener && document.contains(lastOpener)) lastOpener.focus();
  await loadConversations();
});
$("conv-dialog").addEventListener("close", () => {
  if (lastOpener && document.contains(lastOpener)) lastOpener.focus();
});

// Exclusão com nome + consequência
$("delete-form").addEventListener("submit", async (e) => {
  const action = e.submitter && e.submitter.value;
  const dlg = $("delete-dialog");
  if (action !== "confirm") return;
  e.preventDefault();
  await api(`/api/conversations/${dlg.dataset.uuid}`, { method: "DELETE" });
  if (dlg.dataset.mine === "true") location.href = "/";
  else {
    dlg.close();
    await loadConversations($("search").value.trim());
  }
});

// Fechar a aba durante streaming = interromper (servidor marca interrupted).
window.addEventListener("beforeunload", () => {
  if (activeRun && aborter) aborter.abort();
});

try {
  if (localStorage.getItem("cc-head-expanded") === "1") document.body.classList.add("head-expanded");
} catch (_) {}
await loadConversations();
await loadConversation();
await refreshKeyBanner();
