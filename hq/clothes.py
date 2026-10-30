"""服の作成（マスター人体の上で一度だけ行う素材づくり）。

体を切り抜いて厚みを付けるだけの服にしないため、次の手順で作る。

  1. 型紙: 体の該当範囲（骨のウェイトと高さで決める）を取り出し、細分化して布の解像度にする
  2. ゆとり: 体から一定の距離（部位ごとに指定）へ押し出しつつ、平滑化で筋肉・へそなどの凹凸を消す
  3. 布シミュレーション: 体を衝突物にし、肩・腰などを固定して重力で垂らし、しわとたるみを作る
  4. 仕上げ: 厚み（縁が見える）、襟・裾などの追加形状、布の手続き的マテリアル
  5. 隠す範囲: 服の下に完全に隠れる体の頂点（縁から離れた部分）を記録する

出来た服は MPFB の .mhclo 素材として書き出し、別の体型には MPFB が再フィットする。
"""

import math

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


# ---------------------------------------------------------------------------
# 体の目印
# ---------------------------------------------------------------------------

class BodyRef:
    """休止姿勢・体型適用後の体（補助形状なし）と、骨の位置。"""

    def __init__(self, body, rig):
        deps = bpy.context.evaluated_depsgraph_get()
        self.body = body
        self.rig = rig
        self.mesh = bpy.data.meshes.new_from_object(body.evaluated_get(deps), preserve_all_data_layers=True,
                                                    depsgraph=deps)
        self.mesh.name = "BH_bodyref"
        self.proxy = self._smoothed_copy(self.mesh)
        self.bvh = BVHTree.FromObject(body, deps)
        self.names = {g.index: g.name for g in body.vertex_groups}
        self.bones = {b.name: (rig.matrix_world @ b.head_local, rig.matrix_world @ b.tail_local)
                      for b in rig.data.bones}

    def dominant(self, v):
        best, name = 0.0, ""
        for g in v.groups:
            n = self.names.get(g.group, "")
            if n in self.bones and g.weight > best:
                best, name = g.weight, n
        return name

    def along(self, co, bone):
        """骨に沿った位置（根元 0 → 先端 1）。"""
        h, t = self.bones[bone]
        d = t - h
        return (co - h).dot(d) / d.length_squared

    def nearest(self, p):
        loc, normal, _, _ = self.bvh.find_nearest(p)
        return loc, normal

    @staticmethod
    def _smoothed_copy(mesh, iterations=30):
        """へそ・乳首・膝などの凹凸を消した体の下地。服の型紙はここから取る。

        普通の平滑化なので全体が少し縮むが、ゆとりは実際の体からの距離で決めるので問題ない。
        （体積を保つ平滑化は凸部を逆に膨らませるので使わない）"""
        bm = bmesh.new()
        bm.from_mesh(mesh)
        for _ in range(iterations):
            bmesh.ops.smooth_vert(bm, verts=list(bm.verts), factor=0.5, use_axis_x=True, use_axis_y=True,
                                  use_axis_z=True)
        smoothed = mesh.copy()
        smoothed.name = "BH_bodyref_smooth"
        bm.to_mesh(smoothed)
        bm.free()
        return smoothed

    def free(self):
        bpy.data.meshes.remove(self.mesh)
        bpy.data.meshes.remove(self.proxy)


# ---------------------------------------------------------------------------
# 服の定義（範囲・ゆとり・固定部・色）
# ---------------------------------------------------------------------------

def _side(name):
    return name.rsplit("_", 1)[-1] if name.endswith(("_l", "_r")) else ""


def region_tshirt(ref, co, bone):
    neck_h, _ = ref.bones["neck_01"]
    pelvis_h, _ = ref.bones["pelvis"]
    if bone == "head" or bone.startswith(("hand", "lowerarm", "calf")):
        return False
    if bone == "neck_01" and co.z > neck_h.z - 0.02:  # 首の付け根より下は含める（後ろ首が欠けないように）
        return False
    if bone.startswith("upperarm"):
        return ref.along(co, bone) < 0.62
    if co.z < pelvis_h.z - 0.09:
        return False
    if bone.startswith("thigh"):  # 腰の横（太ももの骨が支配的な部分）も裾の高さまでは含める
        return True
    # 首回り（クルーネック）
    horiz = Vector((co.x - neck_h.x, co.y - neck_h.y, 0)).length
    if co.z > neck_h.z - 0.045 and horiz < 0.085:
        return False
    return bone in ("pelvis", "spine_01", "spine_02", "spine_03") or bone.startswith("clavicle")


def region_dress_shirt(ref, co, bone):
    neck_h, _ = ref.bones["neck_01"]
    pelvis_h, _ = ref.bones["pelvis"]
    if bone == "head" or bone.startswith(("hand", "calf", "index", "middle", "ring", "pinky", "thumb")):
        return False
    if bone == "neck_01" and co.z > neck_h.z - 0.02:
        return False
    if bone.startswith("lowerarm"):
        return ref.along(co, bone) < 1.0
    if co.z < pelvis_h.z - 0.11:
        return False
    if bone.startswith("thigh"):
        return True
    horiz = Vector((co.x - neck_h.x, co.y - neck_h.y, 0)).length
    if co.z > neck_h.z - 0.02 and horiz < 0.072:
        return False
    return bone in ("pelvis", "spine_01", "spine_02", "spine_03") or bone.startswith(("clavicle", "upperarm"))


def region_trousers(ref, co, bone):
    pelvis_h, _ = ref.bones["pelvis"]
    foot_h, _ = ref.bones["foot_l"]
    if co.z > pelvis_h.z + 0.12 or co.z < foot_h.z - 0.01:
        return False
    return bone in ("pelvis", "spine_01") or bone.startswith(("thigh", "calf"))


def region_shoes(ref, co, bone):
    foot_h, _ = ref.bones["foot_l"]
    if bone.startswith(("foot", "ball")):
        return co.z < foot_h.z + 0.045
    return bone.startswith("calf") and co.z < foot_h.z + 0.045


def ease_tshirt(ref, co, bone):
    """胸と袖は、筋肉の多い体型でガードの姿勢を取っても肌が突き抜けないだけのゆとりを取る
    （小柄でがっしりした体型の検証で、胸・上腕の突き出しが 3.8% になったため広げた）。"""
    pelvis_h, _ = ref.bones["pelvis"]
    if bone.startswith("upperarm"):
        # 付け根は 0.022（0.030 にすると脇の下で布に穴が開く: 開口部の検査で検出）。
        # 袖口へ向かって広げ、腕を曲げて二の腕が盛り上がっても袖口が食い込まないようにする
        return 0.022 + 0.010 * max(0.0, min(1.0, ref.along(co, bone) / 0.48))
    belly = 1.0 - min(1.0, max(0.0, (co.z - pelvis_h.z) / 0.35))
    # 胸は 0.026: 小柄でがっしりした体型のキックで、ガードした腕に押された胸が 0.020 では 3.3% 突き出した
    return 0.026 + 0.008 * belly


def ease_dress_shirt(ref, co, bone):
    pelvis_h, _ = ref.bones["pelvis"]
    if bone.startswith(("upperarm", "lowerarm")):
        return 0.014 + 0.006 * (ref.along(co, bone) if bone.startswith("lowerarm") else 0.0)
    belly = 1.0 - min(1.0, max(0.0, (co.z - pelvis_h.z) / 0.35))
    return 0.009 + 0.016 * belly


def ease_jeans(ref, co, bone):
    if bone.startswith("calf"):
        return 0.022 + 0.008 * min(1.0, ref.along(co, bone))
    if bone.startswith("thigh"):
        return 0.016 + 0.008 * ref.along(co, bone)
    return 0.010


def ease_slacks(ref, co, bone):
    if bone.startswith("calf"):
        return 0.024 + 0.008 * min(1.0, ref.along(co, bone))
    if bone.startswith("thigh"):
        return 0.020 + 0.006 * ref.along(co, bone)
    return 0.011


# 布全体にかける弱い固定（ゆとりを付けた形へ引き戻す力）。重力で布がずり落ち、膝や足首で
# しわが重なって自己交差する（裏面が黒い裂け目のように見える）のを防ぐ。
BASE_PIN = 0.7


def _armpit(ref, co):
    """脇の下付近（腕と胴が近く、衝突が不安定になる場所）か。"""
    for side in ("l", "r"):
        h, _ = ref.bones[f"upperarm_{side}"]
        if (co - h).length < 0.11 and co.z < h.z:
            return True
    return False


def pin_shirt(ref, co, bone):
    """肩の上面と首回りを固定し、脇の下は半固定にする。"""
    neck_h, _ = ref.bones["neck_01"]
    if bone.startswith("clavicle") or (bone == "spine_03" and co.z > neck_h.z - 0.07):
        return 1.0
    if bone.startswith("upperarm") and ref.along(co, bone) < 0.12 and co.z > ref.bones[bone][0].z - 0.02:
        return 0.7
    if _armpit(ref, co):
        return 0.6
    return BASE_PIN


def pin_trousers(ref, co, bone):
    """腰回りを固定し、股の内側（左右の脚の布が近い）は半固定にする。"""
    pelvis_h, _ = ref.bones["pelvis"]
    if co.z > pelvis_h.z + 0.04:
        return 1.0
    if abs(co.x) < 0.07 and co.z > pelvis_h.z - 0.22:
        return 0.7
    return BASE_PIN


GARMENTS = {
    "tshirt": {"region": region_tshirt, "ease": ease_tshirt, "pin": pin_shirt, "sim_frames": 10,
               "color": (0.11, 0.12, 0.14), "fabric": "knit", "thickness": 0.0025, "roughness": 0.85},
    "jeans": {"region": region_trousers, "ease": ease_jeans, "pin": pin_trousers, "sim_frames": 10,
              "color": (0.055, 0.085, 0.17), "fabric": "denim", "thickness": 0.0035, "roughness": 0.8},
    "sneakers": {"region": region_shoes, "ease": lambda r, c, b: 0.0075, "pin": None, "sim_frames": 0,
                 "color": (0.78, 0.78, 0.76), "fabric": "leather", "thickness": 0.004, "roughness": 0.55,
                 "sole": {"color": (0.92, 0.92, 0.9), "height": 0.012}, "cover_reach": 0.08, "cover_enclosed": True},
    "dress_shirt": {"region": region_dress_shirt, "ease": ease_dress_shirt, "pin": pin_shirt, "sim_frames": 10,
                    "color": (0.62, 0.71, 0.84), "fabric": "cotton", "thickness": 0.002, "roughness": 0.7,
                    "collar": True},
    "slacks": {"region": region_trousers, "ease": ease_slacks, "pin": pin_trousers, "sim_frames": 10,
               "color": (0.07, 0.07, 0.08), "fabric": "wool", "thickness": 0.003, "roughness": 0.75},
    "dress_shoes": {"region": region_shoes, "ease": lambda r, c, b: 0.006, "pin": None, "sim_frames": 0,
                    "color": (0.07, 0.04, 0.025), "fabric": "leather", "thickness": 0.004, "roughness": 0.3,
                    "sole": {"color": (0.03, 0.025, 0.02), "height": 0.010}, "cover_reach": 0.08, "cover_enclosed": True},
}

OUTFITS = {
    "casual": ["tshirt", "jeans", "sneakers"],
    "business": ["dress_shirt", "slacks", "dress_shoes"],
}


# ---------------------------------------------------------------------------
# 型紙 → ゆとり
# ---------------------------------------------------------------------------

def extract_region(ref, region):
    """範囲を、凹凸を消した体の下地（ref.proxy）から取り出す。頂点の並びと重みは体と同じ。"""
    bm = bmesh.new()
    bm.from_mesh(ref.proxy)
    deform = bm.verts.layers.deform.verify()
    keep = set()
    names = ref.names
    for v in bm.verts:
        best, bone = 0.0, ""
        for gi, w in v[deform].items():
            n = names.get(gi, "")
            if n in ref.bones and w > best:
                best, bone = w, n
        if region(ref, v.co, bone):
            keep.add(v.index)
    # 面単位で判定（全頂点が範囲内の面だけ残す）
    drop_faces = [f for f in bm.faces if not all(v.index in keep for v in f.verts)]
    bmesh.ops.delete(bm, geom=drop_faces, context="FACES_ONLY")
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    loose_e = [e for e in bm.edges if not e.link_faces]
    bmesh.ops.delete(bm, geom=loose_e, context="EDGES")
    covered = set(keep)
    return bm, covered


def _bone_of(bm, deform, names, bones, v):
    best, bone = 0.0, ""
    for gi, w in v[deform].items():
        n = names.get(gi, "")
        if n in bones and w > best:
            best, bone = w, n
    return bone


def offset_and_settle(bm, ref, ease, settle=2, step=0.002, reach=0.10):
    """下地の各点から下地の法線方向へ少しずつ進め、実際の体から外側へ ease だけ離れた所で止める。

    へその上では縁の高さで止まるので布は凹みを平らに渡り、下地が体の内側にあっても外に出るまで進む。"""
    deform = bm.verts.layers.deform.verify()
    bm.normal_update()
    for v in bm.verts:
        want = ease(ref, v.co, _bone_of(bm, deform, ref.names, ref.bones, v))
        base, direction = v.co.copy(), v.normal.copy()
        t = 0.0
        while t < reach:
            p = base + direction * t
            q, n = ref.nearest(p)
            if (p - q).dot(n) >= want:
                break
            t += step
        v.co = base + direction * t
    boundary = {v for v in bm.verts if v.is_boundary}
    for _ in range(settle):
        bmesh.ops.smooth_vert(bm, verts=[v for v in bm.verts if v not in boundary], factor=0.5,
                              use_axis_x=True, use_axis_y=True, use_axis_z=True)
        for v in bm.verts:
            q, n = ref.nearest(v.co)
            if (v.co - q).dot(n) < 0.005:
                v.co = q + n * 0.005


def relax_and_ease(bm, ref, ease, iterations=14):
    """平滑化（凹凸を消す）と、体からの距離（ゆとり）への押し出しを交互に行う。

    体から近すぎる所だけを押し出し、離れた所は引き戻さない。引き戻すと、へそのような凹みに
    布が吸い込まれる。実際の布は凹みの上を渡る。"""
    deform = bm.verts.layers.deform.verify()
    targets = {v: ease(ref, v.co, _bone_of(bm, deform, ref.names, ref.bones, v)) for v in bm.verts}
    boundary = {v for v in bm.verts if v.is_boundary}
    for it in range(iterations):
        inner = [v for v in bm.verts if v not in boundary]
        bmesh.ops.smooth_vert(bm, verts=inner, factor=0.6, use_axis_x=True, use_axis_y=True, use_axis_z=True)
        bmesh.ops.smooth_vert(bm, verts=list(boundary), factor=0.3, use_axis_x=True, use_axis_y=True,
                              use_axis_z=False)
        for v in bm.verts:
            q, n = ref.nearest(v.co)
            dist = (v.co - q).dot(n)
            want = targets[v]
            if dist < want:
                v.co = q + n * want


def cut(bm, point, normal, faces=None):
    """平面で切り、normal 側を捨てる（裾・袖口などをまっすぐにする）。faces を渡すとその範囲だけ切る。"""
    faces = list(bm.faces) if faces is None else faces
    verts = {v for f in faces for v in f.verts}
    edges = {e for f in faces for e in f.edges}
    bmesh.ops.bisect_plane(bm, geom=list(verts) + list(edges) + faces, plane_co=point, plane_no=normal,
                           clear_outer=True)


def cut_sleeve(bm, ref, side, along):
    """袖口を腕の軸に垂直な平面で切る。斜めの平面が胴体まで切らないよう、腕の面だけを対象にする。"""
    h, t = ref.bones[f"upperarm_{side}"] if along <= 1.0 else ref.bones[f"lowerarm_{side}"]
    a = along if along <= 1.0 else along - 1.0
    axis = (t - h).normalized()
    deform = bm.verts.layers.deform.verify()
    arm = (f"upperarm_{side}", f"lowerarm_{side}", f"hand_{side}")
    faces = [f for f in bm.faces
             if all(_bone_of(bm, deform, ref.names, ref.bones, v) in arm for v in f.verts)]
    cut(bm, h + (t - h) * a, axis, faces)


def clean(bm, min_faces=40, merge=True):
    """切断で生じた重複頂点・縮退面、面のない辺・頂点、小さな断片を取り除く。

    merge=False: 頂点の結合をしない。布シミュレーション後は折れ重なった布の頂点同士が近づくため、
    結合すると布が裂ける。
    """
    if merge:
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=0.0025)
        bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=0.0015)
    bmesh.ops.delete(bm, geom=[e for e in bm.edges if not e.link_faces], context="EDGES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    seen, small = set(), []
    for f in bm.faces:
        if f in seen:
            continue
        stack, comp = [f], []
        seen.add(f)
        while stack:
            g = stack.pop()
            comp.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h not in seen:
                        seen.add(h)
                        stack.append(h)
        if len(comp) < min_faces:
            small.extend(comp)
    if small:
        bmesh.ops.delete(bm, geom=small, context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")


def mesh_islands(bm):
    """面のつながりごとの集合（大きい順）。"""
    seen, out = set(), []
    for f in bm.faces:
        if f in seen:
            continue
        stack, island = [f], set()
        seen.add(f)
        while stack:
            g = stack.pop()
            island.add(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h not in seen:
                        seen.add(h)
                        stack.append(h)
        out.append(island)
    return sorted(out, key=len, reverse=True)


def round_neckline(bm, ref, radius, depth_front=0.0, drop=0.03, rings=5):
    """首回りの縁を、首の軸を中心にした楕円と滑らかな高さの曲線へ寄せる（前は少し下げる）。

    縁の頂点だけを動かすと、隣の列との間に細い三角形が折れ重なって破片状に見える。
    縁の移動量を rings 列内側まで、離れるほど小さくして伝える。"""
    neck_h, _ = ref.bones["neck_01"]

    def target(co):
        d = Vector((co.x - neck_h.x, co.y - neck_h.y, 0))
        front = max(0.0, -d.normalized().y)
        t = d.normalized() * radius * (1.0 + 0.15 * front)
        return Vector((neck_h.x + t.x, neck_h.y + t.y, neck_h.z - drop - depth_front * front))

    # 布シミュレーションで首の近くへ寄った布（線より上に立ち上がった耳状の部分）は切り落とす
    above = set()
    for v in bm.verts:
        d = Vector((v.co.x - neck_h.x, v.co.y - neck_h.y, 0))
        if v.co.z > neck_h.z - 0.12 and d.length < 0.16:
            t = target(v.co)
            if v.co.z > t.z + 0.002 and d.length < (t - neck_h).to_2d().length + 0.015:
                above.add(v)
    if above:
        bmesh.ops.delete(bm, geom=list({f for v in above for f in v.link_faces}), context="FACES")
        loose = [v for v in bm.verts if not v.link_faces]
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
        islands = mesh_islands(bm)
        for island in islands[1:]:
            if len(island) < 50:
                bmesh.ops.delete(bm, geom=list(island), context="FACES")
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    delta = {}
    for v in bm.verts:
        if not v.is_boundary:
            continue
        d = Vector((v.co.x - neck_h.x, v.co.y - neck_h.y, 0))
        if v.co.z < neck_h.z - 0.12 or d.length > 0.16:
            continue
        delta[v] = target(v.co) - v.co
    frontier = list(delta)
    for ring in range(1, rings + 1):
        weight = 1.0 - ring / (rings + 1)
        nxt = {}
        for v in frontier:
            for e in v.link_edges:
                o = e.other_vert(v)
                if o not in delta and o not in nxt:
                    nxt[o] = delta[v] * weight / (1.0 - (ring - 1) / (rings + 1))
        delta.update(nxt)
        frontier = list(nxt)
    for v, dv in delta.items():
        v.co += dv


TRIMS = {
    # 服ごとの切り落とし: [("hem", 高さ基準の骨, オフセット)] など
    "tshirt": {"hem": ("pelvis", -0.07), "sleeve": 0.48, "neck": 0.078},
    "dress_shirt": {"hem": ("pelvis", -0.10), "sleeve": 1.93, "neck": 0.068, "neck_drop": 0.01,
                    "neck_front": 0.0},
    "jeans": {"waist": ("pelvis", 0.085), "ankle": 0.035},
    "slacks": {"waist": ("pelvis", 0.09), "ankle": 0.03},
}


def smooth_boundary(bm, ref, iterations=4):
    """首回りの縁を、縁に沿ってならす（高さと半径のギザギザを消す）。"""
    neck_h, _ = ref.bones["neck_01"]
    ring = [v for v in bm.verts if v.is_boundary and v.co.z > neck_h.z - 0.15
            and Vector((v.co.x - neck_h.x, v.co.y - neck_h.y, 0)).length < 0.17]
    ring_set = set(ring)
    for _ in range(iterations):
        new = {}
        for v in ring:
            nb = [e.other_vert(v) for e in v.link_edges if e.is_boundary and e.other_vert(v) in ring_set]
            if len(nb) == 2:
                new[v] = (v.co * 2 + nb[0].co + nb[1].co) / 4
        for v, co in new.items():
            v.co = co


def apply_trims(bm, ref, name, merge=True, sleeves=True):
    trim = TRIMS.get(name)
    if not trim:
        return
    if "hem" in trim:
        bone, off = trim["hem"]
        h, _ = ref.bones[bone]
        cut(bm, Vector((0, 0, h.z + off)), Vector((0, 0, -1)))
    if "sleeve" in trim and sleeves:
        for side in ("l", "r"):
            cut_sleeve(bm, ref, side, trim["sleeve"])
    if "waist" in trim:
        bone, off = trim["waist"]
        h, _ = ref.bones[bone]
        cut(bm, Vector((0, 0, h.z + off)), Vector((0, 0, 1)))
    if "ankle" in trim:
        foot_h, _ = ref.bones["foot_l"]
        cut(bm, Vector((0, 0, foot_h.z + trim["ankle"])), Vector((0, 0, -1)))
    if "neck" in trim:
        round_neckline(bm, ref, trim["neck"], depth_front=trim.get("neck_front", 0.02),
                       drop=trim.get("neck_drop", 0.03))
        smooth_boundary(bm, ref)
    clean(bm, merge=merge)


# ---------------------------------------------------------------------------
# 布シミュレーション
# ---------------------------------------------------------------------------

def simulate(obj, ref, pin_fn, frames, body):
    if frames <= 0:
        return 0
    mesh = obj.data
    pin = obj.vertex_groups.new(name="BH_pin")
    deform_names = {g.index: g.name for g in obj.vertex_groups}
    for v in mesh.vertices:
        best, bone = 0.0, ""
        for g in v.groups:
            n = deform_names.get(g.group, "")
            if n in ref.bones and g.weight > best:
                best, bone = g.weight, n
        w = pin_fn(ref, v.co, bone)
        if w > 0:
            pin.add([v.index], w, "REPLACE")

    coll = body.modifiers.new("BH_collision", "COLLISION")
    body.collision.thickness_outer = 0.004
    body.collision.thickness_inner = 0.02
    body.collision.cloth_friction = 8.0

    before = [v.co.copy() for v in mesh.vertices]
    cloth = obj.modifiers.new("BH_cloth", "CLOTH")
    s = cloth.settings
    s.quality = 12
    s.mass = 0.2
    s.tension_stiffness = 10
    s.compression_stiffness = 10
    s.shear_stiffness = 5
    s.bending_stiffness = 0.4
    s.air_damping = 4.0
    s.vertex_group_mass = pin.name
    s.pin_stiffness = 1.0
    cs = cloth.collision_settings
    cs.distance_min = 0.005
    cs.use_self_collision = True
    cs.self_distance_min = 0.003
    cloth.point_cache.frame_start = 1
    cloth.point_cache.frame_end = frames
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, frames
    for f in range(1, frames + 1):
        sc.frame_set(f)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=cloth.name)
    body.modifiers.remove(coll)
    sc.frame_set(1)
    return guard_explosions(obj, before)


def guard_explosions(obj, before, limit=0.04, smooth_passes=4):
    """シミュレーションで limit [m] 以上動いた頂点（破綻）を元へ戻し、周囲をならす。戻した数を返す。"""
    mesh = obj.data
    bad = [i for i, v in enumerate(mesh.vertices) if (v.co - before[i]).length > limit]
    for i in bad:
        mesh.vertices[i].co = before[i]
    if bad:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        region = {bm.verts[i] for i in bad}
        for _ in range(2):
            region |= {e.other_vert(v) for v in list(region) for e in v.link_edges}
        for _ in range(smooth_passes):
            bmesh.ops.smooth_vert(bm, verts=list(region), factor=0.5, use_axis_x=True, use_axis_y=True,
                                  use_axis_z=True)
        bm.to_mesh(mesh)
        bm.free()
    return len(bad)


# ---------------------------------------------------------------------------
# 仕上げ
# ---------------------------------------------------------------------------

def add_collar(bm, ref, height=0.032, fold=0.042):
    """首回りの縁から、立ち襟と折り返しを作る。"""
    neck_h, _ = ref.bones["neck_01"]
    edges = [e for e in bm.edges if e.is_boundary and all(v.co.z > neck_h.z - 0.08 for v in e.verts)
             and all(Vector((v.co.x - neck_h.x, v.co.y - neck_h.y, 0)).length < 0.13 for v in e.verts)]
    if not edges:
        return
    ret = bmesh.ops.extrude_edge_only(bm, edges=edges)
    up = [v for v in ret["geom"] if isinstance(v, bmesh.types.BMVert)]
    for v in up:
        radial = Vector((v.co.x - neck_h.x, v.co.y - neck_h.y, 0)).normalized()
        v.co += Vector((0, 0, height)) - radial * 0.004
    # 2 段目は 1 段目の上端の辺だけを押し出す（縦の辺まで押し出すと破片状になる）
    up_set = set(up)
    top_edges = [e for e in ret["geom"] if isinstance(e, bmesh.types.BMEdge)
                 and all(v in up_set for v in e.verts)]
    ret2 = bmesh.ops.extrude_edge_only(bm, edges=top_edges)
    down = [v for v in ret2["geom"] if isinstance(v, bmesh.types.BMVert)]
    for v in down:
        radial = Vector((v.co.x - neck_h.x, v.co.y - neck_h.y, 0)).normalized()
        v.co += radial * 0.022 + Vector((0, 0, -fold * 0.75))


def dominant_bone(ref, v):
    bone, best = "", 0.0
    for g in v.groups:
        n = ref.names.get(g.group, "")
        if n in ref.bones and g.weight > best:
            best, bone = g.weight, n
    return bone


def shoe_source_verts(ref, side):
    """靴の殻の元にする足の頂点（足・指の付け根の骨に属するか、足首より少し上までのふくらはぎ）。"""
    foot_h, _ = ref.bones[f"foot_{side}"]
    out = []
    for v in ref.mesh.vertices:
        bone = dominant_bone(ref, v)
        if bone in (f"foot_{side}", f"ball_{side}") or (bone == f"calf_{side}" and v.co.z < foot_h.z + 0.05):
            out.append(v)
    return out


def shoe_enclosed_verts(ref, margin=0.012):
    """靴の殻に必ず包まれる体の頂点。殻は shoe_source_verts を外へ押し出した凸包なので、
    足首の開口（foot.z + 0.03）より margin 下までの元頂点はすべて内側にある。"""
    out = set()
    for side in ("l", "r"):
        top = ref.bones[f"foot_{side}"][0].z + 0.03 - margin
        out.update(v.index for v in shoe_source_verts(ref, side) if v.co.z < top)
    return out


def shoe_mesh(ref, side, spec):
    """片足の靴: 足の凸包（指の凹凸がない外形）→ ボクセルで作り直し → 平滑化 → 足首の開口 → 靴底。"""
    from mathutils.kdtree import KDTree  # noqa: F401  （重みの転写は copy_weights で行う）
    foot_h, _ = ref.bones[f"foot_{side}"]
    bm = bmesh.new()
    for v in shoe_source_verts(ref, side):
        bm.verts.new(v.co + v.normal * 0.007)
    hull = bmesh.ops.convex_hull(bm, input=list(bm.verts))
    inner = {g for g in hull["geom_interior"] + hull["geom_unused"] if isinstance(g, bmesh.types.BMVert)}
    bmesh.ops.delete(bm, geom=list(inner), context="VERTS")
    mesh = bpy.data.meshes.new("BH_shoe_tmp")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("BH_shoe_tmp", mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    rem = obj.modifiers.new("remesh", "REMESH")
    rem.mode = "VOXEL"
    rem.voxel_size = 0.006
    sm = obj.modifiers.new("smooth", "SMOOTH")
    sm.factor = 0.8
    sm.iterations = 12
    bpy.ops.object.modifier_apply(modifier=rem.name)
    bpy.ops.object.modifier_apply(modifier=sm.name)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)
    # 足首の開口（上を切り落とす）
    cut(bm, Vector((0, 0, foot_h.z + 0.03)), Vector((0, 0, 1)))
    # 足裏を平らにして靴底を押し出す
    for v in bm.verts:
        if v.co.z < 0.006:
            v.co.z = 0.006
    bm.normal_update()
    bottom = [f for f in bm.faces if f.normal.z < -0.7 and all(v.co.z < 0.0065 for v in f.verts)]
    if bottom:
        ret = bmesh.ops.extrude_face_region(bm, geom=bottom)
        for v in [g for g in ret["geom"] if isinstance(g, bmesh.types.BMVert)]:
            v.co.z -= spec["sole"]["height"] + 0.002
        bmesh.ops.delete(bm, geom=bottom, context="FACES_ONLY")
        for f in bm.faces:
            if all(v.co.z < 0.0065 for v in f.verts):
                f.material_index = 1
    bm.normal_update()
    return bm


def copy_weights(obj, ref):
    """各頂点に、いちばん近い体の頂点の骨のウェイトを写す。"""
    from mathutils.kdtree import KDTree
    src = ref.mesh
    tree = KDTree(len(src.vertices))
    for v in src.vertices:
        tree.insert(v.co, v.index)
    tree.balance()
    groups = {}
    for v in obj.data.vertices:
        _, idx, _ = tree.find(v.co)
        for g in src.vertices[idx].groups:
            name = ref.names.get(g.group, "")
            if name in ref.bones and g.weight > 0:
                if name not in groups:
                    groups[name] = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
                groups[name].add([v.index], g.weight, "REPLACE")


def fabric_material(name, spec):
    """布の手続き的マテリアル（色ムラ＋織り目の凹凸）。焼き込み前提。"""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    color = Vector(spec["color"])
    tex = nt.nodes.new("ShaderNodeTexCoord")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 60.0
    noise.inputs["Detail"].default_value = 6.0
    nt.links.new(tex.outputs["Object"], noise.inputs["Vector"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*(color * 0.82), 1.0)
    ramp.color_ramp.elements[1].color = (*(color * 1.12), 1.0)
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    weave = nt.nodes.new("ShaderNodeTexWave" if spec["fabric"] in ("denim", "wool") else "ShaderNodeTexNoise")
    if weave.bl_idname == "ShaderNodeTexWave":
        weave.inputs["Scale"].default_value = 900.0
        weave.bands_direction = "DIAGONAL"
        weave.inputs["Distortion"].default_value = 2.0
    else:
        weave.inputs["Scale"].default_value = 1400.0 if spec["fabric"] != "leather" else 300.0
    nt.links.new(tex.outputs["Object"], weave.inputs["Vector"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.25 if spec["fabric"] != "leather" else 0.08
    bump.inputs["Distance"].default_value = 0.0004
    nt.links.new(weave.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = spec["roughness"]
    if spec["fabric"] in ("knit", "cotton", "denim", "wool"):
        bsdf.inputs["Sheen Weight"].default_value = 0.3
    mat.diffuse_color = (*color, 1.0)
    return mat


def boundary_loops(mesh):
    """開口部（境界ループ）の数。穴が開くと増える。"""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    edges = {e for e in bm.edges if e.is_boundary}
    seen, count = set(), 0
    for e in edges:
        if e in seen:
            continue
        count += 1
        stack = [e]
        seen.add(e)
        while stack:
            x = stack.pop()
            for v in x.verts:
                for y in v.link_edges:
                    if y in edges and y not in seen:
                        seen.add(y)
                        stack.append(y)
    bm.free()
    return count


# 服ごとの開口部の期待数（首・裾・袖口・ウエスト・足首。靴は左右の足首の開口）
EXPECTED_OPENINGS = {"tshirt": 4, "dress_shirt": 4, "jeans": 3, "slacks": 3, "sneakers": 2, "dress_shoes": 2}


def make_garment(ref, body, rig, name):
    spec = GARMENTS[name]
    if spec.get("sole"):
        bm = shoe_mesh(ref, "l", spec)
        right = shoe_mesh(ref, "r", spec)
        tmp = bpy.data.meshes.new("tmp")
        right.to_mesh(tmp)
        right.free()
        bm.from_mesh(tmp)
        bpy.data.meshes.remove(tmp)
        covered = {v.index for v in ref.mesh.vertices if spec["region"](ref, v.co, ref.dominant(v))}
    else:
        bm, covered = extract_region(ref, spec["region"])
        bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=1, use_grid_fill=True, smooth=0.0)
        apply_trims(bm, ref, name)
        offset_and_settle(bm, ref, spec["ease"])
    mesh = bpy.data.meshes.new(f"BH_{name}")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(f"BH_{name}", mesh)
    bpy.context.collection.objects.link(obj)
    if spec.get("sole"):
        copy_weights(obj, ref)
        obj["bh_boundary_loops"] = boundary_loops(obj.data)
    else:
        for g in body.vertex_groups:  # 体のグループ名をそのまま使う（変形の重みも引き継がれる）
            obj.vertex_groups.new(name=g.name)
    obj["bh_sim_reverted_verts"] = simulate(obj, ref, spec["pin"], spec["sim_frames"], body) if spec["pin"] else 0
    if spec["pin"]:
        # シミュレーション後に裾・首回り・ウエスト・足首をもう一度整える
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        # 袖口はシミュレーション前に切ってある。垂れた袖を平面で切り直すと、しわが切断面と交差して穴が開く
        apply_trims(bm, ref, name, merge=False, sleeves=False)
        if spec.get("collar"):
            # 襟は布シミュレーションの後、整えた首の縁から作る（先に作ると布の動きで崩れる）
            add_collar(bm, ref)
        bm.to_mesh(obj.data)
        bm.free()
    obj["bh_boundary_loops"] = boundary_loops(obj.data)
    bpy.context.view_layer.objects.active = obj
    solid = obj.modifiers.new("BH_thickness", "SOLIDIFY")
    solid.thickness = spec["thickness"]
    solid.offset = 1.0
    # 均一な厚み（even offset）は細い面や鋭い角で極端に伸びてトゲになるので使わない
    solid.use_even_offset = False
    solid.use_rim = True
    bpy.ops.object.modifier_apply(modifier=solid.name)
    for p in obj.data.polygons:
        p.use_smooth = True
    obj.data.materials.append(fabric_material(f"BH_{name}_fabric", spec))
    if spec.get("sole"):
        sole_spec = dict(spec, color=spec["sole"]["color"], fabric="rubber", roughness=0.7)
        obj.data.materials.append(fabric_material(f"BH_{name}_sole", sole_spec))
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    obj.parent = rig
    return obj, covered
