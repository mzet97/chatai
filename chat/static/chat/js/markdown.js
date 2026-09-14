// Renderizador Markdown seguro (sem innerHTML em nenhum caminho).
// Escapa todo HTML; constrói apenas elementos via DOM. Blocos: títulos,
// parágrafos, listas, citações, código com linguagem, tabelas, hr.
export function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function inline(text, out) {
  // Tokeniza code, bold, itálico e links http(s); resto vira texto puro.
  const token = /(`[^`\n]+`|\*\*(.+?)\*\*|\*([^*\n]+)\*|\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\))/g;
  let last = 0;
  let m;
  while ((m = token.exec(text)) !== null) {
    if (m.index > last) out.append(document.createTextNode(text.slice(last, m.index)));
    const t = m[1];
    if (t.startsWith("`")) {
      out.append(el("code", "inline", t.slice(1, -1)));
    } else if (t.startsWith("**")) {
      const b = el("strong");
      b.textContent = m[2];
      out.append(b);
    } else if (t.startsWith("*")) {
      const e = el("em");
      e.textContent = m[3];
      out.append(e);
    } else {
      const a = el("a", null, m[4]);
      a.href = m[5];
      a.rel = "noopener noreferrer";
      out.append(a);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.append(document.createTextNode(text.slice(last)));
}

function isTableRow(line) {
  return /^\s*\|.*\|\s*$/.test(line);
}

function isDelimRow(line) {
  return /^\s*\|?[\s:|-]+\|?[\s:|-]*$/.test(line) && /-/.test(line) && !/[a-zA-Z0-9]/.test(line.replace(/[:|\s-]/g, ""));
}

function splitCells(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

function buildTable(headCells, bodyRows) {
  const wrap = el("div", "table-wrap");
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const hr = document.createElement("tr");
  for (const c of headCells) {
    const th = el("th", null, c);
    hr.append(th);
  }
  thead.append(hr);
  const tbody = document.createElement("tbody");
  for (const row of bodyRows) {
    const tr = document.createElement("tr");
    for (const c of row) tr.append(el("td", null, c));
    tbody.append(tr);
  }
  table.append(thead, tbody);
  wrap.append(table);
  return wrap;
}

function codeBlock(lang, code) {
  const fig = el("figure", "codeblock");
  const head = el("div", "codeblock-head");
  head.append(el("span", "codeblock-lang", lang || "texto"));
  const btn = el("button", "copy-btn", "Copiar");
  btn.type = "button";
  btn.addEventListener("click", () => {
    navigator.clipboard
      .writeText(code)
      .then(() => {
        btn.textContent = "Copiado";
        setTimeout(() => {
          btn.textContent = "Copiar";
        }, 1600);
      })
      .catch(() => {});
  });
  head.append(btn);
  const pre = document.createElement("pre");
  const c = document.createElement("code");
  c.textContent = code.replace(/\n$/, "");
  if (lang) c.setAttribute("data-lang", lang);
  pre.append(c);
  fig.append(head, pre);
  return fig;
}

export function renderMarkdown(src) {
  const text = String(src == null ? "" : src);
  const frag = document.createDocumentFragment();
  const fence = /```(\w*)\n([\s\S]*?)(?:```|$)/g;
  let last = 0;
  let m;
  const blocks = [];
  while ((m = fence.exec(text)) !== null) {
    if (m.index > last) blocks.push({ kind: "md", text: text.slice(last, m.index) });
    blocks.push({ kind: "code", lang: m[1] || "", text: m[2] });
    last = m.index + m[0].length;
  }
  if (last < text.length) blocks.push({ kind: "md", text: text.slice(last) });
  for (const b of blocks) {
    if (b.kind === "code") frag.append(codeBlock(b.lang, b.text));
    else renderBlockText(b.text, frag);
  }
  return frag;
}

function renderBlockText(text, frag) {
  const lines = text.split("\n");
  let i = 0;
  let para = [];
  const flushPara = () => {
    if (!para.length) return;
    const p = document.createElement("p");
    para.forEach((ln, k) => {
      if (k > 0) p.append(document.createElement("br"));
      inline(ln, p);
    });
    frag.append(p);
    para = [];
  };
  const flushList = (items, ordered) => {
    const list = document.createElement(ordered ? "ol" : "ul");
    for (const it of items) {
      const li = document.createElement("li");
      inline(it, li);
      list.append(li);
    }
    frag.append(list);
  };

  while (i < lines.length) {
    const line = lines[i];
    const t = line.trim();
    if (!t) {
      flushPara();
      i++;
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(t);
    if (h) {
      flushPara();
      const level = Math.min(h[1].length + 1, 4);
      const hn = document.createElement("h" + level);
      inline(h[2], hn);
      frag.append(hn);
      i++;
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(t)) {
      flushPara();
      frag.append(document.createElement("hr"));
      i++;
      continue;
    }
    if (t.startsWith(">")) {
      flushPara();
      const quote = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quote.push(lines[i].trim().replace(/^>\s?/, ""));
        i++;
      }
      const bq = document.createElement("blockquote");
      const qp = document.createElement("p");
      quote.forEach((ln, k) => {
        if (k > 0) qp.append(document.createElement("br"));
        inline(ln, qp);
      });
      bq.append(qp);
      frag.append(bq);
      continue;
    }
    const ul = /^[-*+]\s+(.*)$/.exec(t);
    const ol = /^\d+[.)]\s+(.*)$/.exec(t);
    if (ul || ol) {
      flushPara();
      const items = [];
      const ordered = !!ol;
      while (i < lines.length) {
        const mm = ordered
          ? /^\d+[.)]\s+(.*)$/.exec(lines[i].trim())
          : /^[-*+]\s+(.*)$/.exec(lines[i].trim());
        if (!mm) break;
        items.push(mm[1]);
        i++;
      }
      flushList(items, ordered);
      continue;
    }
    if (isTableRow(line) && i + 1 < lines.length && isDelimRow(lines[i + 1])) {
      flushPara();
      const head = splitCells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(splitCells(lines[i]));
        i++;
      }
      frag.append(buildTable(head, rows));
      continue;
    }
    para.push(line);
    i++;
  }
  flushPara();
}
