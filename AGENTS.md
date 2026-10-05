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

- TTS 内容寻址缓存于 `audio/`（可提交）；`nade_audio/` gitignore。
- 新条目：往 `data/hojodoushi.json` 加一条，重跑 build；真题挂接自动生效。

## 4. 版权

- 真题句引用一律注明出处；按用户判定，打乱重建为观测维度＋注明年份级别不涉版权。
- 不把无出处的「真题」写进数据。

## 5. Git

- 仅当用户明确要求时 commit / push。
