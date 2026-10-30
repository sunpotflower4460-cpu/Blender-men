"""MPFB2 を Blender（ヘッドレスの bpy モジュールでも可）に導入し、サービスを読み込むための共通処理。

MPFB2 のコードは GPLv3 なのでリポジトリには含めず、固定したコミットを取得して
Blender のユーザー拡張フォルダへ配置する。同梱アセット（基本メッシュ・targets・リグ等）は CC0。
MakeHuman 本体リポジトリから取得する目のプロキシ（CC0）も MPFB のユーザーデータへ配置する。
"""

import importlib
import os
import shutil
import subprocess
import sys

import bpy
import addon_utils  # bpy の読み込み後でないと import できない

MPFB_REPO = "https://github.com/makehumancommunity/mpfb2.git"
MPFB_COMMIT = "6ff1eb358f6c1959ced77e79e6dd2406d9a71f49"
MAKEHUMAN_REPO = "https://github.com/makehumancommunity/makehuman.git"
MAKEHUMAN_COMMIT = "a8bc2d54ff0ac92e78ff71431b1023eda42bf482"

HQ_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HQ_DIR)
CACHE_DIR = os.environ.get("BH_CACHE_DIR", os.path.join(REPO_ROOT, ".cache"))
EXT_MODULE = "bl_ext.user_default.mpfb"


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fetch_sources():
    """MPFB2 と MakeHuman（目のプロキシのみ）を固定コミットで取得する。取得済みなら何もしない。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    mpfb_src = os.path.join(CACHE_DIR, "mpfb2")
    if not os.path.isdir(os.path.join(mpfb_src, "src", "mpfb")):
        _git("clone", "-q", "--filter=blob:none", MPFB_REPO, mpfb_src)
        _git("checkout", "-q", MPFB_COMMIT, cwd=mpfb_src)
    mh_src = os.path.join(CACHE_DIR, "makehuman")
    if not os.path.isdir(os.path.join(mh_src, "makehuman", "data", "eyes")):
        _git("clone", "-q", "--filter=blob:none", "--sparse", MAKEHUMAN_REPO, mh_src)
        _git("sparse-checkout", "set", "makehuman/data/eyes", cwd=mh_src)
        _git("checkout", "-q", MAKEHUMAN_COMMIT, cwd=mh_src)
    return mpfb_src, mh_src


def install():
    """MPFB2 をユーザー拡張として配置し、目のプロキシを MPFB のユーザーデータへ置く。"""
    mpfb_src, mh_src = fetch_sources()
    ext_dir = os.path.join(bpy.utils.user_resource("EXTENSIONS"), "user_default", "mpfb")
    marker = os.path.join(ext_dir, ".bh_commit")
    if not (os.path.exists(marker) and open(marker).read().strip() == MPFB_COMMIT):
        shutil.rmtree(ext_dir, ignore_errors=True)
        shutil.copytree(os.path.join(mpfb_src, "src", "mpfb"), ext_dir)
        with open(marker, "w") as f:
            f.write(MPFB_COMMIT)
    user_data = os.path.join(bpy.utils.user_resource("EXTENSIONS"), ".user", "user_default", "mpfb", "data")
    eyes_dst = os.path.join(user_data, "eyes")
    if not os.path.isdir(os.path.join(eyes_dst, "high-poly")):
        shutil.copytree(os.path.join(mh_src, "makehuman", "data", "eyes"), eyes_dst, dirs_exist_ok=True)
    return user_data


def enable():
    """MPFB2 を有効化する（未導入なら導入してから）。"""
    install()
    addon_utils.enable(EXT_MODULE, default_set=True)


def service(module_suffix, name):
    """拡張形式の相対 import の都合で、読み込み済みモジュール名の末尾から MPFB のクラスを取り出す。"""
    for mod in list(sys.modules):
        if mod.endswith(module_suffix):
            return getattr(importlib.import_module(mod), name)
    raise ImportError(module_suffix)


def user_data_dir():
    return os.path.join(bpy.utils.user_resource("EXTENSIONS"), ".user", "user_default", "mpfb", "data")
