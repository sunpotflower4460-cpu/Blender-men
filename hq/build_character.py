"""MPFB2 の人体からキャラクター（マスター／派生体型）を組み立てる。

入力は JSON（hq/config/*.json）。処理は段階ごとの関数に分け、後で失敗した段階だけを
作り直せるようにしている。

  1. 人体:   MPFB2 の基本メッシュに体型（macro）と個別 targets を適用
  2. 骨格:   ゲーム共通骨格（MPFB "game_engine" リグ、53 本）を体型に合わせて生成
  3. 肌:     MPFB の LAYERED 肌（手続き的。肌色・粗さを設定）
  4. 目:     MakeHuman の高精細な目プロキシ（CC0）＋ MPFB の手続き的な目のマテリアル

実行方法:
  python hq/build_character.py --config hq/config/master.json --output out/master.blend
"""

import argparse
import json
import os
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bake  # noqa: E402
import face_details  # noqa: E402
import hair  # noqa: E402
import mpfb_env  # noqa: E402

ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--output", required=True)
    return p.parse_args(argv)


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Builder:
    def __init__(self, config, texture_dir):
        self.config = config
        self.texture_dir = texture_dir
        self.timings = {}
        self.human = None
        self.rig = None
        self.eyes = None
        self.eyelashes = None
        self.hair = None
        self.clothes = []
        self.HumanService = mpfb_env.service("mpfb.services.humanservice", "HumanService")
        self.TargetService = mpfb_env.service("mpfb.services.targetservice", "TargetService")
        self.AssetService = mpfb_env.service("mpfb.services.assetservice", "AssetService")
        self.LocationService = mpfb_env.service("mpfb.services.locationservice", "LocationService")
        self.HOP = mpfb_env.service("mpfb.entities.objectproperties", "HumanObjectProperties")

    def _timed(self, name, fn):
        t = time.time()
        result = fn()
        self.timings[name] = round(time.time() - t, 2)
        return result

    # 1. 人体 -----------------------------------------------------------------
    def body(self):
        def run():
            h = self.HumanService.create_human()
            h.name = "BH_body"
            for key, value in self.config.get("macros", {}).items():
                self.HOP.set_value(key, value, entity_reference=h)
            self.TargetService.reapply_macro_details(h)
            targets_root = self.LocationService.get_mpfb_data("targets")
            for rel, weight in self.config.get("targets", {}).items():
                self.TargetService.load_target(h, os.path.join(targets_root, rel), weight=weight)
            return h
        self.human = self._timed("body", run)

    # 2. 骨格 -----------------------------------------------------------------
    def skeleton(self):
        def run():
            rig = self.HumanService.add_builtin_rig(self.human, self.config.get("rig", "game_engine"))
            rig.name = "BH_rig"
            rig.data.name = "BH_rig"
            return rig
        self.rig = self._timed("skeleton", run)

    # 3. 肌 -------------------------------------------------------------------
    def skin(self):
        skin = self.config.get("skin", {})
        color = (*skin.get("color", (0.62, 0.40, 0.28)), 1.0)
        rough = skin.get("roughness", 0.42)

        def run():
            lips = (*skin.get("lips_color", (0.42, 0.20, 0.16)), 1.0)
            overrides = {
                "color": {"SkinColor": color, "SkinOverride": 1.0, "LipsColor": lips, "LipsOverride": 1.0},
                "body": {"SkinColor": color, "Roughness": rough},
                "face": {"SkinColor": color, "Roughness": rough},
                "ears": {"SkinColor": color, "Roughness": rough},
            }
            self.HumanService.set_character_skin(None, self.human, skin_type="LAYERED", slot_overrides=overrides)
        self._timed("skin", run)

    # 4. 目 -------------------------------------------------------------------
    def eyes_asset(self):
        def run():
            path = self.AssetService.find_asset_absolute_path("high-poly.mhclo", asset_subdir="eyes")
            if path is None:
                raise FileNotFoundError("eyes/high-poly.mhclo (run mpfb_env.install())")
            eyes = self.HumanService.add_mhclo_asset(path, self.human, asset_type="Eyes",
                                                     material_type="PROCEDURAL_EYES")
            eyes.name = "BH_eyes"
            iris = self.config.get("eyes", {}).get("iris_color")
            if iris:
                for slot in eyes.material_slots:
                    for node in slot.material.node_tree.nodes:
                        for sock in node.inputs:
                            if sock.name in ("IrisMinorColor", "IrisMajorColor", "IrisSection4Color") and hasattr(sock, "default_value"):
                                sock.default_value = (*iris, 1.0)
            return eyes
        self.eyes = self._timed("eyes", run)

    # 5. 顔の細部（眉・まつげ） ------------------------------------------------
    def face(self):
        face = self.config.get("face", {})

        def run():
            brows = face.get("eyebrows", {})
            if brows.get("enabled", True):
                image = face_details.paint_eyebrows(
                    self.human, self.eyes, image_size=brows.get("resolution", 4096),
                    hairs_per_brow=brows.get("hairs", 650), color=tuple(brows.get("color", (8, 6, 5))),
                    seed=brows.get("seed", 7))
                os.makedirs(self.texture_dir, exist_ok=True)
                path = os.path.join(self.texture_dir, f"{self.config['name']}_eyebrows.png")
                image.save(path)
                face_details.attach_ink_layer(self.human, path, "BH_eyebrows")
            if face.get("eyelashes", True):
                self.eyelashes = face_details.make_eyelashes(self.human, self.rig)
                lash_path = os.path.join(self.texture_dir, f"{self.config['name']}_eyelashes.png")
                face_details.eyelash_texture(self.eyelashes, self.eyes, lash_path)
                self.eyelashes.data.materials.clear()
                self.eyelashes.data.materials.append(face_details.eyelash_material(lash_path))
        self._timed("face", run)

    # 素材の読み込み（MPFB が体型に合わせてフィットさせる） ----------------------------
    def _load_asset(self, kind, name, asset_type):
        path = os.path.join(ASSET_DIR, kind, name, f"{name}.mhclo")
        if not os.path.exists(path):
            raise FileNotFoundError(f"{kind}/{name}: run hq/author_assets.py first")
        obj = self.HumanService.add_mhclo_asset(path, self.human, asset_type=asset_type, subdiv_levels=0,
                                                material_type="GAMEENGINE")
        obj.name = f"BH_{kind}_{name}"
        with open(os.path.join(os.path.dirname(path), "material.json"), encoding="utf-8") as f:
            spec = json.load(f)
        folder = os.path.dirname(path)
        color = bpy.data.images.load(os.path.join(folder, spec["color"]), check_existing=True)
        normal = None
        if spec.get("normal"):
            normal = bpy.data.images.load(os.path.join(folder, spec["normal"]), check_existing=True)
            normal.colorspace_settings.name = "Non-Color"
        if spec.get("alpha_blend"):
            color.alpha_mode = "STRAIGHT"
        mat = bake.game_material(f"BH_{name}", color, normal, roughness=spec.get("roughness", 0.6),
                                 alpha_blend=spec.get("alpha_blend", False))
        mat.use_backface_culling = False
        obj.data.materials.clear()
        obj.data.materials.append(mat)
        for p in obj.data.polygons:
            p.material_index = 0
            p.use_smooth = True
        return obj, spec, folder

    # 7. 髪 -------------------------------------------------------------------
    def hair_asset(self):
        style = self.config.get("hair")
        if not style:
            return

        def run():
            obj, spec, folder = self._load_asset("hair", style, "Clothes")
            # 髪のウェイトは体からの補間ではなく、頭・首・胸への規則で付け直す（腕の動きに引っ張られないように）
            for g in list(obj.vertex_groups):
                obj.vertex_groups.remove(g)
            head = hair.Head(self.human, self.eyes)
            hair.assign_weights(obj, head)
            head.free()
            if spec.get("scalp"):
                face_details.attach_ink_layer(self.human, os.path.join(folder, spec["scalp"]), "BH_scalp")
            return obj
        self.hair = self._timed("hair", run)

    # 8. 服 -------------------------------------------------------------------
    def outfit_assets(self):
        outfit = self.config.get("outfit")
        if not outfit:
            return
        with open(os.path.join(ASSET_DIR, "index.json"), encoding="utf-8") as f:
            outfits = json.load(f)["outfits"]
        names = outfits[outfit] if isinstance(outfit, str) else outfit

        def run():
            return [self._load_asset("clothes", n, "Clothes")[0] for n in names]
        self.clothes = self._timed("outfit", run)

    # 仕上げ: ゲーム用に 1 頂点あたりの骨の影響を 4 本までに制限する -----------------
    def character_meshes(self):
        """骨格で変形するメッシュ（体・目・まつげ・髪・服）。"""
        return [o for o in bpy.data.objects if o.type == "MESH"
                and any(m.type == "ARMATURE" and m.object == self.rig for m in o.modifiers)]

    def game_ready(self):
        """glTF/ゲームエンジンは 1 頂点 4 本の影響が標準。Blender 側でも同じにして、変形を一致させる。

        体型用の補助グループ（body / helper-* など）は MASK で使うので対象外にし、骨のグループだけを扱う。
        """
        def run():
            for obj in self.character_meshes():
                obj.parent = self.rig
                bpy.ops.object.select_all(action="DESELECT")
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.vertex_group_limit_total(group_select_mode="BONE_DEFORM", limit=4)
                bpy.ops.object.vertex_group_normalize_all(group_select_mode="BONE_DEFORM", lock_active=False)
            bpy.ops.object.select_all(action="DESELECT")
        self._timed("game_ready", run)

    # 6. ゲーム用マテリアル（手続き的なマテリアルを画像へ焼き込んで差し替え） -------------
    def materials(self):
        res = self.config.get("bake", {}).get("resolution", 4096)

        reuse = self.config.get("bake", {}).get("reuse")

        def run():
            name = self.config["name"]
            if reuse:
                # 同じ体型・同じ頭皮の焼き込み結果を使い回す（髪型や服だけを替えた比較用）
                baked = {"color": bpy.data.images.load(f"{reuse}_color.png", check_existing=True),
                         "normal": bpy.data.images.load(f"{reuse}_normal.png", check_existing=True)}
                baked["normal"].colorspace_settings.name = "Non-Color"
            else:
                baked = bake.bake_object(self.human, self.texture_dir, f"{name}_body", size=res, normal=True)
            skin = bake.game_material("BH_skin", baked["color"], baked["normal"],
                                      roughness=self.config.get("skin", {}).get("roughness", 0.5))
            self.human.data.materials.clear()
            self.human.data.materials.append(skin)
            iris = os.path.join(mpfb_env.user_data_dir(), "eyes", "materials", "brown_eye.png")
            face_details.setup_eye_materials(self.eyes, iris)
        self._timed("materials", run)

    def build(self):
        self.body()
        self.skeleton()
        self.skin()
        self.eyes_asset()
        self.face()
        self.hair_asset()
        self.outfit_assets()
        self.game_ready()
        if self.config.get("bake", {}).get("enabled", True):
            self.materials()
        self.human["bh_config"] = json.dumps(self.config, ensure_ascii=False)
        return self


def main():
    args = parse_args()
    config = load_config(args.config)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    mpfb_env.enable()
    t = time.time()
    texture_dir = os.path.join(os.path.dirname(os.path.abspath(args.output)), "textures")
    builder = Builder(config, texture_dir).build()
    total = round(time.time() - t, 2)
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.output))
    bpy.ops.file.make_paths_relative()
    bpy.ops.wm.save_mainfile()
    print(json.dumps({"output": args.output, "timings": builder.timings, "total": total}))


if __name__ == "__main__":
    main()
