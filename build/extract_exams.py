#!/usr/bin/env python3
"""Extract real JLPT exam sentences containing each 補助動詞 from the local
N1-N5 question banks (with source attribution).

Output: data/exams/hojodoushi.json  {type_id: [{id, level, source, jp, answer_text}]}
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
OUT = ROOT / "data" / "exams" / "hojodoushi.json"

PATTERNS = {
    "te-miru": r"[てで]み(?:る|た|て|ます|よう|ろ)",
    "te-oku": r"てお(?:く|い|き|こ)",
    "te-shimau": r"てしま(?:う|っ|い|わ)|ちゃ(?:う|っ|い)|じゃ(?:う|っ|い)",
    "te-iru": r"てい(?:る|た|ます|て)",
    "te-aru": r"てあ(?:る|り|っ)",
    "te-kuru": r"てく(?:る|き|こ|れ)|てき(?:た|て|ます)",
    "te-iku": r"ていく|ていき|ていこ|ていった|いってしま",
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
COMPILED = {k: re.compile(v) for k, v in PATTERNS.items()}


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


def collect_exam(d, level):
    text = (d.get("question") or "")
    opts = d.get("options") or []
    answer = d.get("answer")
    ans_text = ""
    if isinstance(answer, int) and 0 <= answer < len(opts):
        ans_text = opts[answer]
    return {
        "id": d.get("id", ""),
        "level": level,
        "source": d.get("source", ""),
        "stem": text if len(text) <= 220 else text[:220] + "…",
        "answer_text": ans_text,
        "options": opts[:4],
    }


def main():
    found = {k: [] for k in PATTERNS}
    seen = {k: set() for k in PATTERNS}
    for level, root in BANKS.items():
        if not root.exists():
            continue
        files = list(root.glob("**/*.json"))
        for f in files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            blob_q = d.get("question") or d.get("stem") or ""
            if not blob_q:
                continue
            rec = None
            for tid, pat in COMPILED.items():
                hits = [s for s in sentences(blob_q, pat) if f"{level}:{s}" not in seen[tid]]
                if not hits:
                    continue
                if len(found[tid]) >= 8:
                    continue
                s = hits[0]
                key = f"{level}:{s}"
                if key in seen[tid]:
                    continue
                seen[tid].add(key)
                if rec is None:
                    rec = collect_exam(d, level)
                found[tid].append({
                    "id": rec["id"], "level": level, "source": rec["source"],
                    "jp": s, "answer_text": rec["answer_text"],
                })
            # fallback: options as sentence source when stem is a bare word
            if not rec and len(blob_q) <= 8:
                for tid, pat in COMPILED.items():
                    if len(found[tid]) >= 8:
                        continue
                    for o in d.get("options") or []:
                        hits = sentences(o, pat)
                        if hits and f"{level}:{hits[0]}" not in seen[tid]:
                            seen[tid].add(f"{level}:{hits[0]}")
                            found[tid].append({
                                "id": d.get("id", ""), "level": level,
                                "source": d.get("source", ""), "jp": hits[0],
                                "answer_text": d.get("answer_text", ""),
                            })
                            break
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    for tid in PATTERNS:
        print(f"{tid:12} {len(found[tid])}")


if __name__ == "__main__":
    main()
