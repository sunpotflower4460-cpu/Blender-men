"""髪・服の素材を、マスター人体（標準体型）の上で一度だけ作り、MPFB の .mhclo 素材として書き出す。

出力（hq/assets/<種類>/<名前>/）:
  <名前>.mhclo / <名前>.obj   MPFB の素材（別の体型には MPFB が再フィットする）
  *_color.png / *_normal.png  焼き込み済みテクスチャ（髪は毛束テクスチャ）
  scalp.png                   髪のみ: 頭皮の下地（体の UV 上の画像。体の焼き込み前に重ねる）
  material.json               ゲーム用マテリアルの設定
hq/assets/index.json         素材の一覧（種類・ファイル・生成時間・ライセンス）

実行方法:
  python hq/author_assets.py [--only tshirt,side_part]
"""

import argparse
import json
import os
import sys
import time
import uuid as uuidlib

import bpy
import bmesh  # bpy の読み込み後でないと import できない

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bake  # noqa: E402
import build_character  # noqa: E402
import clothes  # noqa: E402
import hair  # noqa: E402
import mpfb_env  # noqa: E402

HQ_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(HQ_DIR, "assets")
MASTER_CONFIG = os.path.join(HQ_DIR, "config", "master.json")
HAIR_COLOR = (0.025, 0.018, 0.013)


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--only", default="", help="作る素材名をカンマ区切りで（省略時はすべて）")
    p.add_argument("--texture-size", type=int, default=2048)
    return p.parse_args(argv)


def triangulate(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) != 3])
    bm.to_mesh(obj.data)
    bm.free()


def ensure_uv(obj):
    if obj.data.uv_layers:
        return
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.01)
    bpy.ops.object.mode_set(mode="OBJECT")


def covered_body_verts(ref, garment, max_dist=0.05, erode=1, inside=False):
    """服の下に隠れる体の頂点（評価後メッシュの番号）。法線方向へ光線を飛ばし、肌と同じ向きの服の面に
    当たるものを選び、縁から erode 輪ぶん内側だけを残す（縁の近くは隠すと隙間が見えるため）。

    向きの条件がないと、腕の内側の肌から出た光線が胴体側の服に当たり、見えている肌まで隠してしまう。
    inside=True（閉じた殻の靴）では、殻の内側か表面から 2 mm 以内にある頂点も隠す（底の縁が肌に食い込むため）。"""
    from mathutils.bvhtree import BVHTree
    deps = bpy.context.evaluated_depsgraph_get()
    gbvh = BVHTree.FromObject(garment, deps)
    mesh = ref.mesh
    hit = set()
    for v in mesh.vertices:
        loc, normal, _, dist = gbvh.ray_cast(v.co + v.normal * 0.0005, v.normal, max_dist)
        if loc is not None and abs(normal.dot(v.normal)) > 0.5:
            hit.add(v.index)
        elif inside:
            loc, normal, _, _ = gbvh.find_nearest(v.co, 0.03)
            if loc is not None and (v.co - loc).dot(normal) < 0.002:
                hit.add(v.index)
    neighbours = {v.index: set() for v in mesh.vertices}
    for e in mesh.edges:
        a, b = e.vertices
        neighbours[a].add(b)
        neighbours[b].add(a)
    for _ in range(erode):
        hit = {i for i in hit if neighbours[i] <= hit}
    return hit


def eval_to_original(body):
    """評価後（MASK で補助形状を除いた）頂点番号 → 基本メッシュの頂点番号。"""
    gi = body.vertex_groups["body"].index
    return [v.index for v in body.data.vertices if any(g.group == gi and g.weight > 0 for g in v.groups)]


def write_mhclo(human, obj, kind, name, out_dir, description, delete_verts=None):
    ClothesService = mpfb_env.service("mpfb.services.clothesservice", "ClothesService")
    tmp = obj.copy()
    tmp.data = obj.data.copy()
    bpy.context.collection.objects.link(tmp)
    tmp.vertex_groups.clear()
    for m in list(tmp.modifiers):
        tmp.modifiers.remove(m)
    tmp.parent = None
    tmp.matrix_world = obj.matrix_world
    g = tmp.vertex_groups.new(name="body")
    g.add(list(range(len(tmp.data.vertices))), 1.0, "REPLACE")
    delete_group = None
    if delete_verts:
        delete_group = f"BH_delete_{name}"
        dg = human.vertex_groups.get(delete_group) or human.vertex_groups.new(name=delete_group)
        dg.add(sorted(delete_verts), 1.0, "REPLACE")
    props = {"name": name, "uuid": str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, f"blender-men/{kind}/{name}")),
             "author": "Blender-men (generated)", "license": "CC0", "description": description}
    t = time.time()
    mhclo = ClothesService.create_mhclo_from_clothes_matching(human, tmp, properties_dict=props,
                                                              delete_group=delete_group)
    match_time = time.time() - t
    path = os.path.join(out_dir, f"{name}.mhclo")
    mhclo.write_mhclo(path, reference_scale=ClothesService.get_reference_scale(human))
    bpy.data.objects.remove(tmp)
    if delete_group:
        human.vertex_groups.remove(human.vertex_groups[delete_group])
    return path, round(match_time, 2)


def save_material(obj, out_dir, color_file, normal_file=None, alpha=False, roughness=0.6, extra=None):
    data = {"color": color_file, "normal": normal_file, "alpha_blend": alpha, "roughness": roughness}
    if extra:
        data.update(extra)
    with open(os.path.join(out_dir, "material.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    args = parse_args()
    only = {x for x in args.only.split(",") if x}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mpfb_env.enable()
    config = build_character.load_config(MASTER_CONFIG)
    builder = build_character.Builder(config, os.path.join(ASSET_DIR, "_tmp"))
    builder.body()
    builder.skeleton()
    builder.skin()
    builder.eyes_asset()
    human, rig, eyes = builder.human, builder.rig, builder.eyes
    index_path = os.path.join(ASSET_DIR, "index.json")
    index = json.load(open(index_path, encoding="utf-8")) if os.path.exists(index_path) else {"hair": {}, "clothes": {}}
    eval_map = eval_to_original(human)

    for style, spec in hair.STYLES.items():
        if only and style not in only:
            continue
        t0 = time.time()
        out = os.path.join(ASSET_DIR, "hair", style)
        os.makedirs(out, exist_ok=True)
        obj = hair.make_hair(human, eyes, rig, style, HAIR_COLOR, os.path.join(out, "strands.png"),
                             scalp_path=os.path.join(out, "scalp.png"), scalp_res=4096)
        gen_time = time.time() - t0
        path, match_time = write_mhclo(human, obj, "hair", style, out, spec["description"])
        save_material(obj, out, "strands.png", alpha=True, roughness=0.42, extra={"scalp": "scalp.png"})
        index["hair"][style] = {"mhclo": os.path.relpath(path, ASSET_DIR), "description": spec["description"],
                                "faces": len(obj.data.polygons), "generate_s": round(gen_time, 1),
                                "match_s": match_time, "license": "CC0 (generated in this repository)"}
        bpy.data.objects.remove(obj)
        print(json.dumps({"asset": f"hair/{style}", **index["hair"][style]}, ensure_ascii=False))

    ref = clothes.BodyRef(human, rig)
    for name, spec in clothes.GARMENTS.items():
        if only and name not in only:
            continue
        t0 = time.time()
        out = os.path.join(ASSET_DIR, "clothes", name)
        os.makedirs(out, exist_ok=True)
        obj, _ = clothes.make_garment(ref, human, rig, name)
        expected = clothes.EXPECTED_OPENINGS.get(name)
        if expected is not None and obj.get("bh_boundary_loops") != expected:
            # 穴が開いた服は素材にしない（原因を記録して次へ。既存の素材は上書きしない）
            index["clothes"][name] = {"error": f"開口部 {obj.get('bh_boundary_loops')} 個（期待 {expected} 個）: 穴または裂け"}
            print(json.dumps({"asset": f"clothes/{name}", **index["clothes"][name]}, ensure_ascii=False))
            bpy.data.objects.remove(obj)
            continue
        triangulate(obj)
        ensure_uv(obj)
        gen_time = time.time() - t0
        t1 = time.time()
        baked = bake.bake_object(obj, out, "fabric", size=args.texture_size, normal=True)
        bake_time = time.time() - t1
        # 靴は足の凸包から作るのでつま先で足から離れる。届く距離を広げる
        covered = covered_body_verts(ref, obj, max_dist=spec.get("cover_reach", 0.05),
                                 inside=spec.get("cover_inside", False))
        delete_verts = [eval_map[i] for i in covered]
        path, match_time = write_mhclo(human, obj, "clothes", name, out, f"{name} ({spec['fabric']})",
                                       delete_verts=delete_verts)
        save_material(obj, out, "fabric_color.png", "fabric_normal.png", roughness=spec["roughness"])
        index["clothes"][name] = {"mhclo": os.path.relpath(path, ASSET_DIR), "fabric": spec["fabric"],
                                  "faces": len(obj.data.polygons), "hidden_body_verts": len(delete_verts),
                                  "sim_reverted_verts": obj.get("bh_sim_reverted_verts", 0),
                                  "openings": obj.get("bh_boundary_loops"),
                                  "generate_s": round(gen_time, 1), "bake_s": round(bake_time, 1),
                                  "match_s": match_time, "license": "CC0 (generated in this repository)"}
        for img in baked.values():
            bpy.data.images.remove(img)
        bpy.data.objects.remove(obj)
        print(json.dumps({"asset": f"clothes/{name}", **index["clothes"][name]}, ensure_ascii=False))
    ref.free()
    index["outfits"] = clothes.OUTFITS
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
