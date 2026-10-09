#!/usr/bin/env python3
"""Nadeshiko 例句コレクタ（wago-atlas 用・焼录工具）.

data/*.json の各条目について `nadeshiko search` を実行し、形態素 token で
「その条目の規範用法」を検証した候補を集める。

    python3 build/fetch_nade.py --fetch [--only hojodoushi] [--ids a,b] [--force]
        → /tmp/opencode/nade/raw/<dim>/<id>.json に生レスポンスをキャッシュ

    python3 build/fetch_nade.py --candidates
        → /tmp/opencode/nade/cand/<dim>.json に条目ごとの候補（検証済み・スコア順）

    python3 build/fetch_nade.py --pick [--only dim] [-n 2]
        → data/nade/<dim>.json に上位 n 件を書き出す（cn は空・後で人間が補う）

    python3 build/fetch_nade.py --apply /tmp/opencode/nade/cn/<dim>.json
        → {publicId: cn} を data/nade/<dim>.json にマージ
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
NADE = DATA / "nade"
TMP = Path("/tmp/opencode/nade")
RAW = TMP / "raw"
CAND = TMP / "cand"

CJK = lambda ch: "\u4e00" <= ch <= "\u9fff"
KATAKANA_TO_HIRAGANA = str.maketrans(
    "ァィゥェォャュョッーアイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポ",
    "ぁぃぅぇぉゃゅょっーあいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをんがぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽ",
)


def hira(reading):
    return (reading or "").translate(KATAKANA_TO_HIRAGANA)


_VOWEL = {}
for _base, _v in [("あかさたなはまやらわがざだばぱ", "あ"), ("いきしちにひみりぎじびぴ", "い"),
                  ("うくすつぬふむゆるぐずづぶぷ", "う"), ("えけせてねへめれげぜでべぺ", "え"),
                  ("おこそとのほもよろをごぞどぼぽ", "お")]:
    for _ch in _base:
        _VOWEL[_ch] = _v


def ono_norm(s):
    """オノマトペ表記ゆれ正規化：カタカナ→ひらがな・長音符を母音に展開."""
    out = []
    for ch in hira(s):
        if ch == "ー" and out:
            out.append(_VOWEL.get(out[-1], ""))
        else:
            out.append(ch)
    return "".join(out)


def ruby_from_tokens(content, tokens):
    """平民風情が → 平民(へいみん)風情(ふぜい)が（CLI same logic）."""
    if not tokens:
        return content
    plain, out = [], []
    for tok in tokens:
        surf = tok.get("s", "")
        plain.append(surf)
        runs = tok.get("f")
        if runs:
            piece = []
            for run in runs:
                r = run.get("r")
                text = run.get("t", "")
                if r and any(CJK(c) for c in text):
                    piece.append(f"{text}({hira(r)})")
                else:
                    piece.append(text)
            out.append("".join(piece))
        elif tok.get("r") and any(CJK(c) for c in surf) and surf != hira(tok.get("r")):
            out.append(f"{surf}({hira(tok['r'])})")
        else:
            out.append(surf)
    if "".join(plain).strip() != content.replace("<mark>", "").replace("</mark>", "").strip():
        return content
    return "".join(out)


# ---------------------------------------------------------------- aux specs
# 補助動詞：token の inflection label か、複合動詞表面（d != 補助動詞本体）で判定
AUX = {
    "te-miru":   {"q": "てみる",   "label": "〜てみる",
                  "suf": r"[てで]み(る|た|て|ます|よう|ない|なかった|たい)"},
    "te-oku":    {"q": "ておく",   "label": "〜おく",
                  "suf": r"[てで](おく|おい|おいた|おいて|おき)|と(く|い|いた|いて|き)"},
    "te-shimau": {"q": "てしまう", "label": "〜しまう",
                  "suf": r"[てで]しま(う|った|い|わ)|ちゃ(う|った|い)|じゃ(う|った|い)"},
    "te-iru":    {"q": "ている",   "label": "progressive",
                  "suf": r"[てで](いる|いた|いて|います|いません|る|た)"},
    "te-aru":    {"q": "てある",   "label": "〜てある",
                  "suf": r"[てで]あ(る|った|って)"},
    "te-kuru":   {"q": "てくる",   "label": "〜てくる",
                  "suf": r"[てで](くる|きた|きて|きます|こい|こない)"},
    "te-iku":    {"q": "ていく",   "label": "〜ていく",
                  "suf": r"[てで](いく|いった|いって|いきます|いけ)"},
    "te-kureru": {"q": "てくれる", "label": "〜てくれる",
                  "suf": r"[てで](くれる|くれた|くれて|くれ|くださる|くださった|ください|くださ)"},
    "te-morau":  {"q": "てもらう", "label": "〜てもらう",
                  "suf": r"[てで](もらう|もらった|もらって|もらい|いただく|いただいた|いただき|いただいて)"},
    "te-ageru":  {"q": "てあげる", "label": "〜てあげる",
                  "suf": r"[てで](あげる|あげた|あげて|あげ|さしあげる|さしあげた|さしあげ)"},
    "te-yaru":   {"q": "てやる",   "label": "〜てやる",
                  "suf": r"[てで](やる|やった|やって|やり)"},
    "te-miseru": {"q": "てみせる", "label": "〜てみせる",
                  "suf": r"[てで](みせる|みせた|みせて|みせ)"},
    "hajimeru":  {"q": "し始め",   "suf": r"始め(る|た|て|ます|よう|ない|なかった)"},
    "tsuzukeru": {"q": "し続け",   "suf": r"続け(る|た|て|ます|よう|ない|なかった)"},
    "owaru":     {"q": "し終え",   "suf": r"終わ(る|った|って|ります|り|らない)|終え(る|た|て)"},
    "kiru":      {"q": "し切れ",   "suf": r"切(る|った|って|り|れ|れない|れる|れず)"},
    "komu":      {"q": "思い込ん", "suf": r"込(む|んだ|んで|み|める|め|もう)"},
    "nuku":      {"q": "守り抜",   "suf": r"抜(く|いた|いて|き|ける|け|こう)"},
    "kakeru":    {"q": "読みかけ", "suf": r"かけ(る|た|て|ます|ない|よう|)"},
    "naosu":     {"q": "直して",   "suf": r"直(す|した|して|し|そう)"},
}

# 接続助詞など：補助的な label 判定（動詞に融合して token 化されるもの）
LABELS = {
    "nagaramo": "〜ながら",
    "naka": "〜ながら",
    "tsutsu": "〜つつ",
    "tari": "〜たり",
    "te-setsuzoku": "〜て",
    "nari": "〜なり",
}

# 複数 token にまたがる表現（連結一致を許可）
MULTI = {
    "sorega": ["それが"],
    "tokini": ["ときに"],
    "saichuu": ["最中に"],
    "karakoso": ["からこそ"],
    "bakari-ka": ["ばかりか"],
    "bakari-ni": ["ばかりに"],
    "dakeni": ["だけに"],
    "dake-ni": ["だけあって"],
    "nitsuke": ["につけ"],
    "tabini": ["たびに"],
    "tabi": ["ごとに"],
    "uchi-ni-s": ["うちに"],
    "karatoitte": ["からといって"],
    "toshitemo": ["としても"],
    "toshitara": ["としたら", "とすれば"],
    "toatte": ["とあって"],
    "monodakara": ["ものだから"],
    "monode": ["もので"],
    "mononara": ["ものなら"],
    "monono": ["ものの"],
    "monowo": ["ものを"],
    "toiedomo": ["といえども"],
    "sobashikara": ["そばから"],
    "sobobakara": ["そばから"],
    "totan": ["とたん"],
    "yainaya": ["や否や"],
    "gahayaika": ["が早いか"],
    "bakoso": ["ばこそ"],
    "sore-nishitemo": ["それにしても"],
    "sonotame": ["そのため"],
    "sonoue": ["そのうえ"],
    "sonokekka": ["その結果"],
    "toiukotonowo": ["とはいうものの"],
    "nimokakawarazu": ["にもかかわらず"],
    "katoitte": ["かといって"],
    "nitaishite": ["に対して"],
    "sore-nitaishite": ["それに対して"],
    "dokoroka": ["どころか"],
    "sore-dokoroka": ["どころか"],
    "sono-hoka": ["そのほか"],
    "jissaini": ["実際に"],
}

# 末尾が活用語に融合する接続助詞など（nade 検証専用）
SURFACE_TAIL = {
    "zuni": r"ずに$",
}

# data 側 match が狭すぎる条目用の nade 検証 regex（出題用 match は変えない）
NADE_MATCH = {
    "ageru-present": r"(?:人|友達|彼女|彼|妹|姉|母|父|みんな|あんた|お前)に(?:物を)?あげ|プレゼントをあげ",
    "ageru-rei": r"例(?:を|に)(?:あげ|挙げ)|例として挙げ",
    "au-fuku": r"合う服|服に合|色に合",
}

# 語形に依存する特殊 query・検証
OVERRIDES = {
    "demo": {"require": r"^でも$", "pos": ["接続詞"], "q": "でも"},
    "daga": {"q": "だが"},
    "kedo": {"pos": ["助詞", "接続詞"], "q": "けど"},
    "kedo-setsuzoku": {"pos": ["助詞"], "q": "けれど"},
    "keredomo": {"pos": ["助詞", "接続詞"], "q": "けれども"},
    "kedo-setsuzoku": {"pos": ["助詞"], "q": "けれど"},
    "ga-setsuzoku": {"pos": ["助詞"], "q": "が"},
    "noni": {"pos": ["助詞", "接続詞"], "q": "のに"},
    "node": {"pos": ["接続詞"], "q": "ので"},
    "kara-setsuzoku": {"pos": ["助詞"], "q": "から"},
    "mottomo": {"pos": ["接続詞"], "q": "もっとも"},
    "nao": {"pos": ["接続詞"], "q": "なお"},
    "sate": {"pos": ["接続詞"], "q": "さて"},
    "mata": {"pos": ["接続詞", "副詞"], "q": "また"},
    "sarani": {"pos": ["副詞"], "q": "さらに"},
    "katsu": {"pos": ["接続詞", "副詞"], "q": "かつ"},
    "gyakuni": {"q": "逆に"},
    "taishoutekini": {"q": "対照的に"},
    "tatoeba": {"pos": ["副詞"], "q": "たとえば"},
    "iwaba": {"pos": ["副詞"], "q": "いわば"},
    "yousuruni": {"pos": ["副詞", "連語"], "q": "要するに"},
    "nanishiro": {"pos": ["副詞"], "q": "何しろ"},
    "tonikaku": {"pos": ["副詞"], "q": "とにかく"},
    "izurenisemo": {"q": "いずれにせよ"},
    "tomoare": {"q": "ともあれ"},
    "sorewa-souto": {"q": "それはそうと"},
    "tokini": {"pos": ["副詞", "接続詞"], "q": "ときに"},
    "somosomo": {"pos": ["副詞", "接続詞"], "q": "そもそも"},
    "ittai": {"pos": ["副詞"], "q": "いったい"},
    "deha": {"q": "では"},
    "soredeha": {"q": "それでは"},
    "sorede": {"q": "それで"},
    "sokode": {"q": "そこで"},
    "soreni": {"q": "それに"},
    "soredemo": {"q": "それでも"},
    "sorega": {"q": "それが"},
    "soushite": {"q": "そうして"},
    "shikamo": {"q": "しかも"},
    "shikashinagara": {"q": "しかしながら"},
    "soretomo": {"q": "それとも"},
    "aruiwa": {"q": "あるいは"},
    "moshikuwa": {"q": "もしくは"},
    "mataha": {"q": "または"},
    "oyobi": {"q": "および"},
    "narabini": {"q": "ならびに"},
    "ippou": {"q": "一方"},
    "tahou": {"q": "他方"},
    "kaette": {"q": "かえって"},
    "tokorode": {"q": "ところで"},
    "tokoroga": {"q": "ところが"},
    "tohaie": {"q": "とはいえ"},
    "tohaie-setsuzoku": {"q": "とはいえ"},
    "toiunowa": {"q": "というのは"},
    "toiuwakede": {"q": "というわけで"},
    "chinamini": {"q": "ちなみに"},
    "gutaitekini": {"q": "具体的には"},
    "kakushite": {"q": "かくして"},
    "yueni": {"q": "ゆえに"},
    "shitagatte": {"q": "したがって"},
    "desukara": {"q": "ですから"},
    "nanode": {"q": "なので"},
    # 指示詞・形式名詞・和語動詞（match regex 側で検証）
    "kore": {"q": "これ"}, "sore": {"q": "それ"}, "are": {"q": "あれ"},
    "dore": {"q": "どれ"}, "kono": {"q": "この"}, "sono": {"q": "その"},
    "ano": {"q": "あの"}, "dono": {"q": "どの"},
}


def load_items():
    dims = json.loads((DATA / "dimensions.json").read_text(encoding="utf-8"))
    dimname = {d["id"]: d.get("name", d["id"]) for d in dims.get("dimensions", [])}
    items = []
    for f in sorted(DATA.glob("*.json")):
        if f.name in ("dimensions.json", "exam_blocklist.json"):
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        for it in d.get("items", []):
            it = dict(it)
            it["dim"] = f.stem
            it["dimName"] = dimname.get(f.stem, f.stem)
            items.append(it)
    return items


def clean(s):
    s = s.replace("〜", "").replace("～", "").replace(" ", "").replace("　", "")
    return s


def query_of(it):
    oid = it["id"]
    if oid in AUX:
        return AUX[oid]["q"]
    if oid in OVERRIDES and OVERRIDES[oid].get("q"):
        return OVERRIDES[oid]["q"]
    w = clean(it["word"])
    w = re.sub(r"（[^）]*）", "", w)
    w = re.split(r"[・／/]", w)[0]
    w = w.split("＋")[0]
    return w or clean(it["word"])


def token_list(seg):
    return [t for t in seg["textJa"]["tokens"] if t.get("kind") != "symbol"]


def label_of(tok):
    return " ".join((tok.get("inflection") or {}).get("labels") or [])


def match_seg(it, seg):
    """条目の規範用法かを token で検証する. → (ok, why)."""
    oid, word = it["id"], it["word"]
    text = seg["textJa"]["content"]
    toks = token_list(seg)

    # 1) data 側 match regex（keishiki/shijishi/wago-doushi は誤命中ガード済み）
    if it.get("match") or oid in NADE_MATCH:
        for pat in (NADE_MATCH.get(oid), it.get("match")):
            if not pat:
                continue
            try:
                if re.search(pat, text):
                    return True, "match-regex"
            except re.error:
                pass

    # 2) 補助動詞（label / 複合動詞表面 / 連用形＋補助動詞の分割 token）
    if oid in AUX:
        spec = AUX[oid]
        excl = clean(it["word"])
        if excl.startswith(("て", "で")):
            excl = excl[1:]
        for i, t in enumerate(toks):
            if spec.get("label") and spec["label"] in label_of(t):
                return True, "aux-label"
            if t.get("p") not in ("動詞", "名詞", "形容詞"):
                continue
            s = t.get("s", "")
            if spec.get("suf") and re.search(r".+" + spec["suf"], s) and t.get("d") != excl:
                return True, "aux-surface"
            if spec.get("suf") and re.fullmatch(spec["suf"], s):
                prev = toks[i - 1] if i > 0 else None
                if prev and prev.get("p") in ("動詞", "名詞") and prev.get("e") == t.get("b"):
                    return True, "aux-split"
        return False, ""

    # 3) 接続助詞の label（〜ながら/〜つつ 等が動詞 token に融合）
    if oid in LABELS:
        for t in toks:
            if LABELS[oid] in label_of(t):
                return True, "label"
        # label が無い場合でも単独 token 一致は下の汎用へ
    tail = SURFACE_TAIL.get(oid)
    if tail:
        for i, t in enumerate(toks):
            if t.get("p") == "動詞" and re.search(r".+" + tail, t.get("s", "")):
                return True, "surface-tail"
            if oid == "zuni" and t.get("p") == "動詞" and t.get("s", "").endswith("ず") \
                    and i + 1 < len(toks) and toks[i + 1].get("s") == "に" \
                    and t.get("e") == toks[i + 1].get("b"):
                return True, "surface-tail"

    # 4) 汎用：token 完全一致 / 辞書形一致 / 表記ゆれ（カタカナ・読み） / 連接一致
    base = clean(it["word"])
    alts = [clean(x) for x in re.split(r"[・／/]", re.sub(r"（[^）]*）", "", base)) if clean(x)]
    want_pos = OVERRIDES.get(oid, {}).get("pos")
    # 接続助詞は用言（動詞・形容詞・形状詞・助動詞）の後ろに来るものだけ採る
    after_yougen = oid in ("ga-setsuzoku", "kara-setsuzoku", "noni", "kedo", "keredo-setsuzoku",
                           "keredomo", "nari", "nitsuke", "gahayaika")
    for alt in alts:
        pat = OVERRIDES.get(oid, {}).get("require")
        if pat and not re.search(pat, alt):
            continue
        for i, t in enumerate(toks):
            if want_pos and t.get("p") not in want_pos:
                continue
            if after_yougen and t.get("p") == "助詞":
                prev = toks[i - 1] if i > 0 else None
                if not prev or prev.get("p") not in ("動詞", "形容詞", "形状詞", "助動詞"):
                    continue
            s = t.get("s", "")
            if s == alt:
                return True, "token"
            if hira(s) == alt and hira(s) != s:
                return True, "token-kana"
            if t.get("d") == alt and t.get("p") in ("動詞", "形容詞", "形状詞"):
                return True, "dict"
            if hira(t.get("r", "")) == alt and t.get("r"):
                return True, "reading"
            if it["dim"] == "onomatope" and s.startswith(alt) and len(s) <= len(alt) + 2:
                return True, "token-ext"
            if it["dim"] == "onomatope" and ono_norm(s) == ono_norm(alt):
                return True, "token-ono"
        if len(alt) >= 4:
            if _contiguous_contains(toks, alt):
                return True, "concat"
        for mul in MULTI.get(oid, []):
            if len(mul) >= 3 and _contiguous_contains(toks, mul):
                return True, "multi"
    return False, ""


def _contiguous_contains(toks, target):
    for i in range(len(toks)):
        buf = ""
        for j in range(i, len(toks)):
            if j > i and toks[j].get("b") != toks[j - 1].get("e"):
                break
            buf += toks[j].get("s", "")
            if buf == target:
                return True
            if len(buf) >= len(target) + 2:
                break
    return False


def fetch(only=None, ids=None, force=False):
    items = load_items()
    n_ok = n_skip = n_err = 0
    for it in items:
        if only and it["dim"] != only:
            continue
        if ids and it["id"] not in ids:
            continue
        out = RAW / it["dim"] / f"{it['id']}.json"
        if out.exists() and not force:
            n_skip += 1
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        q = query_of(it)
        for attempt in range(5):
            r = subprocess.run(["nadeshiko", "search", q, "--once", "-n", "20", "--json", str(out)],
                               capture_output=True, text=True)
            err = (r.stderr or "") + (r.stdout or "")
            if r.returncode == 0 and out.exists() and "429" not in err:
                break
            if "429" in err:
                wait = 30 + attempt * 15
                print(f"  [429] {it['dim']}/{it['id']}: wait {wait}s")
                time.sleep(wait)
            else:
                break
        if r.returncode != 0 or not out.exists() or "429" in err:
            n_err += 1
            print(f"[!] {it['dim']}/{it['id']} ({q}): {err.strip()[:120]}")
            continue
        n_ok += 1
        if n_ok % 25 == 0:
            print(f"  ... {n_ok} fetched")
        time.sleep(0.55)
    print(f"[fetch] new={n_ok} cached={n_skip} err={n_err}")


def media_name(seg, inc):
    m = (inc.get("media") or {}).get(seg.get("mediaPublicId"), {})
    return m.get("nameEn") or m.get("nameRomaji") or m.get("nameJa") or "?"


def fmt_time(ms):
    s = int(ms / 1000)
    return f"{s // 60}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def candidates():
    items = load_items()
    qmap = {}
    for it in items:
        qmap[it["id"]] = it
    out_by_dim = {}
    for dim_dir in sorted(RAW.glob("*")):
        dim = dim_dir.name
        rows = []
        used = set()
        for f in sorted(dim_dir.glob("*.json")):
            iid = f.stem
            it = qmap.get(iid)
            if not it:
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            inc = d.get("includes") or {}
            cands = []
            for seg in d.get("segments") or []:
                rating = seg.get("contentRating")
                if rating not in ("SAFE", "SUGGESTIVE"):
                    continue
                ok, why = match_seg(it, seg)
                if not ok:
                    continue
                content = seg["textJa"]["content"]
                jp = ruby_from_tokens(content, seg["textJa"].get("tokens"))
                en = (seg.get("textEn") or {}).get("content") or ""
                urls = seg.get("urls") or {}
                cands.append({
                    "sid": seg["publicId"],
                    "jp": jp,
                    "jp_raw": content,
                    "en": en,
                    "en_machine": (seg.get("textEn") or {}).get("isMachineTranslated", True),
                    "media": media_name(seg, inc),
                    "ep": f"EP{seg.get('episode')}",
                    "at": fmt_time(seg.get("startTimeMs", 0)),
                    "url": f"https://nadeshiko.co/en/sentence/{seg['publicId']}",
                    "audio": urls.get("audioUrl"),
                    "thumb": urls.get("imageUrl"),
                    "rating": rating,
                    "why": why,
                    "_len": len(content),
                })
            # score: SAFE first, human translation, fuller sentence, no repetition
            def score(c):
                raw = c["jp_raw"]
                reps = raw.count(clean(it["word"])) + (1 if it["id"] in AUX and raw.count(AUX[it["id"]]["q"].rstrip("ん")) > 1 else 0)
                ends = 0 if raw.rstrip()[-1:] in "。！？" else 1
                return (c["rating"] != "SAFE",
                        1 if c["en_machine"] else 0,
                        ends,
                        1 if reps >= 2 else 0,
                        abs(c["_len"] - 18),
                        0 if c["why"] in ("token", "dict", "aux-label", "match-regex") else 1,
                        c["sid"])
            cands.sort(key=score)
            uniq, seen, seen_jp = [], set(), set()
            for c in cands:
                if c["sid"] in seen:
                    continue
                key = re.sub(r"[\s　]+", "", c["jp_raw"])
                if key in seen_jp:
                    continue
                seen.add(c["sid"])
                seen_jp.add(key)
                uniq.append(c)
            rows.append((it, uniq[:6]))
        out_by_dim[dim] = rows

    CAND.mkdir(parents=True, exist_ok=True)
    for dim, rows in out_by_dim.items():
        payload = {it["id"]: {"word": it["word"], "subtype": it["subtype"], "cands": cs}
                   for it, cs in rows}
        (CAND / f"{dim}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        hit = sum(1 for _, cs in rows if cs)
        print(f"[candidates] {dim}: {hit}/{len(rows)} entries with candidates")


def pick(only=None, n=2):
    items = {it["id"]: it for it in load_items()}
    NADE.mkdir(exist_ok=True)
    picks_file = TMP / "picks.json"
    overrides = json.loads(picks_file.read_text(encoding="utf-8")) if picks_file.exists() else {}
    used = set()
    used_jp = set()
    for f in sorted(CAND.glob("*.json")):
        dim = f.stem
        if only and dim != only:
            continue
        payload = json.loads(f.read_text(encoding="utf-8"))
        out = {}
        for iid, row in payload.items():
            pool = row["cands"]
            if iid in overrides:
                want = overrides[iid]
                pool = [c for c in pool if c["sid"] in want] + \
                       [c for c in pool if c["sid"] not in want]
            clips = []
            for c in pool:
                if len(clips) >= (len(overrides[iid]) if iid in overrides else n):
                    break
                if c["sid"] in used:
                    continue
                jpkey = re.sub(r"[\s　]+", "", c["jp_raw"])
                if jpkey in used_jp:
                    continue
                if not c.get("audio") or not c.get("thumb"):
                    continue
                if iid in overrides and c["sid"] not in overrides[iid]:
                    continue
                used.add(c["sid"])
                used_jp.add(jpkey)
                clips.append({
                    "sid": c["sid"],
                    "jp": c["jp"],
                    "en": c["en"],
                    "cn": "",
                    "media": c["media"],
                    "ep": c["ep"],
                    "at": c["at"],
                    "url": c["url"],
                    "audio": c["audio"],
                    "thumb": c["thumb"],
                })
            if clips:
                out[iid] = clips
        (NADE / f"{dim}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        clips_total = sum(len(v) for v in out.values())
        print(f"[pick] {dim}: {len(out)} entries / {clips_total} clips → data/nade/{dim}.json")


def apply_cn(path):
    p = Path(path)
    trans = json.loads(p.read_text(encoding="utf-8"))
    if p.name != "all.json" and (NADE / p.name).exists():
        targets = {p.stem: NADE / p.name}
    else:
        targets = {f.stem: f for f in NADE.glob("*.json")}
    n = 0
    for dim, f in targets.items():
        d = json.loads(f.read_text(encoding="utf-8"))
        for iid, clips in d.items():
            for c in clips:
                if c.get("sid") in trans:
                    c["cn"] = trans[c["sid"]]
                    n += 1
        f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[apply] filled cn for {n} clips")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--ids")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--candidates", action="store_true")
    ap.add_argument("--pick", action="store_true")
    ap.add_argument("-n", type=int, default=2)
    ap.add_argument("--apply")
    a = ap.parse_args()
    if a.fetch:
        fetch(a.only, set(a.ids.split(",")) if a.ids else None, a.force)
    if a.candidates:
        candidates()
    if a.pick:
        pick(a.only, a.n)
    if a.apply:
        apply_cn(a.apply)
    if not any([a.fetch, a.candidates, a.pick, a.apply]):
        ap.print_help()


if __name__ == "__main__":
    main()
