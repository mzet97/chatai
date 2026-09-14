// Tela de configurações: aparência, conexão (efetiva+chave+diagnóstico), padrões, modelos.
import { api } from "./api.js";
import { initThemeSection } from "./theme.js";

const $ = (id) => document.getElementById(id);

initThemeSection();
$("last-test").textContent = localStorage.getItem("chat:last-test") || "";

async function loadEffective() {
  const d = await api("/api/settings");
  const dl = $("effective");
  dl.innerHTML = "";
  const rows = [
    ["Modelo", d.model, d.model_origin], ["Endpoint", d.base_url, d.base_url_origin],
    ["Timeout", d.timeout_seconds, d.timeout_origin], ["Tentativas", d.max_retries, d.retries_origin],
    ["Máx. saída", d.max_output_tokens, d.output_origin], ["Orçamento entrada", d.input_budget, d.budget_origin],
    ["Chave (origem)", d.key_origin, ""],
  ];
  for (const [k, v, o] of rows) {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = `${v}  (origem: ${o || "—"})`;
    dl.append(dt, dd);
  }
  $("s-model").value = d.model === "(padrão)" ? "" : "";
  $("s-base-url").value = d.base_url;
  $("s-timeout").value = d.timeout_seconds;
  $("s-retries").value = d.max_retries;
  $("key-origin").textContent = d.key_origin;
  $("keychain-av").textContent = d.keychain_available ? "sim" : "não";
  $("s-use-keychain").checked = !!d.keychain_ref;
}

async function save(partial) {
  const body = {
    default_model: $("s-model").value.trim(),
    base_url: $("s-base-url").value.trim(),
    timeout_seconds: Number($("s-timeout").value),
    max_retries: Number($("s-retries").value),
    use_keychain: $("s-use-keychain").checked,
    ...partial,
  };
  try {
    await api("/api/settings/save", { method: "POST", body });
    $("s-msg").textContent = "Salvo. Alterações valem nas próximas chamadas; .env exige reinício.";
    await loadEffective();
    await loadModels();
  } catch (e) {
    if (e.data && e.data.needs_confirmation) {
      if (confirm("O endpoint mudou. Confirmar? Credenciais e histórico só vão para este destino.")) {
        await save({ ...partial, confirm_endpoint_change: true });
      }
    } else $("s-msg").textContent = "Erro: " + e.message;
  }
}

$("s-save").addEventListener("click", () => save({}));
$("s-save-key").addEventListener("click", () => {
  const v = $("s-key").value;
  if (!v) { $("s-msg").textContent = "Vazio = manter. Nada foi alterado."; return; }
  save({ api_key: v }).then(() => { $("s-key").value = ""; });
});
$("s-remove-key").addEventListener("click", async () => {
  if (!confirm("Remover a referência da chave?")) return;
  await api("/api/settings/remove-key", { method: "POST" });
  await loadEffective();
});

$("d-run").addEventListener("click", async () => {
  const ol = $("d-steps");
  ol.innerHTML = "";
  $("d-generation").textContent = "";
  const li = document.createElement("li");
  li.textContent = "Executando…";
  ol.append(li);
  try {
    const data = await api("/api/settings/diagnose", {
      method: "POST", body: { include_generation: $("d-gen").checked },
    });
    ol.innerHTML = "";
    for (const s of data.steps) {
      const item = document.createElement("li");
      item.textContent = `${s.id}: ${s.ok ? "OK" : "FALHA"} — ${s.detail}`;
      ol.append(item);
    }
    const failed = data.steps.filter((s) => !s.ok).length;
    const summary = `Último teste: ${new Date().toLocaleString()} · ${data.steps.length - failed}/${data.steps.length} OK${failed ? " — ver detalhes acima" : ""}`;
    localStorage.setItem("chat:last-test", summary);
    $("last-test").textContent = summary;
    if (data.generation) $("d-generation").textContent = JSON.stringify(data.generation, null, 2);
  } catch (e) {
    li.textContent = "Erro: " + e.message;
  }
});

async function loadModels() {
  const meta = $("m-meta"), ul = $("m-list");
  try {
    const data = await api("/api/models");
    ul.innerHTML = "";
    const dl = $("model-list");
    dl.innerHTML = "";
    if (!data.models.length) {
      meta.textContent = data.message || "Sem modelos.";
      return;
    }
    meta.textContent = `Atualizado em ${data.fetched_at} · candidato: ${data.candidate || "—"}`;
    if (data.warning) {
      const w = $("model-warning");
      w.hidden = false;
      w.textContent = data.warning;
    } else $("model-warning").hidden = true;
    for (const m of data.models) {
      const li = document.createElement("li");
      li.textContent = `${m.display_name} (${m.id})`;
      ul.append(li);
      const opt = document.createElement("option");
      opt.value = m.id;
      dl.append(opt);
    }
  } catch (e) {
    meta.textContent = "Erro: " + e.message;
  }
}

$("m-refresh").addEventListener("click", async () => {
  $("m-meta").textContent = "Atualizando…";
  try {
    await api("/api/models", { method: "POST" });
    await loadModels();
  } catch (e) {
    $("m-meta").textContent = "Erro: " + e.message;
  }
});

await loadEffective();
await loadModels();
