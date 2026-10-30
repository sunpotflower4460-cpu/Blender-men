"""ベース人体（骨組み + 簡易マネキン）を生成するスクリプト。

Blender 標準アドオン Rigify の Human メタリグをテンプレートとして使い、
プリセット JSON のプロポーションに合わせて骨を調整したうえで、

  * BH_metarig : 体型の「設計図」。キャラごとにここを編集する
  * BH_rig     : Rigify が生成したコントロールリグ（将来アニメーションに使う）
  * BH_body    : DEF ボーンに追従する簡易マネキンメッシュ（体型確認用）

を 1 つの .blend に保存する。

実行方法:
  blender -b -P scripts/build_base_human.py -- --preset presets/base.json
  python scripts/build_base_human.py --preset presets/base.json   # pip の bpy モジュール使用時
"""

import argparse
import json
import math
import os
import sys

import bpy
import addon_utils  # bpy の読み込み後でないと import できない
from mathutils import Matrix, Vector

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_PARAMS = {
    "name": "base",
    "height": 1.70,          # 頭頂（spine.006 の tail）までの身長 [m]
    "leg_length": 1.0,       # 太もも + すねの長さ倍率
    "torso_length": 1.0,     # 胴（骨盤〜首の付け根）の長さ倍率
    "arm_length": 1.0,       # 上腕 + 前腕の長さ倍率
    "shoulder_width": 1.0,   # 肩幅倍率
    "hip_width": 1.0,        # 股関節の幅倍率
    "head_scale": 1.0,       # 頭部（顔ボーン含む）の大きさ倍率
    "girth": 1.0,            # マネキンの太さ倍率（骨格には影響しない）
}

# マネキン用の断面半径 [m]（身長 1.70m 基準）。
# (根元の半径, 先端の半径, X 方向の扁平率, Z 方向の扁平率, 端の丸みの深さ)
# 端の丸みの深さ None は半径と同じ（普通のカプセル）。
# 表に無いボーン（顔・pelvis・breast など）はマネキンに含めない。
REFERENCE_HEIGHT = 1.70
BODY_SHAPES = {
    "spine":       (0.125, 0.115, 1.25, 0.85, 0.09),
    "spine.001":   (0.115, 0.115, 1.20, 0.80, 0.04),
    "spine.002":   (0.120, 0.130, 1.25, 0.80, 0.04),
    "spine.003":   (0.130, 0.110, 1.25, 0.80, 0.04),
    "spine.004":   (0.056, 0.052, 1.0, 1.0, 0.02),
    "spine.005":   (0.052, 0.050, 1.0, 1.0, 0.02),
    "shoulder":    (0.040, 0.050, 1.0, 1.0, None),
    "upper_arm":   (0.048, 0.040, 1.0, 1.0, None),
    "forearm":     (0.038, 0.028, 1.0, 1.0, None),
    "hand":        (0.020, 0.020, 1.0, 1.0, None),
    "palm":        (0.013, 0.012, 1.0, 1.0, None),
    "thumb":       (0.011, 0.009, 1.0, 1.0, None),
    "f_":          (0.009, 0.007, 1.0, 1.0, None),
    "thigh":       (0.085, 0.055, 1.0, 1.0, None),
    "shin":        (0.052, 0.036, 1.0, 1.0, None),
    "foot":        (0.034, 0.030, 1.0, 0.8, None),
    "toe":         (0.030, 0.022, 1.0, 0.7, None),
}


# ---------------------------------------------------------------------------
# 引数・プリセット
# ---------------------------------------------------------------------------

def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--preset", default=os.path.join(REPO_ROOT, "presets", "base.json"))
    parser.add_argument("--output", help="保存先 .blend（省略時 models/<name>_human.blend）")
    parser.add_argument("--preview", help="プレビュー PNG の保存先（省略時は描画しない）")
    parser.add_argument("--no-rig", action="store_true", help="Rigify のコントロールリグを生成しない")
    return parser.parse_args(argv)


def load_params(path):
    params = dict(DEFAULT_PARAMS)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    unknown = set(data) - set(DEFAULT_PARAMS) - {"description"}
    if unknown:
        raise ValueError(f"未知のパラメータ: {sorted(unknown)}")
    params.update({k: v for k, v in data.items() if k in DEFAULT_PARAMS})
    return params


# ---------------------------------------------------------------------------
# メタリグのプロポーション調整
#
# 編集ボーンの head/tail を {名前: [head, tail]} の辞書に写し、
# 純粋な座標変換を順番に適用してから最後にまとめて書き戻す。
# 接続ボーンの関節位置を共有したまま変形できるようにするため。
# ---------------------------------------------------------------------------

class Skeleton:
    def __init__(self, edit_bones):
        self.pts = {eb.name: [eb.head.copy(), eb.tail.copy()] for eb in edit_bones}
        self.children = {eb.name: [c.name for c in eb.children] for eb in edit_bones}

    def subtree(self, name, include_self=True):
        out = [name] if include_self else []
        for c in self.children[name]:
            out.extend(self.subtree(c))
        return out

    def translate(self, names, delta):
        for n in names:
            for p in self.pts[n]:
                p += delta

    def scale_about(self, names, center, factor):
        for n in names:
            for p in self.pts[n]:
                p[:] = center + (p - center) * factor

    def scale_chain(self, chain, factor):
        """chain の骨を根元から順に factor 倍し、先の骨はその分だけ平行移動する。"""
        head = self.pts[chain[0]][0].copy()
        for n in chain:
            h, t = self.pts[n]
            new_t = head + (t - h) * factor
            self.pts[n] = [head.copy(), new_t]
            head = new_t
        old_end = None
        tail_children = [c for c in self.children[chain[-1]] if c not in chain]
        if tail_children:
            old_end = self.pts[tail_children[0]][0].copy()
        delta = head - old_end if old_end is not None else Vector()
        for c in tail_children:
            self.translate(self.subtree(c), delta)
        return delta

    def write_back(self, edit_bones):
        for eb in edit_bones:
            eb.head, eb.tail = self.pts[eb.name]

    def min_z(self):
        return min(min(h.z, t.z) for h, t in self.pts.values())


def apply_proportions(sk, p):
    sides = ("L", "R")

    # 脚の長さ: 太もも・すねを伸縮して足を平行移動し、最後に全身を接地させる
    for s in sides:
        sk.scale_chain([f"thigh.{s}", f"shin.{s}"], p["leg_length"])
    floor_shift = -sk.min_z()
    sk.translate(list(sk.pts), Vector((0, 0, floor_shift)))

    # 胴の長さ: 骨盤〜胸の骨を腰(spine の head)を基準に Z 方向へ伸縮
    hip = sk.pts["spine"][0].copy()
    torso = ["spine", "spine.001", "spine.002", "spine.003"]
    torso += [f"{b}.{s}" for b in ("pelvis", "breast", "shoulder") for s in sides]
    neck_before = sk.pts["spine.004"][0].copy()
    arm_roots_before = {s: sk.pts[f"upper_arm.{s}"][0].copy() for s in sides}
    t = p["torso_length"]
    for n in torso:
        for pt in sk.pts[n]:
            pt.z = hip.z + (pt.z - hip.z) * t
    def torso_map(v):
        return Vector((v.x, v.y, hip.z + (v.z - hip.z) * t))
    sk.translate(sk.subtree("spine.004"), torso_map(neck_before) - neck_before)
    for s in sides:
        root = arm_roots_before[s]
        sk.translate(sk.subtree(f"upper_arm.{s}"), torso_map(root) - root)

    # 腕の長さ: 上腕・前腕を伸縮（手はそのまま平行移動）
    for s in sides:
        sk.scale_chain([f"upper_arm.{s}", f"forearm.{s}"], p["arm_length"])

    # 肩幅: 腕全体を X 方向へずらし、鎖骨(shoulder)の先端も合わせる
    for s in sides:
        root = sk.pts[f"upper_arm.{s}"][0]
        delta = Vector((root.x * (p["shoulder_width"] - 1.0), 0, 0))
        sk.translate(sk.subtree(f"upper_arm.{s}"), delta)
        sk.pts[f"shoulder.{s}"][1] += delta

    # 腰幅: 脚全体を X 方向へずらし、骨盤の骨の先端も合わせる
    for s in sides:
        root = sk.pts[f"thigh.{s}"][0]
        delta = Vector((root.x * (p["hip_width"] - 1.0), 0, 0))
        sk.translate(sk.subtree(f"thigh.{s}"), delta)
        sk.pts[f"pelvis.{s}"][1] += delta

    # 頭の大きさ: 頭の骨と顔ボーン一式を頭の付け根を中心に拡縮
    sk.scale_about(sk.subtree("spine.006"), sk.pts["spine.006"][0].copy(), p["head_scale"])

    # 身長: 頭頂(spine.006 tail)が指定の高さになるよう全体を原点基準で拡縮
    top = sk.pts["spine.006"][1].z
    sk.scale_about(list(sk.pts), Vector(), p["height"] / top)


# ---------------------------------------------------------------------------
# シーン構築
# ---------------------------------------------------------------------------

def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon_utils.enable("rigify", default_set=True)


def build_metarig(params):
    bpy.ops.object.armature_human_metarig_add()
    metarig = bpy.context.object
    metarig.name = "BH_metarig"
    metarig.data.name = "BH_metarig"
    bpy.ops.object.mode_set(mode="EDIT")
    sk = Skeleton(metarig.data.edit_bones)
    apply_proportions(sk, params)
    sk.write_back(metarig.data.edit_bones)
    bpy.ops.object.mode_set(mode="OBJECT")
    return metarig


def generate_rig(metarig):
    metarig.data.rigify_rig_basename = "BH_rig"
    bpy.context.view_layer.objects.active = metarig
    metarig.select_set(True)
    bpy.ops.pose.rigify_generate()
    rig = bpy.data.objects["BH_rig"]
    metarig.hide_set(True)
    metarig.hide_render = True
    return rig


def body_shape_for(base_name):
    if base_name in BODY_SHAPES:
        return BODY_SHAPES[base_name]
    for key, shape in BODY_SHAPES.items():
        if base_name.startswith(key):
            return shape
    return None


def add_capsule(verts, faces, matrix, length, r0, r1, ax, az, cap, segments=16, cap_rings=5):
    """ボーン空間（Y 軸が骨の向き）の先細りカプセルを verts/faces に追加する。"""
    cap0 = r0 if cap is None else cap
    cap1 = r1 if cap is None else cap
    # 断面リング (骨方向の位置 y, 半径)
    rings = [(-cap0 * math.cos((math.pi / 2) * i / cap_rings), r0 * math.sin((math.pi / 2) * i / cap_rings))
             for i in range(1, cap_rings + 1)]
    rings += [(length + cap1 * math.cos((math.pi / 2) * i / cap_rings), r1 * math.sin((math.pi / 2) * i / cap_rings))
              for i in range(cap_rings, 0, -1)]
    if length <= 0 and r0 == r1:
        del rings[cap_rings]  # 長さ 0（楕円体）のときは赤道リングの重複を除く

    start = len(verts)
    verts.append(matrix @ Vector((0, -cap0, 0)))
    for y, r in rings:
        for k in range(segments):
            phi = 2 * math.pi * k / segments
            verts.append(matrix @ Vector((r * ax * math.cos(phi), y, r * az * math.sin(phi))))
    verts.append(matrix @ Vector((0, length + cap1, 0)))
    first, last = start, len(verts) - 1

    def ring_idx(i, k):
        return start + 1 + i * segments + (k % segments)

    for k in range(segments):
        faces.append((first, ring_idx(0, k + 1), ring_idx(0, k)))
        faces.append((last, ring_idx(len(rings) - 1, k), ring_idx(len(rings) - 1, k + 1)))
    for i in range(len(rings) - 1):
        for k in range(segments):
            faces.append((ring_idx(i, k), ring_idx(i, k + 1), ring_idx(i + 1, k + 1), ring_idx(i + 1, k)))
    return range(start, len(verts))


def build_body(armature, params):
    """DEF ボーンごとにカプセルを作り、そのボーンに 100% ウェイトで割り当てる。"""
    size = params["height"] / REFERENCE_HEIGHT
    girth = size * params["girth"]
    deform_bones = [b for b in armature.data.bones if b.use_deform]
    verts, faces, groups = [], [], {}

    for bone in deform_bones:
        # "DEF-thigh.L.001" → base "thigh", seg 1 / "DEF-f_index.02.L" → base "f_index.02"
        parts = bone.name.removeprefix("DEF-").split(".")
        seg = 0
        if len(parts) >= 3 and parts[-2] in ("L", "R") and parts[-1].isdigit():
            seg = int(parts.pop())
        if parts[-1] in ("L", "R"):
            parts.pop()
        base = ".".join(parts)
        if base == "spine.006":
            continue  # 頭は別途楕円体で作る
        shape = body_shape_for(base if base.startswith("spine") else parts[0])
        if shape is None:
            continue
        r0, r1, ax, az, cap = shape
        # 分割ボーン（thigh / thigh.001 など）は全体の太さの変化を前後で分け合う
        if parts[0] in ("upper_arm", "thigh", "shin", "forearm"):
            r0, r1 = (r0, (r0 + r1) / 2) if seg == 0 else ((r0 + r1) / 2, r1)
        idx = add_capsule(verts, faces, bone.matrix_local, bone.length,
                          r0 * girth, r1 * girth, ax, az,
                          None if cap is None else cap * girth)
        groups.setdefault(bone.name, []).extend(idx)

    # 頭: spine.006 に沿った楕円体（下顎の分だけ前に寄せる）
    head = armature.data.bones["DEF-spine.006"]
    hl = head.length
    head_matrix = head.matrix_local @ Matrix.Translation((0, hl * 0.5, -hl * 0.12))
    idx = add_capsule(verts, faces, head_matrix, 0.0,
                      hl * 0.47, hl * 0.47, 0.88, 1.05, hl * 0.52, segments=24, cap_rings=10)
    groups.setdefault(head.name, []).extend(idx)

    mesh = bpy.data.meshes.new("BH_body")
    mesh.from_pydata([tuple(v) for v in verts], [], faces)
    mesh.update()
    for poly in mesh.polygons:
        poly.use_smooth = True
    body = bpy.data.objects.new("BH_body", mesh)
    bpy.context.collection.objects.link(body)
    for name, indices in groups.items():
        vg = body.vertex_groups.new(name=name)
        vg.add(list(indices), 1.0, "REPLACE")
    body.parent = armature
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = armature

    mat = bpy.data.materials.new("BH_skin_neutral")
    mat.diffuse_color = (0.80, 0.66, 0.56, 1.0)
    mesh.materials.append(mat)
    return body


def organize(objects, params):
    coll = bpy.data.collections.new("BaseHuman")
    bpy.context.scene.collection.children.link(coll)
    for obj in objects:
        for c in list(obj.users_collection):
            c.objects.unlink(obj)
        coll.objects.link(obj)
    # 後から体型を作り替えるときの元データとしてパラメータを残す
    for obj in objects:
        if obj.type == "ARMATURE":
            obj["base_human_params"] = json.dumps(params, ensure_ascii=False)


def render_preview(path, params):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_cavity = True
    scene.render.film_transparent = False
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 900
    h = params["height"]

    cam_data = bpy.data.cameras.new("PreviewCam")
    cam_data.type = "ORTHO"
    cam_data.sensor_fit = "VERTICAL"
    cam_data.ortho_scale = h * 1.12
    cam = bpy.data.objects.new("PreviewCam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    renders = []
    for label, loc, rot in (
        ("front", (0, -10, h * 0.5), (math.radians(90), 0, 0)),
        ("side", (10, 0, h * 0.5), (math.radians(90), 0, math.radians(90))),
    ):
        cam.location = loc
        cam.rotation_euler = rot
        out = path.replace(".png", f"_{label}.png")
        scene.render.filepath = out
        bpy.ops.render.render(write_still=True)
        renders.append(out)
    bpy.data.objects.remove(cam)
    return renders


def main():
    args = parse_args()
    params = load_params(args.preset)
    reset_scene()

    metarig = build_metarig(params)
    objects = [metarig]
    if not args.no_rig:
        rig = generate_rig(metarig)
        body = build_body(rig, params)
        objects += [rig, body]
    organize(objects, params)

    if args.preview:
        os.makedirs(os.path.dirname(os.path.abspath(args.preview)), exist_ok=True)
        render_preview(os.path.abspath(args.preview), params)

    output = args.output or os.path.join(REPO_ROOT, "models", f"{params['name']}_human.blend")
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(output), compress=True)
    print(f"saved: {output}")


if __name__ == "__main__":
    main()
