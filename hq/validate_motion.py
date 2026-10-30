"""ゲーム用骨格での変形と GLB 書き出しを検証する。

1. game_engine 骨格にパンチ・キック・しゃがみ（＋休止）のポーズをキーフレームで付ける
2. Blender 上で各ポーズの変形を数値化（辺の伸び縮み、目が頭に追従しているか）
3. GLB に書き出し → 新しいシーンへ読み込み直し → 同じフレームの頂点位置を Blender と比較
4. Blender 版と GLB 版の各ポーズを描画（Workbench で変形を見やすく）

結果は JSON（--report）に保存する。基準を満たさない場合は終了コード 1。

実行方法:
  python hq/validate_motion.py --blend out/master.blend --glb out/master.glb \
      --render-dir previews/hq/master/motion --report out/master_motion.json
"""

import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

FRAMES = {"rest": 1, "punch": 11, "kick": 21, "crouch": 31}

# 合格基準
MAX_GLB_ERROR_M = 0.005        # GLB と Blender の頂点位置の差（最大）[m]
MAX_EDGE_STRETCH = 2.5         # 辺の長さが休止時の何倍まで伸びてよいか
MIN_EDGE_SHRINK = 0.25         # 辺の長さが休止時の何倍まで縮んでよいか
MAX_BAD_EDGE_RATIO = 0.002     # 上の範囲を外れる辺の割合
MAX_EYE_DRIFT_M = 0.004        # 目と頭の骨の距離の変化
MAX_PENETRATION = 0.03         # 服の頂点のうち、体の内側へ 2mm 以上めり込んだものの割合


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--glb", required=True)
    p.add_argument("--render-dir", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--save-posed", help="ポーズ付きの .blend を保存する（任意）")
    p.add_argument("--image-format", default="JPEG", choices=["JPEG", "AUTO", "WEBP"],
                   help="GLB に埋め込む画像の形式（JPEG は容量が小さい。形状の検証には影響しない）")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# ポーズ（骨の向きを体の座標で指定する。正面 -Y、上 +Z、キャラクターの左 +X）
# ---------------------------------------------------------------------------

def _update():
    bpy.context.view_layer.update()


def aim(rig, name, direction):
    pb = rig.pose.bones[name]
    _update()
    cur = (pb.tail - pb.head).normalized()
    q = cur.rotation_difference(Vector(direction).normalized())
    pivot = pb.head.copy()
    pb.matrix = Matrix.Translation(pivot) @ q.to_matrix().to_4x4() @ Matrix.Translation(-pivot) @ pb.matrix


def turn(rig, name, axis, degrees):
    pb = rig.pose.bones[name]
    _update()
    q = Quaternion(Vector(axis), math.radians(degrees))
    pivot = pb.head.copy()
    pb.matrix = Matrix.Translation(pivot) @ q.to_matrix().to_4x4() @ Matrix.Translation(-pivot) @ pb.matrix


def shift(rig, name, delta):
    pb = rig.pose.bones[name]
    _update()
    pb.matrix = Matrix.Translation(Vector(delta)) @ pb.matrix


def guard(rig, sides=("l", "r")):
    for s in sides:
        sx = 1 if s == "l" else -1
        aim(rig, f"upperarm_{s}", (sx * 0.30, -0.45, -0.84))
        aim(rig, f"lowerarm_{s}", (-sx * 0.20, -0.50, 0.84))
        for f in ("index", "middle", "ring", "pinky"):
            for i, ang in (("01", 70), ("02", 85), ("03", 60)):
                turn(rig, f"{f}_{i}_{s}", (1, 0, 0), ang)


def pose_rest(rig):
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)


def pose_punch(rig, height):
    pose_rest(rig)
    shift(rig, "pelvis", (0, 0, -0.04 * height))
    turn(rig, "spine_02", (0, 0, 1), 18)
    aim(rig, "thigh_l", (0.10, -0.32, -0.94))
    aim(rig, "calf_l", (0.08, -0.02, -1.0))
    aim(rig, "thigh_r", (-0.12, 0.30, -0.95))
    aim(rig, "calf_r", (-0.10, 0.42, -0.90))
    guard(rig, ("l",))
    aim(rig, "clavicle_r", (-1.0, -0.35, 0.0))
    aim(rig, "upperarm_r", (-0.10, -1.0, 0.04))
    aim(rig, "lowerarm_r", (-0.04, -1.0, 0.04))


def pose_kick(rig, height):
    pose_rest(rig)
    turn(rig, "spine_01", (1, 0, 0), -10)
    aim(rig, "thigh_l", (0.10, 0.06, -1.0))
    aim(rig, "calf_l", (0.08, 0.10, -1.0))
    aim(rig, "thigh_r", (-0.08, -0.92, 0.38))
    aim(rig, "calf_r", (-0.05, -1.0, 0.12))
    aim(rig, "foot_r", (0.0, -1.0, 0.25))
    guard(rig)


def pose_crouch(rig, height):
    pose_rest(rig)
    shift(rig, "pelvis", (0, 0.05 * height, -0.30 * height))
    turn(rig, "spine_01", (1, 0, 0), 18)
    for s, sx in (("l", 1), ("r", -1)):
        aim(rig, f"thigh_{s}", (sx * 0.28, -0.84, -0.46))
        aim(rig, f"calf_{s}", (sx * 0.10, 0.55, -0.83))
        aim(rig, f"foot_{s}", (sx * 0.05, -1.0, -0.15))
    guard(rig)


POSES = {"rest": pose_rest, "punch": pose_punch, "kick": pose_kick, "crouch": pose_crouch}


def key_all(rig, frame):
    for pb in rig.pose.bones:
        pb.keyframe_insert("location", frame=frame)
        pb.keyframe_insert("rotation_quaternion", frame=frame)
        pb.keyframe_insert("scale", frame=frame)


def build_animation(rig, height):
    rig.animation_data_create()
    rig.animation_data.action = bpy.data.actions.new("BH_validation")
    bpy.context.view_layer.objects.active = rig
    for pb in rig.pose.bones:
        pb.rotation_mode = "QUATERNION"
    for name, frame in FRAMES.items():
        bpy.context.scene.frame_set(frame)
        if name == "rest":
            pose_rest(rig)
        else:
            POSES[name](rig, height)
        key_all(rig, frame)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, max(FRAMES.values())


# ---------------------------------------------------------------------------
# 計測
# ---------------------------------------------------------------------------

def evaluated_coords(obj):
    deps = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(deps)
    mesh = ev.to_mesh()
    coords = [obj.matrix_world @ v.co for v in mesh.vertices]
    edges = [tuple(e.vertices) for e in mesh.edges]
    ev.to_mesh_clear()
    return coords, edges


def edge_stats(rest, posed, edges):
    bad, worst_stretch, worst_shrink = 0, 1.0, 1.0
    for a, b in edges:
        r = (rest[a] - rest[b]).length
        if r < 1e-6:
            continue
        ratio = (posed[a] - posed[b]).length / r
        worst_stretch = max(worst_stretch, ratio)
        worst_shrink = min(worst_shrink, ratio)
        if ratio > MAX_EDGE_STRETCH or ratio < MIN_EDGE_SHRINK:
            bad += 1
    return {"edges": len(edges), "bad_edges": bad, "bad_ratio": round(bad / max(1, len(edges)), 5),
            "max_stretch": round(worst_stretch, 3), "min_shrink": round(worst_shrink, 3)}


def _inside_ratio(body, garment, reach):
    deps = bpy.context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(body, deps)
    coords, _ = evaluated_coords(garment)
    inside = 0
    for c in coords:
        loc, normal, _, dist = bvh.find_nearest(c, reach)
        if loc is not None and (c - loc).dot(normal) < -0.002:
            inside += 1
    return round(inside / max(1, len(coords)), 5)


def penetration(body, garment):
    """服と体の干渉。

    visible: 見えている（Delete で消していない）肌から 1.5cm 以内の服の頂点のうち、肌より 2mm 以上
             内側にあるものの割合。これが大きいと、服の上に肌が突き出して見える（合否判定に使う）。
    under:   服の下で消している肌も含めた体に対する同じ割合（参考値。布が肌に食い込んでいる量）。
    """
    visible = _inside_ratio(body, garment, 0.015)
    masks = [m for m in body.modifiers if m.type == "MASK" and m.name.startswith("Delete")]
    for m in masks:
        m.show_viewport = False
    under = _inside_ratio(body, garment, 0.03)
    for m in masks:
        m.show_viewport = True
    return {"visible": visible, "under": under}


def eye_drift(rig, eyes, rest_offset):
    _update()
    head = rig.matrix_world @ rig.pose.bones["head"].head
    coords, _ = evaluated_coords(eyes)
    centre = sum(coords, Vector()) / len(coords)
    local = (rig.matrix_world @ rig.pose.bones["head"].matrix).inverted() @ centre
    if rest_offset is None:
        return local, 0.0
    return rest_offset, (local - rest_offset).length


# ---------------------------------------------------------------------------
# 描画（Workbench：変形が見やすいスタジオ照明）
# ---------------------------------------------------------------------------

def setup_workbench():
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "MATERIAL"
    sc.display.shading.show_cavity = True
    sc.render.resolution_x, sc.render.resolution_y = 600, 800
    sc.render.film_transparent = False
    if sc.world is None:
        sc.world = bpy.data.worlds.new("BH_validate_world")
    sc.world.color = (0.32, 0.33, 0.35)
    cam = bpy.data.objects.get("BH_validate_cam")
    if cam is None:
        cam = bpy.data.objects.new("BH_validate_cam", bpy.data.cameras.new("BH_validate_cam"))
        sc.collection.objects.link(cam)
    sc.camera = cam
    for obj in sc.objects:
        if obj.type == "MESH":
            for slot in obj.material_slots:
                if slot.material is not None:
                    slot.material.diffuse_color = (0.78, 0.62, 0.52, 1.0) if "eye" not in obj.name.lower() else (0.9, 0.9, 0.9, 1.0)
    return cam


# 全身（斜め前）と、変形が厳しい部位の寄り
SHOTS = {
    "full": ((1.9, -2.6, 1.15), (0, 0, 0.82), 50),
    "hips": ((0.35, -1.5, 0.55), (0, 0, 0.62), 70),
    "shoulder": ((-1.2, -1.0, 1.45), (-0.15, -0.2, 1.25), 70),
}


def aim_camera(cam, shot):
    loc, target, lens = SHOTS[shot]
    cam.data.lens = lens
    cam.location = loc
    cam.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()


def render(path):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def render_pose(cam, out_dir, prefix, pose):
    aim_camera(cam, "full")
    render(os.path.join(out_dir, f"{prefix}_{pose}.png"))
    detail = {"crouch": "hips", "kick": "hips", "punch": "shoulder"}.get(pose)
    if detail:
        aim_camera(cam, detail)
        render(os.path.join(out_dir, f"{prefix}_{pose}_{detail}.png"))


# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
    rig = bpy.data.objects["BH_rig"]
    body = bpy.data.objects["BH_body"]
    eyes = bpy.data.objects.get("BH_eyes")
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and o.parent == rig]
    height = max((rig.matrix_world @ b.tail_local).z for b in rig.data.bones)
    build_animation(rig, height)

    report = {"blend": args.blend, "glb": args.glb, "poses": {}, "glb_check": {}}
    sc = bpy.context.scene
    sc.frame_set(FRAMES["rest"])
    rest_coords, edges = evaluated_coords(body)
    eye_ref, _ = eye_drift(rig, eyes, None) if eyes else (None, 0.0)
    blender_coords = {}
    cam = setup_workbench()
    os.makedirs(args.render_dir, exist_ok=True)
    for name, frame in FRAMES.items():
        sc.frame_set(frame)
        coords, _ = evaluated_coords(body)
        blender_coords[name] = {o.name: evaluated_coords(o)[0] for o in meshes}
        stats = edge_stats(rest_coords, coords, edges)
        if eyes:
            _, drift = eye_drift(rig, eyes, eye_ref)
            stats["eye_drift_m"] = round(drift, 5)
        stats["penetration"] = {o.name: penetration(body, o) for o in meshes if o.name.startswith("BH_clothes")}
        report["poses"][name] = stats
        render_pose(cam, args.render_dir, "blender", name)
    if args.save_posed:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.save_posed), copy=True)

    # GLB 書き出し（Armature 以外のモディファイアは適用：MASK で補助形状を除く。細分化は外す）
    for o in meshes:
        for m in o.modifiers:
            if m.type == "SUBSURF":
                m.show_viewport = False
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = rig
    os.makedirs(os.path.dirname(os.path.abspath(args.glb)), exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=os.path.abspath(args.glb), export_format="GLB", use_selection=True,
                              export_apply=True, export_animations=True, export_skins=True,
                              export_morph=False, export_all_influences=False, export_yup=True,
                              export_image_format=args.image_format)
    report["glb_check"]["size_bytes"] = os.path.getsize(args.glb)

    # 読み込み直して比較
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=os.path.abspath(args.glb))
    sc = bpy.context.scene
    # 読み込み時に骨の表示用として作られるメッシュ（Icosphere）は除き、スキン付きのメッシュだけを比較する
    imported = {o.name: o for o in bpy.data.objects
                if o.type == "MESH" and any(m.type == "ARMATURE" for m in o.modifiers)}
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    report["glb_check"]["bones"] = len(arm.data.bones)
    report["glb_check"]["meshes"] = sorted(imported)
    action = arm.animation_data.action if arm.animation_data else None
    report["glb_check"]["has_animation"] = action is not None
    sc.frame_set(FRAMES["rest"])
    errors = {}
    for name_src, src_rest in blender_coords["rest"].items():
        dst = imported.get(name_src)
        if dst is None:
            errors[name_src] = "missing in GLB"
            continue
        tree = KDTree(len(src_rest))
        for i, c in enumerate(src_rest):
            tree.insert(c, i)
        tree.balance()
        sc.frame_set(FRAMES["rest"])
        dst_rest, _ = evaluated_coords(dst)
        mapping, rest_err = [], 0.0
        for c in dst_rest:
            _, idx, dist = tree.find(c)
            mapping.append(idx)
            rest_err = max(rest_err, dist)
        per_pose = {"rest_map_err_m": round(rest_err, 6), "vertices_glb": len(dst_rest),
                    "vertices_blender": len(src_rest)}
        for pose, frame in FRAMES.items():
            sc.frame_set(frame)
            dst_pose, _ = evaluated_coords(dst)
            src_pose = blender_coords[pose][name_src]
            diffs = sorted((dst_pose[i] - src_pose[mapping[i]]).length for i in range(len(dst_pose)))
            per_pose[pose] = {"max_m": round(diffs[-1], 6), "p99_m": round(diffs[int(len(diffs) * 0.99)], 6),
                              "mean_m": round(sum(diffs) / len(diffs), 6)}
        errors[name_src] = per_pose
    report["glb_check"]["vertex_error"] = errors

    # GLB 版の描画
    cam = setup_workbench()
    for name, frame in FRAMES.items():
        sc.frame_set(frame)
        render_pose(cam, args.render_dir, "glb", name)

    # 判定
    failures = []
    for name, st in report["poses"].items():
        if st["bad_ratio"] > MAX_BAD_EDGE_RATIO:
            failures.append(f"{name}: 破綻した辺の割合 {st['bad_ratio']}")
        if st.get("eye_drift_m", 0) > MAX_EYE_DRIFT_M:
            failures.append(f"{name}: 目が頭からずれた {st['eye_drift_m']} m")
        for garment, ratio in st.get("penetration", {}).items():
            if ratio["visible"] > MAX_PENETRATION:
                failures.append(f"{name}: {garment} の上に肌が突き出す割合 {ratio['visible']}")
    for mesh_name, per in errors.items():
        if isinstance(per, str):
            failures.append(f"GLB: {mesh_name} {per}")
            continue
        for pose in FRAMES:
            if per[pose]["max_m"] > MAX_GLB_ERROR_M:
                failures.append(f"GLB {mesh_name} {pose}: 最大誤差 {per[pose]['max_m']} m")
    report["failures"] = failures
    report["passed"] = not failures
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": report["passed"], "failures": failures[:10]}, ensure_ascii=False))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
