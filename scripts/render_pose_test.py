"""生成した .blend のリグで関節を曲げ、マネキンが追従するか確認する画像を描画する。

.blend 自体は保存しない（ポーズはこの確認のためだけに付ける）。

実行方法:
  blender -b models/base_human.blend -P scripts/render_pose_test.py -- --out previews/pose_test.png
  python scripts/render_pose_test.py --blend models/base_human.blend --out previews/pose_test.png
"""

import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Euler, Vector


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", help="開く .blend（blender -b で開いている場合は不要）")
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def rotate(rig, bone, x=0.0, y=0.0, z=0.0):
    pb = rig.pose.bones[bone]
    pb.rotation_mode = "XYZ"
    pb.rotation_euler = Euler((math.radians(x), math.radians(y), math.radians(z)))


def main():
    args = parse_args()
    if args.blend:
        bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
    rig = bpy.data.objects["BH_rig"]
    body = bpy.data.objects["BH_body"]

    # 首・背骨
    rotate(rig, "head", z=20)
    rotate(rig, "neck", x=10)
    rotate(rig, "chest", y=-8)
    # 左腕は FK に切り替えて肩・肘・手首を回す（0 = IK, 1 = FK）
    rig.pose.bones["upper_arm_parent.L"]["IK_FK"] = 1.0
    rotate(rig, "upper_arm_fk.L", x=60, z=-30)
    rotate(rig, "forearm_fk.L", x=80)
    rotate(rig, "hand_fk.L", x=-20)
    # 左手の指を握る
    for f in ("f_index", "f_middle", "f_ring", "f_pinky"):
        for i in ("01", "02", "03"):
            rotate(rig, f"{f}.{i}.L", x=60)
    # 右腕は IK のまま、手のコントローラを動かす
    rig.pose.bones["hand_ik.R"].location = Vector((0.0, 0.25, 0.30))
    # 右脚は IK：足を前に上げて膝を曲げる
    rig.pose.bones["foot_ik.R"].location = Vector((0.0, 0.25, 0.30))

    bpy.context.view_layer.update()
    # マネキンが実際に変形したかを数値でも確認する
    rest = [v.co.copy() for v in body.data.vertices]
    evaluated = body.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
    moved = sum((v.co - r).length > 1e-4 for v, r in zip(evaluated.vertices, rest))
    print(f"moved vertices: {moved} / {len(rest)}")

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.render.resolution_x = 1200
    scene.render.resolution_y = 900
    h = json.loads(rig["base_human_params"])["height"]
    cam_data = bpy.data.cameras.new("PoseCam")
    cam_data.lens = 50
    cam = bpy.data.objects.new("PoseCam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.location = (h * 1.1, -h * 1.8, h * 0.7)
    direction = Vector((0, 0, h * 0.5)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    scene.render.filepath = os.path.abspath(args.out)
    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
