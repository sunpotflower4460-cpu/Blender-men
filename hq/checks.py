"""組み立て済みキャラクター（.blend）の静的検査。骨格・ウェイト・髪・顔・服・マテリアルの不整合を調べる。

結果は JSON（--report）。failures が空なら合格（終了コード 0）。

実行方法:
  python hq/checks.py --blend out/char.blend --report out/checks.json
"""

import argparse
import json
import os
import sys

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

EXPECTED_BONES = 53
HAIR_BONES = {"head", "neck_01", "spine_03"}


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--report", required=True)
    return p.parse_args(argv)


def evaluated(obj):
    deps = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(deps)
    mesh = ev.to_mesh()
    pts = [obj.matrix_world @ v.co for v in mesh.vertices]
    ev.to_mesh_clear()
    return pts


def weight_stats(obj, bones):
    names = {g.index: g.name for g in obj.vertex_groups}
    unweighted, over4, bad_sum, used = 0, 0, 0, set()
    for v in obj.data.vertices:
        ws = [(names[g.group], g.weight) for g in v.groups if names.get(g.group) in bones and g.weight > 1e-4]
        if not ws:
            unweighted += 1
            continue
        if len(ws) > 4:
            over4 += 1
        if abs(sum(w for _, w in ws) - 1.0) > 0.02:
            bad_sum += 1
        used.update(n for n, _ in ws)
    return {"vertices": len(obj.data.vertices), "unweighted": unweighted, "over_4": over4,
            "bad_sum": bad_sum, "bones_used": sorted(used)}


def material_is_exportable(obj):
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes:
            return False
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            return False
        link = bsdf.inputs["Base Color"].links
        if link and link[0].from_node.type != "TEX_IMAGE":
            return False
    return True


ENCLOSE_DIRS = [Vector(d) for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, -1))]


def enclosed(tree, p, reach=0.3):
    """p が服の殻（靴など）に囲まれているか: 横 4 方向と下へ飛ばした光線がすべて同じ服に当たる。
    上には飛ばさない（靴は足首が開いている）。シャツやズボンは裾が開いているので下向きの光線が抜ける。"""
    return all(tree.ray_cast(p, d, reach)[0] is not None for d in ENCLOSE_DIRS)


def exposed_hidden_skin(body, clothes, reach=0.06, samples=None):
    """Delete マスクで隠した体の頂点のうち、法線方向 reach 以内に同じ向きの服の面がなく、
    服の殻にも囲まれていないものを数える。"""
    masks = [m for m in body.modifiers if m.type == "MASK" and m.name.startswith("Delete")]
    if not masks or not clothes:
        return 0, 0
    hidden = set()
    for m in masks:
        g = body.vertex_groups.get(m.vertex_group)
        if g is None:
            continue
        for v in body.data.vertices:
            if any(x.group == g.index and x.weight > 0 for x in v.groups):
                hidden.add(v.index)
    for m in masks:
        m.show_viewport = False
    deps = bpy.context.evaluated_depsgraph_get()
    ev = body.evaluated_get(deps)
    mesh = ev.to_mesh()
    # 補助形状を隠す MASK は残っているので、評価後の頂点番号 → 元の番号の対応を作る
    gi = body.vertex_groups["body"].index
    original = [v.index for v in body.data.vertices if any(x.group == gi and x.weight > 0 for x in v.groups)]
    trees = [BVHTree.FromObject(o, deps) for o in clothes]
    exposed = 0
    count = 0
    for i, v in enumerate(mesh.vertices):
        if original[i] not in hidden:
            continue
        count += 1
        p, n = body.matrix_world @ v.co, (body.matrix_world.to_3x3() @ v.normal).normalized()
        covered = False
        for t in trees:
            loc, normal, _, _ = t.ray_cast(p + n * 0.0005, n, reach)
            if loc is not None and abs(normal.dot(n)) > 0.3:
                covered = True
                break
        if not covered:
            covered = any(enclosed(t, p) for t in trees)
        if not covered:
            exposed += 1
            if samples is not None and len(samples) < 20:
                samples.append([round(c, 3) for c in p])
    ev.to_mesh_clear()
    for m in masks:
        m.show_viewport = True
    return exposed, count


def main():
    args = parse_args()
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
    failures, report = [], {"blend": args.blend}
    rig = bpy.data.objects.get("BH_rig")
    body = bpy.data.objects.get("BH_body")
    if rig is None or body is None:
        failures.append("骨格: BH_rig または BH_body がない")
        report["failures"] = failures
        json.dump(report, open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        sys.exit(1)
    config = json.loads(body.get("bh_config", "{}"))

    # 骨格
    bones = {b.name for b in rig.data.bones if b.use_deform}
    report["skeleton"] = {"bones": len(rig.data.bones), "deform": len(bones)}
    if len(rig.data.bones) != EXPECTED_BONES or len(bones) != EXPECTED_BONES:
        failures.append(f"骨格: 骨の数 {len(rig.data.bones)}（期待 {EXPECTED_BONES}）")
    body_pts = evaluated(body)
    height = max(p.z for p in body_pts) - min(p.z for p in body_pts)
    report["skeleton"]["height_m"] = round(height, 3)
    if not 1.40 < height < 2.15:
        failures.append(f"骨格: 身長 {height:.2f} m が範囲外")

    # 骨格で変形するメッシュとウェイト
    meshes = [o for o in bpy.data.objects if o.type == "MESH"
              and any(m.type == "ARMATURE" and m.object == rig for m in o.modifiers)]
    report["meshes"] = {}
    for o in meshes:
        st = weight_stats(o, bones)
        st["exportable_material"] = material_is_exportable(o)
        report["meshes"][o.name] = st
        # 体の補助形状（MASK で隠す頂点）は骨のウェイトがなくてよいので、見える頂点だけで判定する
        limit = 0 if o is not body else len(o.data.vertices) - len(body_pts)
        if st["unweighted"] > limit:
            failures.append(f"ウェイト: {o.name} に骨のウェイトがない頂点 {st['unweighted']}")
        if st["over_4"]:
            failures.append(f"ウェイト: {o.name} に 5 本以上の骨の影響を受ける頂点 {st['over_4']}")
        if st["bad_sum"]:
            failures.append(f"ウェイト: {o.name} にウェイトの合計が 1 でない頂点 {st['bad_sum']}")
        if not st["exportable_material"]:
            failures.append(f"マテリアル: {o.name} が画像テクスチャでない（glTF で見た目が変わる）")

    deps = bpy.context.evaluated_depsgraph_get()
    full_bvh = None
    masks = [m for m in body.modifiers if m.type == "MASK" and m.name.startswith("Delete")]
    for m in masks:
        m.show_viewport = False
    full_bvh = BVHTree.FromObject(body, bpy.context.evaluated_depsgraph_get())
    for m in masks:
        m.show_viewport = True

    # 髪
    hair = [o for o in meshes if o.name.startswith("BH_hair")]
    if config.get("hair") and len(hair) != 1:
        failures.append(f"髪: 髪のメッシュが {len(hair)} 個（期待 1）")
    for o in hair:
        st = report["meshes"][o.name]
        extra = set(st["bones_used"]) - HAIR_BONES
        if extra:
            failures.append(f"髪: 頭・首・胸以外の骨に結ばれている {sorted(extra)}")
        pts = evaluated(o)
        inside = sum(1 for p in pts if (lambda r: r[0] is not None and (p - r[0]).dot(r[1]) < -0.003)
                     (full_bvh.find_nearest(p)[:2]))
        st["inside_head_ratio"] = round(inside / max(1, len(pts)), 4)
        if st["inside_head_ratio"] > 0.02:
            failures.append(f"髪: 頭の中に埋まった頂点の割合 {st['inside_head_ratio']}")

    # 顔（目）
    eyes = bpy.data.objects.get("BH_eyes")
    if eyes is None:
        failures.append("顔: 目がない")
    else:
        pts = evaluated(eyes)
        left = [p for p in pts if p.x > 0]
        right = [p for p in pts if p.x < 0]
        cl = sum(left, left[0] * 0) / len(left)
        cr = sum(right, right[0] * 0) / len(right)
        head = rig.matrix_world @ rig.data.bones["head"].head_local
        report["eyes"] = {"symmetry_m": round(abs(cl.x + cr.x) + abs(cl.y - cr.y) + abs(cl.z - cr.z), 4),
                          "distance_to_head_bone_m": round(((cl + cr) / 2 - head).length, 3)}
        if report["eyes"]["symmetry_m"] > 0.004:
            failures.append(f"顔: 目の左右がずれている {report['eyes']['symmetry_m']} m")
        if report["eyes"]["distance_to_head_bone_m"] > 0.16:
            failures.append("顔: 目が頭の骨から離れすぎている")
    if not bpy.data.objects.get("BH_eyelashes"):
        failures.append("顔: まつげがない")

    # 服
    clothes = [o for o in meshes if o.name.startswith("BH_clothes")]
    report["clothes"] = {}
    for o in clothes:
        name = o.name.replace("BH_clothes_", "")
        has_mask = any(m.type == "MASK" and name in m.name for m in body.modifiers)
        pts = evaluated(o)
        bvh = BVHTree.FromObject(body, deps)
        poke = 0
        for p in pts:
            loc, normal, _, _ = bvh.find_nearest(p, 0.015)
            if loc is not None and (p - loc).dot(normal) < -0.002:
                poke += 1
        ratio = round(poke / max(1, len(pts)), 4)
        report["clothes"][name] = {"hidden_body_mask": has_mask, "skin_pokes_through_ratio": ratio}
        if not has_mask:
            failures.append(f"服: {name} の下の体を隠す設定がない")
        if ratio > 0.03:
            failures.append(f"服: {name} の上に肌が突き出す割合 {ratio}")

    # 隠した肌の露出: 服の下として隠した体の頂点の真上（法線方向）に服があるか。
    # 服がない所を隠すと、体の内側（黒い穴）が見える。
    samples = []
    exposed, hidden = exposed_hidden_skin(body, clothes, samples=samples)
    report["hidden_skin"] = {"hidden_vertices": hidden, "exposed": exposed,
                             "exposed_ratio": round(exposed / max(1, hidden), 4), "exposed_samples": samples}
    if hidden and exposed / hidden > 0.005:
        failures.append(f"服: 隠した肌のうち真上に服がない頂点 {exposed}/{hidden}（体に穴が見える）")

    report["failures"] = failures
    report["passed"] = not failures
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"passed": report["passed"], "failures": failures}, ensure_ascii=False))
    sys.exit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
