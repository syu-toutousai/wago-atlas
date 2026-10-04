#!/usr/bin/env python3
"""Extract real JLPT exam sentences containing each item of every dimension.

Reads all data/*.json (except dimensions.json), uses item["match"] if given,
else a pattern derived from item["word"]; items with extract=false are skipped.
Scans the local N1-N5 banks and writes data/exams/<dimension>.json:
    {item_id: [{id, level, source, jp, answer_text}, ...]}  (max 8 each)
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANKS = {
    "n1": Path("/home/naruto/scratch/jlpt-n1-question-bank/past-exams"),
    "n2": Path("/home/naruto/scratch/jlpt-question-bank/past-exams/n2"),
    "n3": Path("/home/naruto/scratch/jlpt-question-bank/past-exams/n3"),
    "n4": Path("/home/naruto/scratch/jlpt-question-bank/past-exams/n4"),
    "n5": Path("/home/naruto/scratch/jlpt-question-bank/past-exams/n5"),
}
MAX_PER_ITEM = 8

# hand-tuned patterns for M1（word からの導出より精密）
OVERRIDES = {
    "te-miru": r"[てで]み(?:る|た|て|ます|よう|ろ)",
    "te-oku": r"てお(?:く|い|き|こ)",
    "te-shimau": r"てしま(?:う|っ|い|わ)|ちゃ(?:う|っ|い)|じゃ(?:う|っ|い)",
    "te-iru": r"てい(?:る|た|ます|て)",
    "te-aru": r"てあ(?:る|り|っ)",
    "te-kuru": r"てく(?:る|き|こ|れ)|てき(?:た|て|ます)",
    "te-iku": r"ていく|ていき|ていこ|ていった",
    "te-kureru": r"てくれ(?:る|た|て|な)|てくださ(?:る|い|っ)",
    "te-morau": r"てもら(?:う|っ|い|え|お)|ていただ(?:く|い|き|け)",
    "te-ageru": r"てあげ(?:る|た|て)|てさしあげ",
    "te-yaru": r"てや(?:る|っ|り|れ)",
    "te-miseru": r"てみせ(?:る|た|て)",
    "hajimeru": r"[ぁ-ん]始め(?:る|た|て)|し始め|み始め|き始め|り始め|い始め",
    "tsuzukeru": r"[ぁ-ん]続け(?:る|た|て)|し続け|み続け|き続け|り続け",
    "owaru": r"[ぁ-ん]終わ(?:る|った|って)|し終わ|み終わ|き終わ|り終わ",
    "kiru": r"使い切|疲れ切|割り切れ|踏み切|言い切|読み切|食べ切|やり切|知り切|売り切",
    "komu": r"思い込|考え込|落ち込|話し込|眠り込|座り込|飛び込|吹き込|冷え込|静まり込",
    "nuku": r"走り抜|考え抜|知り抜|やり抜|生き抜|守り抜|歌い抜|戦い抜|勝ち抜",
    "kakeru": r"言いかけ|読みかけ|書きかけ|やりかけ|食べかけ|死にかけ|話しかけ|問いかけ|投げかけ|呼びかけ",
    "naosu": r"書き直|描き直|やり直|立て直|見直|読み直|考え直|作り直",
}


def word_pattern(word):
    """Turn a display word like 〜けれど（も）／〜けど into a safe regex."""
    w = word.replace("〜", "")
    parts = re.split(r"[・／/、]", w)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        tokens = re.split(r"(（[^）]*）)", p)
        seg = ""
        for t in tokens:
            if t.startswith("（") and t.endswith("）"):
                inner = t[1:-1]
                alts = "|".join(re.escape(x) for x in re.split(r"[／/]", inner) if x)
                seg += f"(?:{alts})?" if alts else ""
            else:
                seg += re.escape(t)
        if seg:
            out.append(seg)
    return "|".join(out)


def sentences(text, pat, max_len=160):
    out = []
    for s in re.split(r"(?<=[。！？])", text or ""):
        s = s.strip()
        if s and pat.search(s) and len(s) <= max_len:
            out.append(s)
    if not out and text:
        m = pat.search(text)
        if m:
            a = max(0, m.start() - 60)
            out.append(text[a:m.end() + 60].replace("\n", " ").strip())
    return out


def main():
    data_dir = ROOT / "data"
    exam_dir = data_dir / "exams"
    exam_dir.mkdir(parents=True, exist_ok=True)
    for df in sorted(data_dir.glob("*.json")):
        if df.name in ("dimensions.json",):
            continue
        data = json.loads(df.read_text(encoding="utf-8"))
        items = data.get("items") or []
        if not items:
            continue
        found = {it["id"]: [] for it in items}
        seen = {it["id"]: set() for it in items}
        compiled = {}
        for it in items:
            if it.get("extract") is False:
                compiled[it["id"]] = None
                continue
            pat = it.get("match") or OVERRIDES.get(it["id"]) or word_pattern(it["word"])
            compiled[it["id"]] = re.compile(pat) if pat else None

        for level, root in BANKS.items():
            if not root.exists():
                continue
            for f in root.glob("**/*.json"):
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                blob = d.get("question") or d.get("stem") or ""
                if not blob:
                    continue
                opts = d.get("options") or []
                ans = d.get("answer")
                ans_text = opts[ans - 1] if isinstance(ans, int) and 0 < ans <= len(opts) else ""
                for it in items:
                    pat = compiled.get(it["id"])
                    if pat is None or len(found[it["id"]]) >= MAX_PER_ITEM:
                        continue
                    hits = [s for s in sentences(blob, pat) if f"{level}:{s}" not in seen[it["id"]]]
                    if not hits and len(blob) <= 10:
                        for o in opts:
                            hits = sentences(o, pat)
                            if hits:
                                break
                    if not hits:
                        continue
                    s = hits[0]
                    seen[it["id"]].add(f"{level}:{s}")
                    found[it["id"]].append({
                        "id": d.get("id", ""), "level": level,
                        "source": d.get("source", ""), "jp": s,
                        "answer_text": ans_text or d.get("answer_text", ""),
                    })
        out = exam_dir / df.name
        out.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
        total = sum(len(v) for v in found.values())
        print(f"{df.name}: {len(items)} items, {total} exam sentences -> {out.name}")


if __name__ == "__main__":
    main()
