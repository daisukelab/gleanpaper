# gleampaper — 仕様書

> 論文スクリーニング＆要約支援ツール
> 最終更新: 2026-02-19

---

## 概要

学術論文プリプリントサーバー（arXiv、将来的に IEEE Xplore 等）から毎日新着論文を取得し、
ユーザーが定義した興味リストに基づいてスクリーニングする。
スクリーニング結果を人間がタグを付けてレビューし、タグ付きの論文を LLM で要約する、
2ステージ構成のヒューマンインザループ型ツール。

**タグが選択の基準であり、整理の軸でもある。**
タグを付けること = 要約対象として選ぶこと、かつ後から検索・フィルタリングするためのメタデータになる。

---

## アーキテクチャ

```
interests.yaml（トピック定義＋タグ定義）
      │
      ▼
┌─────────────────────┐
│   stage1_screen.py  │  ← Stage 1: スクリーニング
└─────────────────────┘
      │
      ├── screened/YYYY-MM-DD.json      （生データ・機械用）
      └── review/YYYY-MM-DD.md          （レビューファイル・人間用）
                                               │
                              [人間がタグを書き込む → タグ付き = 要約対象]
                                               │
                               ┌───────────────────────────┐
                               │   stage2_summarize.py     │  ← Stage 2: 要約（将来実装）
                               └───────────────────────────┘
                                               │
                                  digest/YYYY-MM/SOURCE_ID.md
                                  （論文1件につき1ファイル）
```

---

## ディレクトリ構成

```
gleampaper/
├── docs/
│   └── spec.md                    # 本仕様書
├── config/
│   ├── interests.yaml             # 興味リスト・タグ定義（ユーザーが随時編集）
│   └── summarize.yaml             # Stage 2 LLM 設定（将来実装）
├── screened/                      # Stage 1 生データ出力
│   └── YYYY-MM-DD.json
├── review/                        # 人間レビュー用 Markdown
│   └── YYYY-MM-DD.md
├── digest/                        # Stage 2 要約出力（論文ごとに 1 ファイル）
│   └── YYYY-MM/
│       ├── arxiv_2602.12345.md
│       └── arxiv_2602.23456.md
├── stage1_screen.py               # Stage 1 スクリプト
├── stage2_summarize.py            # Stage 2 スクリプト（将来実装）
├── requirements.txt
└── .gitignore
```

> `screened/`, `review/`, `digest/` 以下の日次ファイルはローカル保存のみ（`.gitignore` 対象）。
> プログラム本体とコンフィグテンプレートのみ GitHub で管理する。

---

## interests.yaml 仕様

### 設計方針

- キーワードは **トピック単位でグループ化**し、重みはトピックレベルで管理
- 各トピックに **`tag`** を定義する。このタグが論文の分類軸になる
- `enabled: false` で削除せず一時的に無効化
- 将来的に複数ファイルに分割して `include:` で読み込む拡張を予定

### フォーマット

```yaml
version: 1

# ── 取得設定 ──────────────────────────────────────
fetch:
  categories:
    primary:    [cs.LG, cs.CL, cs.AI]   # 重点カテゴリ（ボーナス 1.0）
    secondary:  [cs.CV, stat.ML]         # 補助カテゴリ（ボーナス 0.7）
  days_back: 1       # 何日前まで遡るか（通常 1）
  max_results: 500   # API 1回あたりの最大取得件数

# ── スクリーニング設定 ────────────────────────────
screening:
  min_score: 5       # この未満は捨てる
  top_n: 50          # 最大保存件数

# ── 除外キーワード ────────────────────────────────
exclude_keywords:
  - "survey"
  - "tutorial"

# ── トピック定義 ──────────────────────────────────
# tag: レビューファイルで提案され、digest ファイルのフロントマターに書き込まれる
topics:
  - name: "Large Language Models"
    tag: "llm"                     # ← タグ（短く・機械可読な文字列）
    weight: 10
    enabled: true
    keywords:
      - "large language model"
      - "LLM"
      - "instruction tuning"
      - "RLHF"
      - "chain of thought"
      - "in-context learning"
      - "few-shot"

  - name: "Agents & Planning"
    tag: "agents"
    weight: 9
    enabled: true
    keywords:
      - "agent"
      - "tool use"
      - "autonomous"
      - "planning"
      - "reasoning"

  - name: "Diffusion Models"
    tag: "diffusion"
    weight: 7
    enabled: true
    keywords:
      - "diffusion model"
      - "score matching"
      - "denoising"
      - "DDPM"

  # enabled: false で一時停止（削除不要）
  - name: "Quantum Computing"
    tag: "quantum"
    weight: 6
    enabled: false
    keywords:
      - "quantum circuit"
      - "qubit"
```

---

## Stage 1: スクリーニング（`stage1_screen.py`）

### 入力

| 入力 | 説明 |
|------|------|
| `config/interests.yaml` | 監視カテゴリ・キーワード・タグ・スコア閾値 |
| arXiv API | 新着論文（平日のみ取得） |

### 処理フロー

1. `interests.yaml` を読み込む
2. 指定カテゴリの新着論文を arXiv API から取得
3. 各論文のタイトル＋アブストラクトに対してキーワードマッチング
4. スコアを計算（後述）
5. `min_score` 未満を除外、上位 `top_n` 件に絞り込む
6. `screened/YYYY-MM-DD.json` に生データを保存
7. `review/YYYY-MM-DD.md` にレビューファイルを生成

### スコアリング

```
score = Σ( マッチしたキーワードが属するトピックの weight × マッチ数 )
        × カテゴリボーナス ( primary: 1.0 / secondary: 0.7 )
```

- タイトルマッチはアブストラクトマッチの 2 倍の重みを付与

### 出力 1: `screened/YYYY-MM-DD.json`

機械可読な生データ。Stage 2 が参照する。

```json
{
  "date": "2026-02-19",
  "fetched_count": 412,
  "screened_count": 43,
  "papers": [
    {
      "source": "arxiv",
      "source_id": "2602.12345",
      "title": "Improving Chain-of-Thought Reasoning with RL",
      "authors": ["Smith, J.", "Lee, K."],
      "abstract": "We propose a method that...",
      "categories": ["cs.LG", "cs.AI"],
      "url": "https://arxiv.org/abs/2602.12345",
      "pdf_url": "https://arxiv.org/pdf/2602.12345",
      "score": 27.0,
      "matched_topics": [
        {
          "topic": "Large Language Models",
          "tag": "llm",
          "weight": 10,
          "matched_keywords": ["LLM", "instruction tuning", "chain of thought"]
        },
        {
          "topic": "Agents & Planning",
          "tag": "agents",
          "weight": 9,
          "matched_keywords": ["agent", "reasoning"]
        }
      ]
    }
  ]
}
```

### 出力 2: `review/YYYY-MM-DD.md`

人間がレビューするための Markdown ファイル。VSCode で開き、各論文の `tags:` 行にタグを書き込む。
**タグが書かれた論文が Stage 2 の要約対象となる。空欄 = スキップ。**

提案タグは `matched_topics` から自動生成。そのまま使っても、編集・削除・追加してもよい。

```markdown
# arXiv Review — 2026-02-19
> 取得: 412件 → スクリーニング: 43件
> `tags:` にタグを書いた論文が要約されます → `python stage2_summarize.py`
> タグ例: llm, agents, diffusion, quantum

---

<!-- source: arxiv | id: 2602.12345 | score: 27.0 -->
### Improving Chain-of-Thought Reasoning with RL
Smith, J., Lee, K. | cs.LG, cs.AI | [arxiv](https://arxiv.org/abs/2602.12345) | [PDF](https://arxiv.org/pdf/2602.12345)
`suggested: llm, agents`
> We propose a method that combines RL with CoT prompting. Our approach achieves
> 15% improvement on reasoning benchmarks by assigning step-level rewards...

tags:

---

<!-- source: arxiv | id: 2602.23456 | score: 23.5 -->
### Fast Diffusion Sampling via Adaptive Step Size Control
Jones, A. | cs.CV | [arxiv](https://arxiv.org/abs/2602.23456)
`suggested: diffusion`
> Existing diffusion samplers require hundreds of NFE. We propose an adaptive
> step size controller that reduces sampling steps by 60%...

tags:

---
```

**フォーマット仕様：**
- スコア降順で並べる
- `suggested:` は matched_topics の tag を列挙（参考表示）
- `tags:` 行はユーザーが編集する欄。カンマ区切りで複数タグ可
- アブストラクトは冒頭 2〜3 文のみ（全文は JSON に保存）
- `<!-- source: ... | id: ... -->` コメントに識別子を埋め込み（Stage 2 が参照）
- `[PDF]` リンクを arXiv abstract リンクの隣に配置（クリックでブラウザ表示）

**VSCode でのレビュー推奨レイアウト：**

```
┌──────────────────────┬──────────────────────┐
│  review/**.md        │  Markdown プレビュー  │
│  （タグを記入）      │  （リンクをクリック） │
│                      │                      │
│  tags: audio-repr    │  [arxiv] [PDF] ←クリック
└──────────────────────┴──────────────────────┘
```

プレビューは `Cmd+Shift+V` で開く（または右上の Preview アイコン）。
編集中に `Cmd+Click` でもリンクを直接開ける。

**VSCode での操作イメージ：**

```markdown
tags: llm, agents    ← タグを書く（要約対象）
tags:                ← 空欄のまま（スキップ）
tags: llm            ← suggested から一部だけ選ぶ
tags: llm, mynewtag  ← 独自タグを追加することも可
```

---

## Stage 2: 要約（`stage2_summarize.py`）

### 入力

| 入力 | 説明 |
|------|------|
| `review/YYYY-MM-DD.md` | `tags:` が記入された論文の ID とタグを抽出 |
| `screened/YYYY-MM-DD.json` | 該当 ID のフル情報（アブストラクト等）を取得 |
| `config/summarize.yaml` | 使用モデル・top_n 設定 |

### 処理フロー

1. `review/YYYY-MM-DD.md` から `tags:` が空でない論文を抽出（ID・タグ一覧を取得）
2. `screened/YYYY-MM-DD.json` から該当論文のフルデータを取得
3. スコア上位 `top_n` 件を Claude API に送信し要約生成
4. 論文ごとに `digest/YYYY-MM/SOURCE_ID.md` を生成

### 要約フォーマット（6項目）

各項目は **できるだけ1行**で記述。重要な点が複数ある場合はそれぞれ1行。
利用・言及された既存モデル／データセット／手法は、関連する行の直後に箇条書きで記載。
論文に書かれていないことは類推・補足しない。

| # | 項目 | 内容 |
|---|------|------|
| 1 | **どんなもの？** | 論文の概要と目的 |
| 2 | **先行研究と比べてどこがすごい？** | 新規性・独自性 |
| 3 | **技術や手法の肝はどこ？** | コアアイデア・技術的ポイント |
| 4 | **どうやって有効だと検証した？** | 実験・評価・比較方法 |
| 5 | **議論はある？** | 限界・課題・議論点 |
| 6 | **次に読むべき論文は？** | 論文内で言及された重要な関連研究 |

### 出力: `digest/YYYY-MM/SOURCE_ID.md`（論文 1 件につき 1 ファイル）

YAML フロントマターにメタデータを持たせることで、ビューアや HTML 変換ツールと連携しやすくする。

**ファイル名規則：** `{source}_{source_id}.md`
例: `arxiv_2602.12345.md`、将来的に `ieee_1234567.md`

```markdown
---
title: "Improving Chain-of-Thought Reasoning with RL"
source: arxiv
source_id: "2602.12345"
url: "https://arxiv.org/abs/2602.12345"
authors:
  - "Smith, J."
  - "Lee, K."
date_published: "2026-02-19"
date_gleaned: "2026-02-19"
tags:
  - llm
  - agents
score: 27.0
---

# Improving Chain-of-Thought Reasoning with RL

**Authors**: Smith, J., Lee, K.
**Published**: 2026-02-19
**Tags**: `llm` `agents`
**URL**: https://arxiv.org/abs/2602.12345
**PDF**: https://arxiv.org/pdf/2602.12345

### 1. どんなもの？
CoT プロンプティングに強化学習を組み合わせ、LLM の多段階推論精度を向上させる手法の提案。

### 2. 先行研究と比べてどこがすごい？
ステップ単位の報酬設計により、既存の最終出力ベースの RL より細粒度な誤り訂正が可能。

### 3. 技術や手法の肝はどこ？
各推論ステップに個別の報酬を与え、誤ったステップを直接ペナルティとして学習する。
  - PPO（強化学習アルゴリズム）
  - GPT-4（ベースモデル）

### 4. どうやって有効だと検証した？
GSM8K・MATH・BBH の3ベンチマークで SOTA と比較し、平均 15% の精度向上を確認。
  - GSM8K、MATH、BBH（評価データセット）

### 5. 議論はある？
報酬モデルの品質に性能が依存するため、報酬ハッキングのリスクがある。
ステップ単位のアノテーションコストが高く、大規模適用に課題が残る。

### 6. 次に読むべき論文は？
（アブストラクトからは不明）

## Abstract

We propose a method that combines reinforcement learning with chain-of-thought
prompting to improve multi-step reasoning...
```

**フロントマターを持たせる理由：**

| 用途 | 説明 |
|------|------|
| ビューア連携 | Obsidian 等でタグ・日付によるフィルタリングが可能 |
| HTML 変換 | `pandoc` でメタデータ付き HTML に変換可能 |
| 将来の検索 | フロントマターを読む簡易インデックスを作りやすい |
| 拡張性 | IEEE Xplore 等の別ソースも同じ構造で収容できる |

---

## 実行方法

```bash
# Stage 1: 本日分をスクリーニング
python stage1_screen.py

# 日付を指定して実行
python stage1_screen.py --date 2026-02-18

# 設定確認（キーワード統計・カバレッジ）
python stage1_screen.py --check

# VSCode でレビューファイルを開く
code review/2026-02-19.md

# Stage 2: タグ付き論文を要約（将来実装）
python stage2_summarize.py
python stage2_summarize.py 2026-02-19    # 日付指定
```

---

## スケジューリング（macOS）

`launchd` を使い、平日の指定時刻に自動実行する（設定手順は別途）。

---

## GitHub 管理方針

| 対象 | Git 管理 |
|------|---------|
| `stage1_screen.py` | ✅ 管理対象 |
| `stage2_summarize.py` | ✅ 管理対象 |
| `config/interests.yaml` | ✅ テンプレートとして管理 |
| `config/summarize.yaml` | ✅ テンプレートとして管理 |
| `screened/*.json` | ❌ ローカルのみ |
| `review/*.md` | ❌ ローカルのみ |
| `digest/**/*.md` | ❌ ローカルのみ |

---

## 将来拡張

- **フェーズ 3**: スクリーニング済み論文からキーワードを自動抽出し `interests.yaml` に追記提案
- **IEEE Xplore 対応**: `fetch` に `source` 設定を追加し複数ソースに対応（ファイル名は `ieee_ID.md`）
- **インデックス生成**: `digest/` 以下のフロントマターを読んでタグ別・日付別の一覧 Markdown を自動生成
- **通知連携**: レビューファイル生成後に Slack / メール で通知
