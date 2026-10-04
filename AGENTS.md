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
