"""Fragmentação por estrutura + orçamento do tokenizer (RAG-04).

Alvo 320 tokens, overlap ≤48 (unidades inteiras), entrada E5 ≤510 tokens
com prefixo `passage: `. Nunca trunca silenciosamente: subdivide unidades
grandes preservando localizadores e verifica cobertura total.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TARGET_TOKENS = 320
OVERLAP_TOKENS = 48
MAX_INPUT_TOKENS = 510  # com prefixo, antes dos especiais; modelo aceita 512
UNIT_SPLIT_TOKENS = 160
HINT_MAX_WORDS = 24

PASSAGE_PREFIX = "passage: "

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…\n])\s+")
_PDF_MARK = re.compile(r"\[p(\d+)\]\n?")


@dataclass
class Unit:
    text: str
    locator: dict


@dataclass
class ChunkDraft:
    text: str  # passagem original citável
    context_hint: str  # só busca, nunca citado
    search_text: str  # representação de busca (hint + texto)
    locator: dict
    token_count: int  # tokens da entrada E5 completa (com prefixo)
    order: int = 0
    split: list = field(default_factory=list)  # [k, n] se subdividido


def prefixed(text: str) -> str:
    return text if text.startswith(PASSAGE_PREFIX) else PASSAGE_PREFIX + text


def _units_for_ext(text: str, locators: dict, ext: str) -> list[Unit]:
    if ext == ".pdf":
        return _units_pdf(text)
    if ext == ".docx":
        return _units_docx(text, locators)
    if ext == ".md":
        return _units_md(text, locators)
    return _units_txt(text)


def _units_pdf(text: str) -> list[Unit]:
    units: list[Unit] = []
    page = 0
    for part in _PDF_MARK.split(text):
        if re.fullmatch(r"\d+", (part or "").strip()):
            page = int(part.strip())
            continue
        for para in (part or "").split("\n\n"):
            para = para.strip()
            if para:
                units.append(Unit(para, {"page": page}))
    return units


def _units_docx(text: str, locators: dict) -> list[Unit]:
    blocks = locators.get("blocks", []) if isinstance(locators, dict) else []
    parts = [p.strip() for p in text.split("\n\n")]
    units: list[Unit] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        loc = {"block": blocks[i]} if i < len(blocks) else {"block": f"p{i + 1}"}
        units.append(Unit(part, loc))
    return units


def _units_md(text: str, locators: dict) -> list[Unit]:
    lines = text.split("\n")
    sections = locators.get("sections", []) if isinstance(locators, dict) else []
    units: list[Unit] = []
    path: list[str] = []
    buf: list[tuple[int, str]] = []  # (nº linha, conteúdo)
    in_fence = False

    def flush(end_line: int):
        nonlocal buf
        paras: list[list[tuple[int, str]]] = []
        cur: list[tuple[int, str]] = []
        for nl, content in buf:
            if content.strip():
                cur.append((nl, content))
            elif cur:
                paras.append(cur)
                cur = []
        if cur:
            paras.append(cur)
        for para in paras:
            t = "\n".join(c for _, c in para).strip()
            if t:
                units.append(
                    Unit(
                        t,
                        {
                            "section": list(path),
                            "lines": [para[0][0], para[-1][0]],
                        },
                    )
                )
        buf = []

    start = 0
    for i, line in enumerate(lines, start=1):
        s = line.strip()
        if s.startswith("```"):
            if not in_fence:
                flush(i - 1)
                in_fence = True
                start = i
            else:
                in_fence = False
                code = "\n".join(lines[start - 1 : i]).strip()
                if code:
                    units.append(
                        Unit(
                            code,
                            {"section": list(path), "lines": [start, i], "code": True},
                        )
                    )
            continue
        if in_fence:
            continue
        if s.startswith("#"):
            flush(i - 1)
            title = s.strip("# ").strip()
            level = len(s) - len(s.lstrip("#"))
            # Reconstrói caminho: mantém ancestrais de nível menor.
            path = _md_path(path, title, level, sections, i)
            continue
        buf.append((i, line))
    flush(len(lines))
    return units


def _md_path(
    path: list, title: str, level: int, sections: list, line: int
) -> list[str]:
    # Usa a tabela de seções da extração quando casa com a linha.
    known = [s for s in sections if s.get("line") == line]
    if known:
        # Ancestrais: seções anteriores de nível menor.
        anc = [
            s["title"]
            for s in sections
            if s.get("line", 0) < line and s.get("level", 9) < level
        ]
        return anc[-3:] + [title]
    titles = [p if isinstance(p, str) else p[0] for p in path]
    return (titles + [title])[-4:]


def _units_txt(text: str) -> list[Unit]:
    units: list[Unit] = []
    buf: list[tuple[int, str]] = []
    for i, line in enumerate(text.split("\n"), start=1):
        if line.strip():
            buf.append((i, line))
        elif buf:
            t = "\n".join(c for _, c in buf).strip()
            units.append(Unit(t, {"lines": [buf[0][0], buf[-1][0]]}))
            buf = []
    if buf:
        t = "\n".join(c for _, c in buf).strip()
        units.append(Unit(t, {"lines": [buf[0][0], buf[-1][0]]}))
    return units


def _split_oversized(units: list[Unit], encode) -> list[Unit]:
    out: list[Unit] = []
    for u in units:
        if len(encode(prefixed(u.text))) <= UNIT_SPLIT_TOKENS:
            out.append(u)
            continue
        pieces = [p for p in _SENTENCE_SPLIT.split(u.text) if p.strip()]
        if len(pieces) <= 1:
            pieces = _token_windows(u.text, encode)
        acc: list[str] = []
        acc_n = 0
        for p in pieces:
            n = len(encode(p))
            if acc and acc_n + n > UNIT_SPLIT_TOKENS:
                out.append(Unit("\n".join(acc).strip(), {**u.locator}))
                acc, acc_n = [], 0
            acc.append(p)
            acc_n += n
        if acc:
            out.append(Unit("\n".join(acc).strip(), {**u.locator}))
    # Marca subdivisões do mesmo localizador original.
    return out


def _token_windows(text: str, encode) -> list[str]:
    toks = encode(text)
    if len(toks) <= UNIT_SPLIT_TOKENS:
        return [text]
    # Sem acesso ao decode aqui: divide por palavras até caber.
    words = text.split()
    parts, cur, cur_n = [], [], 0
    for w in words:
        n = len(encode(w + " "))
        if cur and cur_n + n > UNIT_SPLIT_TOKENS:
            parts.append(" ".join(cur))
            # Overlap: recarrega últimas palavras até OVERLAP_TOKENS.
            keep: list[str] = []
            keep_n = 0
            for w2 in reversed(cur):
                n2 = len(encode(w2 + " "))
                if keep_n + n2 > OVERLAP_TOKENS:
                    break
                keep.append(w2)
                keep_n += n2
            cur, cur_n = list(reversed(keep)), keep_n
        cur.append(w)
        cur_n += n
    if cur:
        parts.append(" ".join(cur))
    return parts


def _hint_for(units: list[Unit]) -> str:
    for u in reversed(units):
        sec = u.locator.get("section")
        if sec:
            hint = " > ".join(sec)
            words = hint.split()
            return " ".join(words[:HINT_MAX_WORDS])
    return ""


def chunk_extraction(
    text: str, locators: dict, ext: str, encode
) -> list[ChunkDraft]:
    """Fragmenta extração. `encode`: str -> list[int] (sem especiais)."""
    units = _split_oversized(_units_for_ext(text or "", locators or {}, ext), encode)
    drafts: list[ChunkDraft] = []
    cur: list[Unit] = []
    cur_n = 0

    def flush():
        nonlocal cur, cur_n
        if not cur:
            return
        body = "\n\n".join(u.text for u in cur)
        hint = _hint_for(cur)
        search = f"{hint}\n{body}" if hint else body
        n = len(encode(prefixed(search)))
        if n > MAX_INPUT_TOKENS:
            # Unidade patológica mesmo após split: janelas de tokens.
            for k, win in enumerate(_token_windows(body, encode)):
                ws = f"{hint}\n{win}" if hint else win
                drafts.append(
                    ChunkDraft(
                        text=win,
                        context_hint=hint,
                        search_text=ws,
                        locator={"spans": [u.locator for u in cur]},
                        token_count=len(encode(prefixed(ws))),
                        order=len(drafts),
                        split=[k + 1, 0],
                    )
                )
            # Preenche total de janelas a posteriori.
            total = sum(1 for d in drafts if d.split and d.split[1] == 0)
            for d in drafts:
                if d.split and d.split[1] == 0:
                    d.split[1] = total
        else:
            drafts.append(
                ChunkDraft(
                    text=body,
                    context_hint=hint,
                    search_text=search,
                    locator=(
                        {"spans": [u.locator for u in cur]}
                        if len(cur) > 1
                        else dict(cur[0].locator)
                    ),
                    token_count=n,
                    order=len(drafts),
                )
            )
        # Overlap: unidades finais até OVERLAP_TOKENS.
        keep: list[Unit] = []
        keep_n = 0
        for u in reversed(cur):
            n_u = len(encode(u.text))
            if keep and keep_n + n_u > OVERLAP_TOKENS:
                break
            keep.append(u)
            keep_n += n_u
        cur = list(reversed(keep))
        cur_n = keep_n

    for u in units:
        n_u = len(encode(prefixed(u.text)))
        if cur and cur_n + n_u > TARGET_TOKENS:
            flush()
        cur.append(u)
        # Soma real com separadores (barato e exato).
        probe = "\n\n".join(x.text for x in cur)
        cur_n = len(encode(prefixed(probe)))
    flush()

    # Garantia anti-truncamento: toda unidade coberta por algum chunk.
    covered: set[int] = set()
    for d in drafts:
        spans = d.locator.get("spans", [d.locator])
        for idx, u in enumerate(units):
            if u.locator in spans and u.text in d.text:
                covered.add(idx)
    assert len(covered) == len(units), (
        f"chunking perdeu {len(units) - len(covered)} de {len(units)} unidades"
    )
    for d in drafts:
        assert d.token_count <= MAX_INPUT_TOKENS, "chunk excede limite E5"
    return drafts
