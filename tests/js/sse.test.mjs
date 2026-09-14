// Teste do parser SSE (chat/static/chat/js/sse.js) — SDD §12 item 4.
// Rode: node --test tests/js/sse.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readSSE } from "../../chat/static/chat/js/sse.js";

function fakeResponse(chunks) {
  return {
    body: {
      getReader() {
        let i = 0;
        return {
          async read() {
            if (i >= chunks.length) return { done: true, value: undefined };
            return { done: false, value: chunks[i++] };
          },
          releaseLock() {},
        };
      },
    },
  };
}

const enc = (s) => new TextEncoder().encode(s);

async function collect(chunks) {
  const out = [];
  for await (const ev of readSSE(fakeResponse(chunks))) out.push(ev);
  return out;
}

test("evento quebrado entre chunks é remontado", async () => {
  const evs = await collect([enc('data: {"type":"a"'), enc(',"n":1}\n\n')]);
  assert.deepEqual(evs, [{ type: "a", n: 1 }]);
});

test("UTF-8 dividido no meio do caractere", async () => {
  const full = enc('data: {"type":"text_delta","text":"Olá 🙂"}\n\n');
  const cut = full.length - 5; // corta dentro do emoji
  const evs = await collect([full.slice(0, cut), full.slice(cut)]);
  assert.deepEqual(evs, [{ type: "text_delta", text: "Olá 🙂" }]);
});

test("CRLF, vários eventos no mesmo chunk e comentários ignorados", async () => {
  const raw = ': ping\r\ndata: {"type":"x"}\r\n\r\ndata: {"type":"y"}\n\n: outro\n';
  const evs = await collect([enc(raw)]);
  assert.deepEqual(evs, [{ type: "x" }, { type: "y" }]);
});

test("campos data em múltiplas linhas concatenam com \\n", async () => {
  const evs = await collect([enc("data: linha1\ndata: linha2\n\n")]);
  // "linha1\nlinha2" não é JSON: erro de protocolo com mensagem padrão
  assert.deepEqual(evs, [{ type: "error", code: "protocol", message: "Evento SSE inválido." }]);
});

test("JSON inválido vira erro de protocolo, sem quebrar o stream", async () => {
  const evs = await collect([enc("data: {quebrado\n\n"), enc('data: {"type":"ok"}\n\n')]);
  assert.deepEqual(evs, [
    { type: "error", code: "protocol", message: "Evento SSE inválido." },
    { type: "ok" },
  ]);
});

test("markdown/código partido entre chunks chega intacto ao done", async () => {
  const a = 'data: {"type":"text_delta","text":"```py\\nprint(1';
  const b = ')\\n``` **negrito**"}\n\ndata: {"type":"done","text":"x"}\n\n';
  const evs = await collect([enc(a), enc(b)]);
  assert.equal(evs[0].text, "```py\nprint(1)\n``` **negrito**");
  assert.equal(evs[1].type, "done");
});
