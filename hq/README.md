# hq — 高品質キャラクター生成基盤（MPFB2 ベース）

既存のカプセル体（`scripts/`）とは別系統の、本番用の生成経路です。
高品質な人体マスターと再利用できる髪・服の素材を一度作り、個々の人物は
**体型パラメータと素材の選択**だけで組み立てます。

## 技術の比較と採用理由

| 方式 | 体・顔の品質 | 体型変更時の破綻 | ゲーム骨格 | 判定 |
| --- | --- | --- | --- | --- |
| カプセル体（`claude/human-axis-prep`） | マネキン | なし（形が単純） | Rigify（706 本） | 量産の主経路にしない |
| Skin モディファイア体（`claude/lucid-hawking-mlvo9u`） | 粘土状 | なし | Rigify | 不採用 |
| 骨格から生成した有機体（`codex/body-from-skeleton`） | 顔・手が塊 | なし | Rigify | 不採用 |
| **MPFB2（MakeHuman）** | **自然な顔・手・足** | **シェイプキーで連続変形** | **game_engine（53 本）** | **採用** |

前回 MakeHuman の基本メッシュが「ドレス」「髪の板」に見えたのは、補助形状（`helper-*`）を
体と一緒に描画していたためで、MPFB2 はこれを MASK モディファイアで正しく隠します。

骨格は MPFB の `game_engine`（Unreal 系の命名、53 本、MakeHuman のウェイト付き）を採用し、
既存の BH_rig（Rigify）とも比較した（`hq/validate_motion.py` の結果を参照）。

## 構成

```
hq/mpfb_env.py         MPFB2（固定コミット）を取得して Blender の拡張として導入
hq/build_character.py  JSON → キャラクター（.blend）。段階ごとに分かれていて個別にやり直せる
hq/face_details.py     眉（肌への描き込み）・まつげ・目（眼球＋角膜）
hq/hair.py             ヘアカードの生成（生え際・流れ・重力・衝突）
hq/clothes.py          服の生成（型紙 → ゆとり → 布シミュレーション → 仕上げ）
hq/author_assets.py    髪・服をマスター上で作り、MPFB の .mhclo 素材として書き出す
hq/bake.py             手続き的マテリアルを画像へ焼き込み、glTF 用マテリアルに差し替える
hq/render_views.py     正面・斜め・側面・背面・顔の描画（Cycles）
hq/validate_motion.py  パンチ・キック・しゃがみでの変形、服のめり込み、GLB 往復の検証
hq/config/*.json       キャラクターの設定
hq/assets/             髪・服の素材（.mhclo / .obj / テクスチャ / material.json）
```

## 使い方

（検証結果を反映して追記）
