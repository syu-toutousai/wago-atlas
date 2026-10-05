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
# STRICT: 短い機能語の誤命中（複合語・別語内部）を防ぐ境界付きパターン
STRICT = {
    "mada": r"まだ(?!まだ)",
    "kedo": r"(?:だ)?けど(?=[、。！？\s「」』]|$|ね|な|よ|も)",
    "noni": r"のに(?=[、。！？\s「」]|$|ね|な|よ|は|も|、)",
    "node": r"(?<![そこあど])ので",
    "deha": r"(?:^|(?<=[。！？\n]))(?!それ)では(?!な)",
    "sorede": r"(?<!は)それで(?=[、。！？」])",
    "sokode": r"そこで(?=[、。！？])",
    "demo": r"(?:^|(?<=[。！？\n]))でも(?=[、。！？」])",
    "tohaie": r"とはいえ(?!な|ま)",
    "soreni": r"それに(?=[、。加])",
    "katsu": r"かつ(?=[一-龯])",
    "nao": r"(?:^|(?<=[。！？\n]))なお(?=[、。])",
    "tokorode": r"(?:^|(?<=[。！？\n]))ところで(?=[、。])",
    "nitsuke": r"(?:(?<=[るい])|(?<=何か))につけ(?![てこ])",
    "gahayaika": r"が早いか(?![ら])",
    "sonouchi": r"そのうち(?!の)",
    "yoku": r"(?<!く)よく(?!ない|よく)",
    "tsuini": r"ついに(?!て)",
    "samo": r"(?:^|(?<=[。！？、]))さも(?!し)",
    "douka": r"(?:^|(?<=[。！？]))どうか(?=[、。お願])",
    "tatoe": r"たとえ(?!ば)",
    "mottomo": r"もっとも(?=[、。])",
    "tsui": r"(?<!つい)(?<![き])つい(?!て|で|に|つ|た)",
    "futo": r"(?<![一-龯])ふと(?![ん一-龯])",
    "mou": r"(?<!いも)もう(?!と)",
    "moshi": r"もし(?!かして)",
    "mata": r"また(?![はもま])",
    "sate": r"さて(?=[、。])",
    "tokini": r"ときに(?=[、。])",
}
OVERRIDES = {
    "te-miru": r"[てで]み(?:る|た|て|ます|よう|ろ)",
    "te-oku": r"てお(?:く|い|き|こ)",
    "te-shimau": r"てしま(?:う|っ|い|わ)|ちゃ(?:う|っ|い)|じゃ(?:う|っ|い)",
    "te-iru": r"てい(?:る|た|ます|て)",
    "te-aru": r"てあ(?:る|り|っ)",
    "te-kuru": r"てく(?:る|き|こ)|てき(?:た|て|ます)",
    "te-iku": r"ていく|ていき|ていこ|ていった",
    "te-kureru": r"てくれ(?:る|た|て|な)|てくださ(?:る|い|っ)",
    "te-morau": r"てもら(?:う|っ|い|え|お)|ていただ(?:く|い|き|け)",
    "te-ageru": r"てあげ(?:る|た|て)|てさしあげ",
    "te-yaru": r"てや(?:る|り|れ|った)(?!て)(?![気方])",
    "te-miseru": r"てみせ(?:る|た|て)",
    "hajimeru": r"[いきしちにひみりぎじびぴえけせぜてでねへめべぺ]始め(?:る|た|て)|し始め|み始め|き始め|り始め|い始め",
    "tsuzukeru": r"[いきしちにひみりぎじびぴえけせぜてでねへめべぺ]続け(?:る|た|て)|し続け|み続け|き続け|り続け",
    "owaru": r"[いきしちにひみりぎじびぴえけせぜてでねへめべぺ]終わ(?:る|った|って)|し終わ|み終わ|き終わ|り終わ",
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


CTX_BAD = re.compile(r"選びなさい|最もよい|最も適当|記号|1・2・3・4")
BAD_FRAG = re.compile(r"[（(]\s*[）)]|[（(]\d{1,3}[）)]|\[\d{1,2}\]|★")


def split_sentences(text):
    if text:
        text = "\n".join(
            ln for ln in text.split("\n")
            if not re.match(r"^\s*[（(【]?(注|中略|略)", ln) and ln.strip() not in ("i", "I")
        )
    return [s.strip() for s in re.split(r"(?<=[。！？])", text or "") if s.strip()]


def match_sentences(slist, pat, max_len=160):
    """Return [(idx, sentence)] of clean sentences containing pat."""
    out = []
    for idx, s in enumerate(slist):
        if not s or not pat.search(s) or len(s) > max_len or len(s) < 6:
            continue
        # 空欄・並べ替えマーカーを含む断片はコーパス向けに除外
        if BAD_FRAG.search(s):
            continue
        out.append((idx, s))
    return out


def ctx_of(slist, idx, blocked):
    """Previous/next sentence as readable context (empty when unusable)."""
    def ok(s):
        return (s and 6 <= len(s) <= 130 and s not in blocked
                and not CTX_BAD.search(s) and not BAD_FRAG.search(s)
                and not re.fullmatch(r"[\d\s・,，。、]+", s))
    before = slist[idx - 1] if idx - 1 >= 0 else ""
    after = slist[idx + 1] if idx + 1 < len(slist) else ""
    return (before if ok(before) else ""), (after if ok(after) else "")



def main():
    data_dir = ROOT / "data"
    exam_dir = data_dir / "exams"
    exam_dir.mkdir(parents=True, exist_ok=True)
    block_path = data_dir / "exam_blocklist.json"
    blocklist = json.loads(block_path.read_text(encoding="utf-8")) if block_path.exists() else {}
    for df in sorted(data_dir.glob("*.json")):
        if df.name in ("dimensions.json", "exam_blocklist.json"):
            continue
        data = json.loads(df.read_text(encoding="utf-8"))
        items = data.get("items") or []
        if not items:
            continue
        found = {it["id"]: [] for it in items}
        seen = {it["id"]: set() for it in items}
        bl = blocklist.get(df.stem) or []
        if isinstance(bl, dict):
            bl = list(bl.keys())
        blocked = set(bl)
        compiled = {}
        for it in items:
            if it.get("extract") is False:
                compiled[it["id"]] = None
                continue
            pat = it.get("match") or STRICT.get(it["id"]) or OVERRIDES.get(it["id"]) or word_pattern(it["word"])
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
                    slist = split_sentences(blob)
                    all_hits = match_sentences(slist, pat)
                    hits = [(i, s) for i, s in all_hits
                            if s not in blocked and f"{level}:{s}" not in seen[it["id"]]]
                    if not all_hits and len(blob) <= 10:
                        for o in opts:
                            slist2 = split_sentences(o)
                            oh = match_sentences(slist2, pat)
                            if oh:
                                slist, all_hits, hits = slist2, oh, oh
                                break
                    if not all_hits and blob:
                        m = pat.search(blob)
                        if m:
                            a = max(0, m.start() - 60)
                            frag = blob[a:m.end() + 60].replace("\n", " ").strip()
                            if (len(frag) >= 10 and not BAD_FRAG.search(frag)
                                    and frag not in blocked
                                    and f"{level}:{frag}" not in seen[it["id"]]):
                                all_hits = hits = [(-1, frag)]
                    if not hits:
                        continue
                    idx, s = hits[0]
                    seen[it["id"]].add(f"{level}:{s}")
                    cb, ca = ctx_of(slist, idx, blocked) if idx >= 0 else ("", "")
                    m2 = pat.search(s)
                    found[it["id"]].append({
                        "id": d.get("id", ""), "level": level,
                        "source": d.get("source", ""), "jp": s,
                        "ctx_before": cb, "ctx_after": ca,
                        "match": m2.group(0) if m2 else "",
                        "answer_text": ans_text or d.get("answer_text", ""),
                    })
        out = exam_dir / df.name
        out.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
        total = sum(len(v) for v in found.values())
        print(f"{df.name}: {len(items)} items, {total} exam sentences -> {out.name}")


if __name__ == "__main__":
    main()
