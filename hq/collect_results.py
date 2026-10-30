"""generate.py の出力フォルダ群から、検査・動作検証・所要時間をまとめた表（JSON と Markdown）を作る。

実行方法:
  python hq/collect_results.py --out hq/output/results.json out/master out/char_standard ...
"""

import argparse
import json
import os


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, help="results.json の出力先（同じ名前の .md も書く）")
    p.add_argument("dirs", nargs="+")
    return p.parse_args()


def load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def summarize(folder):
    report = load(os.path.join(folder, "report.json"))
    checks = load(os.path.join(folder, "checks.json"))
    motion = load(os.path.join(folder, "motion.json"))
    state = load(os.path.join(folder, "state.json"))
    config = load(report.get("config", "")) if report.get("config") else {}
    poses = {k: v for k, v in motion.get("poses", {}).items() if k != "rest"}
    glb = motion.get("glb_check", {})
    errors = [p["max_m"] for mesh in glb.get("vertex_error", {}).values()
              for k, p in mesh.items() if isinstance(p, dict) and "max_m" in p]
    visible = [v["visible"] for pose in poses.values() for v in pose.get("penetration", {}).values()]
    return {
        "name": report.get("name", os.path.basename(folder)),
        "passed": report.get("passed", False),
        "hair": config.get("hair"),
        "outfit": config.get("outfit"),
        "height_m": checks.get("skeleton", {}).get("height_m"),
        "bones": checks.get("skeleton", {}).get("bones"),
        "bad_edge_ratio_max": max((p["bad_ratio"] for p in poses.values()), default=None),
        "skin_poke_visible_max": max(visible, default=None),
        "hidden_skin_exposed": checks.get("hidden_skin", {}).get("exposed"),
        "glb_vertex_error_max_mm": round(max(errors) * 1000, 4) if errors else None,
        "glb_mb": round(glb.get("size_bytes", 0) / 1e6, 1),
        "seconds": {s: state.get(s, {}).get("seconds") for s in ("build", "checks", "render", "validate")},
        "total_seconds": report.get("total_seconds"),
        "failures": [f for s in state.values() if isinstance(s, dict) for f in s.get("failures", [])],
    }


def markdown(rows):
    head = ("| キャラクター | 合格 | 髪 | 服 | 身長 | 破綻した辺（最大） | 肌の突き出し（最大） | GLB 往復誤差 | GLB | "
            "生成時間（build / checks / render / validate） |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
    lines = []
    for r in rows:
        s = r["seconds"]
        lines.append(
            f"| {r['name']} | {'✓' if r['passed'] else '✗'} | {r['hair']} | {r['outfit']} | {r['height_m']} m | "
            f"{r['bad_edge_ratio_max'] * 100:.2f}% | {r['skin_poke_visible_max'] * 100:.1f}% | "
            f"{r['glb_vertex_error_max_mm']} mm | {r['glb_mb']} MB | "
            f"{s['build']} / {s['checks']} / {s['render']} / {s['validate']} 秒 |")
    return head + "\n".join(lines) + "\n"


def main():
    args = parse_args()
    rows = [summarize(d) for d in args.dirs]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    md = markdown(rows)
    with open(os.path.splitext(args.out)[0] + ".md", "w", encoding="utf-8") as f:
        f.write(md)
    print(md)


if __name__ == "__main__":
    main()
