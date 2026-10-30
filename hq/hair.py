"""髪（ヘアカード）の生成。

頭の目印（目・眉・頭頂・耳）から生え際を決め、頭皮に根元を配置し、髪型ごとの流れ・重力・
頭との衝突を考えながら毛束の芯線を伸ばし、半透明の毛束テクスチャを貼った帯（カード）にする。

  * 根元の配置も毛束の流れも頭の目印からの相対位置で決まるので、体型が変わっても同じ規則で生える
  * テクスチャは髪色そのもので描く（glTF でも同じ見た目になる）
  * 頭皮には髪色の下地をインクレイヤーとして塗り、地肌の透けを抑える
  * ウェイトは頭（長い髪は高さに応じて首・胸にも）へ割り当てる
"""

import math
import random

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from PIL import Image, ImageDraw

# 髪型の定義。長さ [m] は頭頂付近の値で、側頭部・後頭部は *_scale で短くする
STYLES = {
    "crew": {
        "description": "短髪（クルーカット）。頭頂 3〜4cm、側面と後ろは刈り上げ",
        "length": (0.026, 0.040), "side_scale": 0.42, "back_scale": 0.5,
        "cards": 5200, "width": 0.0085, "segments": 3,
        "flow": "back", "lift": 0.30, "gravity": 0.05,
        "offset": 0.0012, "layer": 0.0011, "max_offset": 0.018,
    },
    "side_part": {
        "description": "七三分け。分け目から反対側へ流し、前髪を横へ",
        "length": (0.065, 0.095), "side_scale": 0.45, "back_scale": 0.45,
        "cards": 3400, "width": 0.011, "segments": 5,
        "flow": "side_part", "part_azimuth": 38.0, "lift": 0.18, "gravity": 0.22,
        "offset": 0.0015, "layer": 0.0016, "max_offset": 0.026,
    },
    "long": {
        "description": "肩までの長髪。中央分けで両側と後ろへ下ろす",
        "length": (0.24, 0.30), "side_scale": 0.85, "back_scale": 1.0,
        "cards": 3000, "width": 0.013, "segments": 9,
        "flow": "center_part", "part_azimuth": 0.0, "lift": 0.10, "gravity": 0.75,
        "offset": 0.0018, "layer": 0.0013,
    },
}


def _smoothstep(a, b, x):
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


class Head:
    """休止姿勢・体型適用後の体から、頭の目印と表面検索を用意する。"""

    def __init__(self, body, eyes):
        deps = bpy.context.evaluated_depsgraph_get()
        self.eval_obj = body.evaluated_get(deps)
        mesh = self.eval_obj.to_mesh()
        self.mesh = mesh
        self.bvh = BVHTree.FromObject(body, deps)
        names = {g.index: g.name for g in body.vertex_groups}
        self.head_ids = []
        for v in mesh.vertices:
            ws = {names[g.group]: g.weight for g in v.groups if names.get(g.group, "") in ("head", "neck_01")}
            if ws.get("head", 0.0) > 0.5:
                self.head_ids.append(v.index)
        pts = [mesh.vertices[i].co for i in self.head_ids]
        self.top = max(p.z for p in pts)
        ev = eyes.evaluated_get(deps)
        emesh = ev.to_mesh()
        epts = [eyes.matrix_world @ v.co for v in emesh.vertices]
        ev.to_mesh_clear()
        self.eye_l = sum((p for p in epts if p.x > 0), Vector()) / len([p for p in epts if p.x > 0])
        self.eye_r = sum((p for p in epts if p.x < 0), Vector()) / len([p for p in epts if p.x < 0])
        self.eye_z = (self.eye_l.z + self.eye_r.z) / 2
        self.brow_z = self.eye_z + 0.018
        band = [p for p in pts if abs(p.z - (self.eye_z + 0.03)) < 0.01]
        self.front_y = min(p.y for p in band)
        self.back_y = max(p.y for p in band)
        self.centre = Vector((0.0, (self.front_y + self.back_y) / 2, self.eye_z + 0.02))
        # 頭蓋の横幅（耳を除く）: 耳より前後の帯で測る
        self.skull_half = {}
        for p in pts:
            d = p - self.centre
            az = abs(math.degrees(math.atan2(d.x, -d.y)))
            if 35 < az < 62 or 122 < az < 145:
                key = round(p.z, 2)
                self.skull_half[key] = max(self.skull_half.get(key, 0.0), abs(p.x))

    def free(self):
        self.eval_obj.to_mesh_clear()

    def azimuth(self, p):
        d = p - self.centre
        return math.degrees(math.atan2(d.x, -d.y))  # 0 = 正面, +90 = キャラクターの左, ±180 = 後ろ

    def hairline_z(self, az):
        """方位角ごとの生え際の高さ（これより上に髪が生える）。"""
        a = abs(az)
        table = [(0, self.brow_z + 0.050), (22, self.brow_z + 0.048), (38, self.brow_z + 0.036),
                 (52, self.brow_z + 0.018), (66, self.eye_z + 0.008), (80, self.eye_z - 0.006),
                 (92, self.eye_z - 0.016), (118, self.eye_z - 0.030), (150, self.eye_z - 0.075),
                 (180, self.eye_z - 0.088)]
        for (a0, z0), (a1, z1) in zip(table, table[1:]):
            if a <= a1:
                t = (a - a0) / (a1 - a0)
                return z0 + (z1 - z0) * t
        return table[-1][1]

    def is_ear(self, p):
        az = abs(self.azimuth(p))
        if not (65 < az < 125) or not (self.eye_z - 0.07 < p.z < self.eye_z + 0.03):
            return False
        key = round(p.z, 2)
        half = self.skull_half.get(key)
        return half is not None and abs(p.x) > half + 0.004

    def is_scalp(self, p):
        return p.z > self.hairline_z(self.azimuth(p)) and not self.is_ear(p)

    def nearest(self, p):
        loc, normal, _, _ = self.bvh.find_nearest(p)
        return loc, normal


def sample_roots(head, count, rng):
    """頭皮の面を面積に比例して選び、その中に根元を置く。"""
    mesh = head.mesh
    mesh.calc_loop_triangles()
    head_set = set(head.head_ids)
    tris, areas = [], []
    for tri in mesh.loop_triangles:
        vs = tri.vertices
        if not all(v in head_set for v in vs):
            continue
        pts = [mesh.vertices[v].co for v in vs]
        c = (pts[0] + pts[1] + pts[2]) / 3
        if head.is_scalp(c):
            tris.append(pts)
            areas.append(tri.area)
    total = sum(areas)
    cumulative, acc = [], 0.0
    for a in areas:
        acc += a
        cumulative.append(acc)
    roots = []
    import bisect
    for _ in range(count):
        i = bisect.bisect_left(cumulative, rng.random() * total)
        a, b, c = tris[min(i, len(tris) - 1)]
        r1, r2 = rng.random(), rng.random()
        if r1 + r2 > 1:
            r1, r2 = 1 - r1, 1 - r2
        roots.append(a + (b - a) * r1 + (c - a) * r2)
    return roots, tris


def flow_direction(style, head, p, normal):
    """髪型ごとの流れの向き（頭の表面に沿う向きに投影して使う）。"""
    az = head.azimuth(p)
    back = Vector((0, 1, 0))
    down = Vector((0, 0, -1))
    side = Vector((1 if p.x >= 0 else -1, 0, 0))
    if style["flow"] == "back":
        d = back * 1.0 + down * 0.35
        if abs(az) < 40 and p.z > head.brow_z + 0.04:
            d = back * 1.0 + Vector((0, 0, 0.25))
    elif style["flow"] == "side_part":
        part = style["part_azimuth"]
        # 分け目より左（+X 側）はそのまま左へ、右側は分け目から右後ろへ流す
        if az > part:
            d = Vector((1, 0.35, -0.2))
        else:
            d = Vector((-1, 0.55, -0.15))
            if abs(az) < 45 and p.z > head.brow_z + 0.03:
                d = Vector((-1, 0.25, 0.05))  # 前髪は横へ
        if abs(az) > 100:
            d = back * 0.4 + down * 1.0
    else:  # center_part
        d = side * 0.8 + down * 0.6
        if abs(az) > 110:
            d = back * 0.3 + down * 1.0
        if abs(az) < 25:
            d = side * 1.0 + back * 0.2 + down * 0.3
    t = d - normal * d.dot(normal)
    if t.length < 1e-6:
        t = down - normal * down.dot(normal)
    return t.normalized()


def grow_strand(style, head, root, rng):
    loc, normal = head.nearest(root)
    az = abs(head.azimuth(root))
    lo, hi = style["length"]
    length = rng.uniform(lo, hi)
    height = _smoothstep(head.eye_z, head.top, root.z)
    if az > 60:
        scale = style["side_scale"] if az < 125 else style["back_scale"]
        length *= scale + (1 - scale) * height
    segs = style["segments"]
    step = length / segs
    pts = [loc + normal * style["offset"]]
    direction = (flow_direction(style, head, loc, normal) * (1 - style["lift"]) + normal * style["lift"]).normalized()
    for k in range(1, segs + 1):
        p = pts[-1] + direction * step
        q, n = head.nearest(p)
        flow = flow_direction(style, head, q, n)
        g = style["gravity"] * (k / segs)
        direction = (flow * (1 - g) + Vector((0, 0, -1)) * g + direction * 0.5).normalized()
        jitter = Vector((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))) * 0.08
        direction = (direction + jitter).normalized()
        min_off = style["offset"] + style["layer"] * k
        height = (p - q).dot(n)
        if height < min_off:
            p = q + n * min_off
        elif "max_offset" in style and height > style["max_offset"]:
            # 短い髪型で毛束が頭から離れて跳ねないようにする
            p = q + n * style["max_offset"]
        pts.append(p)
    return pts


def strand_texture(path, color, variants=4, size=(1024, 2048), seed=3):
    """毛束のテクスチャ（髪色＋アルファ）。横に variants 種類並べる。下端が根元、上端が毛先。"""
    rng = random.Random(seed)
    w, h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    vw = w // variants
    base = [int(255 * c ** (1 / 2.2)) for c in color]
    for v in range(variants):
        x0 = v * vw
        for _ in range(85):
            x = x0 + vw * rng.uniform(0.1, 0.9)
            tip = rng.uniform(0.62, 1.0)
            amp = rng.uniform(0, vw * 0.03)
            phase = rng.uniform(0, math.tau)
            bright = rng.uniform(0.75, 1.45)
            col = tuple(min(255, int(c * bright)) for c in base)
            steps = 24
            pts = []
            for s in range(steps + 1):
                t = s / steps * tip
                pts.append((x + amp * math.sin(phase + t * 9), (1 - t) * (h - 1)))
            for s in range(steps):
                t = s / steps
                alpha = int(255 * (0.95 if t < 0.65 else 0.95 * (1 - (t - 0.65) / 0.35)))
                draw.line([pts[s], pts[s + 1]], fill=(*col, max(0, alpha)), width=3)
    img.save(path)
    return path


def build_cards(strands, style, head, rng, variants=4):
    """芯線から帯状のカードを作る（UV: u = 種類の列, v = 根元 0 → 毛先 1）。"""
    bm = bmesh.new()
    uv_layer = bm.loops.layers.uv.new("UVMap")
    for pts in strands:
        root = pts[0]
        _, n = head.nearest(root)
        variant = rng.randrange(variants)
        u0, u1 = variant / variants, (variant + 1) / variants
        width = style["width"] * rng.uniform(0.8, 1.25)
        rows = []
        for k, p in enumerate(pts):
            tangent = (pts[min(k + 1, len(pts) - 1)] - pts[max(k - 1, 0)]).normalized()
            _, nk = head.nearest(p)
            side = tangent.cross(nk)
            if side.length < 1e-6:
                side = tangent.cross(n)
            side.normalize()
            taper = 1.0 - 0.45 * (k / (len(pts) - 1))
            hw = width * 0.5 * taper
            rows.append((bm.verts.new(p - side * hw), bm.verts.new(p + side * hw)))
        for k in range(len(rows) - 1):
            a, b = rows[k]
            c, d = rows[k + 1]
            f = bm.faces.new((a, b, d, c))
            v0, v1 = k / (len(rows) - 1), (k + 1) / (len(rows) - 1)
            for loop, (uu, vv) in zip(f.loops, ((u0, v0), (u1, v0), (u1, v1), (u0, v1))):
                loop[uv_layer].uv = (uu, vv)
    mesh = bpy.data.meshes.new("BH_hair")
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def assign_weights(obj, head):
    """髪の頂点を頭へ。肩より下へ垂れる長い髪は、高さに応じて首・胸の骨へ分ける。"""
    groups = {name: obj.vertex_groups.new(name=name) for name in ("head", "neck_01", "spine_03")}
    neck_z = head.eye_z - 0.11
    chest_z = head.eye_z - 0.24
    for v in obj.data.vertices:
        z = v.co.z
        w_head = _smoothstep(neck_z - 0.02, neck_z + 0.06, z)
        w_chest = 1.0 - _smoothstep(chest_z - 0.04, neck_z - 0.02, z)
        w_neck = max(0.0, 1.0 - w_head - w_chest)
        for name, w in (("head", w_head), ("neck_01", w_neck), ("spine_03", w_chest)):
            if w > 1e-4:
                groups[name].add([v.index], w, "REPLACE")


def scalp_ink(head, body, image_size, color, path):
    """髪の生える範囲（頭皮）を髪色で塗った RGBA 画像（UV 空間）を作る。生え際はぼかす。"""
    from face_details import SurfaceUV
    from PIL import ImageFilter
    surf = SurfaceUV(body)
    scale = 2
    img = Image.new("RGBA", (image_size * scale, image_size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    mesh = surf.mesh
    rgb = tuple(int(255 * c ** (1 / 2.2)) for c in color)
    head_set = set(head.head_ids)
    for poly in mesh.polygons:
        if not all(v in head_set for v in poly.vertices):
            continue
        c = sum((mesh.vertices[v].co for v in poly.vertices), Vector()) / len(poly.vertices)
        if not head.is_scalp(c):
            continue
        pts = [(surf.uv[li].uv.x * image_size * scale, (1 - surf.uv[li].uv.y) * image_size * scale)
               for li in poly.loop_indices]
        draw.polygon(pts, fill=(*rgb, 235))
    surf.free()
    img = img.filter(ImageFilter.GaussianBlur(radius=3 * scale))
    img = img.resize((image_size, image_size), Image.LANCZOS)
    img.save(path)
    return path


def make_hair(body, eyes, rig, style_name, color, texture_path, scalp_path=None, scalp_res=4096, seed=5):
    style = STYLES[style_name]
    rng = random.Random(seed)
    head = Head(body, eyes)
    roots, _ = sample_roots(head, style["cards"], rng)
    strands = [grow_strand(style, head, r, rng) for r in roots]
    mesh = build_cards(strands, style, head, rng)
    obj = bpy.data.objects.new(f"BH_hair_{style_name}", mesh)
    bpy.context.collection.objects.link(obj)
    assign_weights(obj, head)
    mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    obj.parent = rig

    import bake
    strand_texture(texture_path, color, seed=seed)
    image = bpy.data.images.load(texture_path, check_existing=True)
    image.alpha_mode = "STRAIGHT"
    mat = bake.game_material(f"BH_hair_{style_name}", image, roughness=0.42, alpha_blend=True)
    mat.use_backface_culling = False
    obj.data.materials.append(mat)
    for p in obj.data.polygons:
        p.use_smooth = True
    if scalp_path:
        scalp_ink(head, body, scalp_res, color, scalp_path)
    head.free()
    return obj
