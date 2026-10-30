"""ベース人体に肌・目・髪を付け、色付きの完成形として .blend と描画を出力する。

入力は build_base_human.py --body makehuman で作った .blend（BH_body / BH_rig を含む）。
目は MakeHuman の目プロキシ（CC0, assets/eyes）を、体と同じ変換（回転・拡縮・接地）で配置する。

実行方法:
  python scripts/colorize_human.py --blend models/base_human_mh.blend --output models/base_human_colored.blend --out-dir previews
"""

import argparse
import math
import os

import bpy
from mathutils import Vector

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(REPO_ROOT, "assets")
RAW_BODY = os.path.join(ASSETS, "makehuman_base.obj")
EYE_OBJ = os.path.join(ASSETS, "eyes", "eye_low_poly.obj")
EYE_TEX = os.path.join(ASSETS, "eyes", "brown_eye.png")
HEIGHT = 1.70


def parse_args():
    import sys
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--out-dir", required=True)
    return p.parse_args(argv)


def mh_transform_params():
    """MakeHuman 生データ（Y 上・デシメートル）→ Blender（Z 上・メートル）の変換係数を求める。"""
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=RAW_BODY)
    raw = next(o for o in bpy.data.objects if o not in before)
    raw.rotation_euler = (math.radians(90), 0, 0)
    bpy.context.view_layer.update()
    zs = [(raw.matrix_world @ v.co).z for v in raw.data.vertices]
    scale = HEIGHT / (max(zs) - min(zs))
    zmin = min(zs) * scale
    bpy.data.objects.remove(raw, do_unlink=True)
    return scale, zmin


def import_eyes(scale, zmin):
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=EYE_OBJ)
    eyes = next(o for o in bpy.data.objects if o not in before)
    eyes.name = "BH_eyes"
    eyes.rotation_euler = (math.radians(90), 0, 0)
    eyes.scale = (scale, scale, scale)
    bpy.context.view_layer.update()
    eyes.location.z = -zmin
    bpy.context.view_layer.objects.active = eyes
    eyes.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    mat = bpy.data.materials.new("BH_eye")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(EYE_TEX)
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.05
    bsdf.inputs["Coat Weight"].default_value = 0.6
    eyes.data.materials.clear()
    eyes.data.materials.append(mat)
    bpy.ops.object.shade_smooth()
    eyes.parent = bpy.data.objects["BH_rig"]
    return eyes


def skin_material():
    mat = bpy.data.materials.new("BH_skin")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.74, 0.55, 0.45, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.42
    bsdf.inputs["Subsurface Weight"].default_value = 0.12
    bsdf.inputs["Subsurface Radius"].default_value = (1.0, 0.35, 0.22)
    bsdf.inputs["Subsurface Scale"].default_value = 0.004
    # 細かい毛穴と凹凸（ノイズ → バンプ）
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 400.0
    noise.inputs["Detail"].default_value = 8.0
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.04
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def hair(body):
    """頭頂付近の頂点に重みを置き、そこから髪（パーティクルヘア）を生やす。"""
    vg = body.vertex_groups.new(name="BH_hair_region")
    top = [v.index for v in body.data.vertices if v.co.z > 1.58]
    vg.add(top, 1.0, "REPLACE")

    body.modifiers.new("BH_hair_mod", "PARTICLE_SYSTEM")
    ps = body.particle_systems[-1]
    ps.vertex_group_density = vg.name
    settings = bpy.data.particles.new("BH_hair")
    settings.type = "HAIR"
    settings.count = 2500
    settings.hair_length = 0.22
    settings.emit_from = "FACE"
    settings.use_emit_random = True
    settings.use_advanced_hair = True
    settings.hair_step = 6
    settings.child_type = "NONE"
    settings.radius_scale = 0.0018
    settings.root_radius = 1.0
    settings.tip_radius = 0.2
    settings.use_hair_bspline = True
    settings.material = len(body.data.materials) + 1
    ps.settings = settings

    mat = bpy.data.materials.new("BH_hair_mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.12, 0.065, 0.035, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.35
    body.data.materials.append(mat)
    return ps


def studio(center_z):
    world = bpy.data.worlds.new("BH_world")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.78, 0.79, 0.82, 1.0)
    bg.inputs["Strength"].default_value = 0.9
    bpy.context.scene.world = world
    for name, loc, energy, size in (("key", (1.8, -2.4, 2.6), 900, 2.0), ("rim", (-2.0, 2.2, 2.2), 500, 2.0)):
        bpy.ops.object.light_add(type="AREA", location=loc)
        light = bpy.context.object
        light.name = f"BH_{name}"
        light.data.energy = energy
        light.data.size = size
        light.rotation_euler = (math.radians(60), 0, math.radians(35 if name == "key" else -150))


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
    for label, loc in (("front", (0, -4.2, center_z)), ("three_quarter", (3.0, -3.4, center_z))):
        cam.location = loc
        cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = os.path.join(out_dir, f"colored_{label}.png")
        bpy.ops.render.render(write_still=True)


def main():
    args = parse_args()
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
    body = bpy.data.objects["BH_body"]
    rig = bpy.data.objects["BH_rig"]

    body.data.materials.clear()
    body.data.materials.append(skin_material())
    scale, zmin = mh_transform_params()
    import_eyes(scale, zmin)
    hair(body)

    for obj in bpy.data.objects:
        if obj.name.startswith("WGT") or obj.name in ("BH_metarig",):
            obj.hide_render = True
    studio(HEIGHT * 0.5)
    render(os.path.abspath(args.out_dir), HEIGHT * 0.52)

    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.output), compress=True)
    print(f"saved: {args.output}")


if __name__ == "__main__":
    main()
