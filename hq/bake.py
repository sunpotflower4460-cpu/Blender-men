"""手続き的なマテリアル（MPFB の LAYERED 肌・手続き的な目など）を画像テクスチャへ焼き込み、
glTF/ゲームエンジンで使える単純なマテリアル（Principled BSDF + 画像）に差し替える。

焼き込むのは色（ライティングなし）。肌の細かい凹凸は法線マップとしても焼き込む。
"""

import os

import bpy

BAKE_SAMPLES = 4


def _bake_image(name, size, non_color=False):
    image = bpy.data.images.get(name)
    if image is not None:
        bpy.data.images.remove(image)
    image = bpy.data.images.new(name, size, size, alpha=False, float_buffer=False)
    if non_color:
        image.colorspace_settings.name = "Non-Color"
    return image


def _add_target_nodes(obj, image):
    """各マテリアルに焼き込み先の画像ノードを追加して選択状態にする（Cycles は選択中の画像ノードへ焼く）。"""
    nodes = []
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes:
            continue
        node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        node.name = "BH_bake_target"
        node.image = image
        for n in mat.node_tree.nodes:
            n.select = False
        node.select = True
        mat.node_tree.nodes.active = node
        nodes.append((mat, node))
    return nodes


def _remove_target_nodes(nodes):
    for mat, node in nodes:
        mat.node_tree.nodes.remove(node)


def bake_object(obj, out_dir, prefix, size=2048, normal=False, margin=8):
    """obj のマテリアル（スロットが複数でも UV は共通）を、色（と任意で法線）の画像へ焼き込む。"""
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = BAKE_SAMPLES
    sc.render.bake.margin = margin
    sc.render.bake.use_selected_to_active = False

    # 休止姿勢・補助形状なしの状態で焼く（細分化は外す）。
    # 服の下を隠す Delete マスクも外す: 隠したまま焼くとその部分のテクスチャが黒いまま残り、
    # 別の服（例: 長袖 → 半袖）に着替えたときに黒い肌が見える
    saved = []
    for m in obj.modifiers:
        if m.type == "SUBSURF" or (m.type == "MASK" and m.name.startswith("Delete")):
            saved.append((m, m.show_render))
            m.show_render = False

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    os.makedirs(out_dir, exist_ok=True)
    results = {}

    color = _bake_image(f"{prefix}_color", size)
    nodes = _add_target_nodes(obj, color)
    sc.render.bake.use_pass_direct = False
    sc.render.bake.use_pass_indirect = False
    sc.render.bake.use_pass_color = True
    bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, margin=margin)
    _remove_target_nodes(nodes)
    color.filepath_raw = os.path.join(out_dir, f"{prefix}_color.png")
    color.file_format = "PNG"
    color.save()
    results["color"] = color

    if normal:
        nrm = _bake_image(f"{prefix}_normal", size, non_color=True)
        nodes = _add_target_nodes(obj, nrm)
        bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT", margin=margin)
        _remove_target_nodes(nodes)
        nrm.filepath_raw = os.path.join(out_dir, f"{prefix}_normal.png")
        nrm.file_format = "PNG"
        nrm.save()
        results["normal"] = nrm

    for m, show in saved:
        m.show_render = show
    return results


def composite_over(color_image, overlay_path, out_path):
    """焼き込んだ色の画像の上に、同じ UV の RGBA 画像（頭皮の下地など）をアルファで重ねて保存する。"""
    import numpy as np
    from PIL import Image
    base = Image.open(bpy.path.abspath(color_image.filepath_raw)).convert("RGB")
    over = Image.open(overlay_path).convert("RGBA")
    if over.size != base.size:
        over = over.resize(base.size, Image.LANCZOS)
    b = np.asarray(base, dtype=np.float32)
    o = np.asarray(over, dtype=np.float32)
    a = o[..., 3:4] / 255.0
    out = (o[..., :3] * a + b * (1 - a)).round().astype(np.uint8)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    Image.fromarray(out, "RGB").save(out_path)
    return bpy.data.images.load(out_path, check_existing=False)


def game_material(name, color_image, normal_image=None, roughness=0.5, alpha_blend=False):
    """glTF で書き出せる単純なマテリアル（画像テクスチャ + Principled BSDF）。"""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = color_image
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    if alpha_blend:
        nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
        mat.surface_render_method = "BLENDED"
    if normal_image is not None:
        ntex = nt.nodes.new("ShaderNodeTexImage")
        ntex.image = normal_image
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(ntex.outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = roughness
    return mat
