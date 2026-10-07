#!/usr/bin/env python3
"""和語アトラス M1 — 補助動詞コースウェア builder.

data/hojodoushi.json        … 20 型本体（engine/blueprint/例文）
data/exams/hojodoushi.json  … 各型の JLPT 真题命中句（extract_exams.py 生成）
TTS: edge-tts, 音声は audio/ に内容アドレスでキャッシュ。単ファイル index.html を出力。
"""
import base64
import copy
import hashlib
import importlib.util
import json
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_obs_spec = importlib.util.spec_from_file_location("observe", Path(__file__).parent / "observe.py")
observe_mod = importlib.util.module_from_spec(_obs_spec)
_obs_spec.loader.exec_module(observe_mod)

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data" / "hojodoushi.json"
EXAMS = ROOT / "data" / "exams" / "hojodoushi.json"
AUDIO_DIR = ROOT / "audio"
OUT = ROOT / "index.html"

VOICE = "ja-JP-NanamiNeural"
RATE = "-6%"
SEED = 20261004
MIN_MP3 = 300
TABS_META = [["list", "🗺️ 一覧"], ["detail", "📖 詳解"], ["exams", "📝 真題"], ["quiz", "🎯 クイズ"]]
BANK_META = [
    ["exam",    "📝 真題句クイズ"],
    ["fill",    "✍️ 穴埋め"],
    ["recog",   "📘 意味認識"],
    ["listen",  "🎧 聴解判別"],
    ["listen2", "🎧 聴解・意味理解"],
    ["listen3", "🎧 聴解・書き取り"],
]

# 例文中の補助動詞部分を（　）にする regex（id → pattern）
BLANKS = {
    "te-miru": r"[てで]み(て|る|た|ます|よう)",
    "te-oku": r"[てで]お(いて|く|いた|き)",
    "te-shimau": r"[てで]しま(った|う|い)|ちゃ(った|う)|じゃ(った|う)",
    "te-iru": r"[てで]い(る|た|ます)",
    "te-aru": r"[てで]あ(る)",
    "te-kuru": r"[てで](き|く)(た|る|て|ます)",
    "te-iku": r"[てで](い|き)(った|く|て)",
    "te-kureru": r"[てで]くれ(た|る|て)|[てで]くださ(った|る|い)",
    "te-morau": r"[てで]もら(った|う|い|って)|[てで]いただ(いた|く|き)",
    "te-ageru": r"[てで]あげ(た|る|て)|[てで]さしあげ(た|る)",
    "te-yaru": r"[てで]や(った|る|り)",
    "te-miseru": r"[てで]みせ(た|る|て)",
    "hajimeru": r"始め(た|る|て)",
    "tsuzukeru": r"続け(た|る|て)",
    "owaru": r"終わ(った|る|って)",
    "kiru": r"切(った|る|って)",
    "komu": r"込(んだ|む|んで|み)",
    "nuku": r"抜(いた|く|いて|き)",
    "kakeru": r"かけ(た|る|て)",
    "naosu": r"直(した|す|して)",
}


# 補助動詞の活用（空欄の前後関係に合わせて選択肢を活用させる）
_AUX_ICHIDAN = {"みる", "いる", "くれる", "あげる", "みせる", "始める", "続ける", "かける"}
_AUX_GODAN = {
    "う": ("って", "った", "い", "おう"), "く": ("いて", "いた", "き", "こう"),
    "ぐ": ("いで", "いだ", "ぎ", "ごう"), "す": ("して", "した", "し", "そう"),
    "つ": ("って", "った", "ち", "とう"), "ぬ": ("んで", "んだ", "に", "のう"),
    "む": ("んで", "んだ", "み", "もう"), "ぶ": ("んで", "んだ", "び", "ぼう"),
    "る": ("って", "った", "り", "ろう"),
}
_AUX_GODAN_KNOWN = {"おく", "しまう", "ある", "もらう", "やる", "終わる", "切る", "込む", "抜く", "直す"}
_AUX_CATS = ("dict", "past", "te", "reny", "masu", "vol")


def split_aux(word):
    w = (word or "").replace("〜", "")
    return (w[:1], w[1:]) if w.startswith("て") else ("", w)


def conjugate_aux(verb, cat):
    if verb == "する":
        return {"dict": "する", "past": "した", "te": "して", "reny": "し",
                "masu": "します", "vol": "しよう"}.get(cat)
    if verb == "くる":
        return {"dict": "くる", "past": "きた", "te": "きて", "reny": "き",
                "masu": "きます", "vol": "こよう"}.get(cat)
    if verb == "いく":
        return {"dict": "いく", "past": "いった", "te": "いって", "reny": "いき",
                "masu": "いきます", "vol": "いこう"}.get(cat)
    if verb in _AUX_ICHIDAN:
        s = verb[:-1]
        return {"dict": verb, "past": s + "た", "te": s + "て", "reny": s,
                "masu": s + "ます", "vol": s + "よう"}.get(cat)
    if verb not in _AUX_GODAN_KNOWN:
        return None
    t = _AUX_GODAN.get(verb[-1])
    if not t:
        return None
    te, past, reny, vol = t
    return {"dict": verb, "past": verb[:-1] + past, "te": verb[:-1] + te,
            "reny": verb[:-1] + reny, "masu": verb[:-1] + reny + "ます",
            "vol": verb[:-1] + vol}.get(cat)


def aux_form(word, surface):
    """空欄の表層が補助動詞のどの活用形か判定する。→ (接続の て/で, 活用カテゴリ)"""
    if not surface:
        return None, None
    prefix, verb = split_aux(word)
    if prefix == "て":
        if surface[:1] not in ("て", "で"):
            return None, None
        conn, rest = surface[:1], surface[1:]
    else:
        conn, rest = "", surface
    for cat in _AUX_CATS:
        f = conjugate_aux(verb, cat)
        if f is not None and f == rest:
            return conn, cat
    return None, None


def aux_label(word, conn, cat):
    prefix, verb = split_aux(word)
    f = conjugate_aux(verb, cat)
    if f is None:
        return None
    return (conn or "") + f if prefix == "て" else f


def j(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def load_data():
    dimensions = {}
    dim_file = ROOT / "data" / "dimensions.json"
    if dim_file.exists():
        dd = json.loads(dim_file.read_text(encoding="utf-8"))
        for d in dd.get("dimensions", []):
            dimensions[d["id"]] = d
    items = []
    for f in sorted((ROOT / "data").glob("*.json")):
        if f.name == "dimensions.json" or f.parent.name == "exams":
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        dim_id = f.stem
        dim_name = dimensions.get(dim_id, {}).get("name", dim_id)
        for it in d.get("items", []):
            it = dict(it)
            it["dim"] = dim_id
            it["dimName"] = dim_name
            items.append(it)
    exams = {}
    for f in sorted((ROOT / "data" / "exams").glob("*.json")):
        try:
            ed = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for k, v in ed.items():
            exams.setdefault(k, []).extend(v)
    errs = []
    ids = set()
    for it in items:
        iid = it.get("id")
        if not iid or iid in ids:
            errs.append(f"bad/dup id: {iid}")
        ids.add(iid)
        for k in ("word", "read", "meaning", "engine", "blueprint", "level"):
            if not it.get(k):
                errs.append(f"{iid} missing {k}")
        if not it.get("examples"):
            errs.append(f"{iid} missing examples")
    if errs:
        sys.exit("[!] data errors:\n  " + "\n  ".join(errs))
    return {"dimensions": dimensions, "items": items, "exams": exams}


# ---------------------------------------------------------------- furigana
try:
    import pykakasi
    _kks = pykakasi.kakasi()
    HAS_KAKASI = True
except ImportError:
    HAS_KAKASI = False

_KANJI = r"\u4e00-\u9fff\u3007\u303b\u3400-\u4dbf"
_INLINE = re.compile(rf"([{_KANJI}]{{1,8}})\s*\(([ぁ-んァ-ンのー]{{1,10}})\)")
_KANJI_RE = re.compile(rf"[{_KANJI}]")
_READING_OVERRIDES = [("心当たり", "こころあたり"), ("お手上げ", "おてあげ"), ("経つ", "たつ")]


def _furi(text):
    if not HAS_KAKASI:
        return text
    out = []
    for tok in _kks.convert(text):
        orig, hira = tok["orig"], tok["hira"]
        if orig == hira or not hira:
            out.append(orig)
        elif _KANJI_RE.search(orig) and not _KANJI_RE.search(hira):
            out.append(f"<ruby>{orig}<rt>{hira}</rt></ruby>")
        else:
            out.append(orig)
    return "".join(out)


def add_furigana(text):
    if not HAS_KAKASI or not _KANJI_RE.search(text):
        return text
    text = re.sub(r"(\d+)時", r"\1時(じ)", text)
    matches = []
    for phrase, reading in _READING_OVERRIDES:
        for m in re.finditer(re.escape(phrase), text):
            matches.append((m.start(), m.end(), phrase, reading))
    for m in _INLINE.finditer(text):
        matches.append((m.start(), m.end(), m.group(1), m.group(2)))
    matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    clean = []
    for m in matches:
        if not clean or m[0] >= clean[-1][1]:
            clean.append(m)
    out, pos = [], 0
    for start, end, kanji, reading in clean:
        out.append(_furi(text[pos:start]))
        out.append(f"<ruby>{kanji}<rt>{reading}</rt></ruby>")
        pos = end
    out.append(_furi(text[pos:]))
    return "".join(out)


# ---------------------------------------------------------------- tts
def cache_path(logical_id, text):
    h = hashlib.sha1(text.encode()).hexdigest()[:10]
    return AUDIO_DIR / f"{logical_id}-{h}.mp3"


def gen_one(task):
    logical_id, text = task
    path = cache_path(logical_id, text)
    if path.exists() and path.stat().st_size > MIN_MP3:
        return True
    for _ in range(3):
        r = subprocess.run(
            ["edge-tts", "--voice", VOICE, f"--rate={RATE}", "--text", text,
             "--write-media", str(path)], capture_output=True)
        if r.returncode == 0 and path.exists() and path.stat().st_size > MIN_MP3:
            return True
    return False


def gen_audio(items):
    AUDIO_DIR.mkdir(exist_ok=True)
    tasks = []
    for it in items:
        tasks.append((it["id"], it["read"]))
        for i, ex in enumerate(it.get("examples") or []):
            tasks.append((f"{it['id']}-e{i}", ex["jp"]))
    todo = [(lid, txt) for lid, txt in tasks
            if not (p := cache_path(lid, txt)).exists() or p.stat().st_size <= MIN_MP3]
    print(f"[1/3] audio: {len(tasks)} clips, {len(todo)} to synthesize")
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = dict(zip([t[0] for t in todo], ex.map(gen_one, todo)))
    failed = [lid for lid, ok in results.items() if not ok]
    if failed:
        print(f"[!] {len(failed)} failed: {failed[:10]}")
    keep = {cache_path(lid, txt).name for lid, txt in tasks}
    for p in AUDIO_DIR.glob("*.mp3"):
        if p.name not in keep:
            p.unlink()
    audio = {}
    for lid, txt in tasks:
        p = cache_path(lid, txt)
        if p.exists() and p.stat().st_size > MIN_MP3:
            audio[lid] = "data:audio/mpeg;base64," + base64.b64encode(p.read_bytes()).decode()
    return audio


# ---------------------------------------------------------------- questions
def _word_pattern(word):
    w = (word or "").replace("〜", "")
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


def blank_example(it):
    """例文の補助動詞部分を空欄化する → (空欄文, 元の表層形, 原文)。"""
    ex = (it.get("examples") or [{}])[0]
    jp = ex.get("jp", "")
    pat = BLANKS.get(it["id"]) or it.get("match") or _word_pattern(it["word"])
    if pat:
        m = re.search(pat, jp)
        if m:
            return jp[:m.start()] + "（　）" + jp[m.end():], m.group(0), jp
    return jp, None, jp


def build_questions(items, exams_by_id, audio):
    rng = random.Random(SEED)
    qs = []

    def add(bank, ref, **kw):
        kw.update({"bank": bank, "ref": ref, "type": kw.get("type", "choice")})
        qs.append(kw)

    def others(it, field, count=3):
        own = it.get(field)
        rest = [x[field] for x in items if x["id"] != it["id"] and x.get(field) != own]
        rng.shuffle(rest)
        out = []
        for c in rest:
            if len(out) >= count:
                break
            if c not in out:
                out.append(c)
        return out

    def others_dim(it, field, count=3):
        """Distractors from the same dimension (exam quizzes stay on-topic)."""
        own = it.get(field)
        pool = [x[field] for x in items
                if x["id"] != it["id"] and x.get("dim") == it.get("dim") and x.get(field) != own]
        rng.shuffle(pool)
        out = []
        for c in pool:
            if len(out) >= count:
                break
            if c not in out:
                out.append(c)
        if len(out) < count:
            for c in others(it, field, count):
                if c not in out:
                    out.append(c)
                if len(out) >= count:
                    break
        return out[:count]

    def inflect_opts(it, surface, distract):
        """補助動詞（D1）は空欄の表層と同じ活用形・同じ接続クラスの選択肢に揃える。"""
        if it.get("dim") == "hojodoushi":
            conn, cat = aux_form(it["word"], surface) if surface else (None, None)
            if cat is None:
                return None, None
            own = it["word"].replace("〜", "")
            is_te = own.startswith("て")  # テ形系か連用形系か（スロットの接続クラス）
            correct = aux_label(it["word"], conn if is_te else "", cat)
            # 同じ接続クラスの補助動詞だけを選択肢にする
            # （テ形スロットに連用形系＝降っ始め、連用形スロットにテ形系＝使いておいて は不成立）
            pool = [x["word"] for x in items
                    if x["id"] != it["id"] and x.get("dim") == it.get("dim")
                    and x.get("word", "").replace("〜", "").startswith("て") == is_te]
            rng.shuffle(pool)
            seen, opts = {correct}, [correct]
            for w in pool:
                lbl = aux_label(w, conn if is_te else "", cat)
                if not lbl or lbl in seen:
                    continue
                seen.add(lbl)
                opts.append(lbl)
                if len(opts) == 4:
                    break
            rng.shuffle(opts)
            return opts, opts.index(correct)
        opts = [it["word"]] + list(distract)
        rng.shuffle(opts)
        return opts, opts.index(it["word"])

    for it in items:
        iid = it["id"]
        dim_name = it.get("dimName", "和語")
        add("recog", f"{iid}:recog",
            q=f"「{it['word']}」の意味は？",
            opts=[it["meaning"]] + others_dim(it, "meaning"),
            ans=0,
            exp=f"{it['word']}＝{it['meaning']}<br>🔧 {it['engine']}<br>🧭 {it['blueprint']}")
        bl, surf, _ = blank_example(it)
        if "（　）" in bl:
            opts, ans = inflect_opts(it, surf, others_dim(it, "word"))
            if opts:
                add("fill", f"{iid}:fill",
                    q=f"（　）に入る〈{dim_name}〉は？<br><span class='jp'>{bl}</span>",
                    opts=opts, ans=ans,
                    exp=f"原句：{it['examples'][0]['jp']}<br>{it['examples'][0].get('cn','')}<br>🔧 {it['engine']}")
        ex0 = it["examples"][0]
        if f"{iid}-e0" in audio:
            opts = [it["word"]] + others_dim(it, "word")
            rng.shuffle(opts)
            add("listen", f"{iid}:listen", type="listen", aid=f"{iid}-e0",
                q=f"🎧 音声に含まれる〈{dim_name}〉は？", opts=opts, ans=opts.index(it["word"]),
                exp=f"原句：{ex0['jp']}<br>{ex0.get('cn','')}")
            cn_pool = [ex0.get("cn", "")] + [x["examples"][0].get("cn", "")
                                             for x in items if x["id"] != iid and x.get("examples")]
            cn_pool = [c for c in cn_pool if c][:4]
            jp_pool = [ex0["jp"]] + [x["examples"][0]["jp"]
                                     for x in items if x["id"] != iid and x.get("examples")]
            jp_pool = jp_pool[:4]
            if len(cn_pool) == 4:
                rng.shuffle(cn_pool)
                add("listen2", f"{iid}:listen2", type="listen", aid=f"{iid}-e0",
                    q="🎧 音声の意味に最も近いのは？", opts=cn_pool,
                    ans=cn_pool.index(ex0["cn"]),
                    exp=f"原句：{ex0['jp']}<br>{ex0['cn']}")
            if len(jp_pool) == 4:
                rng.shuffle(jp_pool)
                add("listen3", f"{iid}:listen3", type="listen", aid=f"{iid}-e0",
                    q="🎧 音声と同じ文は？", opts=jp_pool,
                    ans=jp_pool.index(ex0["jp"]),
                    exp=f"原句：{ex0['jp']}<br>{ex0['cn']}")
        for n, e in enumerate(exams_by_id.get(iid, [])):
            jp = e["jp"]
            blanked = None
            surface = None
            pat = BLANKS.get(iid) or it.get("match") or _word_pattern(it["word"])
            # 共起ガード（例: 〜ことか は どんなに/どれほど/なんと と共起したときだけ感叹句型）
            guard = it.get("guard")
            guarded = (not guard) or bool(re.search(guard, jp))
            if pat and guarded:
                m2 = re.search(pat, jp)
                if m2:
                    surface = m2.group(0)
                    blanked = jp[:m2.start()] + "（　）" + jp[m2.end():]
            # 補助動詞の match は活用した断片（割り切れ 等）のことがあるため、match 代用挖空はしない
            if not blanked and guarded and e.get("match") and e["match"] in jp and iid not in BLANKS:
                surface = e["match"]
                blanked = jp.replace(e["match"], "（　）", 1)
            if not blanked:
                continue  # 无法安全挖空则不出题（语料仍在真題 Tab 展示）
            opts, ans = inflect_opts(it, surface, others_dim(it, "word"))
            if not opts:
                continue  # 活用形が判定できない補助動詞は出題しない（語料は真題 Tab に残る）
            cb = e.get("ctx_before", "")
            ca = e.get("ctx_after", "")
            ctxb_html = f"<div class='ctx'>{cb}</div>" if cb else ""
            ctxa_html = f"<div class='ctx'>{ca}</div>" if ca else ""
            hint = "" if (cb or ca) else f"<div class='hint'>ヒント（意味）：{it['meaning']}</div>"
            add("exam", f"{iid}:exam-{n}",
                q=(f"📝 実出題文の空欄補充〈{it.get('dimName', '和語')}〉（自動生成の穴埋め・原題そのものではない）<br>"
                   f"{ctxb_html}<span class='jp'>{blanked}</span>{ctxa_html}"
                   f"{hint}"
                   f"<div class='hint'>出典：{e['source']}</div>"),
                opts=opts, ans=ans,
                exp=f"正解：{it['word']}＝{it['meaning']}<br>🔧 {it.get('engine','')}<br>原句：{jp}<br>出典：{e['source']}")
    return qs


# ---------------------------------------------------------------- template
TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>補助動詞の和語レンズ ・ 和語アトラス M1</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#1c2333;--sub:#5b6478;--line:#e4e7f0;
--acc:#0f766e;--acc2:#e6fffa;--ok:#188a52;--okbg:#e9f7ef;--ng:#d33f49;--ngbg:#fdecee;--gold:#b8860b}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
background:var(--bg);color:var(--ink);padding-bottom:90px}
header{background:linear-gradient(135deg,#134e4a,#0f766e);color:#fff;padding:26px 20px 20px}
header h1{font-size:24px} header .kana{opacity:.92;font-size:14px;margin-top:6px}
header .tags span{display:inline-block;background:rgba(255,255,255,.22);border-radius:99px;
padding:2px 10px;font-size:12px;margin:10px 6px 0 0}
.wrap{max-width:880px;margin:0 auto;padding:0 16px}
nav{display:flex;gap:8px;margin:-18px 0 16px;position:relative;z-index:2}
nav button{flex:1;border:none;border-radius:12px;padding:12px 2px;font-size:14px;cursor:pointer;
background:var(--card);box-shadow:0 2px 10px rgba(30,40,90,.08);color:var(--sub);font-weight:600}
nav button.on{background:var(--ink);color:#fff}
.card{background:var(--card);border-radius:16px;padding:18px;margin-bottom:14px;
box-shadow:0 2px 10px rgba(30,40,90,.06)}
.jp{font-size:16.5px;line-height:1.9;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif}
.jp ruby rt{font-size:.52em;color:var(--sub)}
.cn{font-size:13.5px;color:var(--sub);margin-top:3px}
.row{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px dashed var(--line)}
.row:last-child{border-bottom:none}
.btn{flex:none;width:34px;height:34px;border-radius:50%;border:none;background:var(--acc2);
color:var(--acc);font-size:15px;cursor:pointer;display:flex;align-items:center;justify-content:center}
.btn.mini-btn{width:26px;height:26px;font-size:12px}
.btn.playing{animation:pulse 1s infinite}
@keyframes pulse{50%{transform:scale(1.18);background:var(--acc);color:#fff}}
.intro{background:linear-gradient(135deg,#ecfdf5,#e0f2fe)}
.intro h2{font-size:17px;margin-bottom:8px;color:#0f766e}
.intro p{font-size:14px;line-height:1.75}
.steps{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.steps div{flex:1;min-width:180px;background:#fff;border-radius:12px;padding:10px 12px;font-size:12.5px;line-height:1.6;
box-shadow:0 1px 6px rgba(30,40,90,.07)}
.steps b{color:#0f766e}
.mini-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;padding:12px 0 2px}
.grp-h{background:var(--acc);color:#fff;border-radius:14px;padding:12px 16px;display:flex;
justify-content:space-between;align-items:baseline;margin-top:14px}
.grp-h h3{font-size:16.5px}.grp-h span{font-size:12px;opacity:.9}
.dimhead{font-size:17px;margin:18px 0 8px;color:var(--acc);border-left:5px solid var(--acc);
padding-left:10px;scroll-margin-top:70px}
.dimhead .hint{font-weight:400}
/* observe dashboard */
.tblwrap{overflow-x:auto}
table.obs{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
table.obs th,table.obs td{border:1px solid var(--line);padding:6px 8px;text-align:center;vertical-align:top}
table.obs th{background:#f7f8fc;font-weight:700}
table.obs td.dimcell{text-align:left;min-width:170px}
table.obs td.zero{background:#fff5f5;color:#c92a2a}
table.obs tr.totrow td{background:#f7f8fc}
table.obs .ex{display:block;font-size:10.5px;color:var(--sub)}
table.obs td.lm{text-align:left}
table.obs .jpmin{font-family:"Hiragino Mincho ProN","Yu Mincho",serif}
table.obs .resp{font-weight:700;color:var(--acc)}
.obar-row{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px}
.obar{flex:1;max-width:420px;height:12px;border-radius:99px;overflow:hidden;background:#eef1f6;display:flex}
.obar i{display:block;height:100%}
.obar .ow{background:#188a52}
.ladder{border-left:4px solid;border-radius:10px;background:#fafbfe;padding:9px 12px;margin:8px 0}
.ladder-h{display:flex;justify-content:space-between;gap:8px;align-items:baseline;flex-wrap:wrap}
.ladder-list{display:flex;flex-wrap:wrap;gap:6px 10px;margin-top:6px;font-size:14px}
.ladder-list a{font-weight:700}
.ladder-list .hint{font-size:10.5px;margin-left:2px}.obar .ok{background:#9c36b5}.obar .om{background:#b8860b}
.mini{background:#fff;border-radius:14px;padding:12px 10px;cursor:pointer;text-align:left;border:2px solid transparent;
box-shadow:0 1px 6px rgba(30,40,90,.08);transition:.15s}
.mini:hover{transform:translateY(-2px);border-color:var(--acc)}
.mini .nm{font-weight:800;font-size:16px;margin:2px 0}
.mini .im{font-size:11.5px;color:var(--sub);line-height:1.55}
.chip{display:inline-block;border-radius:99px;padding:2px 10px;font-size:11px;font-weight:700;margin-right:6px}
.chip.sub{background:#e6fffa;color:#0f766e}
.chip.lv{background:#f1f3f8;color:#5b6478}
.org-wago{background:var(--okbg);color:var(--ok)}
.gram-lexical{background:#e9f7ef;color:#188a52}
.gram-mid{background:#fff8e1;color:#b8860b}
.gram-grammaticalized{background:#f3e8ff;color:#7c3aed}
.org-kango{background:#f8f0fc;color:#9c36b5}
.org-mixed{background:#fff8e1;color:#b8860b}
.noun{scroll-margin-top:70px;border-left:5px solid var(--acc)}
.noun h2{font-size:19px}
.noun h2 .jl{float:right;font-size:11px;background:#f1f3f8;color:var(--sub);
border-radius:99px;padding:2px 10px;font-weight:600}
.meta{display:flex;align-items:center;gap:8px;margin:8px 0 2px;flex-wrap:wrap}
.meta code{background:#f2f4fa;border:1px solid var(--line);color:var(--ink);
border-radius:8px;padding:2px 9px;font-size:12px}
.meanbox{background:#ecfdf5;border-radius:12px;padding:10px 13px;font-size:14px;line-height:1.8;margin:10px 0}
.note{font-size:13px;color:var(--gold);margin-top:10px;border-top:1px dashed var(--line);padding-top:9px;line-height:1.65}
h3.sec{font-size:15px;color:var(--sub);margin:16px 0 8px;font-weight:600}
h3.verbhead{color:var(--acc);border-left:4px solid var(--acc);padding-left:8px;margin:18px 0 4px}
.q{font-size:16.5px;line-height:1.75;margin-bottom:14px}
.opt{display:block;width:100%;text-align:left;padding:12px 14px;margin:8px 0;font-size:15.5px;
border-radius:12px;border:2px solid var(--line);background:#fff;cursor:pointer;line-height:1.5}
.opt:hover:not(:disabled){border-color:var(--acc)}
.opt.right{border-color:var(--ok);background:var(--okbg)}
.opt.wrong{border-color:var(--ng);background:var(--ngbg)}
.opt:disabled{cursor:default;opacity:.92}
.exp{margin-top:10px;padding:11px 13px;border-radius:10px;font-size:14px;line-height:1.7}
.exp.ok{background:var(--okbg);color:var(--ok)} .exp.ng{background:var(--ngbg);color:var(--ng)}
.bar{position:fixed;bottom:0;left:0;right:0;background:var(--card);
box-shadow:0 -2px 12px rgba(30,40,90,.09);padding:10px 16px;z-index:5}
.bar .wrap{display:flex;justify-content:space-between;align-items:center}
.score{font-weight:700;color:var(--acc)} .next{border:none;background:var(--acc);color:#fff;
border-radius:10px;padding:10px 22px;font-size:15px;cursor:pointer}
.next[disabled]{opacity:.35;cursor:default}
.fin{text-align:center;padding:30px 10px}
.fin .big{font-size:44px;font-weight:800;color:var(--acc)}
.hint{font-size:12.5px;color:var(--sub);margin-top:4px;line-height:1.6}
code.inline{background:#eceff7;border-radius:6px;padding:1px 7px;font-size:.92em}
.exam-card .ctx,.card .ctx{font-size:13.5px;color:var(--sub);line-height:1.85;font-family:"Hiragino Mincho ProN","Yu Mincho","Noto Serif CJK JP",serif;opacity:.9;margin:2px 0}
.exam-card{background:#fff;border-radius:14px;padding:14px 16px;margin-bottom:12px;
box-shadow:0 2px 10px rgba(30,40,90,.06);border-left:4px solid var(--ng)}
.exam-hdr{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.exam-src{font-size:11.5px;color:var(--sub)}
.src{display:inline-block;border-radius:99px;padding:1px 8px;font-size:10.5px;font-weight:700;vertical-align:1px}
.src-jlpt{background:var(--ngbg);color:var(--ng)}
a.jump{color:#0f766e;text-decoration:none;font-weight:700}
a.jump:hover{text-decoration:underline}
</style>
</head>
<body>
<header><div class="wrap">
<h1>和語アトラス（M1〜D7）</h1>
<div class="kana">補助動詞・接続表現・副詞・形式名詞・オノマトペ・和語動詞の多義・指示詞 —— 動詞の後ろ、文と文の間、用言の手前、名詞の代わり、音そのもの、そして指し示す座標に潜む和語の文法。JLPT 真题命中付き 🌊</div>
<div class="tags">__TAGS__</div>
</div></header>

<nav class="wrap" id="nav"></nav>
<main class="wrap" id="main"></main>

<div class="bar"><div class="wrap">
<span class="hint" id="barinfo">オフライン利用可 · 🔊で再生</span>
<button class="next" id="next" onclick="nextQ()" style="display:none">次の問題 →</button>
<span class="score" id="score"></span>
</div></div>

<script>
const AUDIO=__AUDIO__;
const ITEMS=__ITEMS__;
const EXAMS=__EXAMS__;
const BANKS=__BANKS__;
const ORIGIN_COUNT=__ORIGIN_COUNT__;
const GRAM={lexical:["実語寄り","#188a52"],mid:["中間","#b8860b"],grammaticalized:["文法化済み","#7c3aed"]};
const gramOf=n=>n.grammaticalization||"";
const gramChip=n=>n.grammaticalization?`<span class="chip gram-${n.grammaticalization}" title="実語との距離">${GRAM[n.grammaticalization][0]}</span>`:"";
const DIMENSIONS=__DIMENSIONS__;
const OBSERVE=__OBSERVE__;
let QS=__QS__;
const $=s=>document.querySelector(s);
let curAudio=null,curBtn=null;
function play(id,btn){
  const src=AUDIO[id];if(!src)return;
  if(curAudio){curAudio.pause();curAudio.currentTime=0;}
  document.querySelectorAll('.btn').forEach(b=>b.classList.remove('playing'));
  curAudio=new Audio(src);curBtn=btn||null;
  if(curBtn){curBtn.classList.add('playing');curAudio.onended=()=>curBtn.classList.remove('playing');}
  curAudio.play();
}
function rowHTML(iid,i){
  const ex=ITEMS.find(x=>x.id===iid).examples[i];
  const sid=`${iid}-e${i}`;
  const b=AUDIO[sid]?`<button class="btn" onclick="play('${sid}',this)">▶</button>`:"";
  return `<div class="row">${b}<div><div class="jp">${ex.jp}</div><div class="cn">${ex.cn}</div></div></div>`;
}
function examHTML(e){
  return `<div class="exam-card">
    <div class="exam-hdr"><span class="src src-jlpt">JLPT</span><span class="exam-src">${e.source}</span></div>
    ${e.ctx_before?`<div class="ctx">${e.ctx_before}</div>`:""}
    <div class="jp">${e.jp}</div>
    ${e.ctx_after?`<div class="ctx">${e.ctx_after}</div>`:""}
    ${e.answer_text?`<div class="hint">答案：${e.answer_text}</div>`:""}
    ${e.items&&e.items.length?`<div class="hint">関連：${e.items.map(id=>{const n=ITEMS.find(x=>x.id===id);
      return n?`<a class="jump" href="javascript:goDetail('${id}')">${n.word}</a>`:id;}).join("、")}</div>`:""}
  </div>`;
}

/* ---------- tabs ---------- */
const TABS=[["list","🗺️ 一覧"],["detail","📖 詳解"],["exams","📝 真題"],["observe","📊 観測"],["quiz","🎯 クイズ"]];
let tab="list";
function renderNav(){
  $("#nav").innerHTML=TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}
function goDetail(iid){goTab('detail');setTimeout(()=>{const el=document.getElementById('n-'+iid);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},60);}

/* ---------- list ---------- */
function shortMean(m){return m.split(/[：:；;，,（(]/)[0];}
function dimItems(dimId){return ITEMS.filter(n=>n.dim===dimId);}
function dimList(){
  const ids=[...new Set(ITEMS.map(n=>n.dim))];
  return ids.map(id=>({id, name:(DIMENSIONS[id]||{}).name||id,
    order:(DIMENSIONS[id]||{}).order||"", note:(DIMENSIONS[id]||{}).note||""}))
    .sort((a,b)=>String(a.order).localeCompare(String(b.order)));
}
function renderList(){
  let h=`<div class="card intro"><h2>和語の文法エンジンを多層で 🌊</h2>
  <p>M1＝<b>補助動詞</b>（動詞の後ろ）・M2＝<b>接続表現</b>（文と文の間）・M3＝<b>副詞・連用修飾</b>（用言の手前）・
  D4＝<b>形式名詞</b>（名詞の代わり）・D5＝<b>オノマトペ</b>（音そのもの）・D6＝<b>和語動詞の多義</b>（一語のネットワーク）・D7＝<b>指示詞</b>（こそあど座標）。
  どの層も<b>和語（または和語由来の機能語）</b>が文の調整つまみになる——文法化の最前線である。</p>
  <div class="steps">
    <div><b>① 和語が主役</b><br>見る・置く・仕舞う・呉れる…が「〜てみる／〜ておく／〜てしまう／〜てくれる」に。</div>
    <div><b>② 文法化の度合い</b><br>実語性が消えるほど機能語化。Engine 欄で「何がどう薄れたか」を確認。</div>
    <div><b>③ 真題命中付き</b><br>📝 真題 Tab に JLPT N1-N5 の実出題文（出典付き）。各条目にも自動で掛かる。</div>
    <div><b>④ 六层题库</b><br>真題・穴埋め・意味認識・聴解三層＋錯題本。跨维度混合练习。</div>
  </div>
  <div class="hint" style="margin-top:10px">語種内訳：和語 ${ORIGIN_COUNT.wago}・漢語 ${ORIGIN_COUNT.kango}・混種 ${ORIGIN_COUNT.mixed}
  ——接続表現も接続詞はほぼ和語（〜ながら・〜ので・〜のに…），漢語系（且つ・但し・因みに）は少数派。</div></div>`;
  dimList().forEach(dim=>{
    const members=dimItems(dim.id);
    if(!members.length)return;
    h+=`<div class="grp"><div class="grp-h"><h3>${dim.order} ${dim.name}</h3><span>${members.length} 条</span></div>
    <div class="mini-wrap">${members.map(n=>`
      <button class="mini" onclick="goDetail('${n.id}')">
        <div class="nm">${n.word}</div>
        <div class="im"><span class="chip sub">${n.subtype}</span><span class="chip lv">${n.level}</span>${gramChip(n)}</div>
        <div class="im" style="margin-top:4px">${shortMean(n.meaning)}</div>
      </button>`).join("")}</div></div>`;
  });
  $("#main").innerHTML=h;
}

/* ---------- detail ---------- */
function renderDetail(){
  let h="";
  dimList().forEach(dim=>{
    const members=dimItems(dim.id);
    if(!members.length)return;
    h+=`<h2 class="dimhead" id="d-${dim.id}">${dim.order} ${dim.name} <span class="hint">${dim.note}</span></h2>`;
    let lastVerb="";
    members.forEach(n=>{
    if(n.verb && n.verb!==lastVerb){lastVerb=n.verb;h+=`<h3 class="sec verbhead">🔤 ${n.verb}</h3>`;}
    const iid=n.id;
    const org=n.origin||"wago";
    const orgName={wago:"和語",kango:"漢語",mixed:"混種"}[org];
    h+=`<div class="card noun" id="n-${iid}">
      <h2>${n.word}<span class="jl">${n.level}</span></h2>
      <div class="meta"><span class="chip sub">${n.subtype}</span>
        <span class="chip org-${org}" title="語種">${orgName}</span>${gramChip(n)}
        <code>${n.read}</code>
        ${AUDIO[iid]?`<button class="btn mini-btn" onclick="play('${iid}',this)">▶</button>`:""}
      </div>
      <div class="meanbox">📌 <b>意味</b>　${n.meaning}
        <br>🧩 <b>典型</b>　${n.pattern||n.word}
        <br>🔧 <b>Engine</b>　${n.engine}
        <br>🧭 <b>Blueprint</b>　${n.blueprint}</div>
      ${(n.examples||[]).map((_,i)=>rowHTML(iid,i)).join("")}
      ${(n._exams&&n._exams.length)?`<h3 class="sec">📝 JLPT 出題（${n._exams.length}）</h3>`:""}
      ${(n._exams||[]).map(e=>examHTML(e)).join("")}
      ${n.note?`<div class="note">💡 ${n.note}</div>`:""}
    </div>`;
    });
  });
  $("#main").innerHTML=h;
}

/* ---------- exams ---------- */
function renderExams(){
  const flat=[];
  Object.keys(EXAMS).forEach(tid=>{
    (EXAMS[tid]||[]).forEach(e=>flat.push(Object.assign({type:tid},e)));
  });
  flat.sort((a,b)=>(a.level+a.source).localeCompare(b.level+b.source));
  const nTypes=Object.keys(EXAMS).filter(k=>EXAMS[k].length).length;
  let h=`<div class="card intro"><h2>📝 JLPT 真題コーパス（和語アトラス）</h2>
  <p>ローカルの N1-N5 真題库から、各条目的実出題文を抽出（出典付き）。計 ${flat.length} 句・${nTypes} 条目。
  これらは<strong>実際の過去問の文</strong>で、本ページは「原文観察」用のコーパス。
  クイズの〈真題句クイズ〉は、実出題文の該当箇所を空欄にした<strong>自動生成の穴埋め問題</strong>（JLPT 原題そのものではない）。前後の文脈を併記し、文脈が取れない場合は意味ヒントを添える。</p>
  <p class="hint">JLPT 官方不公开真题；出处为公开整理站点收录，按用户判定注明出处的引用不涉版权问题。</p></div>`;
  dimList().forEach(dim=>{
    const members=dimItems(dim.id).filter(n=>(EXAMS[n.id]||[]).length);
    if(!members.length)return;
    h+=`<h2 class="dimhead">${dim.order} ${dim.name} <span class="hint">${members.length} 条目命中</span></h2>`;
    members.forEach(n=>{
      const list=EXAMS[n.id]||[];
      h+=`<h3 class="sec" style="border-left:4px solid var(--acc);padding-left:8px;color:var(--acc)">
        ${n.word} <span class="hint">${n.subtype}｜${n.meaning}</span></h3>`;
      h+=list.map(e=>examHTML(Object.assign({items:[n.id]},e))).join("");
    });
  });
  $("#main").innerHTML=h;
}

/* ---------- observe (M5) ---------- */
function renderObserve(){
  const o=OBSERVE, lv=o.levels;
  let h=`<div class="card intro"><h2>📊 和語アトラス観測</h2>
  <p><b>${o.totals.dimensions}</b> 维度・<b>${o.totals.items}</b> 条目・<b>${o.totals.exams}</b> 真題命中。
  覆盖矩阵＝维度×级别的「条目数／真题命中数」；呼応マトリクス＝陳述副詞的搭配要求；
  空缺清单就是下一阶段的 growth backlog。</p></div>`;
  h+=`<div class="card"><h3 class="sec">🗺️ 覆盖矩阵（条目 / 真題）</h3><div class="tblwrap"><table class="obs">
    <tr><th>维度</th>${lv.map(x=>`<th>${x}</th>`).join("")}<th>計</th></tr>`;
  o.dims.forEach(d=>{
    h+=`<tr><td class="dimcell"><b>${d.order} ${d.name}</b><div class="hint">${d.note||""}</div></td>`;
    lv.forEach(x=>{const c=d.byLevel[x];
      h+=`<td class="${c.items?'':'zero'}">${c.items}<span class="ex">${c.exams?"/"+c.exams:""}</span></td>`;});
    h+=`<td><b>${d.items}</b><span class="ex">/${d.exams}</span></td></tr>`;
  });
  h+=`<tr class="totrow"><td><b>合計</b></td>${lv.map(x=>
    `<td><b>${o.levelTotals[x].items}</b><span class="ex">/${o.levelTotals[x].exams}</span></td>`).join("")}<td></td></tr>
    </table></div></div>`;
  if(o.grammaticalization){
    h+=`<div class="card"><h3 class="sec">🧬 補助動詞の文法化ラダー（実語との距離）</h3>
      <p class="hint">左＝語彙義がほぼそのまま（授受・局面動詞）；中＝比喩的拡張の中間；右＝アスペクト・状態の純マーカー。文法化は二分割ではなく連続体——ここでは三帯に整理。</p>`;
    const lanes=[["lexical","実語寄り","語彙義がそのまま（授受・局面動詞）","#188a52"],
                 ["mid","中間","方向・比喩的拡張（来る・行く・切る…）","#b8860b"],
                 ["grammaticalized","文法化済み","アスペクト・状態のマーカー（ている・ておく・てしまう・てある）","#7c3aed"]];
    lanes.forEach(([k,label,note,color])=>{
      const list=o.grammaticalization[k]||[];
      h+=`<div class="ladder" style="border-color:${color}">
        <div class="ladder-h" style="color:${color}"><b>${label}</b>
          <span class="hint">${note}（${list.length}）</span></div>
        <div class="ladder-list">${list.map(x=>`<a class="jump" href="javascript:goDetail('${x.id}')">${x.word}</a><span class="hint">${x.level}</span>`).join("")}</div></div>`;
    });
    h+=`</div>`;
  }
  h+=`<div class="card"><h3 class="sec">🧲 呼応マトリクス（陳述副詞 × 文末形式，${o.response.length} 条）</h3>
    <div class="tblwrap"><table class="obs"><tr><th>副詞</th><th>級</th><th>呼応先</th><th>意味</th></tr>`;
  o.response.forEach(r=>{
    h+=`<tr><td class="jpmin">${r.word}</td><td>${r.level}</td><td class="resp">${r.response}</td><td class="lm">${r.meaning}</td></tr>`;
  });
  h+=`</table></div></div>`;
  h+=`<div class="card"><h3 class="sec">🌡️ 語種比（和／漢／混）・文法化ラダー</h3>`;
  o.dims.forEach(d=>{
    const t=(d.origins.wago+d.origins.kango+d.origins.mixed)||1;
    h+=`<div class="obar-row"><b>${d.order}</b>
      <div class="obar"><i class="ow" style="width:${d.origins.wago/t*100}%"></i>
      <i class="ok" style="width:${d.origins.kango/t*100}%"></i>
      <i class="om" style="width:${d.origins.mixed/t*100}%"></i></div>
      <span class="hint">和 ${d.origins.wago}・漢 ${d.origins.kango}・混 ${d.origins.mixed}</span></div>`;
  });
  h+=`<div class="hint" style="margin-top:8px">実語 → 機能語の阶梯：`+
     o.dims.map(d=>`${d.order} ${d.name}（${d.items}）`).join(" ▸ ")+`</div></div>`;
  if(o.future.length){
    h+=`<div class="card"><h3 class="sec">🗓️ ロードマップ（未収録维度）</h3>`+
      o.future.map(d=>`<div class="srcrow"><b>${d.order} ${d.name}</b> <span class="hint">${d.note||""}</span></div>`).join("")+
      `</div>`;
  }
  h+=`<div class="card"><h3 class="sec">🕳️ 空缺清单（覆盖矩阵中的 0，growth backlog）</h3>`;
  if(o.gaps.length){
    const byDim={};o.gaps.forEach(g=>{(byDim[g.order+" "+g.dimName]=byDim[g.order+" "+g.dimName]||[]).push(g.level);});
    h+=Object.entries(byDim).map(([k,v])=>`<div class="srcrow"><b>${k}</b>：缺 ${v.join("・")}</div>`).join("");
  }else{h+=`<div class="hint">全部维度 N5-N1 均有条目。</div>`;}
  h+=`</div>`;
  $("#main").innerHTML=h;
}

/* ---------- quiz ---------- */
const shuffle=a=>a.map(x=>[Math.random(),x]).sort((p,q)=>p[0]-q[0]).map(p=>p[1]);
function lsGet(k,d){try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}
function wrongBook(){return lsGet("wago-wrong",{})}
function addWrong(ref){const w=wrongBook();w[ref]=1;lsSet("wago-wrong",w);}
function delWrong(ref){const w=wrongBook();delete w[ref];lsSet("wago-wrong",w);}
function wrongCount(){return Object.keys(wrongBook()).length;}
let mode=null,pool=[],order=[],qi=0,correct=0,answered=false;
function countBank(key){return QS.filter(q=>q.bank===key).length;}
function renderQuizTab(){
  if(!mode){
    showNext(false);$("#score").textContent="";
    const rows=BANKS.filter(([k])=>countBank(k)>0).map(([k,label])=>
      `<button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('${k}')">${label} · ${countBank(k)}問</button>`).join("");
    const wc=wrongCount();
    const wrongRow=wc?`<button class="opt" style="max-width:400px;margin:0 auto 10px;border-color:var(--gold)" onclick="startQuiz('wrong')">📕 错题重练 · ${wc}問<br><span style="font-size:12px;color:var(--gold)">做对即移出错题本</span></button>`:
      `<div class="hint" style="margin-bottom:10px">错题本是空的——答错的题会自动收进来 📕</div>`;
    $("#main").innerHTML=`<div class="card" style="text-align:center;padding:28px 16px">
      <div style="font-size:19px;font-weight:700;margin-bottom:4px">选择训练关卡</div>
      <div class="hint" style="margin-bottom:18px">题目由 hojodoushi.json 自动生成 · 全部随机打乱</div>
      ${rows}${wrongRow}
      <button class="opt" style="max-width:400px;margin:0 auto 10px" onclick="startQuiz('mix')">🎲 混合交错 · 全量随机</button>
      ${wc?`<button class="opt" style="max-width:220px;margin:14px auto 0;font-size:13px;padding:8px" onclick="if(confirm('清空错题本？')){localStorage.removeItem('wago-wrong');renderQuizTab();}">🗑️ 清空错题本</button>`:""}
    </div>`;
    return;
  }
  startQuiz(mode);
}
function startQuiz(m){
  mode=m;
  if(m==="mix")pool=QS.slice();
  else if(m==="wrong"){const w=wrongBook();pool=QS.filter(q=>w[q.ref]);}
  else pool=QS.filter(q=>q.bank===m);
  order=shuffle(pool.map((_,x)=>x));
  qi=0;correct=0;answered=false;
  renderQ();
}
function curQ(){return pool[order[qi]];}
function renderQ(){
  const q=curQ();updateScore();
  let body="";
  if(q.type==="listen"){
    body=AUDIO[q.aid]?`<div style="text-align:center;margin:6px 0 14px">
      <button class="btn" style="width:56px;height:56px;font-size:24px;margin:auto" onclick="play('${q.aid}',this)">🔊</button>
      <div class="hint">可反复点击重听</div></div>`
      :`<div class="hint" style="text-align:center;margin-bottom:10px">（这条发音还没生成）</div>`;
  }
  let optHTML="";
  if(q.opts){
    optHTML=shuffle(q.opts.map((t,i)=>({t,ok:i===q.ans})))
      .map(o=>`<button class="opt" data-ok="${o.ok?1:0}" onclick="pick(this)">${o.t}</button>`).join("");
  }
  showNext(false);
  const label=mode==="mix"?"混合":mode==="wrong"?"错题本":(BANKS.find(([k])=>k===mode)||["",""])[1];
  $("#main").innerHTML=`<div class="card">
    <div class="hint">第 ${qi+1} 题 / 共 ${order.length} 题 · ${label}</div>
    <div class="q">${q.q}</div>${body}${optHTML}
    <div id="fb"></div></div>`;
}
function showNext(v){const b=$("#next");b.style.display=v?"inline-block":"none";b.disabled=!v;}
function pick(btn){
  if(answered)return;answered=true;
  const q=curQ();
  const ok=btn.dataset.ok==="1";
  if(ok)correct++;else addWrong(q.ref);
  if(ok&&mode==="wrong")delWrong(q.ref);
  document.querySelectorAll(".opt").forEach(b=>{b.disabled=true;if(b.dataset.ok==="1")b.classList.add("right");});
  if(!ok)btn.classList.add("wrong");
  $("#fb").innerHTML=`<div class="exp ${ok?'ok':'ng'}">${ok?"⭕ 正解！":"❌ 惜しい！"} ${q.exp||""}</div>`;
  showNext(true);updateScore();
  window.scrollTo(0,document.body.scrollHeight);
}
function nextQ(){
  answered=false;qi++;
  if(qi>=order.length)finish();else renderQ();
}
function finish(){
  showNext(false);
  const total=order.length,pct=Math.round(correct/total*100);
  const msg=pct===100?"🏆 完璧！和語のエンジン、完全掌握！":pct>=70?"👍 かなりいい！错题趁热打铁":"📖 詳解タブで復習してから再挑戦";
  $("#main").innerHTML=`<div class="card fin">
    <div class="big">${correct} / ${total}</div>
    <div style="font-size:20px;margin:12px 0">${msg}</div>
    <button class="next" style="display:inline-block;margin:4px" onclick="startQuiz('${mode}')">もう一度挑戦</button><br>
    <button class="opt" style="max-width:280px;margin:14px auto 0" onclick="backToBanks()">别的关卡选一选</button></div>`;
  $("#score").textContent="";$("#barinfo").textContent=`正确率 ${pct}%`;
  window.scrollTo(0,0);
}
function backToBanks(){mode=null;render();}
function updateScore(){$("#score").textContent=`✔ ${correct} / ${order.length}`;}

/* ---------- init ---------- */
function render(){
  if(tab!=="quiz")showNext(false);
  if(tab==="list")renderList();
  else if(tab==="detail")renderDetail();
  else if(tab==="exams")renderExams();
  else if(tab==="observe")renderObserve();
  else renderQuizTab();
}
renderNav();render();
</script>
</body>
</html>
"""


def main():
    d = load_data()
    items, exams, dimensions = d["items"], d["exams"], d["dimensions"]
    audio = gen_audio(items)
    print("[2/3] questions...")
    qs = build_questions(items, exams, audio)
    counts = {k: sum(1 for q in qs if q["bank"] == k) for k, _ in BANK_META}
    for k, label in BANK_META:
        print(f"      {label}: {counts[k]} 問")

    display = copy.deepcopy(items)
    excopy = copy.deepcopy(exams)
    for it in display:
        for ex in it.get("examples") or []:
            ex["jp"] = add_furigana(ex["jp"])
        it["_exams"] = []
    for lst in excopy.values():
        for e in lst:
            e["jp"] = add_furigana(e["jp"])
            e["answer_text"] = add_furigana(e.get("answer_text", ""))
            e["ctx_before"] = add_furigana(e.get("ctx_before", ""))
            e["ctx_after"] = add_furigana(e.get("ctx_after", ""))
    for tid, lst in excopy.items():
        for e in lst:
            e2 = dict(e)
            e2["items"] = []
            for it in display:
                if it["id"] == tid:
                    it["_exams"].append(e2)
    tags = (f"<span>{len(items)} 型</span><span>{sum(len(v) for v in exams.values())} 真題句</span>"
            f"<span>{len(audio)} 音声</span><span>N5〜N1</span><span>大和言葉レンズ</span>")
    origins = {"wago": 0, "kango": 0, "mixed": 0}
    for it in display:
        key = it.get("origin", "wago")
        origins[key] = origins.get(key, 0) + 1
    html = (TEMPLATE
            .replace("__TAGS__", tags)
            .replace("__DIMENSIONS__", j(dimensions))
            .replace("__OBSERVE__", j(observe_mod.compute(dimensions, items, exams)))
            .replace("__AUDIO__", j(audio))
            .replace("__ITEMS__", j(display))
            .replace("__EXAMS__", j(excopy))
            .replace("__BANKS__", j(BANK_META))
            .replace("__ORIGIN_COUNT__", j(origins))
            .replace("__QS__", j(qs)))
    OUT.write_text(html, encoding="utf-8")
    print(f"[3/3] wrote {OUT} ({OUT.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
