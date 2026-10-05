#!/usr/bin/env python3
"""Observation layer for wago-atlas (M5): coverage matrix, response matrix,
origin ratios, grammaticalization ladder, gap list. Pure computation — build.py
imports compute() and injects the result; `python3 build/observe.py --json`
prints it for standalone inspection.
"""
import json
from pathlib import Path

LEVELS = ["N5", "N4", "N3", "N2", "N1"]
ORIGINS = ["wago", "kango", "mixed"]


def compute(dimensions, items, exams):
    order = {d.get("order", "Z"): d["id"] for d in dimensions.values()}
    dims = [dimensions[k] for _, k in sorted(order.items())]
    present = [d for d in dims if any(it.get("dim") == d["id"] for it in items)]
    future = [d for d in dims if d not in present]

    rows = []
    gaps = []
    for d in present:
        members = [it for it in items if it.get("dim") == d["id"]]
        by_level = {}
        for lv in LEVELS:
            lv_items = [it for it in members if it.get("level") == lv]
            lv_exams = 0
            for it in lv_items:
                lv_exams += len(exams.get(it["id"], []))
            by_level[lv] = {"items": len(lv_items), "exams": lv_exams}
            if not lv_items:
                gaps.append({"dim": d["id"], "dimName": d["name"], "level": lv})
        origins = {o: sum(1 for it in members if it.get("origin", "wago") == o) for o in ORIGINS}
        rows.append({
            "id": d["id"], "order": d.get("order", ""), "name": d["name"], "note": d.get("note", ""),
            "items": len(members),
            "exams": sum(v["exams"] for v in by_level.values()),
            "byLevel": by_level, "origins": origins,
        })

    gram = {"lexical": [], "mid": [], "grammaticalized": []}
    for it in items:
        g = it.get("grammaticalization")
        if g in gram:
            gram[g].append({"word": it["word"], "id": it["id"], "level": it["level"],
                            "engine": it.get("engine", "")})
    response = sorted(
        [{"word": it["word"], "read": it.get("read", ""), "level": it["level"],
          "subtype": it.get("subtype", ""), "response": it["response"],
          "meaning": it["meaning"]} for it in items if it.get("response")],
        key=lambda x: (x["level"], x["subtype"], x["word"]),
    )
    level_totals = {}
    for lv in LEVELS:
        level_totals[lv] = {
            "items": sum(1 for it in items if it.get("level") == lv),
            "exams": sum(len(exams.get(it["id"], [])) for it in items if it.get("level") == lv),
        }
    return {
        "levels": LEVELS,
        "dims": rows,
        "future": [{"id": d["id"], "order": d.get("order", ""), "name": d["name"], "note": d.get("note", "")} for d in future],
        "response": response,
        "grammaticalization": gram,
        "gaps": gaps,
        "levelTotals": level_totals,
        "totals": {
            "items": len(items),
            "exams": sum(len(v) for v in exams.values()),
            "dimensions": len(present),
        },
    }


def main():
    root = Path(__file__).resolve().parent.parent
    dims = {}
    for d in json.loads((root / "data" / "dimensions.json").read_text(encoding="utf-8"))["dimensions"]:
        dims[d["id"]] = d
    items = []
    for f in sorted((root / "data").glob("*.json")):
        if f.name == "dimensions.json" or f.parent.name == "exams":
            continue
        for it in json.loads(f.read_text(encoding="utf-8")).get("items", []):
            it["dim"] = f.stem
            items.append(it)
    exams = {}
    for f in sorted((root / "data" / "exams").glob("*.json")):
        for k, v in json.loads(f.read_text(encoding="utf-8")).items():
            exams.setdefault(k, []).extend(v)
    print(json.dumps(compute(dims, items, exams), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
