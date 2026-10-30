"""キャラクターの .blend を、決まった照明・カメラで描画する（正面・斜め・側面・背面・顔）。

描画時だけ体に細分化（Subdivision 1）を足す。ゲーム用メッシュは変更しない。

実行方法:
  python hq/render_views.py --blend out/master.blend --out-dir previews/hq/master [--samples 64] [--views front,face]
"""

import argparse
import json
import os
import sys

import bpy
from mathutils import Vector

VIEWS = {
    # 名前: (カメラ方向（体の中心から見た向き）, 注視点の高さ比, 距離比, 焦点距離, 解像度)
    "front": ((0.0, -1.0, 0.05), 0.5, 3.0, 70, (700, 1000)),
    "three_quarter": ((0.72, -0.72, 0.08), 0.5, 3.0, 70, (700, 1000)),
    "side": ((1.0, 0.0, 0.05), 0.5, 3.0, 70, (700, 1000)),
    "back": ((0.0, 1.0, 0.05), 0.5, 3.0, 70, (700, 1000)),
    "face": ((0.32, -0.95, 0.05), 0.925, 0.45, 85, (800, 800)),
}


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--samples", type=int, default=64)
    p.add_argument("--views", default=",".join(VIEWS))
    p.add_argument("--prefix", default="")
    return p.parse_args(argv)


def setup_scene(samples):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.cycles.transparent_max_bounces = 96  # 半透明の髪カードが何層も重なるため
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Medium High Contrast"
    world = bpy.data.worlds.new("BH_render_world")
    sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.30, 0.31, 0.33, 1.0)
    bg.inputs["Strength"].default_value = 0.35
    return sc


def add_lights(center, height):
    # キー（右前上）・フィル（左前）・リム（後ろ上）の 3 灯
    for name, offset, energy, size in (
        ("key", (1.6, -2.4, 1.3), 90, 1.6),
        ("fill", (-2.2, -1.6, 0.6), 30, 2.0),
        ("rim", (0.6, 2.6, 1.5), 75, 1.4),
    ):
        loc = center + Vector(offset) * (height / 1.75)
        data = bpy.data.lights.new(f"BH_{name}", "AREA")
        data.energy = energy
        data.size = size
        obj = bpy.data.objects.new(f"BH_{name}", data)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = loc
        obj.rotation_euler = (center - loc).to_track_quat("-Z", "Y").to_euler()


def character_bounds():
    body = bpy.data.objects["BH_body"]
    deps = bpy.context.evaluated_depsgraph_get()
    ev = body.evaluated_get(deps)
    mesh = ev.to_mesh()
    pts = [body.matrix_world @ v.co for v in mesh.vertices]
    ev.to_mesh_clear()
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def main():
    args = parse_args()
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
    sc = setup_scene(args.samples)
    body = bpy.data.objects["BH_body"]
    if not any(m.type == "SUBSURF" for m in body.modifiers):
        sub = body.modifiers.new("BH_render_subsurf", "SUBSURF")
        sub.levels = 1
        sub.render_levels = 1
    lo, hi = character_bounds()
    height = hi.z - lo.z
    center = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z + height * 0.5))
    add_lights(center, height)

    cam_data = bpy.data.cameras.new("BH_render_cam")
    cam = bpy.data.objects.new("BH_render_cam", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    os.makedirs(args.out_dir, exist_ok=True)
    written = []
    for name in args.views.split(","):
        direction, h_ratio, dist_ratio, lens, (rx, ry) = VIEWS[name]
        target = Vector((center.x, center.y, lo.z + height * h_ratio))
        cam.location = target + Vector(direction).normalized() * height * dist_ratio
        cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
        cam_data.lens = lens
        sc.render.resolution_x, sc.render.resolution_y = rx, ry
        path = os.path.join(os.path.abspath(args.out_dir), f"{args.prefix}{name}.png")
        sc.render.filepath = path
        bpy.ops.render.render(write_still=True)
        written.append(path)
    print(json.dumps({"rendered": written}))


if __name__ == "__main__":
    main()
