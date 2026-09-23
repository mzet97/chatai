// Componente compartilhado "Enviar documentos" (Conhecimento + Fontes).
// Um POST por arquivo (FormData, campo `file`), progresso real via XHR,
// polling do job a cada 2s. Sem listeners duplicados: o diálogo é montado
// do zero a cada abertura e descartado ao fechar.
import { api, csrfToken, newIdempotencyKey } from "./api.js";

const ACCEPT = ".txt,.md,.pdf,.docx";
const EXT_OK = new Set(["txt", "md", "pdf", "docx"]);
const MAX_FILES = 10;
const MAX_BYTES = 20 * 1024 * 1024;
const CONCURRENCY = 2;
const POLL_MS = 2000;

const PHASES = {
  queued: "Na fila", extracting: "Extraindo texto", chunking: "Preparando trechos",
  embedding: "Indexando", publishing: "Indexando",
};

function fmtSize(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KiB`;
  return `${(n / 1048576).toFixed(1)} MiB`;
}

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function extOf(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

function preCheck(file) {
  const ext = extOf(file.name);
  if (!EXT_OK.has(ext)) return `Tipo .${ext || "?"} não aceito (TXT, MD, PDF, DOCX).`;
  if (file.size > MAX_BYTES) return `Excede 20 MiB (${fmtSize(file.size)}).`;
  if (file.size === 0) return "Arquivo vazio.";
  return null;
}

function parseBody(xhr) {
  const ct = xhr.getResponseHeader("Content-Type") || "";
  if (ct.includes("application/json")) {
    try { return JSON.parse(xhr.responseText); } catch { return {}; }
  }
  return { message: xhr.responseText.slice(0, 200) || `HTTP ${xhr.status}` };
}

function postFile(baseUuid, item, onProgress) {
  return new Promise((resolve) => {
    const form = new FormData();
    form.append("file", item.file);
    form.append("client_key", item.key);
    if (item.conflict) form.append("on_name_conflict", item.conflict);
    const xhr = new XMLHttpRequest();
    item.xhr = xhr;
    // Sem Content-Type manual: o navegador gera o boundary ([R2]).
    xhr.open("POST", `/api/rag/bases/${baseUuid}/upload`);
    xhr.setRequestHeader("X-CSRFToken", csrfToken());
    xhr.withCredentials = true;
    if (xhr.upload) {
      xhr.upload.onprogress = (ev) => {
        if (ev.lengthComputable && ev.total > 0) {
          onProgress(Math.round((ev.loaded / ev.total) * 100), false);
        } else {
          onProgress(0, true); // total desconhecido: indeterminado
        }
      };
    }
    xhr.onload = () => {
      const body = parseBody(xhr);
      if (xhr.status === 202 || (xhr.status === 200 && body.job_id)) {
        resolve({ ok: true, body });
      } else if (xhr.status === 409 && body.code === "name_conflict") {
        resolve({ ok: false, conflict: true, body });
      } else if (xhr.status === 403) {
        resolve({ ok: false, error: "Sessão expirada ou CSRF inválido. Recarregue a página." });
      } else {
        resolve({ ok: false, error: body.message || `HTTP ${xhr.status}` });
      }
    };
    xhr.onerror = () => resolve({ ok: false, error: "Falha de rede durante o envio." });
    xhr.ontimeout = () => resolve({ ok: false, error: "Tempo esgotado no envio." });
    xhr.send(form);
  });
}

async function pollJob(jobId, alive) {
  let fails = 0;
  for (;;) {
    if (!alive()) return null;
    try {
      const d = await api(`/api/rag/jobs/${jobId}`);
      fails = 0;
      if (["ready", "failed", "needs_ocr", "cancelled"].includes(d.state)) return d;
    } catch {
      fails += 1;
      if (fails > 15) return { state: "gone", error: "" }; // job removido no servidor
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}

function phaseLabel(job) {
  if (PHASES[job.state]) {
    let s = PHASES[job.state];
    if (job.progress_total > 0) s += ` (${job.progress_known}/${job.progress_total})`;
    return s;
  }
  return job.state;
}

/** Diálogo "Enviar documentos". Opções: baseUuid inicial, conversationUuid
 * (exibe "Usar nesta conversa"), onPublished(baseUuid) ao concluir. */
export async function openUploadDialog({ baseUuid = null, conversationUuid = null, onPublished = null } = {}) {
  const opener = document.activeElement;
  const dlg = el("dialog", null, "upload-dialog");
  dlg.setAttribute("aria-labelledby", "up-title");
  dlg.append(el("h2", "Enviar documentos", null));
  dlg.querySelector("h2").id = "up-title";

  // --- base de destino + criar base ---
  const baseWrap = el("div", null, "up-row");
  const baseLabel = el("label", "Base de destino ", null);
  baseLabel.setAttribute("for", "up-base");
  const baseSel = el("select");
  baseSel.id = "up-base";
  baseLabel.append(baseSel);
  baseWrap.append(baseLabel);
  const newBaseInput = el("input");
  newBaseInput.id = "up-newbase";
  newBaseInput.maxLength = 120;
  newBaseInput.placeholder = "Nova base…";
  newBaseInput.setAttribute("aria-label", "Nome da nova base");
  const newBaseBtn = el("button", "Criar base", "btn");
  newBaseBtn.type = "button";
  baseWrap.append(newBaseInput, newBaseBtn);
  dlg.append(baseWrap);

  const hint = el("p", "TXT, MD, PDF com texto, DOCX — até 20 MiB por arquivo, 10 por lote.", "meta");
  dlg.append(hint);
  const workerNote = el("p", "", "meta");
  dlg.append(workerNote);

  // --- dropzone + seletor real ---
  const zone = el("div", null, "up-zone");
  zone.tabIndex = 0;
  zone.setAttribute("role", "button");
  zone.setAttribute("aria-label", "Área para soltar arquivos ou selecionar");
  zone.append(el("p", "Arraste arquivos para cá ou", null));
  const pickBtn = el("button", "Selecionar arquivos", "btn");
  pickBtn.type = "button";
  zone.append(pickBtn);
  const fileInput = el("input");
  fileInput.type = "file";
  fileInput.accept = ACCEPT;
  fileInput.multiple = true;
  fileInput.hidden = true;
  fileInput.setAttribute("aria-label", "Selecionar arquivos");
  zone.append(fileInput);
  dlg.append(zone);

  const list = el("ul", null, "up-files");
  dlg.append(list);

  const foot = el("menu");
  const sendBtn = el("button", "Enviar e indexar", "btn primary");
  sendBtn.type = "button";
  sendBtn.disabled = true;
  const closeBtn = el("button", "Fechar", "btn");
  closeBtn.type = "button";
  foot.append(sendBtn, closeBtn);
  dlg.append(foot);
  document.body.append(dlg);

  const items = []; // {file,key,error,li,statusEl,barEl,xhr,jobId,done}
  let sending = false;
  let alive = true;

  function refreshSend() {
    const ok = baseSel.value && !sending && items.some((i) => !i.error && !i.jobId);
    sendBtn.disabled = !ok;
  }

  function addFiles(files) {
    for (const f of files) {
      if (items.length >= MAX_FILES) break;
      if (items.some((i) => i.file.name === f.name && i.file.size === f.size)) continue;
      const item = { file: f, key: newIdempotencyKey(), error: preCheck(f), jobId: null, done: false };
      const li = el("li", null, "up-file");
      const head = el("div", null, "up-file-head");
      head.append(el("strong", f.name, null));
      head.append(el("span", `${fmtSize(f.size)} · .${extOf(f.name) || "?"}`, "meta"));
      const rm = el("button", "remover", "linklike");
      rm.type = "button";
      rm.setAttribute("aria-label", `Remover ${f.name}`);
      rm.addEventListener("click", () => {
        if (item.xhr && !item.done) { try { item.xhr.abort(); } catch { /* ignore */ } }
        items.splice(items.indexOf(item), 1);
        li.remove();
        refreshSend();
      });
      head.append(rm);
      li.append(head);
      item.statusEl = el("p", item.error || "Selecionado", "meta");
      li.append(item.statusEl);
      item.barEl = el("progress");
      item.barEl.max = 100;
      item.barEl.value = 0;
      item.barEl.hidden = true;
      li.append(item.barEl);
      item.li = li;
      items.push(item);
      list.append(li);
    }
    refreshSend();
  }

  pickBtn.addEventListener("click", () => fileInput.click());
  zone.addEventListener("click", (e) => { if (e.target === zone) fileInput.click(); });
  zone.addEventListener("keydown", (e) => {
    if (e.target !== zone) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener("change", () => { addFiles([...fileInput.files]); fileInput.value = ""; });
  ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => {
    e.preventDefault(); zone.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => {
    e.preventDefault(); zone.classList.remove("over");
  }));
  zone.addEventListener("drop", (e) => {
    if (e.dataTransfer && e.dataTransfer.files.length) addFiles([...e.dataTransfer.files]);
  });

  async function loadBases(select) {
    try {
      const d = await api("/api/rag/bases");
      baseSel.innerHTML = "";
      for (const b of d.results || []) {
        const o = document.createElement("option");
        o.value = b.uuid;
        o.textContent = b.name;
        baseSel.append(o);
      }
      if (select) baseSel.value = select;
      if (!baseSel.value && baseSel.options.length) baseSel.selectedIndex = 0;
    } catch {
      baseSel.innerHTML = "";
    }
    refreshSend();
  }

  newBaseBtn.addEventListener("click", async () => {
    const name = newBaseInput.value.trim();
    if (!name) { newBaseInput.focus(); return; }
    try {
      const b = await api("/api/rag/bases", { method: "POST", body: { name } });
      newBaseInput.value = "";
      await loadBases(b.uuid);
    } catch (e) {
      workerNote.textContent = `Falha ao criar base: ${e.message}`;
    }
  });
  baseSel.addEventListener("change", refreshSend);

  try {
    const w = await api("/api/rag/worker/status");
    workerNote.textContent = w.online
      ? ""
      : "Processador de documentos offline: arquivos ficarão Na fila até o worker rodar (python manage.py rag_worker).";
  } catch { /* sem status: segue sem aviso */ }

  function setItemStatus(item, text) { item.statusEl.textContent = text; }

  async function finishItem(item, base, res) {
    if (res.conflict) {
      // Nome igual, conteúdo novo: escolha explícita, sem sobrescrever calado.
      setItemStatus(item, `“${item.file.name}” já existe com outro conteúdo.`);
      const row = el("div", null, "up-conflict");
      const bv = el("button", "Enviar como nova versão", "btn");
      bv.type = "button";
      const bs = el("button", "Manter como documento separado", "btn");
      bs.type = "button";
      const pick = (mode) => {
        item.conflict = mode;
        row.remove();
        setItemStatus(item, "Selecionado");
        refreshSend();
      };
      bv.addEventListener("click", () => pick("version"));
      bs.addEventListener("click", () => pick("separate"));
      row.append(bv, bs);
      item.li.append(row);
      return;
    }
    if (!res.ok) {
      item.done = true;
      setItemStatus(item, `Falha no envio: ${res.error} `);
      addRetry(item, base, true);
      return;
    }
    item.jobId = res.body.job_id;
    if (res.body.duplicate) setItemStatus(item, "Recebido (já enviado antes). Acompanhando…");
    else setItemStatus(item, "Recebido. Na fila…");
    const job = await pollJob(item.jobId, () => alive);
    if (!job) return; // diálogo fechado: job segue no servidor
    if (job.state === "ready") {
      let finalNote = "Pronto para consulta.";
      try {
        const doc = await api(`/api/rag/documents/${res.body.doc_uuid}`);
        if (doc.state === "partial") finalNote = "Cobertura parcial: ver detalhes na base.";
      } catch { /* mantém pronto */ }
      item.done = true;
      setItemStatus(item, finalNote);
      if (conversationUuid) addUseInConversation(item, base);
      if (onPublished) { try { onPublished(base); } catch { /* ignore */ } }
    } else if (job.state === "needs_ocr") {
      item.done = true;
      setItemStatus(item, "Necessita OCR: sem texto extraível. Não indexado.");
      addRetry(item, base, false);
    } else if (job.state === "cancelled") {
      item.done = true;
      setItemStatus(item, "Processamento cancelado.");
    } else if (job.state === "gone") {
      item.done = true;
      setItemStatus(item, "Acompanhamento perdido (job removido?). Verifique a base.");
    } else {
      item.done = true;
      setItemStatus(item, `Falha na indexação: ${job.error || job.state} `);
      addRetry(item, base, false);
    }
  }

  function addRetry(item, base, resend) {
    const btn = el("button", "Tentar novamente", "btn");
    btn.type = "button";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      if (resend || !item.jobId) {
        item.conflict = null;
        item.done = false;
        item.jobId = null;
        setItemStatus(item, "Selecionado");
        refreshSend();
        return;
      }
      try {
        const r = await api(`/api/rag/jobs/${item.jobId}/retry`, { method: "POST", body: {} });
        item.jobId = r.uuid;
        item.done = false;
        btn.remove();
        setItemStatus(item, "Na fila…");
        const job = await pollJob(item.jobId, () => alive);
        await finishJobOnly(item, base, job);
      } catch (e) {
        btn.disabled = false;
        setItemStatus(item, `Falha ao tentar de novo: ${e.message}`);
      }
    });
    item.statusEl.append(btn);
  }

  async function finishJobOnly(item, base, job) {
    if (!job) return;
    await finishItem(item, base, { ok: true, body: { job_id: job.uuid || item.jobId, doc_uuid: item.docUuid } });
  }

  function addUseInConversation(item, baseUuid) {
    const btn = el("button", "Usar nesta conversa", "btn primary");
    btn.type = "button";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const cur = await api(`/api/rag/conversations/${conversationUuid}/sources`);
        const bases = [...new Set([...(cur.bases || []), baseUuid])];
        await api(`/api/rag/conversations/${conversationUuid}/sources`, {
          method: "PUT", body: { bases, mode: cur.mode || "always" },
        });
        setItemStatus(item, "Pronto para consulta. Base selecionada nesta conversa (não só este arquivo).");
      } catch (e) {
        btn.disabled = false;
        setItemStatus(item, `Pronto para consulta. Falha ao selecionar: ${e.message}`);
      }
    });
    item.statusEl.append(document.createTextNode(" "), btn);
  }

  sendBtn.addEventListener("click", async () => {
    const base = baseSel.value;
    const queue = items.filter((i) => !i.error && !i.jobId);
    if (!base || !queue.length || sending) return;
    sending = true;
    refreshSend();
    const onLeave = (e) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", onLeave);
    let idx = 0;
    async function worker() {
      while (idx < queue.length) {
        const item = queue[idx++];
        if (!alive) return;
        item.barEl.hidden = false;
        setItemStatus(item, "Enviando… 0%");
        const res = await postFile(base, item, (pct, indet) => {
          if (!alive) return;
          if (indet) {
            item.barEl.removeAttribute("value");
            setItemStatus(item, "Enviando…");
          } else {
            item.barEl.value = pct;
            setItemStatus(item, pct >= 100 ? "Aguardando confirmação…" : `Enviando… ${pct}%`);
          }
        });
        item.barEl.hidden = true;
        if (res.ok && res.body.doc_uuid) item.docUuid = res.body.doc_uuid;
        await finishItem(item, base, res);
      }
    }
    await Promise.all([worker(), worker()]);
    window.removeEventListener("beforeunload", onLeave);
    sending = false;
    refreshSend();
  });

  function close() {
    alive = false;
    dlg.close();
    dlg.remove();
    if (opener && document.contains(opener)) opener.focus();
  }
  closeBtn.addEventListener("click", close);
  dlg.addEventListener("cancel", (e) => {
    if (sending) {
      e.preventDefault();
      workerNote.textContent = "Envio em andamento: aguarde a confirmação ou cancele cada arquivo em “remover”.";
      return;
    }
    alive = false;
    dlg.remove();
    if (opener && document.contains(opener)) opener.focus();
  });

  await loadBases(baseUuid);
  dlg.showModal();
  (baseSel.options.length ? pickBtn : newBaseInput).focus();
}
