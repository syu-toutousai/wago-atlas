# AGENTS.md — wago-atlas 作业守则

本 repo 以**大和言葉**为唯一核心维度，覆盖 JLPT N5～N1+ 考点。

## 1. 核心原则

- **和語视角**：所有条目先问「它的和语本体是什么、如何文法化/功能化」；
  漢語只作对照（origin: wago/kango/mixed，读音判定）。
- **数据外置**：条目在 `data/*.json`；真题命中在 `data/exams/*.json`（由
  `build/extract_exams.py` 从本地题库生成，**不得手改**）。
- **必注出处**：真题句一律带 `source`（级别·年份·月份·題号）。
- **不伪造**：例句/释义可自写，但真题与考点覆盖不得虚构。

## 2. 数据 schema（补助动词为例）

`id/word/read/subtype/level/origin/engine/blueprint/meaning/pattern/examples[]/note`
——`engine`＝実語→機能語の変化、`blueprint`＝一句话机制，二者必填。
補助動詞は加えて `grammaticalization`（lexical＝実語寄り／mid＝中間／grammaticalized＝文法化済み）。

## 2.5 術語（学校文法・橋本進吉体系）

自写的语法解说（subtype/engine/blueprint/meaning/note、UI 文案、README、AGENTS）一律用
**学校文法（橋本進吉体系）**的规范术语，不用 JSL/俗用说法：

| 避免（JSL/俗用） | 采用（学校文法） |
|---|---|
| 辞書形・基本形 | 終止形（首次可注「辞書形」） |
| ます形 | 連用形＋助動詞「ます」 |
| て形 | 連用形＋接続助詞「て」 |
| ない形 | 未然形＋助動詞「ない」 |
| た形 | 連用形＋助動詞「た」 |
| イ形容詞・ナ形容詞 | 形容詞・形容動詞 |
| 普通形／丁寧形 | 常体／敬体 |
| 用言修饰语、连体修饰语 | 連用修飾語／連体修飾語 |
| 语气、情态（ムード） | 文末表現（助動詞・終助詞等） |

活用六形＝未然形・連用形・終止形・連体形・仮定形・命令形；助詞分类＝格助詞・接続助詞・
副助詞・係助詞・終助詞・間投助詞。
**例外**：真题句・Nadeshiko 台词・引用文献照原样引用，不改写。

## 3. 构建

```bash
python3 build/extract_exams.py   # 题库 → data/exams/（带出典）
python3 build/build.py           # → index.html（单文件离线）
```

- TTS 内容寻址缓存于 `audio/`（可提交）；`nade_audio/` gitignore（予約、現未使用）。
- 新条目：往 `data/<dim>.json` 加一条，重跑 build；真题挂接自动生效。
- Nadeshiko 実写例句：`data/nade/<dim>.json` に条目 id ごとのクリップ配列。
  `build.py` が `data/*.json` の item に merge し、`jp` に ruby を付けて
  「🎬 原声」Tab と 詳解カードに表示する（下記 §3.5）。

## 3.5 Nadeshiko 実写例句（data/nade/*.json）

各条目に**真实动画・日剧台词**（缩略图＋原声＋EN/中文解说）を付ける層。
素材はローカル CLI `nadeshiko search`（nadeshiko.co, AGPL-3.0）から取得し、
**CDN 直リンク**で表示する（音声/画像はオンライン時のみ；HTML は軽量のまま）。

```bash
python3 build/fetch_nade.py --fetch        # 各条目 search → /tmp/opencode/nade/raw 缓存（nadeshiko CLI）
python3 build/fetch_nade.py --candidates   # token 検証＋採点 → /tmp/opencode/nade/cand/<dim>.json
python3 build/fetch_nade.py --pick -n 2    # 上位を data/nade/<dim>.json へ（cn は空）
# 校阅（/tmp/opencode/nade/picks.json で sid 指定の差し替え・`[]` で不収録）
python3 build/fetch_nade.py --apply /tmp/opencode/nade/cn/<dim>.json   # {sid: cn} を注入
python3 build/build.py
```

- `picks.json`（`{item_id: [sid,...]}`）で自動選抜を上書き；`[]` は「Nadeshiko 無し」。
- token 検証の原則（誤命中を語料にしない）：
  - 汎用は **token 完全一致/辞書形一致**；カタカナ・読みの表記ゆれは正規化して照合；
  - 補助動詞は inflection label（attempt〜てみる 等）か、複合動詞表面＋`d != 本体`；
  - 接続助詞（が・から・のに・けれど…）は**用言（動詞・形容詞・形状詞・助動詞）の後続**のみ；
  - 形式名詞・指示詞・和語動詞は data 側 `match` regex（誤命中ガード済み）を再利用；
  - 既知の不採用例：〜が早いか←「のが早いか」、〜につけ←「煮付け」、〜もので←「もので（手段）」、
    〜ものを←目的語の「ものを」、て-setsuzoku 等の汎用すぎる語。
- 語料紅線（§6）は Nadeshiko にも適用：**台詞は照原样引用**（表記不改写）。
- 現在の覆盖：447 条目中 **432 条目・860 段**（ヒット無し/汎用すぎる語は未収録）。

## 4. 版权

- 真题句引用一律注明出处；按用户判定，打乱重建为观测维度＋注明年份级别不涉版权。
- 不把无出处的「真题」写进数据。

## 5. Git

- 仅当用户明确要求时 commit / push。

## 6. JLPT 卷面结构与语料红线（必读・每次会话）

> 防止「自动生成伪真题」。全文与事故档案：`/home/naruto/scratch/japanese-learning/JLPT_EXAM_STRUCTURE.md`

**真题卷里的文本不全是正文**——还有故意写错的干扰项、选项片段、出题说明。按题型区分：

| 問題 | 题干 | 选项 | 语料 |
|---|---|---|---|
| 問題1 読み方 | 含目标词句子 | 读音 | 题干句 ✅ |
| 問題2 文脈規定 | 挖空句 | 词 | 题干句 ✅ |
| 問題3 言い換え | 目标句 | 近义短语 | 题干句 ✅ |
| 問題4 用法 | 仅目标词 | 四句（1正3误） | **仅正解正确；误用错项严禁入语料** ❌ |
| 問題5 文法形式 | 挖空句 | 形式 | 题干句 ✅ |
| 問題6 並べ替え | ★片段 | 片段 | ❌ |
| 問題7 文章の文法 | 篇章+编号空 | 片段 | 仅篇章正文 ✅ |
| 問題8–13 読解 | 篇章正文 | 设问+选项句 | 正文 ✅；设问/选项 ❌ |
| 聴解 | 脚本 | 编号/图/短句 | 选项 ❌ |

**红线（硬性）**：
1. 只收完整句（以 `。！？` 结尾）；片段、`(注N)` 行、编号、释义行不收。
2. **禁止扫描选项**——选项从不是正文；問題4 错项是故意误用。
3. 出题装置文字（「〜を一つ選びなさい」等）禁入。
4. 読解设问/选项句禁入；挖空/★/编号所在句子禁入。
5. 转写错字走校对流程修源（如 `ものの。→ものの、`），不得在课件层将错就错。

**已知事故**：N1 2022-07 問題4(20)「結末」误用句「細い枝の結末に…咲いている」曾生成 N5 级「〜ている」挖空题——故选项扫描已全面废除。
