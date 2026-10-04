#!/usr/bin/env python3
"""和語アトラス M1 — 補助動詞コースウェア builder.

data/hojodoushi.json        … 20 型本体（engine/blueprint/例文）
data/exams/hojodoushi.json  … 各型の JLPT 真题命中句（extract_exams.py 生成）
TTS: edge-tts, 音声は audio/ に内容アドレスでキャッシュ。単ファイル index.html を出力。
"""
import base64
import copy
import hashlib
import json
import random
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
    ["exam",    "📝 真題（原句）"],
    ["fill",    "✍️ 穴埋め"],
    ["recog",   "📘 意味認識"],
    ["listen",  "🎧 聴解判別"],
    ["listen2", "🎧 聴解・意味理解"],
    ["listen3", "🎧 聴解・書き取り"],
]

# 例文中の補助動詞部分を（　）にする regex（id → pattern）
BLANKS = {
    "te-miru": r"[てで]み(て|る|た|ます)",
    "te-oku": r"[てで]お(いて|く|いた|き)",
    "te-shimau": r"[てで]しま(った|う)|ちゃ(った|う)|じゃ(った|う)",
    "te-iru": r"[てで]い(る|た|ます)",
    "te-aru": r"[てで]あ(る)",
    "te-kuru": r"[てで](き|く)(た|る|て|ます)",
    "te-iku": r"[てで](い|き)(った|く|て)",
    "te-kureru": r"[てで]くれ(た|る|て)|[てで]くださ(った|る|い)",
    "te-morau": r"[てで]もら(った|う|い)|[てで]いただ(いた|く|き)",
    "te-ageru": r"[てで]あげ(た|る|て)|[てで]さしあげ(た|る)",
    "te-yaru": r"[てで]や(った|る|り)",
    "te-miseru": r"[てで]みせ(た|る|て)",
    "hajimeru": r"始め(た|る|て)",
    "tsuzukeru": r"続け(た|る|て)",
    "owaru": r"終わ(った|る|って)",
    "kiru": r"切(った|る|って)",
    "komu": r"込(んだ|む|んで)",
    "nuku": r"抜(いた|く|いて)",
    "kakeru": r"かけ(た|る|て)",
    "naosu": r"直(した|す|して)",
}


def j(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def load_data():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    exams = json.loads(EXAMS.read_text(encoding="utf-8")) if EXAMS.exists() else {}
    items = data["items"]
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
    return data, items, exams


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
_READING_OVERRIDES = [("心当たり", "こころあたり"), ("お手上げ", "おてあげ")]


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
def blank_example(it):
    ex = (it.get("examples") or [{}])[0]
    jp = ex.get("jp", "")
    pat = BLANKS.get(it["id"])
    if pat:
        return re.sub(pat, "（　）", jp, count=1)
    return jp


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

    for it in items:
        iid = it["id"]
        add("recog", f"{iid}:recog",
            q=f"「{it['word']}」の意味は？",
            opts=[it["meaning"]] + others(it, "meaning"),
            ans=0,
            exp=f"{it['word']}＝{it['meaning']}<br>🔧 {it['engine']}<br>🧭 {it['blueprint']}")
        bl = blank_example(it)
        if "（　）" in bl:
            opts = [it["word"]] + others(it, "word")
            rng.shuffle(opts)
            add("fill", f"{iid}:fill",
                q=f"（　）に入る補助動詞は？<br><span class='jp'>{bl}</span>",
                opts=opts, ans=opts.index(it["word"]),
                exp=f"原句：{it['examples'][0]['jp']}<br>{it['examples'][0].get('cn','')}<br>🔧 {it['engine']}")
        ex0 = it["examples"][0]
        if f"{iid}-e0" in audio:
            opts = [it["word"]] + others(it, "word")
            rng.shuffle(opts)
            add("listen", f"{iid}:listen", type="listen", aid=f"{iid}-e0",
                q="🎧 音声に含まれる補助動詞は？", opts=opts, ans=opts.index(it["word"]),
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
            opts = [it["word"]] + others(it, "word")
            rng.shuffle(opts)
            add("exam", f"{iid}:exam-{n}",
                q=(f"📝 次の真題文に含まれる補助動詞は？<br><span class='jp'>{e['jp']}</span>"
                   f"<div class='hint'>{e['source']}</div>"),
                opts=opts, ans=opts.index(it["word"]),
                exp=f"正解：{it['word']}＝{it['meaning']}<br>出典：{e['source']}")
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
.mini{background:#fff;border-radius:14px;padding:12px 10px;cursor:pointer;text-align:left;border:2px solid transparent;
box-shadow:0 1px 6px rgba(30,40,90,.08);transition:.15s}
.mini:hover{transform:translateY(-2px);border-color:var(--acc)}
.mini .nm{font-weight:800;font-size:16px;margin:2px 0}
.mini .im{font-size:11.5px;color:var(--sub);line-height:1.55}
.chip{display:inline-block;border-radius:99px;padding:2px 10px;font-size:11px;font-weight:700;margin-right:6px}
.chip.sub{background:#e6fffa;color:#0f766e}
.chip.lv{background:#f1f3f8;color:#5b6478}
.org-wago{background:var(--okbg);color:var(--ok)}
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
<h1>補助動詞の和語レンズ</h1>
<div class="kana">ほじょどうし —— 動詞の向こう側にある和語の文法。和語アトラス M1／JLPT 真题命中付き 🌊</div>
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
    <div class="jp">${e.jp}</div>
    ${e.answer_text?`<div class="hint">答案：${e.answer_text}</div>`:""}
    ${e.items&&e.items.length?`<div class="hint">関連：${e.items.map(id=>{const n=ITEMS.find(x=>x.id===id);
      return n?`<a class="jump" href="javascript:goDetail('${id}')">${n.word}</a>`:id;}).join("、")}</div>`:""}
  </div>`;
}

/* ---------- tabs ---------- */
const TABS=[["list","🗺️ 一覧"],["detail","📖 詳解"],["exams","📝 真題"],["quiz","🎯 クイズ"]];
let tab="list";
function renderNav(){
  $("#nav").innerHTML=TABS.map(([k,l])=>
    `<button class="${k===tab?'on':''}" onclick="goTab('${k}')">${l}</button>`).join("");
}
function goTab(k){tab=k;renderNav();render();window.scrollTo(0,0);}
function goDetail(iid){goTab('detail');setTimeout(()=>{const el=document.getElementById('n-'+iid);if(el)el.scrollIntoView({behavior:'smooth',block:'start'});},60);}

/* ---------- list ---------- */
function renderList(){
  let h=`<div class="card intro"><h2>補助動詞＝和語の文法エンジン 🌊</h2>
  <p>「食べ<b>てみる</b>」「準備し<b>ておく</b>」「忘れ<b>てしまう</b>」——動詞の後ろに付いて、
  アスペクト・授受・方向・試行…を担う<b>和語の機能語</b>。実語（見る・置く・仕舞う）の意味が
  薄れて文法の接着剤になる——これが<b>文法化</b>の最前線である。</p>
  <div class="steps">
    <div><b>① 和語の動詞が主役</b><br>補助動詞は全部もと和語動詞：見る・置く・仕舞う・居る・来る・行く・呉れる・貰う・上げる…</div>
    <div><b>② 文法化の度合い</b><br>実語性が消えるほど機能語化：「見る」→「〜てみる」は試行マーカー。</div>
    <div><b>③ 授受は視点の問題</b><br>てくれる／てもらう／てあげるは「誰の視点で語るか」が正解の分かれ目。</div>
    <div><b>④ 真題命中付き</b><br>📝 真題 Tab に JLPT N1-N5 の実出題文（出典付き）。各型のカードにも自動で掛かる。</div>
  </div>
  <div class="hint" style="margin-top:10px">語種内訳：和語 ${ORIGIN_COUNT.wago}・漢語 ${ORIGIN_COUNT.kango}・混種 ${ORIGIN_COUNT.mixed}
  —— 補助動詞は<b>清一色大和言葉</b>。まさに和語の文法エンジン。</div></div>`;
  h+=`<div class="mini-wrap">`+ITEMS.map(n=>`
    <button class="mini" onclick="goDetail('${n.id}')">
      <div class="nm">${n.word}</div>
      <div class="im"><span class="chip sub">${n.subtype}</span><span class="chip lv">${n.level}</span></div>
      <div class="im" style="margin-top:4px">${shortMean(n.meaning)}</div>
    </button>`).join("")+`</div>`;
  $("#main").innerHTML=h;
}
function shortMean(m){return m.split(/[：:；;，,（(]/)[0];}

/* ---------- detail ---------- */
function renderDetail(){
  let h="";
  ITEMS.forEach(n=>{
    const iid=n.id;
    const org=n.origin||"wago";
    const orgName={wago:"和語",kango:"漢語",mixed:"混種"}[org];
    h+=`<div class="card noun" id="n-${iid}">
      <h2>${n.word}<span class="jl">${n.level}</span></h2>
      <div class="meta"><span class="chip sub">${n.subtype}</span>
        <span class="chip org-${org}" title="語種">${orgName}</span>
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
  let h=`<div class="card intro"><h2>📝 JLPT 真題コーパス（補助動詞）</h2>
  <p>ローカルの N1-N5 真题库から、各補助動詞の実出題文を抽出（出典付き）。
  計 ${flat.length} 句・${nTypes} 型。原文を観察して「どの動詞が機能語化しているか」を見抜く練習に。</p>
  <p class="hint">JLPT 官方不公开真题；出处为公开整理站点收录，按用户判定注明出处的引用不涉版权问题。</p></div>`;
  ITEMS.forEach(n=>{
    const list=EXAMS[n.id]||[];
    if(!list.length)return;
    h+=`<h3 class="sec" style="border-left:4px solid var(--acc);padding-left:8px;color:var(--acc)">
      ${n.word} <span class="hint">${n.subtype}｜${n.meaning}</span></h3>`;
    h+=list.map(e=>examHTML(Object.assign({items:[n.id]},e))).join("");
  });
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
  else renderQuizTab();
}
renderNav();render();
</script>
</body>
</html>
"""


def main():
    data, items, exams = load_data()
    meta = data.get("meta", {})
    n_links = sum(len(v) for v in exams.values())
    audio = gen_audio(items)
    print(f"[2/3] questions...")
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
    ex_list = []
    for tid, lst in excopy.items():
        for e in lst:
            e2 = dict(e)
            e2["jp"] = add_furigana(e2["jp"])
            e2["answer_text"] = add_furigana(e2.get("answer_text", ""))
            e2["items"] = [tid]
            ex_list.append(e2)
            for it in display:
                if it["id"] == tid:
                    it["_exams"].append(e2)
    for it in display:
        for e in it.get("_exams", []):
            e["items"] = []
    tags = (f"<span>{len(items)} 型</span><span>{sum(len(v) for v in exams.values())} 真題句</span>"
            f"<span>{len(audio)} 音声</span><span>N5〜N1</span><span>大和言葉レンズ</span>")
    origins = {"wago": 0, "kango": 0, "mixed": 0}
    for it in display:
        key = it.get("origin", "wago")
        origins[key] = origins.get(key, 0) + 1
    html = (TEMPLATE
            .replace("__TAGS__", tags)
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
