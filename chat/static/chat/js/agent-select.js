// Seletor Chat/Agente/Equipe por conversa (M1). Independente do chat.js:
// lê a uuid da URL, carrega perfis e PATCHea a seleção. Sem delegação aqui.
import { api } from "./api.js";

const $ = (id) => document.getElementById(id);

function convUuid() {
  const m = location.pathname.match(/^\/c\/([^/]+)\/?$/);
  return m ? m[1] : null;
}

async function sync() {
  const modeEl = $("agent-mode");
  const selEl = $("agent-select");
  if (!modeEl || !selEl) return;
  const uuid = convUuid();
  const disabled = !uuid;
  modeEl.disabled = disabled;
  selEl.disabled = disabled;
  let profiles = [];
  try {
    const data = await api("/api/agents");
    profiles = data.results;
  } catch {
    return;
  }
  selEl.innerHTML = "";
  const none = document.createElement("option");
  none.value = "";
  none.textContent = profiles.length ? "Escolher perfil…" : "Sem perfis (ver Agentes)";
  selEl.appendChild(none);
  for (const p of profiles) {
    const pub = p.versions.find((v) => v.published);
    const opt = document.createElement("option");
    opt.value = p.uuid;
    opt.textContent = pub ? `${p.name} (r${pub.revision})` : `${p.name} (sem publicação)`;
    selEl.appendChild(opt);
  }
  if (!uuid) {
    modeEl.value = "chat";
    showTeamBanner("chat");
    return;
  }
  try {
    const conv = await api(`/api/conversations/${uuid}`);
    modeEl.value = conv.agent_mode || "chat";
    selEl.value = conv.agent_definition_uuid || "";
  } catch {
    modeEl.value = "chat";
  }
  showTeamBanner(modeEl.value);
}

function showTeamBanner(mode) {
  const banner = $("team-banner");
  if (banner) banner.hidden = mode !== "team";
}

// Envios serializados: trocas rápidas (perfil + modo) geram PATCHes
// sobrepostos e a resposta mais lenta sobrescreveria o DOM com o modo
// antigo. A fila garante que o último estado exibido é o mais recente.
let pushQueue = Promise.resolve();

function push() {
  const uuid = convUuid();
  if (!uuid) return Promise.resolve();
  // Intenção capturada no momento do evento: a resposta de um envio
  // anterior nunca pode sobrescrever a escolha mais recente do usuário.
  const body = { agent_mode: $("agent-mode").value };
  // Perfil sempre enviado quando escolhido (mesmo em Chat, onde fica inerte):
  // permite pré-escolher o perfil antes de trocar para Agente/Equipe.
  if ($("agent-select").value) body.agent_definition_uuid = $("agent-select").value;
  else if (body.agent_mode !== "chat") body.agent_definition_uuid = null;
  pushQueue = pushQueue.then(() => doPush(uuid, body)).catch(() => {});
  return pushQueue;
}

async function doPush(uuid, body) {
  const modeEl = $("agent-mode");
  const selEl = $("agent-select");
  try {
    const conv = await api(`/api/conversations/${uuid}`, { method: "PATCH", body });
    modeEl.value = conv.agent_mode || "chat";
    selEl.value = conv.agent_definition_uuid || "";
    showTeamBanner(modeEl.value);
  } catch (err) {
    alert(err.message);
    await sync();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  if (!$("agent-mode")) return;
  $("agent-mode").addEventListener("change", push);
  $("agent-select").addEventListener("change", push);
  const origReplace = history.replaceState.bind(history);
  history.replaceState = (...args) => {
    origReplace(...args);
    sync().catch(() => {});
  };
  sync().catch(() => {});
});
