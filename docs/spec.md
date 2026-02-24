# gleanpaper — 仕様書

> 論文スクリーニング＆要約支援ツール
> 最終更新: 2026-02-24（snap.py 正式化・仕様全面整合）

---

## 概要

学術論文プリプリントサーバー（arXiv・OpenReview・IEEE Xplore）から新着論文を取得し、
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
      ├─────────────────────────────────────────────────────┐
      ▼                                                     ▼
┌─────────────────────┐                        ┌─────────────────────┐
│   1_screen.py  │  ← Stage 1: スクリーニング  │      snap.py        │  ← 単発取得・即時要約
└─────────────────────┘                        └─────────────────────┘
      │                                                     │
      ├── screened/YYYY-MM-DD.json      （生データ・機械用）   │
      └── review/YYYY-MM-DD.md          （レビューファイル・人間用）
                                               │             │
                              [人間がタグを書き込む]           │
                                               │             │
                               ┌───────────────────────────┐ │
                               │   2_summarize.py     │ │
                               └───────────────────────────┘ │
                                               │             │
                                  digest/YYYY-MM/{source}_{id}_{slug}.md
                                  （論文1件につき1ファイル）
```

---

## ディレクトリ構成

```
gleanpaper/
├── docs/
│   └── spec.md                    # 本仕様書
├── config/
│   ├── interests.yaml             # 興味リスト・タグ定義（ユーザーが随時編集）
│   └── summarize.yaml             # Stage 2 LLM 設定
├── screened/                      # Stage 1 生データ（未処理・レビュー中）
│   └── YYYY-MM-DD.json
├── review/                        # 人間レビュー用 Markdown（未処理・タグ記入中）
│   └── YYYY-MM-DD.md
├── archive/                       # Stage 2 処理済みファイルの移動先
│   ├── screened/
│   │   └── YYYY-MM-DD.json
│   └── review/
│       └── YYYY-MM-DD.md
├── digest/                        # Stage 2 / snap 要約出力（論文ごとに 1 ファイル）
│   └── YYYY-MM/
│       └── arxiv_2602.12345_improving-chain-of-thought.md
├── 1_screen.py               # Stage 1 スクリプト
├── 2_summarize.py            # Stage 2 スクリプト
├── snap.py                        # 単発取得・即時要約スクリプト
├── requirements.txt
└── .gitignore
```

> `screened/`, `review/`, `archive/`, `digest/` 以下の日次ファイルはローカル保存のみ（`.gitignore` 対象）。
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

## Stage 1: スクリーニング（`1_screen.py`）

### 入力

| 入力 | 説明 |
|------|------|
| `config/interests.yaml` | 監視カテゴリ・キーワード・タグ・スコア閾値 |
| arXiv API | 新着論文 |

### 動作モード

#### インクリメンタルモード（引数なし・デフォルト）

```
python 1_screen.py
```

1. `screened/` と `archive/screened/` から最終取得日を自動検出
2. 翌日〜UTC 今日の範囲を一括 fetch（API 呼び出し 1 回）
3. `result.published.date()` で日付ごとに分割・スクリーニング
4. 結果のある日付だけファイルを生成（空白日・インデックスラグは自動スキップ）

arXiv の検索インデックスラグや週末の空白日を意識せず運用できる。

#### 単日モード（日付指定 / --rescreen / --force）

```
python 1_screen.py 2026-02-18          # 指定日を取得
python 1_screen.py -re 2026-02-18      # 再スクリーニング（既存タグ保持）
python 1_screen.py -re                 # 最新レビューファイルを再スクリーニング
python 1_screen.py --force 2026-02-18  # 既存ファイルを上書き
```

- `effective_days_back` により月曜指定時は週末分も含む範囲を取得
- `-re` / `--rescreen` に日付を省略すると `review/` 内の最新ファイルを対象とする
- 既存タグは `← restored` マーカー付きで復元される

### 処理フロー（インクリメンタルモード）

1. `interests.yaml` を読み込む
2. 最終取得日を検出し、翌日〜UTC 今日を arXiv API から一括取得
3. 各論文のタイトル＋アブストラクトに対してキーワードマッチング＆スコア計算
4. `min_score` 未満を除外し、`result.published.date()` で日付ごとに分類
5. 結果のある各日付について `screened/YYYY-MM-DD.json` と `review/YYYY-MM-DD.md` を生成

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
      "date_published": "2026-02-19",
      "score": 27.0,
      "matched_topics": [
        {
          "topic": "Large Language Models",
          "tag": "llm",
          "weight": 10,
          "matched_keywords": ["LLM", "instruction tuning", "chain of thought"]
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
> `tags:` にタグを書いた論文が要約されます → `python 2_summarize.py`
> 利用可能なタグ: llm, agents, diffusion

---

<!-- source: arxiv | id: 2602.12345 | score: 27.0 -->
### Improving Chain-of-Thought Reasoning with RL
Smith, J., Lee, K. | cs.LG, cs.AI | [arxiv](https://arxiv.org/abs/2602.12345) | [PDF](https://arxiv.org/pdf/2602.12345)
`suggested: llm, agents`
> We propose a method that combines RL with CoT prompting. Our approach achieves
> 15% improvement on reasoning benchmarks by assigning step-level rewards...

tags: llm, agents  ← restored

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
- `← restored` は `-re` 実行時に既存タグが復元されたことを示すマーカー（stage2 が除去して読む）
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

**VSCode での操作イメージ：**

```markdown
tags: llm, agents    ← タグを書く（要約対象）
tags:                ← 空欄のまま（スキップ）
tags: llm            ← suggested から一部だけ選ぶ
tags: llm, mynewtag  ← 独自タグを追加することも可
```

---

## Stage 2: 要約（`2_summarize.py`）

### 入力

| 入力 | 説明 |
|------|------|
| `review/YYYY-MM-DD.md` | `tags:` が記入された論文の ID とタグを抽出 |
| `screened/YYYY-MM-DD.json` | 該当 ID のフル情報（アブストラクト等）を取得 |
| `config/summarize.yaml` | 使用モデル・top_n 設定 |

### 処理フロー

1. `review/YYYY-MM-DD.md` から `tags:` が空でない論文を抽出（ID・タグ一覧を取得）
2. `screened/YYYY-MM-DD.json`（なければ `archive/screened/` も参照）から該当論文のフルデータを取得
3. スコア上位 `top_n` 件について PDF をダウンロードし Claude API に送信して要約生成（`--skip-pdf` 時はアブストラクトのみ送信）
4. 論文ごとに `digest/YYYY-MM/{source}_{id}_{slug}.md` を生成
5. 処理完了後、`review/YYYY-MM-DD.md` と `screened/YYYY-MM-DD.json` を `archive/` に移動（`--no-archive` で無効化）

### PDF 全文モード（デフォルト）

`pdf_url` から論文 PDF をダウンロードし、base64 エンコードして Claude API のドキュメント機能で送信する。
アブストラクトのみのモードと比べ、参考文献リストや実験詳細など本文の情報を踏まえた要約が得られる。

- PDF ダウンロードに失敗した場合はアブストラクトのみにフォールバック
- トークン消費が増えるため、コスト・速度が気になる場合は `--skip-pdf` を使用
- `--skip-pdf` 時は答えられない項目に「**（アブストラクトからは不明）**」と注記
- PDF 全文時は「**（論文に記載なし）**」と注記

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

### 出力: `digest/YYYY-MM/{source}_{source_id}_{slug}.md`（論文 1 件につき 1 ファイル）

YAML フロントマターにメタデータを持たせることで、ビューアや HTML 変換ツールと連携しやすくする。

**ファイル名規則：** `{source}_{source_id}_{タイトル先頭のスラッグ}.md`
例: `arxiv_2602.12345_Improving-Chain-of-Thought-Reasoning.md`

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
...
```

> `citation_count` は `snap.py` のみ付与する。`2_summarize.py` はバッチ処理のため
> レート制限リスクを避けて引用数取得を行わない。

**フロントマターを持たせる理由：**

| 用途 | 説明 |
|------|------|
| ビューア連携 | Obsidian 等でタグ・日付によるフィルタリングが可能 |
| HTML 変換 | `pandoc` でメタデータ付き HTML に変換可能 |
| 将来の検索 | フロントマターを読む簡易インデックスを作りやすい |
| 拡張性 | 別ソースも同じ構造で収容できる |

---

## snap.py: 単発取得・即時要約

URL を 1 件指定して即時に取得・要約するツール。バッチではなくアドホックな調査に使う。

### 対応ソース

| ソース | URL 例 |
|--------|--------|
| arXiv | `https://arxiv.org/abs/2602.XXXXX` |
| OpenReview | `https://openreview.net/forum?id=XXXXX` |
| IEEE Xplore | `https://ieeexplore.ieee.org/document/XXXXXXX` |

### 処理フロー

1. URL からソースを判定し、メタデータ（タイトル・著者・アブストラクト）を取得
2. `interests.yaml` のキーワードに照合し、スコア上位トピックのタグを自動付与
3. Google Scholar から引用数を取得（`scholarly` ライブラリ、未インストール時は警告のみ）
4. PDF をダウンロードし Claude API で要約生成（`--skip-pdf` 時はアブストラクトのみ）
5. `digest/YYYY-MM/{source}_{id}_{slug}.md` に保存

### 自動タグ付与

`interests.yaml` の全トピックに対してスコアを計算し、上位 N 件（デフォルト 5）のタグを自動付与する。
`--tags` で手動上書きも可能。

### 出力フォーマット

`2_summarize.py` と同じ 6 項目フォーマット。引用数フィールドが追加される点が異なる。

```markdown
---
title: "..."
source: arxiv
source_id: "2602.12345"
citation_count: 42        # snap.py のみ付与
tags:
  - llm
---

**Citations**: 42
```

`citation_count: null` の場合はヘッダーに「取得不可」と表示。

### CLI

```
python snap.py <URL>                  # PDF全文モード（デフォルト）
python snap.py <URL> --skip-pdf       # アブストラクトのみ
python snap.py <URL> --tags llm,agents  # タグを手動指定
python snap.py <URL> --top 3          # 自動タグ上位 N 件（デフォルト 5）
python snap.py <URL> --force          # digest が既存でも上書き
python snap.py <URL> --dry-run        # API 呼び出しなし（メタデータ表示のみ）
```

---

## 実行方法

```bash
# Stage 1: インクリメンタル（前回取得日の翌日〜今日を自動取得）
python 1_screen.py

# Stage 1: 日付を指定して単日取得
python 1_screen.py 2026-02-18

# Stage 1: 設定確認（キーワード統計・カバレッジ）
python 1_screen.py --check

# Stage 1: 既存ファイルを再スクリーニング（タグ保持）
python 1_screen.py -re               # 最新レビューファイルを対象
python 1_screen.py -re 2026-02-18    # 日付指定

# VSCode でレビューファイルを開く
code review/2026-02-19.md

# Stage 2: タグ付き論文を要約 → 完了後に archive/ へ自動移動
python 2_summarize.py

# Stage 2: アーカイブせず処理のみ（再処理など）
python 2_summarize.py --no-archive

# Stage 2: アブストラクトのみ（トークン節約）
python 2_summarize.py --skip-pdf

# Stage 2: 日付指定
python 2_summarize.py --date 2026-02-18

# snap: URL を指定して1件だけ即時取得・要約
python snap.py https://arxiv.org/abs/2602.XXXXX
python snap.py https://openreview.net/forum?id=XXXXX
python snap.py https://ieeexplore.ieee.org/document/XXXXXXX

# snap: アブストラクトのみ（PDF スキップ）
python snap.py <URL> --skip-pdf

# snap: タグを手動指定
python snap.py <URL> --tags llm,agents

# snap: 自動タグ上位 3 件のみ付与
python snap.py <URL> --top 3

# snap: 内容確認のみ（API 呼び出しなし）
python snap.py <URL> --dry-run
```

---

## スケジューリング（macOS）

`launchd` を使い、平日の指定時刻に自動実行する（設定手順は別途）。

---

## GitHub 管理方針

| 対象 | Git 管理 |
|------|---------|
| `1_screen.py` | ✅ 管理対象 |
| `2_summarize.py` | ✅ 管理対象 |
| `snap.py` | ✅ 管理対象 |
| `config/interests.yaml` | ✅ テンプレートとして管理 |
| `config/summarize.yaml` | ✅ テンプレートとして管理 |
| `screened/*.json` | ❌ ローカルのみ |
| `review/*.md` | ❌ ローカルのみ |
| `archive/**` | ❌ ローカルのみ |
| `digest/**/*.md` | ❌ ローカルのみ |

---

## 将来拡張

- **フェーズ 3**: スクリーニング済み論文からキーワードを自動抽出し `interests.yaml` に追記提案
- **インデックス生成**: `digest/` 以下のフロントマターを読んでタグ別・日付別の一覧 Markdown を自動生成
- **通知連携**: レビューファイル生成後に Slack / メール で通知
