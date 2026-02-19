#!/usr/bin/env python3
"""
gleampaper — Stage 1: Screen

Fetches papers from arXiv and screens them against config/interests.yaml.

Outputs:
  screened/YYYY-MM-DD.json   raw data (machine-readable, for Stage 2)
  review/YYYY-MM-DD.md       human review file (write tags to select papers for Stage 2)

Usage:
  python stage1_screen.py                  # today
  python stage1_screen.py --date 2026-02-18
  python stage1_screen.py --check          # show config statistics
"""

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import arxiv
import yaml

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config" / "interests.yaml"
SCREENED_DIR = BASE_DIR / "screened"
REVIEW_DIR = BASE_DIR / "review"

# Title keyword matches are weighted more heavily than abstract matches
TITLE_WEIGHT_MULTIPLIER = 2


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        sys.exit(
            f"[error] Config file not found: {path}\n"
            f"        Create config/interests.yaml based on the spec."
        )
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Scoring ───────────────────────────────────────────────────────────────────

def score_paper(title: str, abstract: str, categories: list, config: dict) -> tuple:
    """
    Returns (score, matched_topics).
    score is 0.0 and matched_topics is [] if the paper is excluded.

    matched_topics: list of dicts with keys topic, tag, weight, matched_keywords.
    """
    title_lower = title.lower()
    abstract_lower = abstract.replace("\n", " ").lower()

    # Exclude keywords: skip paper entirely if any match
    for excl in config.get("exclude_keywords", []):
        full_text = f"{title_lower} {abstract_lower}"
        if re.search(r"\b" + re.escape(excl.lower()) + r"\b", full_text):
            return 0.0, []

    # Category bonus
    primary_cats = set(config["fetch"]["categories"].get("primary", []))
    secondary_cats = set(config["fetch"]["categories"].get("secondary", []))
    paper_cats = set(categories)

    if paper_cats & primary_cats:
        category_bonus = 1.0
    elif paper_cats & secondary_cats:
        category_bonus = 0.7
    else:
        # Cross-listed paper not in config categories — no penalty
        category_bonus = 1.0

    total_score = 0.0
    matched_topics = []

    for topic in config.get("topics", []):
        if not topic.get("enabled", True):
            continue

        weight = topic.get("weight", 1)
        matched_kw = []

        for kw in topic.get("keywords", []):
            pat = re.compile(r"\b" + re.escape(kw.lower()) + r"\b", re.IGNORECASE)
            title_hits = len(pat.findall(title_lower))
            abstract_hits = len(pat.findall(abstract_lower))

            if title_hits or abstract_hits:
                matched_kw.append(kw)
                total_score += weight * (
                    title_hits * TITLE_WEIGHT_MULTIPLIER + abstract_hits
                )

        if matched_kw:
            matched_topics.append(
                {
                    "topic": topic["name"],
                    "tag": topic.get(
                        "tag", topic["name"].lower().replace(" ", "-")
                    ),
                    "weight": weight,
                    "matched_keywords": matched_kw,
                }
            )

    return round(total_score * category_bonus, 1), matched_topics


# ── Fetch from arXiv ──────────────────────────────────────────────────────────

def effective_days_back(target_date: date, configured_days_back: int) -> int:
    """Extend days_back automatically on Mondays to cover the weekend gap."""
    if target_date.weekday() == 0:  # Monday
        return max(configured_days_back, 3)
    return configured_days_back


def fetch_arxiv(config: dict, target_date: date) -> tuple:
    """Returns (list[arxiv.Result], fetched_count)."""
    fetch_cfg = config.get("fetch", {})
    primary = fetch_cfg.get("categories", {}).get("primary", [])
    secondary = fetch_cfg.get("categories", {}).get("secondary", [])
    all_cats = primary + secondary
    max_results = fetch_cfg.get("max_results", 500)
    days_back = effective_days_back(
        target_date, fetch_cfg.get("days_back", 1)
    )

    start_date = target_date - timedelta(days=days_back - 1)
    date_from = start_date.strftime("%Y%m%d") + "000000"
    date_to = target_date.strftime("%Y%m%d") + "235959"

    cat_query = " OR ".join(f"cat:{c}" for c in all_cats)
    query = f"({cat_query}) AND submittedDate:[{date_from} TO {date_to}]"

    print(f"      Categories : {', '.join(all_cats)}")
    print(f"      Date range : {start_date} – {target_date}")
    print(f"      Max results: {max_results}")

    client = arxiv.Client(num_retries=3, delay_seconds=3)
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    results = list(client.results(search))
    return results, len(results)


# ── Convert arXiv result to dict ──────────────────────────────────────────────

def result_to_dict(result: arxiv.Result, score: float, matched_topics: list) -> dict:
    # entry_id like 'http://arxiv.org/abs/2602.12345v1'
    raw_id = result.entry_id.split("/")[-1]          # '2602.12345v1'
    arxiv_id = re.sub(r"v\d+$", "", raw_id)          # '2602.12345'
    url = f"https://arxiv.org/abs/{arxiv_id}"
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    authors = [a.name for a in result.authors[:5]]
    if len(result.authors) > 5:
        authors.append("et al.")

    abstract = result.summary.replace("\n", " ").strip()

    return {
        "source": "arxiv",
        "source_id": arxiv_id,
        "title": result.title,
        "authors": authors,
        "abstract": abstract,
        "categories": list(result.categories),
        "url": url,
        "pdf_url": pdf_url,
        "score": score,
        "matched_topics": matched_topics,
    }


# ── Screen ────────────────────────────────────────────────────────────────────

def screen_papers(raw: list, config: dict) -> list:
    screening = config.get("screening", {})
    min_score = screening.get("min_score", 5)
    top_n = screening.get("top_n", 50)

    scored = []
    for r in raw:
        score, matched = score_paper(
            r.title, r.summary, list(r.categories), config
        )
        if score >= min_score and matched:
            scored.append(result_to_dict(r, score, matched))

    scored.sort(key=lambda p: p["score"], reverse=True)
    return scored[:top_n]


# ── Output helpers ────────────────────────────────────────────────────────────

def abstract_preview(abstract: str, max_sentences: int = 3) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", abstract.strip())
    return " ".join(sentences[:max_sentences])


def wrap_as_blockquote(text: str, line_width: int = 88) -> str:
    words = text.split()
    lines, buf = [], []
    for word in words:
        buf.append(word)
        if len(" ".join(buf)) > line_width:
            lines.append("> " + " ".join(buf[:-1]))
            buf = [word]
    if buf:
        lines.append("> " + " ".join(buf))
    return "\n".join(lines)


# ── Save screened JSON ────────────────────────────────────────────────────────

def save_screened_json(papers: list, target_date: date, fetched: int) -> Path:
    SCREENED_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENED_DIR / f"{target_date}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "date": str(target_date),
                "fetched_count": fetched,
                "screened_count": len(papers),
                "papers": papers,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return path


# ── Save review Markdown ──────────────────────────────────────────────────────

def save_review_md(
    papers: list, target_date: date, fetched: int, config: dict
) -> Path:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = REVIEW_DIR / f"{target_date}.md"

    available_tags = [
        t["tag"]
        for t in config.get("topics", [])
        if t.get("enabled", True) and t.get("tag")
    ]

    lines = [
        f"# arXiv Review — {target_date}",
        f"> 取得: {fetched}件 → スクリーニング: {len(papers)}件",
        f"> `tags:` にタグを書いた論文が要約されます → `python stage2_summarize.py`",
        f"> 利用可能なタグ: {', '.join(available_tags)}",
        "",
    ]

    if not papers:
        lines += [
            "---",
            "",
            "*この日付に該当する論文はスクリーニング結果がありませんでした。*",
            "",
        ]
    else:
        for paper in papers:
            suggested = ", ".join(t["tag"] for t in paper["matched_topics"])
            authors_str = ", ".join(paper["authors"])
            cats_str = ", ".join(paper["categories"])
            preview = abstract_preview(paper["abstract"])
            bq = wrap_as_blockquote(preview)

            pdf_url = paper.get("pdf_url", "")
            pdf_link = f" | [PDF]({pdf_url})" if pdf_url else ""

            lines += [
                "---",
                "",
                f"<!-- source: {paper['source']} | id: {paper['source_id']} | score: {paper['score']} -->",
                f"### {paper['title']}",
                f"{authors_str} | {cats_str} | [arxiv]({paper['url']}){pdf_link}",
                f"`suggested: {suggested}`",
                bq,
                "",
                "tags:",
                "",
            ]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ── --check subcommand ────────────────────────────────────────────────────────

def cmd_check(config: dict) -> None:
    fetch_cfg = config.get("fetch", {})
    screening = config.get("screening", {})
    primary = fetch_cfg.get("categories", {}).get("primary", [])
    secondary = fetch_cfg.get("categories", {}).get("secondary", [])

    print("=== gleampaper — config check ===\n")
    print(f"  Primary categories   : {', '.join(primary)}")
    print(f"  Secondary categories : {', '.join(secondary)}")
    print(f"  max_results          : {fetch_cfg.get('max_results', 500)}")
    print(f"  days_back            : {fetch_cfg.get('days_back', 1)}")
    print(f"  min_score            : {screening.get('min_score', 5)}")
    print(f"  top_n                : {screening.get('top_n', 50)}")

    excl = config.get("exclude_keywords", [])
    print(f"\n  Exclude keywords ({len(excl)}) : {', '.join(excl) or '—'}")

    print(f"\n  Topics:")
    total_kw = 0
    for t in config.get("topics", []):
        status = "✓" if t.get("enabled", True) else "✗"
        kw_list = t.get("keywords", [])
        total_kw += len(kw_list)
        print(
            f"    [{status}] {t['name']}"
            f"  tag={t.get('tag', '-')}"
            f"  weight={t.get('weight', 1)}"
            f"  keywords={len(kw_list)}"
        )
        if t.get("enabled", True):
            for kw in kw_list:
                print(f"           - {kw}")

    print(f"\n  Total keywords: {total_kw}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="gleampaper Stage 1 — fetch and screen arXiv papers"
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Target date (default: today)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Show config statistics and exit",
    )
    args = parser.parse_args()

    config = load_config()

    if args.check:
        cmd_check(config)
        return

    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            sys.exit(f"[error] Invalid date: {args.date}. Use YYYY-MM-DD.")
    else:
        target_date = date.today()

    print(f"gleampaper Stage 1 — {target_date}")
    print("─" * 52)

    print("\n[1/4] Fetching papers from arXiv...")
    raw, fetched = fetch_arxiv(config, target_date)
    print(f"      → {fetched} papers fetched")

    print("\n[2/4] Screening...")
    papers = screen_papers(raw, config)
    min_score = config.get("screening", {}).get("min_score", 5)
    print(f"      → {len(papers)} papers passed (min_score={min_score})")

    print("\n[3/4] Saving screened JSON...")
    json_path = save_screened_json(papers, target_date, fetched)
    print(f"      → {json_path}")

    print("\n[4/4] Generating review Markdown...")
    review_path = save_review_md(papers, target_date, fetched, config)
    print(f"      → {review_path}")

    print("\n" + "─" * 52)
    print("Done. Open the review file and add tags:")
    print(f"  code {review_path}")
    print("Then run Stage 2:")
    print("  python stage2_summarize.py")


if __name__ == "__main__":
    main()
