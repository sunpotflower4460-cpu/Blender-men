"""顔の細部（眉・まつげ）を作る。

眉:   目の位置から眉の弧を決めて顔の表面に投影し、毛を 1 本ずつ UV 空間の線として RGBA 画像へ描く。
      MPFB の LAYERED 肌の「インクレイヤー」として重ねるので、焼き込み時にもそのまま肌テクスチャに入る。
まつげ: 基本メッシュの補助形状（helper-*-eyelashes-*）を別オブジェクトに取り出す。
      体型の変形（シェイプキー）と骨のウェイトをそのまま引き継ぐ。
"""

import math
import random

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform, closest_point_on_tri
from PIL import Image, ImageDraw


class SurfaceUV:
    """体の表面（休止姿勢・体型適用後）の点を UV 座標に変換する。"""

    def __init__(self, body):
        deps = bpy.context.evaluated_depsgraph_get()
        self.eval_obj = body.evaluated_get(deps)
        self.mesh = self.eval_obj.to_mesh()
        self.mesh.calc_loop_triangles()
        self.bvh = BVHTree.FromObject(body, deps)
        self.uv = self.mesh.uv_layers.active.data
        self.tris_by_poly = {}
        for tri in self.mesh.loop_triangles:
            self.tris_by_poly.setdefault(tri.polygon_index, []).append(tri)

    def free(self):
        self.eval_obj.to_mesh_clear()

    def ray_front(self, x, z, y_start=-1.0):
        """正面（-Y）から +Y へ光線を飛ばし、顔に当たった点を返す。"""
        hit, normal, index, _ = self.bvh.ray_cast(Vector((x, y_start, z)), Vector((0, 1, 0)))
        return hit, normal

    def nearest(self, point):
        loc, normal, index, _ = self.bvh.find_nearest(point)
        return loc, normal, index

    def to_uv(self, point):
        loc, _, index = self.nearest(point)
        best = None
        for tri in self.tris_by_poly.get(index, []):
            a, b, c = (self.mesh.vertices[v].co for v in tri.vertices)
            p = closest_point_on_tri(loc, a, b, c)
            d = (p - loc).length
            if best is None or d < best[0]:
                best = (d, tri, a, b, c)
        _, tri, a, b, c = best
        ua, ub, uc = (Vector((*self.uv[l].uv, 0.0)) for l in tri.loops)
        uv = barycentric_transform(loc, a, b, c, ua, ub, uc)
        return uv.x, uv.y


def eye_centres(eyes):
    deps = bpy.context.evaluated_depsgraph_get()
    ev = eyes.evaluated_get(deps)
    mesh = ev.to_mesh()
    pts = [eyes.matrix_world @ v.co for v in mesh.vertices]
    ev.to_mesh_clear()
    left = [p for p in pts if p.x > 0]
    right = [p for p in pts if p.x < 0]
    return sum(left, Vector()) / len(left), sum(right, Vector()) / len(right)


def paint_eyebrows(body, eyes, image_size=4096, hairs_per_brow=650, color=(8, 6, 5), seed=7):
    """眉を描いた RGBA 画像（透明背景）を作って返す。"""
    rng = random.Random(seed)
    surf = SurfaceUV(body)
    left, right = eye_centres(eyes)
    scale = 4  # 細い線をなめらかにするため、4 倍で描いて縮小する
    canvas = Image.new("RGBA", (image_size * scale, image_size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def uv_px(point):
        u, v = surf.to_uv(point)
        return u * image_size * scale, (1.0 - v) * image_size * scale

    for centre, side in ((left, 1.0), (right, -1.0)):
        for _ in range(hairs_per_brow):
            # t: 眉頭(0) → 眉尻(1)。眉頭側に毛を多めに置く
            t = rng.random() ** 1.15
            x = centre.x + side * (-0.019 + 0.043 * t)
            arch = 0.0065 * math.sin(math.pi * min(1.0, t * 1.1))
            z_mid = centre.z + 0.016 + arch - 0.006 * t * t
            half_width = 0.0042 * (1.0 - 0.6 * t)
            z = z_mid + rng.uniform(-half_width, half_width)
            root, _ = surf.ray_front(x, z)
            if root is None:
                continue
            # 毛の向き: 眉頭は上向き、そこから外側へ寝かせ、眉尻はやや下向き
            up = 0.85 * (1.0 - min(1.0, t / 0.25)) + 0.15 - 0.35 * max(0.0, t - 0.7)
            direction = Vector((side * (1.0 - abs(up) * 0.6), 0.0, up)).normalized()
            length = rng.uniform(0.005, 0.009) * (1.0 - 0.3 * t)
            tip = root + direction * length
            tip, _, _ = surf.nearest(tip)
            mid, _, _ = surf.nearest((root + tip) / 2)
            alpha = int(255 * rng.uniform(0.75, 1.0))
            width = max(1, int(scale * rng.uniform(1.2, 1.8)))
            draw.line([uv_px(root), uv_px(mid), uv_px(tip)], fill=(*color, alpha), width=width)
    surf.free()
    return canvas.resize((image_size, image_size), Image.LANCZOS)


def attach_ink_layer(body, image_path, name):
    """画像を MPFB のインクレイヤーとして肌マテリアルへ重ねる。"""
    from mpfb_env import service
    MaterialService = service("mpfb.services.materialservice", "MaterialService")
    material = body.material_slots[0].material
    _, texture_node, layer = MaterialService.add_focus_nodes(material, body.data.uv_layers.active.name)
    image = bpy.data.images.load(image_path)
    image.name = name
    image.alpha_mode = "STRAIGHT"
    texture_node.image = image
    return layer


def make_eyelashes(body, rig, material_color=(0.02, 0.015, 0.012)):
    """補助形状のまつげを別オブジェクトとして取り出す（体型・ウェイトを引き継ぐ）。"""
    lashes = body.copy()
    lashes.data = body.data.copy()
    lashes.name = "BH_eyelashes"
    lashes.data.name = "BH_eyelashes"
    bpy.context.collection.objects.link(lashes)
    for mod in list(lashes.modifiers):
        if mod.type != "ARMATURE":
            lashes.modifiers.remove(mod)
    groups = {g.index for g in lashes.vertex_groups if "eyelashes" in g.name}
    bm = bmesh.new()
    bm.from_mesh(lashes.data)
    deform = bm.verts.layers.deform.verify()
    drop = [v for v in bm.verts if not any(i in groups for i in v[deform].keys())]
    bmesh.ops.delete(bm, geom=drop, context="VERTS")
    bm.to_mesh(lashes.data)
    bm.free()
    mat = bpy.data.materials.new("BH_eyelashes")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*material_color, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.5
    mat.diffuse_color = (*material_color, 1.0)
    lashes.data.materials.clear()
    lashes.data.materials.append(mat)
    lashes.parent = rig
    return lashes


def _islands(obj):
    """メッシュの連結成分（面の集合）を返す。"""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    seen, islands = set(), []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, comp = [f], []
        seen.add(f.index)
        while stack:
            g = stack.pop()
            comp.append(g.index)
            for e in g.edges:
                for h in e.link_faces:
                    if h.index not in seen:
                        seen.add(h.index)
                        stack.append(h)
        islands.append(comp)
    bm.free()
    return islands


def setup_eye_materials(eyes, iris_texture):
    """目を「眼球（虹彩テクスチャ）」と「角膜（透明でつやのある外殻）」の 2 マテリアルに分ける。

    MakeHuman の高精細な目は片目 2 層で、外側の層の方がわずかに大きい。
    """
    import bake
    image = bpy.data.images.load(iris_texture, check_existing=True)
    eyeball = bake.game_material("BH_eyeball", image, roughness=0.35)
    cornea = bpy.data.materials.new("BH_cornea")
    cornea.use_nodes = True
    bsdf = cornea.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.02
    bsdf.inputs["Alpha"].default_value = 0.06
    cornea.surface_render_method = "BLENDED"
    eyes.data.materials.clear()
    eyes.data.materials.append(eyeball)
    eyes.data.materials.append(cornea)
    mesh = eyes.data
    # 左右それぞれ、外側に広い方の部品を角膜とする
    by_side = {}
    for comp in _islands(eyes):
        verts = {v for fi in comp for v in mesh.polygons[fi].vertices}
        span = max(mesh.vertices[v].co.x for v in verts) - min(mesh.vertices[v].co.x for v in verts)
        side = 1 if mesh.vertices[next(iter(verts))].co.x > 0 else -1
        by_side.setdefault(side, []).append((span, comp))
    for parts in by_side.values():
        parts.sort(key=lambda p: p[0])
        for fi in parts[-1][1]:
            mesh.polygons[fi].material_index = 1
    return eyeball, cornea


def eyelash_texture(lashes, eyes, path, size=2048, lashes_per_strip=90, seed=11):
    """まつげの帯（補助形状）の UV 上に、根元から毛先へ向かう細い毛を描いた透過テクスチャを作る。"""
    rng = random.Random(seed)
    left, right = eye_centres(eyes)
    mesh = lashes.data
    uv = mesh.uv_layers.active.data
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    for comp in _islands(lashes):
        loops = [li for fi in comp for li in mesh.polygons[fi].loop_indices]
        us = [uv[li].uv.x for li in loops]
        vs = [uv[li].uv.y for li in loops]
        u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
        # 帯の長い方向をまぶたに沿う向き、短い方向を根元→毛先とみなす
        along_u = (u1 - u0) >= (v1 - v0)
        # 根元側: 目の中心に近い頂点が多い側
        centre = left if mesh.vertices[mesh.loops[loops[0]].vertex_index].co.x > 0 else right
        near = sorted(loops, key=lambda li: (mesh.vertices[mesh.loops[li].vertex_index].co - centre).length)
        root_side = sum((uv[li].uv.y if along_u else uv[li].uv.x) for li in near[:len(near) // 3]) / max(1, len(near) // 3)
        cross0, cross1 = (v0, v1) if along_u else (u0, u1)
        root_at_low = abs(root_side - cross0) < abs(root_side - cross1)
        # 下まぶたの帯（目の中心より下）は、上まつげより短く疎らにする
        strip_z = sum(mesh.vertices[mesh.loops[li].vertex_index].co.z for li in loops) / len(loops)
        lower = strip_z < centre.z
        count = lashes_per_strip // 2 if lower else lashes_per_strip
        for _ in range(count):
            t = rng.uniform(0.04, 0.96)
            a = (u0 + (u1 - u0) * t) if along_u else (v0 + (v1 - v0) * t)
            length = rng.uniform(0.65, 1.0) * (0.55 + 0.45 * math.sin(math.pi * t)) * (0.45 if lower else 1.0)
            start, end = (cross0, cross0 + (cross1 - cross0) * length) if root_at_low else (cross1, cross1 - (cross1 - cross0) * length)
            bend = rng.uniform(-0.15, 0.15) * (u1 - u0 if along_u else v1 - v0) * 0.05
            if along_u:
                pts = [(a * size, (1 - start) * size), ((a + bend) * size, (1 - end) * size)]
            else:
                pts = [(start * size, (1 - a) * size), (end * size, (1 - a - bend) * size)]
            draw.line(pts, fill=(10, 8, 7, int(255 * rng.uniform(0.75, 1.0))), width=2)
    canvas.save(path)
    return path


def eyelash_material(path):
    import bake
    image = bpy.data.images.load(path, check_existing=True)
    image.alpha_mode = "STRAIGHT"
    return bake.game_material("BH_eyelashes", image, roughness=0.5, alpha_blend=True)
