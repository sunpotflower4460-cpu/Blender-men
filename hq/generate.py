"""入力 JSON だけでキャラクターを生成する入口。Blender ファイル・ゲーム用 GLB・画像・検査結果を出力する。

段階:
  build     JSON → .blend（体型・骨格・肌・目・眉・まつげ・髪・服・焼き込み）
  checks    静的検査（骨格・ウェイト・髪・顔・服・マテリアル）
  render    正面・斜め・側面・背面・顔の描画
  validate  パンチ・キック・しゃがみの変形、服のめり込み、GLB 書き出しと往復検証

各段階の入力ハッシュ・成否・所要時間・失敗の原因を <out>/state.json に記録する。
再実行すると、入力が変わらず成功済みの段階は飛ばし、失敗した段階とそれ以降だけをやり直す。
失敗の原因から、作り直すべき範囲（例: 特定の服の素材だけ）を remedy として記録する。

実行方法:
  python hq/generate.py --config hq/config/char_standard.json --out-dir hq/output/char_standard
  python hq/generate.py ... --force            # すべての段階をやり直す
  python hq/generate.py ... --only render       # 指定した段階だけ
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HQ_DIR = os.path.dirname(os.path.abspath(__file__))
STAGES = ["build", "checks", "render", "validate"]
STAGE_CODE = {
    "build": ["build_character.py", "face_details.py", "hair.py", "bake.py", "mpfb_env.py"],
    "checks": ["checks.py"],
    "render": ["render_views.py"],
    "validate": ["validate_motion.py"],
}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--only", default="", help="実行する段階（カンマ区切り）")
    p.add_argument("--samples", type=int, default=64)
    return p.parse_args()


def file_hash(paths):
    h = hashlib.sha256()
    for p in paths:
        with open(p, "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:16]


def asset_files(config):
    """設定が使う髪・服の素材フォルダのファイル。素材を作り直したとき、それを使う人物だけを作り直すため。"""
    with open(os.path.join(HQ_DIR, "assets", "index.json"), encoding="utf-8") as f:
        index = json.load(f)
    entries = []
    if config.get("hair"):
        entries.append(index["hair"][config["hair"]])
    for name in index["outfits"].get(config.get("outfit") or "", []):
        entries.append(index["clothes"][name])
    files = []
    for e in entries:
        folder = os.path.join(HQ_DIR, "assets", os.path.dirname(e["mhclo"]))
        files += sorted(os.path.join(folder, n) for n in os.listdir(folder))
    return files


def remedy(stage, failure, config):
    """失敗の内容から、作り直すべき範囲を示す。"""
    m = re.search(r"服: (\w+)|BH_clothes_(\w+)", failure)
    if m:
        name = m.group(1) or m.group(2)
        return f"python hq/author_assets.py --only {name}  # 服の素材だけを作り直す"
    if failure.startswith("髪"):
        return f"python hq/author_assets.py --only {config.get('hair')}  # 髪の素材だけを作り直す"
    if failure.startswith(("顔", "ウェイト", "骨格", "マテリアル")):
        return "設定（macros など）を見直して build から再実行"
    if "GLB" in failure:
        return "validate だけを再実行（--only validate）"
    return f"{stage} を再実行"


def main():
    args = parse_args()
    out = os.path.abspath(args.out_dir)
    os.makedirs(out, exist_ok=True)
    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)
    name = config["name"]
    blend = os.path.join(out, f"{name}.blend")
    glb = os.path.join(out, f"{name}.glb")
    state_path = os.path.join(out, "state.json")
    state = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}
    py = sys.executable
    commands = {
        "build": [py, os.path.join(HQ_DIR, "build_character.py"), "--config", args.config, "--output", blend],
        "checks": [py, os.path.join(HQ_DIR, "checks.py"), "--blend", blend,
                   "--report", os.path.join(out, "checks.json")],
        "render": [py, os.path.join(HQ_DIR, "render_views.py"), "--blend", blend,
                   "--out-dir", os.path.join(out, "render"), "--samples", str(args.samples)],
        "validate": [py, os.path.join(HQ_DIR, "validate_motion.py"), "--blend", blend, "--glb", glb,
                     "--render-dir", os.path.join(out, "motion"), "--report", os.path.join(out, "motion.json")],
    }
    only = [s for s in args.only.split(",") if s]
    upstream_changed = False
    summary = {"name": name, "config": args.config, "stages": {}}
    for stage in STAGES:
        if only and stage not in only:
            summary["stages"][stage] = state.get(stage, {"skipped": True})
            continue
        inputs = [args.config] + [os.path.join(HQ_DIR, p) for p in STAGE_CODE[stage]]
        if stage == "build":
            inputs += asset_files(config)
        key = file_hash(inputs)
        prev = state.get(stage, {})
        if not args.force and not upstream_changed and prev.get("ok") and prev.get("input_hash") == key:
            summary["stages"][stage] = dict(prev, cached=True)
            continue
        upstream_changed = True
        t = time.time()
        proc = subprocess.run(commands[stage], capture_output=True, text=True)
        seconds = round(time.time() - t, 1)
        entry = {"ok": proc.returncode == 0, "seconds": seconds, "input_hash": key,
                 "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if not entry["ok"]:
            failures = []
            for report in ("checks.json", "motion.json"):
                path = os.path.join(out, report)
                if stage in ("checks", "validate") and os.path.exists(path) and report.startswith(
                        "checks" if stage == "checks" else "motion"):
                    failures = json.load(open(path, encoding="utf-8")).get("failures", [])
            if not failures:
                tail = (proc.stderr or proc.stdout).strip().splitlines()[-6:]
                failures = [" / ".join(tail)]
            entry["failures"] = failures
            entry["remedy"] = sorted({remedy(stage, f, config) for f in failures})
        state[stage] = entry
        summary["stages"][stage] = entry
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print(json.dumps({"stage": stage, **entry}, ensure_ascii=False), flush=True)
        if not entry["ok"]:
            break
    summary["passed"] = all(state.get(s, {}).get("ok") for s in STAGES)
    summary["total_seconds"] = round(sum(state.get(s, {}).get("seconds", 0) for s in STAGES), 1)
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps({"name": name, "passed": summary["passed"], "total_seconds": summary["total_seconds"]},
                     ensure_ascii=False))
    sys.exit(0 if summary["passed"] else 1)


if __name__ == "__main__":
    main()
