// Página Agentes (M1): CRUD, rascunhos, publicar, arquivar, exemplos.
import { api } from "./api.js";

const $ = (id) => document.getElementById(id);
let selected = null; // uuid do perfil
let draftUuid = null; // uuid da versão em edição

function state(msg) {
  $("ag-state").textContent = msg || "";
}

async function refresh() {
  const showArchived = $("ag-show-archived").checked;
  const data = await api(showArchived ? "/api/agents?archived=1" : "/api/agents");
  const ul = $("ag-list");
  ul.innerHTML = "";
  for (const d of data.results) {
    const li = document.createElement("li");
    const pub = d.versions.find((v) => v.published);
    li.textContent = `${d.name} [${d.kind}]${d.archived ? " (arquivado)" : ""} — r${d.versions.length} ${pub ? `(publicada r${pub.revision})` : "(sem publicação)"}`;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn sm";
    btn.textContent = "Abrir";
    btn.addEventListener("click", () => openProfile(d.uuid));
    li.appendChild(document.createTextNode(" "));
    li.appendChild(btn);
    ul.appendChild(li);
  }
  if (!data.results.length) state("Nenhum agente. Crie um ou use os exemplos.");
}

async function openProfile(uuid) {
  selected = uuid;
  const d = await api(`/api/agents/${uuid}`);
  $("ag-versions-section").hidden = false;
  $("ag-versions-title").textContent = `Versões — ${d.name}`;
  const ul = $("ag-versions");
  ul.innerHTML = "";
  for (const v of d.versions) {
    const li = document.createElement("li");
    li.textContent = `r${v.revision} ${v.published ? "publicada" : "rascunho"}${v.complete ? "" : " (incompleta: sem modelo)"}`;
    if (!v.published) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn sm";
      btn.textContent = "Editar";
      btn.addEventListener("click", () => editDraft(v));
      li.appendChild(document.createTextNode(" "));
      li.appendChild(btn);
    }
    ul.appendChild(li);
  }
  $("ag-edit").hidden = true;
  state(d.archived ? "Perfil arquivado: não pode ser selecionado nas conversas." : "");
}

function editDraft(v) {
  draftUuid = v.uuid;
  $("ag-edit").hidden = false;
  $("ag-edit-rev").textContent = `r${v.revision}`;
  $("ag-task").value = v.task_instructions || "";
  $("ag-model").value = v.model || "";
}

$("ag-create").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const d = await api("/api/agents", {
      method: "POST",
      body: { name: $("ag-name").value, kind: $("ag-kind").value, description: $("ag-desc").value },
    });
    state(`Agente "${d.name}" criado como rascunho r1.`);
    await refresh();
    await openProfile(d.uuid);
  } catch (err) {
    state(err.message);
  }
});

$("ag-examples").addEventListener("click", async () => {
  try {
    const out = await api("/api/agents/examples", { method: "POST", body: {} });
    state(`Exemplos prontos: ${out.definitions} perfis novos, ${out.versions} rascunhos novos.`);
    await refresh();
  } catch (err) {
    state(err.message);
  }
});

$("ag-show-archived").addEventListener("change", refresh);

$("ag-new-draft").addEventListener("click", async () => {
  if (!selected) return;
  try {
    const v = await api(`/api/agents/${selected}/drafts`, { method: "POST", body: {} });
    state(`Rascunho r${v.revision} criado.`);
    await openProfile(selected);
  } catch (err) {
    state(err.message);
  }
});

$("ag-duplicate").addEventListener("click", async () => {
  if (!selected) return;
  try {
    const d = await api(`/api/agents/${selected}/duplicate`, { method: "POST", body: {} });
    state(`Duplicado como "${d.name}".`);
    await refresh();
    await openProfile(d.uuid);
  } catch (err) {
    state(err.message);
  }
});

$("ag-archive").addEventListener("click", async () => {
  if (!selected) return;
  try {
    const cur = await api(`/api/agents/${selected}`);
    const d = await api(`/api/agents/${selected}`, {
      method: "PATCH",
      body: { archived: !cur.archived },
    });
    state(d.archived ? "Perfil arquivado." : "Perfil desarquivado.");
    await refresh();
    await openProfile(selected);
  } catch (err) {
    state(err.message);
  }
});

$("ag-save").addEventListener("click", async () => {
  if (!draftUuid) return;
  try {
    const v = await api(`/api/agent-versions/${draftUuid}`, {
      method: "PATCH",
      body: { task_instructions: $("ag-task").value, model: $("ag-model").value },
    });
    state(`Rascunho r${v.revision} salvo.`);
    await openProfile(selected);
  } catch (err) {
    state(err.message);
  }
});

$("ag-publish").addEventListener("click", async () => {
  if (!draftUuid) return;
  try {
    const v = await api(`/api/agent-versions/${draftUuid}/publish`, { method: "POST", body: {} });
    state(`Versão r${v.revision} publicada (imutável).`);
    await openProfile(selected);
  } catch (err) {
    state(err.message);
  }
});

refresh().catch((err) => state(err.message));
