"""架空の男性キャラクター（スーツ姿）を作る。

軸（build_base_human.py の骨格・カプセル体）を土台に、
  * スーツ（ジャケット・スラックス）と白いシャツ、ネクタイ: 体のスキンを複製して厚みを付けた服
  * 黒髪のなでつけ風の髪: 頭の上半分に厚みを付けた髪の殻
  * 黒縁の眼鏡: 平面の枠を厚み付けした単純な形状
を、すべて骨格にウェイトを付けて関節に追従させる。

実行方法:
  python scripts/build_man_character.py --preset presets/character_man.json
"""

import argparse
import json
import math
import os
import sys

import bpy
import bmesh  # bpy の読み込み後でないと import できない
from mathutils import Vector

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS)
import build_base_human as base  # noqa: E402

REPO_ROOT = base.REPO_ROOT


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--preset", default=os.path.join(REPO_ROOT, "presets", "character_man.json"))
    p.add_argument("--out-dir", default=os.path.join(REPO_ROOT, "previews", "man"))
    p.add_argument("--output", default=os.path.join(REPO_ROOT, "models", "man_character.blend"))
    return p.parse_args(argv)


def make_material(name, color, roughness=0.5):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    m.diffuse_color = (*color, 1.0)
    return m


def dominant_group(obj, weights):
    best = None
    for gi, w in weights.items():
        if best is None or w > best[1]:
            best = (obj.vertex_groups[gi].name, w)
    return best[0] if best else ""


def garment(body, rig, name, keep, thickness, material, offset=1.0):
    """体のメッシュから keep(座標, 主なボーン名) が True の頂点だけを残し、厚みを付けて服にする。"""
    obj = body.copy()
    obj.data = body.data.copy()
    obj.name = name
    obj.data.name = name
    for m in list(obj.modifiers):
        obj.modifiers.remove(m)
    bpy.context.collection.objects.link(obj)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    deform = bm.verts.layers.deform.verify()
    drop = [v for v in bm.verts if not keep(v.co, dominant_group(obj, v[deform]))]
    bmesh.ops.delete(bm, geom=drop, context="VERTS")
    bm.to_mesh(obj.data)
    bm.free()

    obj.data.materials.clear()
    obj.data.materials.append(material)
    bpy.context.view_layer.objects.active = obj
    solid = obj.modifiers.new("Solidify", "SOLIDIFY")
    solid.thickness = thickness
    solid.offset = offset
    solid.use_even_offset = True
    bpy.ops.object.modifier_apply(modifier=solid.name)
    arm = obj.modifiers.new("Armature", "ARMATURE")
    arm.object = rig
    obj.parent = rig
    bpy.ops.object.shade_smooth()
    return obj


def keep_jacket(co, g):
    if g.startswith(("DEF-upper_arm", "DEF-shoulder")):
        return True
    if g in ("DEF-spine", "DEF-spine.001", "DEF-spine.002", "DEF-spine.003"):
        if co.z < 0.92:
            return False
        # 前のあき（シャツとネクタイが見える V 字部分）
        if abs(co.x) < 0.035 and co.y < -0.02 and 1.08 < co.z < 1.42:
            return False
        return True
    return False


def keep_shirt(co, g):
    return g in ("DEF-spine", "DEF-spine.001", "DEF-spine.002", "DEF-spine.003") and 0.92 < co.z < 1.48


def keep_trousers(co, g):
    if g.startswith(("DEF-thigh", "DEF-shin")):
        return True
    return g == "DEF-spine" and co.z < 1.02


def bind_to_group(obj, group_name, rig):
    vg = obj.vertex_groups.new(name=group_name)
    vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    arm = obj.modifiers.new("Armature", "ARMATURE")
    arm.object = rig
    obj.parent = rig


def make_tie(rig, head_group_z, front_y, material):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x *= 0.024
        v.co.y *= 0.010
        v.co.z *= 0.28
        v.co.y += front_y
        v.co.z += head_group_z
    mesh = bpy.data.meshes.new("BH_tie")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("BH_tie", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    bind_to_group(obj, "DEF-spine.003", rig)
    return obj


def make_glasses(rig, eye_y, eye_z, material):
    """黒縁の四角い眼鏡。枠は平面の輪を作ってから厚みを付ける。"""
    bm = bmesh.new()

    def frame(cx, w, h, border):
        outer = [(cx - w / 2, eye_z - h / 2), (cx + w / 2, eye_z - h / 2),
                 (cx + w / 2, eye_z + h / 2), (cx - w / 2, eye_z + h / 2)]
        inner = [(cx - w / 2 + border, eye_z - h / 2 + border), (cx + w / 2 - border, eye_z - h / 2 + border),
                 (cx + w / 2 - border, eye_z + h / 2 - border), (cx - w / 2 + border, eye_z + h / 2 - border)]
        o = [bm.verts.new((x, eye_y, z)) for x, z in outer]
        i = [bm.verts.new((x, eye_y, z)) for x, z in inner]
        for k in range(4):
            bm.faces.new((o[k], o[(k + 1) % 4], i[(k + 1) % 4], i[k]))

    frame(-0.036, 0.048, 0.034, 0.0045)
    frame(0.036, 0.048, 0.034, 0.0045)
    def bar(center, size):
        """中心と各辺の長さを指定した直方体を、その部品の頂点だけで作る。"""
        new = bmesh.ops.create_cube(bm, size=1.0)["verts"]
        for v in new:
            v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2])) + Vector(center)

    # 鼻の上のブリッジ
    bar((0, eye_y, eye_z + 0.006), (0.030, 0.004, 0.004))
    # こめかみのつる（耳へ向かう細い棒）
    for sx in (-1, 1):
        bar((sx * 0.078, eye_y + 0.07, eye_z + 0.012), (0.004, 0.14, 0.004))
    mesh = bpy.data.meshes.new("BH_glasses")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("BH_glasses", mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    bpy.context.view_layer.objects.active = obj
    solid = obj.modifiers.new("Solidify", "SOLIDIFY")
    solid.thickness = 0.0035
    solid.offset = 0.0
    bpy.ops.object.modifier_apply(modifier=solid.name)
    bind_to_group(obj, "DEF-spine.006", rig)
    bpy.ops.object.shade_smooth()
    return obj


def keep_hair(co, g):
    """頭の後ろ側と頭頂（額の生え際より上）を覆う。"""
    return g == "DEF-spine.006" and co.z > 1.62 and co.y > -0.02


def pose_arms(rig, down_deg=72):
    """腕を体の横へ下ろす（FK に切り替えてから、上腕のコントローラを回す）。"""
    for side, sign in (("L", 1), ("R", -1)):
        pb_parent = rig.pose.bones.get(f"upper_arm_parent.{side}")
        if pb_parent is not None:
            pb_parent["IK_FK"] = 1.0
        fk = rig.pose.bones.get(f"upper_arm_fk.{side}")
        if fk is not None:
            fk.rotation_mode = "XYZ"
            fk.rotation_euler = (0, 0, math.radians(-sign * down_deg))
        elbow = rig.pose.bones.get(f"forearm_fk.{side}")
        if elbow is not None:
            elbow.rotation_mode = "XYZ"
            elbow.rotation_euler = (0, 0, math.radians(-sign * 10))
    bpy.context.view_layer.update()


def studio():
    world = bpy.data.worlds.new("BH_world")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.20, 0.20, 0.22, 1.0)
    bg.inputs["Strength"].default_value = 0.3
    bpy.context.scene.world = world
    for name, loc, energy, rot in (
        ("key", (1.8, -2.8, 2.6), 450, (math.radians(60), 0, math.radians(35))),
        ("rim", (-2.0, 2.4, 2.4), 250, (math.radians(60), 0, math.radians(-150))),
    ):
        bpy.ops.object.light_add(type="AREA", location=loc)
        light = bpy.context.object
        light.name = f"BH_{name}"
        light.data.energy = energy
        light.data.size = 2.0
        light.rotation_euler = rot


def render(out_dir, center_z):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 96
    scene.cycles.use_denoising = True
    scene.view_settings.view_transform = "AgX"
    scene.render.resolution_x = 900
    scene.render.resolution_y = 1200
    scene.render.film_transparent = False
    cam_data = bpy.data.cameras.new("BH_cam")
    cam_data.lens = 85
    cam = bpy.data.objects.new("BH_cam", cam_data)
    bpy.context.collection.objects.link(cam)
    scene.camera = cam
    os.makedirs(out_dir, exist_ok=True)
    target = Vector((0, 0, center_z))
    for label, loc in (("front", (0, -4.0, center_z)), ("three_quarter", (2.8, -3.2, center_z))):
        cam.location = loc
        cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = os.path.join(out_dir, f"man_{label}.png")
        bpy.ops.render.render(write_still=True)


def main():
    args = parse_args()
    with open(args.preset, encoding="utf-8") as f:
        params = dict(base.DEFAULT_PARAMS)
        params.update({k: v for k, v in json.load(f).items() if k in base.DEFAULT_PARAMS})

    base.reset_scene()
    metarig = base.build_metarig(params)
    rig = base.generate_rig(metarig)
    body = base.build_body(rig, params)
    body.name = "BH_body"

    skin = make_material("BH_skin", (0.62, 0.44, 0.34), 0.5)
    body.data.materials.clear()
    body.data.materials.append(skin)

    suit = make_material("BH_suit", (0.13, 0.14, 0.16), 0.75)
    shirt = make_material("BH_shirt", (0.92, 0.92, 0.90), 0.4)
    tie = make_material("BH_tie_mat", (0.10, 0.12, 0.22), 0.5)
    trousers = make_material("BH_trousers", (0.12, 0.12, 0.13), 0.8)
    hair_mat = make_material("BH_hair_mat", (0.03, 0.025, 0.02), 0.35)
    frame_mat = make_material("BH_frame_mat", (0.02, 0.02, 0.02), 0.3)

    garment(body, rig, "BH_jacket", keep_jacket, 0.03, suit)
    garment(body, rig, "BH_shirt", keep_shirt, 0.012, shirt)
    garment(body, rig, "BH_trousers", keep_trousers, 0.022, trousers)

    # 頭の位置（頭の頂点の平均）と、顔の前面（最も前にある頭の頂点）から眼鏡の位置を決める
    head_verts = []
    for v in body.data.vertices:
        w = {body.vertex_groups[g.group].name: g.weight for g in v.groups}
        if w.get("DEF-spine.006", 0) > 0.5:
            head_verts.append(v.co.copy())
    head_center = sum(head_verts, Vector()) / len(head_verts)
    eye_y = min(c.y for c in head_verts) + 0.012
    eye_z = head_center.z - 0.02

    make_tie(rig, 1.27, -0.145, tie)
    make_glasses(rig, eye_y, eye_z, frame_mat)
    # 頭のメッシュは法線の向きが体と逆なので、厚みは反対側（外向き）へ付ける
    garment(body, rig, "BH_hair", keep_hair, 0.018, hair_mat, offset=-1.0)

    pose_arms(rig)
    studio()
    render(os.path.abspath(args.out_dir), params["height"] * 0.5)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.output), compress=True)
    print(f"saved: {args.output}")


if __name__ == "__main__":
    main()
