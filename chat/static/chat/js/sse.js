// Parser SSE tolerante a frames e UTF-8 divididos entre chunks de rede.
// Usa TextDecoder em modo streaming + buffer de linha; só dispara eventos completos.
export async function* readSSE(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let dataLines = [];
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (value) buf += decoder.decode(value, { stream: true });
      if (done) {
        buf += decoder.decode();
        if (buf && !buf.endsWith("\n")) buf += "\n";
      }
      let idx;
      while ((idx = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, idx).replace(/\r$/, "");
        buf = buf.slice(idx + 1);
        if (line === "") {
          if (dataLines.length) {
            const raw = dataLines.join("\n");
            dataLines = [];
            try {
              yield JSON.parse(raw);
            } catch {
              yield { type: "error", code: "protocol", message: "Evento SSE inválido." };
            }
          }
        } else if (line.startsWith(":")) {
          // comentário/ping: ignorar
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).trimStart());
        }
      }
      if (done) return;
    }
  } finally {
    reader.releaseLock();
  }
}
