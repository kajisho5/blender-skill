# Roadmap

`done` = shipped in v0.1.0. `planned` = filed as a tracked issue but not yet built. `Issue` is
filled in by `scripts/dev/roadmap_to_issues.py` (see CONTRIBUTING.md) -- don't hand-edit it.

A `done` row means the exact capability described exists and was verified against a real
Blender; where the shipped feature only partially matches the row's description (e.g. an item
lists five sub-capabilities and only some exist), the row stays `planned` until the whole thing
is there -- see the corresponding script's `--help` and this repo's CHANGELOG.md for exactly
what v0.1.0 actually does.

| ID | Area | Title | Status | Issue |
|----|------|-------|--------|-------|
| RM-001 | inspect | 非マニフォールド・自己交差・裏返し法線の検出と修正提案 | done | #4 |
| RM-002 | inspect | スケール異常検出（cm/m混在、単位未設定）と`--fix-scale` | done | #5 |
| RM-003 | inspect | 原点ずれ検出と`--origin center\|bottom\|keep` | done | #6 |
| RM-004 | inspect | テクスチャ欠損・パス切れ・過大解像度の一覧 | done | |
| RM-005 | inspect | マテリアル未割当・空スロット検出 | done | |
| RM-006 | inspect | UVの重なり・範囲外・未展開検出 | done | #7 |
| RM-007 | inspect | 頂点カラー/カスタム属性の一覧 | done | #8 |
| RM-008 | inspect | アニメーションクリップ一覧（長さ・fps・ボーン数・ルートモーション有無） | done | #9 |
| RM-009 | inspect | ボーン階層と重み未割当頂点の検出 | planned | #10 |
| RM-010 | inspect | LOD候補の自動提案（三角数から推奨比率） | planned | #11 |
| RM-011 | inspect | ポリカウント予算チェック（ターゲット別） | done | |
| RM-012 | inspect | ドローコール見積り（マテリアル数×オブジェクト数） | planned | #12 |
| RM-013 | inspect | 透明マテリアルの誤設定検出（alpha blend vs clip） | planned | #13 |
| RM-014 | inspect | glTF拡張の使用一覧（KHR_*）と対応表 | planned | #14 |
| RM-015 | inspect | 重複メッシュ/インスタンス化可能なオブジェクト検出 | planned | #15 |
| RM-016 | convert | USD/USDZ 出力の iOS Quick Look 互換性検証 | planned | #16 |
| RM-017 | convert | FBX→glTF のアニメ保持の再import検証を標準化 | done | |
| RM-018 | convert | OBJ/MTL→PBR 変換のヒューリスティック | planned | #17 |
| RM-019 | convert | STL 出力の3Dプリント向け（単位・マニフォールド・壁厚） | planned | #18 |
| RM-020 | convert | Alembic 入出力 | done | |
| RM-021 | convert | PLY 点群の入出力と間引き | planned | #19 |
| RM-022 | convert | Draco 圧縮（Blender glTFエクスポーターの設定経由） | done | |
| RM-023 | convert | KTX2/Basis テクスチャ（toktxがあれば使う、無ければ案内） | done | |
| RM-024 | convert | WebP テクスチャ変換 | planned | #20 |
| RM-025 | convert | .blend → .blend のバージョン変換（古い.blendを4.x/5.xで再保存） | done | |
| RM-026 | convert | 複数ファイル→1つのglbへマージ | done | |
| RM-027 | convert | 1つのglb→オブジェクト単位に分割 | planned | #21 |
| RM-028 | convert | アニメーションだけを別ファイルに分離/結合（Mixamo系ワークフロー） | planned | #22 |
| RM-029 | convert | 座標系変換（Y-up/Z-up、左手/右手）明示指定 | planned | #23 |
| RM-030 | convert | カラースペース変換（sRGB/Linear/ACES）の明示 | planned | #24 |
| RM-031 | optimize | 目標三角数指定でLOD0-3を一括生成 | planned | #25 |
| RM-032 | optimize | テクスチャアトラス化（複数マテリアル→1枚） | done | |
| RM-033 | optimize | テクスチャ解像度の自動決定（画面占有率ベース） | planned | #26 |
| RM-034 | optimize | 未使用ボーン削除・ボーン数上限（モバイル向け） | planned | #27 |
| RM-035 | optimize | アニメーションキーフレーム間引き（許容誤差指定） | planned | #28 |
| RM-036 | optimize | シェイプキー削減 | planned | #29 |
| RM-037 | optimize | インスタンス化（同一メッシュを共有） | planned | #30 |
| RM-038 | optimize | マテリアル統合（同一パラメータをマージ） | planned | #31 |
| RM-039 | optimize | `--target-*` プリセット拡充：sketchfab, vrchat, roblox, gltf-viewer, quicklook | planned | #32 |
| RM-040 | optimize | 最適化前後の視覚差分スコア（レンダ画像のPSNR/SSIMを純Pythonで） | planned | #33 |
| RM-041 | optimize | メッシュのクリーンアップ（孤立頂点・退化面・ゼロ面積） | planned | #34 |
| RM-042 | optimize | 頂点順序/キャッシュ最適化のレポート | planned | #35 |
| RM-043 | optimize | ミップマップ用テクスチャ事前生成 | planned | #36 |
| RM-044 | optimize | 法線マップの形式検出と変換（OpenGL/DirectX） | planned | #37 |
| RM-045 | optimize | 予算超過時の自動段階最適化（目標内に収まるまで） | planned | #38 |
| RM-046 | render | HDRIプリセット（同梱の小さいCC0 HDRI 2〜3枚） | planned | #39 |
| RM-047 | render | 360°ターンテーブルの回転方向・角度・ループ長指定 | planned | #40 |
| RM-048 | render | 製品撮影プリセット（白背景・影あり・パッケージ用） | planned | #41 |
| RM-049 | render | ワイヤーフレーム/クレイ/マットキャップ/UVチェッカーのレンダ | planned | #42 |
| RM-050 | render | 透明背景PNG連番 | done | |
| RM-051 | render | 4方向/8方向スプライト出力（2Dゲーム用） | planned | #43 |
| RM-052 | render | アイソメトリックカメラプリセット | planned | #44 |
| RM-053 | render | カメラの自動フレーミング（バウンディングボックスから） | done | |
| RM-054 | render | アニメーションのプレビュー動画（クリップ指定） | planned | #45 |
| RM-055 | render | 複数アセットのコンタクトシート（ライブラリ一覧用） | planned | #46 |
| RM-056 | render | Cycles GPU検出とフォールバック | planned | #47 |
| RM-057 | render | レンダ時間の事前見積り（`--estimate`） | planned | #48 |
| RM-058 | render | 解像度プリセット（thumbnail/social/4k） | planned | #49 |
| RM-059 | render | Freestyle線画レンダ | planned | #50 |
| RM-060 | render | デプス/ノーマル/IDパスの出力（ML/合成用） | planned | #51 |
| RM-061 | bake | Combined/Diffuse/Normal/AO/Roughness/Metallic/Emission の個別・一括 | planned | #52 |
| RM-062 | bake | ハイポリ→ローポリの法線ベイク（ケージ設定） | planned | #53 |
| RM-063 | bake | ライトマップUV生成とベイク | planned | #54 |
| RM-064 | bake | 頂点カラーへのAOベイク | planned | #55 |
| RM-065 | bake | ベイク結果の自動マテリアル差し替え（プロシージャル→テクスチャ化） | planned | #56 |
| RM-066 | scene | `scene.json` でのグリッド配置・円形配置・ランダム散布 | planned | #57 |
| RM-067 | scene | ライトリグの名前付きプリセットと保存 | planned | #58 |
| RM-068 | scene | 複数アセットの比較シーン（同スケール横並び） | done | |
| RM-069 | scene | 背景・床・環境の切替 | planned | #59 |
| RM-070 | scene | テキスト3D（ロゴ・ラベル）の追加 | planned | #60 |
| RM-071 | scene | 寸法線・スケールバーの自動挿入 | planned | #61 |
| RM-072 | agent-ux | `--explain`：実行したbpy操作を人間向けに要約 | planned | #62 |
| RM-073 | agent-ux | 全スクリプトの`--json`出力にスキーマを定義しSKILL.mdから参照 | planned | #63 |
| RM-074 | agent-ux | エラーメッセージに「次に試すコマンド」を必ず添える | planned | #64 |
| RM-075 | agent-ux | 巨大ファイルの事前警告（三角数/テクスチャ総量）と`--fast`推奨 | planned | #65 |
| RM-076 | agent-ux | 操作の巻き戻し（各出力に`.provenance.json`を残し再現可能に） | planned | #66 |
| RM-077 | agent-ux | 結果のHTMLレポート（ffmpeg-skillのreport.py相当） | planned | #67 |
| RM-078 | agent-ux | ffmpeg-skill との連携：ターンテーブル→ffmpeg-skillで最終書き出し | planned | #68 |
| RM-079 | agent-ux | 進捗のETA表示（Blenderのフレーム進捗をパース） | planned | #69 |
| RM-080 | agent-ux | `--ask`で曖昧な選択肢を質問形式で出力（エージェントがユーザーに転送） | planned | #70 |
| RM-081 | ecosystem | Blender公式MCPサーバーとの併用ガイド（headlessで検証→ライブで微調整） | planned | #71 |
| RM-082 | ecosystem | gltf-transform が入っていれば併用（Draco/meshopt/textureCompress） | planned | #72 |
| RM-083 | ecosystem | three.js 用のプレビューHTML生成（model-viewer / three.js最小ビューア） | planned | #73 |
| RM-084 | ecosystem | Unity/Unreal/Godot のimport設定JSON生成 | planned | #74 |
| RM-085 | ecosystem | Sketchfab / Poly Haven からのCC0ダウンロードスクリプト（テスト用） | planned | #75 |
| RM-086 | ecosystem | VS Code タスク定義の生成 | planned | #76 |
| RM-087 | ecosystem | GitHub Actions テンプレ（PRで自動サムネ・自動チェック） | planned | #77 |
| RM-088 | ecosystem | Docker イメージ（blender headless入り） | planned | #78 |
| RM-089 | ecosystem | pre-commit フック（3Dアセットの`check.py`） | planned | #79 |
| RM-090 | ecosystem | Claude Code plugin marketplace / awesome-* リストへの登録手順 | planned | #80 |
| RM-091 | reliability | 各Blenderバージョンでの機能マトリクス自動生成 | planned | #81 |
| RM-092 | reliability | タイムアウトとメモリ上限（`--timeout`, `--max-memory`） | planned | #82 |
| RM-093 | reliability | クラッシュ時のBlenderログ収集と再現手順出力 | planned | #83 |
| RM-094 | reliability | 決定論（seed固定、レンダ結果の再現性テスト） | planned | #84 |
| RM-095 | reliability | 大規模コーパス（100ファイル）での夜間CI | planned | #85 |
| RM-096 | reliability | bpy非推奨API使用の自動検出（`_compat.py`の網羅性テスト） | planned | #86 |
| RM-097 | reliability | Windowsパス・日本語パス・スペース入りパスのテスト | planned | #87 |
| RM-098 | reliability | 同時実行（複数Blenderプロセス）のロックとキャッシュ整合 | planned | #88 |
| RM-099 | reliability | `verify.py --self-test`（同梱サンプルだけで完結する健全性チェック） | planned | #89 |
| RM-100 | reliability | リリース前チェックリストの自動実行（`make release-check`） | planned | #90 |
