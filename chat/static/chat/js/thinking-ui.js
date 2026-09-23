// Controle Pensamento por conversa (TV-1): modo, nível, orçamento e resumo.
// Módulo próprio para não disputar edições com o compositor (chat.js).
// Lê a conversa atual da URL (/c/<uuid>/); salva via PATCH autenticado.
import { api } from "./api.js";

const $ = (id) => document.getElementById(id);

const MODES = ["default", "disabled", "enabled"];

// Escolha antes da primeira conversa existir (espelha pendingLevel do chat.js).
// O chat.js consome window.__pendingThinking ao criar a conversa.
let pending = null;
window.__pendingThinking = null;

function convUuid() {
  const m = location.pathname.match(/\/c\/([0-9a-f-]{36})\/?/i);
  return m ? m[1] : null;
}

function closeMine() {
  $("thinking-pop").hidden = true;
  $("thinking-btn").setAttribute("aria-expanded", "false");
}

function setDisabled(disabled) {
  for (const id of ["think-mode-default", "think-mode-disabled", "think-mode-enabled",
    "thinking-level", "thinking-budget", "thinking-summary"]) {
    $(id).disabled = disabled;
  }
}

async function load() {
  const uuid = convUuid();
  if (!uuid) {
    // Sem conversa: controles editam o pendente, aplicado ao criar.
    const p = pending || { thinking_mode: "default", thinking_level: "medium", thinking_budget: 1024, thinking_show_summary: false };
    document.querySelector(`input[name="think-mode"][value="${p.thinking_mode}"]`).checked = true;
    $("thinking-level").value = p.thinking_level;
    $("thinking-budget").value = String(p.thinking_budget);
    $("thinking-summary").checked = !!p.thinking_show_summary;
    $("thinking-state").textContent = "Será aplicado ao criar a conversa.";
    setDisabled(false);
    return;
  }
  $("thinking-state").textContent = "Carregando…";
  try {
    const d = await api(`/api/conversations/${uuid}`);
    const mode = MODES.includes(d.thinking_mode) ? d.thinking_mode : "default";
    document.querySelector(`input[name="think-mode"][value="${mode}"]`).checked = true;
    $("thinking-level").value = d.thinking_level || "medium";
    $("thinking-budget").value = String(d.thinking_budget || 1024);
    $("thinking-summary").checked = !!d.thinking_show_summary;
    const cap = (d.thinking_support && d.thinking_support.capability) || "unknown";
    if (cap === "unknown") {
      $("thinking-state").textContent =
        "Modelo sem suporte confirmado: os controles ficam visíveis, mas nada é enviado.";
    } else {
      const names = { adaptive: "adaptativo", legacy: "orçamento manual", always_on: "sempre ativo", none: "sem pensamento" };
      $("thinking-state").textContent = `Modelo com pensamento ${names[cap] || cap}.`;
    }
    setDisabled(false);
    if (cap === "none") setDisabled(true);
  } catch (e) {
    $("thinking-state").textContent = "Erro ao carregar: " + e.message;
    setDisabled(true);
  }
}

async function save(patch) {
  const uuid = convUuid();
  if (!uuid) {
    pending = { ...(pending || { thinking_mode: "default", thinking_level: "medium", thinking_budget: 1024, thinking_show_summary: false }), ...patch };
    window.__pendingThinking = { ...pending };
    $("thinking-state").textContent = "Será aplicado ao criar a conversa.";
    return;
  }
  try {
    await api(`/api/conversations/${uuid}`, { method: "PATCH", body: patch });
    $("thinking-state").textContent = "Salvo. Vale para a próxima resposta.";
  } catch (e) {
    $("thinking-state").textContent =
      e.code === "active_run"
        ? "Disponível após concluir ou interromper a resposta."
        : "Erro ao salvar: " + e.message;
    await load(); // reverte para o estado do servidor
  }
}

function toggle() {
  const pop = $("thinking-pop");
  const willOpen = pop.hidden;
  document.querySelectorAll("#model-pop,#actions-pop,#tools-pop,#sources-pop").forEach((p) => {
    p.hidden = true;
  });
  for (const id of ["model-btn", "tools-btn", "sources-btn", "actions-btn"]) {
    document.getElementById(id)?.setAttribute("aria-expanded", "false");
  }
  pop.hidden = !willOpen;
  $("thinking-btn").setAttribute("aria-expanded", String(willOpen));
  if (willOpen) {
    load();
    pop.focus();
  }
}

export function initThinking() {
  $("thinking-btn").addEventListener("click", toggle);
  document.querySelectorAll('input[name="think-mode"]').forEach((r) =>
    r.addEventListener("change", () => save({ thinking_mode: r.value }))
  );
  $("thinking-level").addEventListener("change", (e) => save({ thinking_level: e.target.value }));
  $("thinking-budget").addEventListener("change", (e) => save({ thinking_budget: Number(e.target.value) }));
  $("thinking-summary").addEventListener("change", (e) => save({ thinking_show_summary: e.target.checked }));
  document.addEventListener("pointerdown", (e) => {
    const pop = $("thinking-pop");
    if (!pop.hidden && !pop.contains(e.target) && e.target !== $("thinking-btn") &&
        !$("thinking-btn").contains(e.target)) {
      closeMine();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeMine();
  });
}

initThinking();
