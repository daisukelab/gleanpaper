#!/usr/bin/env python3
"""
gleampaper — Stage 2: Summarize

Reads tagged papers from review/YYYY-MM-DD.md,
fetches their data from screened/YYYY-MM-DD.json,
and generates per-paper summaries using Claude API.

Output: digest/YYYY-MM/SOURCE_ID.md  (one file per paper, with YAML frontmatter)

Usage:
  python stage2_summarize.py                    # latest review file
  python stage2_summarize.py --date 2026-02-19
  python stage2_summarize.py --dry-run          # show tagged papers, no API call
  python stage2_summarize.py --force            # re-summarize even if digest exists

Requires:
  ANTHROPIC_API_KEY environment variable (or .env file)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import anthropic
import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
SUMMARIZE_CONFIG_PATH = BASE_DIR / "config" / "summarize.yaml"
SCREENED_DIR = BASE_DIR / "screened"
REVIEW_DIR = BASE_DIR / "review"
DIGEST_DIR = BASE_DIR / "digest"

DEFAULT_CONFIG = {
    "model": "claude-sonnet-4-6",
    "max_tokens": 1500,
    "top_n": 20,
}

# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if SUMMARIZE_CONFIG_PATH.exists():
        with open(SUMMARIZE_CONFIG_PATH, encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
            cfg.update({k: v for k, v in user_cfg.items() if v is not None})
    return cfg


# ── Parse review file ─────────────────────────────────────────────────────────

def parse_tagged_papers(review_path: Path) -> list:
    """
    Returns list of dicts: {source, source_id, tags}
    Only papers with non-empty tags: line are included.
    """
    if not review_path.exists():
        sys.exit(f"[error] Review file not found: {review_path}")

    papers = []
    current = {}

    with open(review_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            m = re.search(r"<!--\s*source:\s*(\w+)\s*\|\s*id:\s*([\w.]+)", line)
            if m:
                current = {"source": m.group(1), "source_id": m.group(2)}
                continue
            if current and re.match(r"^tags:\s*\S", line):
                tags_str = line[len("tags:"):].strip()
                # Strip any trailing annotation (e.g., "  ← restored")
                tags_str = re.sub(r"\s*←.*$", "", tags_str).strip()
                current["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]
                papers.append(current)
                current = {}

    return papers


# ── Load screened JSON ────────────────────────────────────────────────────────

def load_screened(target_date: date) -> dict:
    """Returns {source_id: paper_dict}."""
    path = SCREENED_DIR / f"{target_date}.json"
    if not path.exists():
        sys.exit(f"[error] Screened file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {p["source_id"]: p for p in data["papers"]}


# ── Prompt ────────────────────────────────────────────────────────────────────

PROMPT_TEMPLATE = """\
以下の論文を、指定された6項目でまとめてください。

## 注意事項
- この論文に書いてある内容のみを記述してください。論文に記載のない情報を類推・推測して付け加えることは厳禁です。
- 各項目は、できるだけ1行で簡潔に記述してください。
- 重要な点が複数ある場合は、それぞれ1行で列挙してください。
- その項目で利用・言及された既存モデル・データセット・手法は、関連する行の直後に「  - 名称」の形式で箇条書きで記載してください。
- 情報が不十分で答えられない項目は「（アブストラクトからは不明）」と記してください。

## 論文情報
タイトル: {title}
著者: {authors}
関連トピック: {tags}

## アブストラクト
{abstract}

---

日本語で以下の形式で出力してください（見出し行はそのままコピーして使用）：

### 1. どんなもの？

### 2. 先行研究と比べてどこがすごい？

### 3. 技術や手法の肝はどこ？

### 4. どうやって有効だと検証した？

### 5. 議論はある？

### 6. 次に読むべき論文は？
"""


def build_prompt(paper: dict, tags: list) -> str:
    return PROMPT_TEMPLATE.format(
        title=paper["title"],
        authors=", ".join(paper["authors"]),
        tags=", ".join(tags),
        abstract=paper["abstract"],
    )


# ── Summarize via Claude API ──────────────────────────────────────────────────

def summarize(paper: dict, tags: list, cfg: dict, client: anthropic.Anthropic) -> str:
    prompt = build_prompt(paper, tags)
    message = client.messages.create(
        model=cfg["model"],
        max_tokens=cfg["max_tokens"],
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


# ── Write digest file ─────────────────────────────────────────────────────────

def title_slug(title: str, max_len: int = 40) -> str:
    """Return a filesystem-safe slug from the title prefix (before first ':')."""
    prefix = title.split(":")[0].strip() if ":" in title else title.strip()
    slug = re.sub(r"[^\w\s-]", "", prefix)       # remove special chars
    slug = re.sub(r"\s+", "-", slug.strip())      # spaces → hyphens
    slug = re.sub(r"-{2,}", "-", slug)            # collapse multiple hyphens
    return slug[:max_len].rstrip("-")


def digest_filename(paper: dict) -> str:
    slug = title_slug(paper["title"])
    return f"{paper['source']}_{paper['source_id']}_{slug}.md"


def write_digest(paper: dict, tags: list, summary: str, gleaned_date: date) -> Path:
    month_dir = DIGEST_DIR / gleaned_date.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)

    filename = digest_filename(paper)
    path = month_dir / filename

    authors_yaml = "\n".join(f'  - "{a}"' for a in paper["authors"])
    tags_yaml = "\n".join(f"  - {t}" for t in tags)
    tags_inline = " ".join(f"`{t}`" for t in tags)
    pdf_line = f"**PDF**: {paper['pdf_url']}  " if paper.get("pdf_url") else ""

    content = f"""\
---
title: "{paper['title'].replace('"', "'")}"
source: {paper['source']}
source_id: "{paper['source_id']}"
url: "{paper['url']}"
authors:
{authors_yaml}
date_published: "{paper.get('date_published', str(gleaned_date))}"
date_gleaned: "{gleaned_date}"
tags:
{tags_yaml}
score: {paper['score']}
---

# {paper['title']}

**Authors**: {', '.join(paper['authors'])}
**Published**: {paper.get('date_published', str(gleaned_date))}
**Tags**: {tags_inline}
**URL**: {paper['url']}
{pdf_line}

{summary}

## Abstract

{paper['abstract']}
"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ── Helpers ───────────────────────────────────────────────────────────────────

def find_latest_review() -> Path:
    files = sorted(REVIEW_DIR.glob("*.md"), reverse=True)
    if not files:
        sys.exit("[error] No review files found in review/")
    return files[0]


def digest_path_for(paper: dict, target_date: date) -> Path:
    return DIGEST_DIR / target_date.strftime("%Y-%m") / digest_filename(paper)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="gleampaper Stage 2 — summarize tagged papers via Claude API"
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="Review date to process (default: latest review file)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show tagged papers without calling the API",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-summarize even if digest file already exists",
    )
    args = parser.parse_args()

    cfg = load_config()

    # Determine review file and date
    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            sys.exit(f"[error] Invalid date: {args.date}. Use YYYY-MM-DD.")
        review_path = REVIEW_DIR / f"{target_date}.md"
    else:
        review_path = find_latest_review()
        target_date = date.fromisoformat(review_path.stem)

    print(f"gleampaper Stage 2 — {target_date}")
    print("─" * 52)

    # Parse tagged papers from review file
    tagged = parse_tagged_papers(review_path)
    if not tagged:
        print("[info] No tagged papers found.")
        print(f"       Open {review_path} and add tags to papers you want summarized.")
        return

    # Load screened data and merge
    screened = load_screened(target_date)
    candidates = []
    for t in tagged:
        paper = screened.get(t["source_id"])
        if paper:
            candidates.append((t, paper))
        else:
            print(f"[warn] {t['source_id']} not found in screened JSON, skipping.")

    # Sort by score, apply top_n
    candidates.sort(key=lambda x: x[1]["score"], reverse=True)
    candidates = candidates[: cfg["top_n"]]

    # Show plan
    print(f"\n  Tagged  : {len(candidates)} papers")
    print(f"  Model   : {cfg['model']}")
    print(f"  top_n   : {cfg['top_n']}")
    print()
    for t, p in candidates:
        dp = digest_path_for(p, target_date)
        status = "exists" if dp.exists() else "new"
        skip_mark = " (skip)" if dp.exists() and not args.force else ""
        print(f"  [{status}]{skip_mark} [{p['score']:5.1f}] {p['title'][:55]}...")
        print(f"           tags: {', '.join(t['tags'])}")

    if args.dry_run:
        print("\n[dry-run] No API calls made.")
        return

    # Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit(
            "\n[error] ANTHROPIC_API_KEY is not set.\n"
            "        export ANTHROPIC_API_KEY=sk-ant-...\n"
            "        or add it to a .env file in this directory."
        )

    client = anthropic.Anthropic(api_key=api_key)

    print()
    saved = []
    skipped = 0
    for i, (t, paper) in enumerate(candidates, 1):
        dp = digest_path_for(paper, target_date)

        if dp.exists() and not args.force:
            skipped += 1
            continue

        print(f"[{i}/{len(candidates)}] {paper['title'][:60]}...")
        try:
            summary = summarize(paper, t["tags"], cfg, client)
            path = write_digest(paper, t["tags"], summary, target_date)
            saved.append(path)
            print(f"       → {path}")
        except Exception as e:
            print(f"[error] {paper['source_id']}: {e}")

        if i < len(candidates):
            time.sleep(1)  # avoid rate limiting

    print("\n" + "─" * 52)
    print(f"Done.  saved: {len(saved)}  skipped: {skipped}")
    if saved:
        print(f"Digest files: digest/{target_date.strftime('%Y-%m')}/")


if __name__ == "__main__":
    main()
