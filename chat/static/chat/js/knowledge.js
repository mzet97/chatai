// Área Conhecimento: bases, documentos, exclusão. Envio pelo diálogo
// compartilhado (upload.js), também usado em Fontes na conversa.
import { api } from "./api.js";
import { initThemeSection } from "./theme.js";

const $ = (id) => document.getElementById(id);

initThemeSection();

const stateLabels = {
  upload: "enviado", processing: "indexando…", ready: "pronto",
  partial: "cobertura parcial", needs_ocr: "precisa OCR", failed: "falha",
};

let currentBase = null;

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function fmtSize(n) {
  if (n === null || n === undefined) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KiB`;
  return `${(n / 1048576).toFixed(1)} MiB`;
}

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return "—";
  }
}

function extOf(name) {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "?";
}

async function loadBases() {
  $("kb-state").textContent = "Carregando…";
  const d = await api("/api/rag/bases");
  const ul = $("kb-list");
  ul.innerHTML = "";
  for (const b of d.results || []) {
    const li = el("li");
    const btn = el("button", b.name, "linklike");
    btn.type = "button";
    btn.addEventListener("click", () => selectBase(b));
    li.append(btn);
    ul.append(li);
  }
  $("kb-state").textContent = (d.results || []).length ? "" : "Nenhuma base. Crie a primeira acima.";
}

async function selectBase(b) {
  currentBase = b;
  $("kb-docs-section").hidden = false;
  $("kb-docs-title").textContent = `Documentos — ${b.name}`;
  await loadDocs();
}

async function loadDocs() {
  const d = await api(`/api/rag/bases/${currentBase.uuid}/documents`);
  const ul = $("kb-docs");
  ul.innerHTML = "";
  for (const doc of d.results || []) {
    const li = el("li", "", "kb-doc-row");
    li.append(el("strong", doc.name));
    li.append(el(
      "span",
      ` .${extOf(doc.name)} · ${fmtSize(doc.size_bytes)}`
      + `${doc.version ? ` · v${doc.version}` : ""} · ${fmtDate(doc.created_at)}`
      + ` · ${stateLabels[doc.state] || doc.state}`,
      "meta",
    ));
    const info = el("button", "detalhes", "linklike");
    info.type = "button";
    info.addEventListener("click", () => showDoc(doc));
    li.append(info);
    const dl = el("a", "baixar", "linklike");
    dl.href = `/api/rag/documents/${doc.uuid}/download`;
    li.append(dl);
    const del = el("button", "excluir…", "linklike danger-text");
    del.type = "button";
    del.addEventListener("click", () => confirmDeleteDoc(doc));
    li.append(del);
    ul.append(li);
  }
}

async function showDoc(doc) {
  const d = await api(`/api/rag/documents/${doc.uuid}`);
  $("kb-doc-title").textContent = d.name;
  const body = $("kb-doc-body");
  body.innerHTML = "";
  body.append(el("p", `Estado: ${stateLabels[d.state] || d.state} · versão ativa: ${d.active_version ?? "—"}`, "meta"));
  for (const v of d.versions || []) {
    const det = el("details");
    det.append(el("summary", `Versão ${v.number} (${v.state}, ${v.chunks} fragmentos)`));
    if ((v.warnings || []).length) {
      const ul = el("ul", "", "warn");
      for (const w of v.warnings) ul.append(el("li", String(w)));
      det.append(el("p", "Avisos de cobertura — uma busca sem resultado aqui não prova ausência da informação:", "meta"));
      det.append(ul);
    } else {
      det.append(el("p", "Sem avisos de extração.", "meta"));
    }
    body.append(det);
  }
  body.append(el("h3", "Trabalhos recentes"));
  const ul = el("ul");
  for (const j of d.jobs || []) {
    const li = el("li", `${j.state}${j.error ? ` — ${j.error}` : ""}`, "meta");
    if (["failed", "needs_ocr", "cancelled"].includes(j.state)) {
      li.append(document.createTextNode(" "));
      const rb = el("button", "tentar novamente", "linklike");
      rb.type = "button";
      rb.addEventListener("click", async () => {
        rb.disabled = true;
        try {
          await api(`/api/rag/jobs/${j.uuid}/retry`, { method: "POST", body: {} });
          $("kb-doc").close();
          await loadDocs();
        } catch (e) {
          rb.disabled = false;
          li.append(el("span", ` (${e.message})`, "meta"));
        }
      });
      li.append(rb);
    }
    ul.append(li);
  }
  body.append(ul);
  $("kb-doc").showModal();
}

function askConfirm(text) {
  $("kb-confirm-text").textContent = text;
  $("kb-confirm").showModal();
  return new Promise((resolve) => {
    $("kb-confirm-form").addEventListener("close", function h() {
      $("kb-confirm-form").removeEventListener("close", h);
      resolve($("kb-confirm").returnValue === "confirm");
    });
  });
}

async function confirmDeleteDoc(doc) {
  if (!await askConfirm(`Excluir "${doc.name}" e todos os seus fragmentos, vetores e arquivos?`)) return;
  await api(`/api/rag/documents/${doc.uuid}/delete`, { method: "DELETE", body: { confirm: true } });
  await loadDocs();
}

$("kb-create").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("kb-name").value.trim();
  if (!name) return;
  const b = await api("/api/rag/bases", { method: "POST", body: { name } });
  $("kb-name").value = "";
  await loadBases();
  selectBase(b);
});

$("kb-rename").addEventListener("click", async () => {
  const name = prompt("Novo nome da base:", currentBase.name);
  if (!name || !name.trim()) return;
  const b = await api(`/api/rag/bases/${currentBase.uuid}/manage`, { method: "PUT", body: { name: name.trim() } });
  currentBase = b;
  $("kb-docs-title").textContent = `Documentos — ${b.name}`;
  await loadBases();
});

$("kb-delete").addEventListener("click", async () => {
  if (!await askConfirm(`Excluir a base "${currentBase.name}" e todos os documentos?`)) return;
  await api(`/api/rag/bases/${currentBase.uuid}/manage`, { method: "DELETE", body: { confirm: true } });
  currentBase = null;
  $("kb-docs-section").hidden = true;
  await loadBases();
});

$("kb-upload-open").addEventListener("click", async () => {
  const { openUploadDialog } = await import("./upload.js");
  await openUploadDialog({
    baseUuid: currentBase ? currentBase.uuid : null,
    onPublished: async () => { await loadBases(); if (currentBase) await loadDocs(); },
  });
  await loadBases();
  if (currentBase) await loadDocs();
});

$("kb-doc-close").addEventListener("click", () => $("kb-doc").close());

loadBases().catch((err) => { $("kb-state").textContent = `Falha: ${err.message}`; });