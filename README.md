# gleampaper

**Daily arXiv paper screening and summarization tool with human-in-the-loop tagging.**

---

## Overview

gleampaper helps you stay on top of the latest research without being overwhelmed.
It fetches new arXiv preprints every day, scores them against your personal interest list,
and generates a review file where you tag the papers you actually care about.
Tagged papers are then sent to an LLM (Claude API) for focused summarization.

The workflow is intentionally two-stage and human-in-the-loop:

```
arXiv API
    │
    ▼
[Stage 1] Keyword-based screening  →  review/YYYY-MM-DD.md
                                            │
                                   You tag the papers
                                            │
                                            ▼
[Stage 2] LLM summarization        →  digest/YYYY-MM/paper_id.md
```

---

## What You Get

- **Daily review file** — A Markdown file listing screened papers with scores, suggested tags,
  abstract previews, and one-click links to the arXiv page and PDF.
  Open it in VSCode (split editor + preview) and tag papers in minutes.

- **Focused summaries** — Each tagged paper gets its own Markdown file with a YAML front matter
  (title, authors, date, tags, score) and a structured LLM-generated summary:
  main contribution, technical novelty, and relevance to your interests.
  Files are ready for Obsidian, pandoc HTML conversion, or any Markdown viewer.

- **Your own interest list** — `config/interests.yaml` lets you define topics with keywords
  and weights at your own pace. Topics can be enabled/disabled without deleting keywords.
  A general-purpose template is provided in `config/examples/`.

---

## Setup

### 1. Create your `interests.yaml`

`config/interests.yaml` is **not included** in this repository (it is personal and gitignored).
You must create it before running the tool.

The easiest way is to copy the provided example and edit it to match your interests:

```bash
cp config/examples/interests.yaml.example config/interests.yaml
```

Then open `config/interests.yaml` and customize:

- **`fetch.categories`** — arXiv categories to monitor (e.g. `cs.LG`, `eess.AS`)
- **`topics`** — define your research areas, each with a `tag`, `weight`, and `keywords` list
- **`screening.min_score`** / **`top_n`** — control how strictly papers are filtered

The example file (`config/examples/interests.yaml.example`) covers general ML/AI topics
(LLMs, agents, diffusion models, alignment, etc.) and serves as a starting point.
You can add as many topics and keywords as you like — topics can be temporarily disabled
with `enabled: false` without losing your keyword list.

```bash
# Verify your config is valid and see keyword statistics
python stage1_screen.py --check
```

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Create and edit your personal interest list (see Setup above)
cp config/examples/interests.yaml.example config/interests.yaml

# Fetch and screen today's papers
python stage1_screen.py

# Open the review file in VSCode (Cmd+Shift+V to open preview pane)
code review/$(date +%Y-%m-%d).md

# After tagging, run summarization (Stage 2 — coming soon)
python stage2_summarize.py
```

---

## Repository Structure

```
gleampaper/
├── stage1_screen.py          # Stage 1: fetch and screen arXiv papers
├── stage2_summarize.py       # Stage 2: LLM summarization (coming soon)
├── config/
│   ├── interests.yaml        # Your personal interest list
│   └── examples/
│       └── interests.yaml.example   # General-purpose template
├── docs/
│   └── spec.md               # Full specification (Japanese)
├── screened/                 # Stage 1 raw output — local only
├── review/                   # Human review files — local only
└── digest/                   # Stage 2 summaries — local only
```

> **Note (日本語):** 詳細な仕様書は [`docs/spec.md`](docs/spec.md) にあります。申し訳ありませんが、日本語で記述されています。

---

## Roadmap

- [x] Stage 1: arXiv screening with keyword/topic matching
- [x] Human-in-the-loop tagging via Markdown review file
- [ ] Stage 2: LLM summarization (Claude API)
- [ ] IEEE Xplore support
- [ ] Keyword extraction from tagged papers (auto-update interests.yaml)
- [ ] macOS launchd scheduling setup

---

## License

MIT
