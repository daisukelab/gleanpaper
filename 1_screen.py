#!/usr/bin/env python3
"""
gleanpaper — Stage 1: Screen

Fetches papers from arXiv and screens them against config/interests.yaml.

Outputs:
  screened/YYYY-MM-DD.json   raw data (machine-readable, for Stage 2)
  review/YYYY-MM-DD.md       human review file (write tags to select papers for Stage 2)

Usage:
  python 1_screen.py                  # インクリメンタル（前回取得日の翌日〜今日）
  python 1_screen.py 2026-02-18       # 単日モード（指定日のみ取得）
  python 1_screen.py -re              # 最新レビューを再スクリーニング（タグ保持）
  python 1_screen.py -re 2026-02-18   # 日付指定 + re-screen
  python 1_screen.py --force          # re-screen、既存ファイルを上書き
  python 1_screen.py --check          # show config statistics

【インクリメンタルモードについて】
  引数なしで実行すると screened/ および archive/screened/ の最終日付を検出し、
  その翌日〜UTC 今日の範囲を一括 fetch する。
  結果を result.published.date() で日付ごとに分割してファイルを生成するため、
  arXiv 検索インデックスのラグや週末の空白日を自動スキップできる。
"""

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import arxiv
import yaml

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config" / "interests.yaml"
SCREENED_DIR = BASE_DIR / "screened"
REVIEW_DIR = BASE_DIR / "review"
ARCHIVE_DIR = BASE_DIR / "archive"

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
        if re.search(r"\b" + re.escape(excl.lower()) + r"(?:es|s)?\b", full_text):
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
            pat = re.compile(r"\b" + re.escape(kw.lower()) + r"(?:es|s)?\b", re.IGNORECASE)
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

def default_target_date() -> date:
    """Return today in UTC as the default arXiv target date.

    arXiv uses UTC for submittedDate. date.today() returns the local (JST)
    date, which can be ahead of UTC and cause 0 results. Using UTC now
    always returns the correct arXiv date.
    """
    return datetime.now(timezone.utc).date()


def effective_days_back(target_date: date, configured_days_back: int) -> int:
    """Extend days_back automatically on Mondays to cover the weekend gap."""
    if target_date.weekday() == 0:  # Monday
        return max(configured_days_back, 3)
    return configured_days_back


def find_last_screened_date() -> date | None:
    """Return the most recent date found in screened/ or archive/screened/."""
    dates = []
    for d in [SCREENED_DIR, ARCHIVE_DIR / "screened"]:
        for p in d.glob("????-??-??.json"):
            try:
                dates.append(date.fromisoformat(p.stem))
            except ValueError:
                pass
    return max(dates) if dates else None


def fetch_arxiv(config: dict, start_date: date, end_date: date) -> tuple:
    """Returns (list[arxiv.Result], fetched_count)."""
    fetch_cfg = config.get("fetch", {})
    primary = fetch_cfg.get("categories", {}).get("primary", [])
    secondary = fetch_cfg.get("categories", {}).get("secondary", [])
    all_cats = primary + secondary
    max_results = fetch_cfg.get("max_results", 500)

    date_from = start_date.strftime("%Y%m%d") + "000000"
    date_to = end_date.strftime("%Y%m%d") + "235959"

    cat_query = " OR ".join(f"cat:{c}" for c in all_cats)
    query = f"({cat_query}) AND submittedDate:[{date_from} TO {date_to}]"

    print(f"      Categories : {', '.join(all_cats)}")
    print(f"      Date range : {start_date} – {end_date}")
    print(f"      Max results: {max_results}")

    client = arxiv.Client(num_retries=3, delay_seconds=5)
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    for attempt in range(4):
        try:
            results = list(client.results(search))
            return results, len(results)
        except arxiv.HTTPError as e:
            if e.status == 429 and attempt < 3:
                wait = 30 * (attempt + 1)
                print(f"      [rate limit] 429 received. {wait}秒後にリトライ... ({attempt + 1}/3)")
                time.sleep(wait)
            else:
                raise


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
        "date_published": result.published.strftime("%Y-%m-%d"),
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


def screen_and_split(raw: list, config: dict) -> dict:
    """
    Screen papers and group by published date (for incremental mode).
    Returns {date: [paper_dicts]}, each date's list sorted by score, top_n applied.
    """
    screening = config.get("screening", {})
    min_score = screening.get("min_score", 5)
    top_n = screening.get("top_n", 50)

    by_date: dict = {}
    for r in raw:
        score, matched = score_paper(
            r.title, r.summary, list(r.categories), config
        )
        if score >= min_score and matched:
            pub_date = r.published.date()
            by_date.setdefault(pub_date, []).append(
                result_to_dict(r, score, matched)
            )

    for d in by_date:
        by_date[d].sort(key=lambda p: p["score"], reverse=True)
        by_date[d] = by_date[d][:top_n]

    return by_date


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


# ── Parse existing tags from review file ─────────────────────────────────────

def parse_existing_tags(review_path: Path) -> dict:
    """
    Read an existing review Markdown and return {source_id: tags_string}.
    Only entries where tags: is non-empty are returned.
    """
    if not review_path.exists():
        return {}

    existing = {}
    current_id = None

    with open(review_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            # Detect paper ID from HTML comment
            m = re.search(r"<!--\s*source:\s*\w+\s*\|\s*id:\s*([\w.]+)", line)
            if m:
                current_id = m.group(1)
                continue
            # Detect tags line
            if current_id and re.match(r"^tags:\s*\S", line):
                tags_value = line[len("tags:"):].strip()
                existing[current_id] = tags_value
                current_id = None  # reset after capturing

    return existing


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
    papers: list,
    target_date: date,
    fetched: int,
    config: dict,
    existing_tags: dict = None,
) -> Path:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = REVIEW_DIR / f"{target_date}.md"
    existing_tags = existing_tags or {}

    available_tags = [
        t["tag"]
        for t in config.get("topics", [])
        if t.get("enabled", True) and t.get("tag")
    ]

    # Count how many existing tags will be restored
    restored = sum(1 for p in papers if p["source_id"] in existing_tags)
    new_count = len(papers) - restored

    header_note = ""
    if existing_tags:
        header_note = (
            f"> **再スクリーニング済み** — "
            f"タグ保持: {restored}件 / 新規追加: {new_count}件"
        )

    lines = [
        f"# arXiv Review — {target_date}",
        f"> 取得: {fetched}件 → スクリーニング: {len(papers)}件",
        f"> `tags:` にタグを書いた論文が要約されます → `python 2_summarize.py`",
        f"> 利用可能なタグ: {', '.join(available_tags)}",
    ]
    if header_note:
        lines.append(header_note)
    lines.append("")

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

            # Restore existing tag or leave blank
            restored_tag = existing_tags.get(paper["source_id"], "")
            tag_line = f"tags: {restored_tag}" if restored_tag else "tags: "
            tag_marker = "  ← restored" if restored_tag else ""

            lines += [
                "---",
                "",
                f"<!-- source: {paper['source']} | id: {paper['source_id']} | score: {paper['score']} -->",
                f"### {paper['title']}",
                f"{authors_str} | {cats_str} | [arxiv]({paper['url']}){pdf_link}",
                f"`suggested: {suggested}`",
                bq,
                "",
                tag_line + tag_marker,
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

    print("=== gleanpaper — config check ===\n")
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
        description="gleanpaper Stage 1 — fetch and screen arXiv papers"
    )
    parser.add_argument(
        "date",
        nargs="?",
        metavar="YYYY-MM-DD",
        help="Target date for single-day mode (default: incremental mode)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Show config statistics and exit",
    )
    parser.add_argument(
        "-re", "--rescreen",
        action="store_true",
        help="Re-run screening for an existing date, preserving tags already entered",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing screened/review files without preserving tags",
    )
    args = parser.parse_args()

    config = load_config()

    if args.check:
        cmd_check(config)
        return

    # ── Single-date mode (date given, or --rescreen/--force without date) ─────
    if args.date or args.rescreen or args.force:
        if args.date:
            try:
                target_date = date.fromisoformat(args.date)
            except ValueError:
                sys.exit(f"[error] Invalid date: {args.date}. Use YYYY-MM-DD.")
        else:
            # --rescreen or --force without date: use latest review file
            review_files = sorted(REVIEW_DIR.glob("????-??-??.md"), reverse=True)
            if not review_files:
                sys.exit("[error] No review files found. Please specify a date.")
            target_date = date.fromisoformat(review_files[0].stem)
            print(f"[info] 日付省略のため最新レビューファイルを使用: {target_date}")

        review_path = REVIEW_DIR / f"{target_date}.md"
        json_path = SCREENED_DIR / f"{target_date}.json"

        # Guard: warn if files already exist and neither --rescreen nor --force given
        if (review_path.exists() or json_path.exists()) and not args.rescreen and not args.force:
            print(f"[warn] Output files for {target_date} already exist:")
            if json_path.exists():
                print(f"       {json_path}")
            if review_path.exists():
                print(f"       {review_path}")
            print(f"\n  Use --rescreen to re-run and preserve existing tags.")
            print(f"  Use --force    to overwrite everything.")
            sys.exit(0)

        # Load existing tags before overwriting (--rescreen only)
        existing_tags = {}
        if args.rescreen and review_path.exists():
            existing_tags = parse_existing_tags(review_path)
            tagged_count = len(existing_tags)
            if tagged_count:
                print(f"[info] Found {tagged_count} tagged paper(s) — will preserve.")
            else:
                print(f"[info] No tags found in existing review file.")

        print(f"gleanpaper Stage 1 — {target_date}")
        print("─" * 52)

        fetch_cfg = config.get("fetch", {})
        days_back = effective_days_back(target_date, fetch_cfg.get("days_back", 1))
        start_date = target_date - timedelta(days=days_back - 1)

        print("\n[1/4] Fetching papers from arXiv...")
        raw, fetched = fetch_arxiv(config, start_date, target_date)
        print(f"      → {fetched} papers fetched")

        print("\n[2/4] Screening...")
        papers = screen_papers(raw, config)
        min_score = config.get("screening", {}).get("min_score", 5)
        print(f"      → {len(papers)} papers passed (min_score={min_score})")

        print("\n[3/4] Saving screened JSON...")
        json_path = save_screened_json(papers, target_date, fetched)
        print(f"      → {json_path}")

        print("\n[4/4] Generating review Markdown...")
        review_path = save_review_md(papers, target_date, fetched, config, existing_tags)
        print(f"      → {review_path}")
        if args.rescreen and existing_tags:
            restored = sum(1 for p in papers if p["source_id"] in existing_tags)
            added = len(papers) - restored
            print(f"      タグ保持: {restored}件 / 新規追加: {added}件")

        if existing_tags:
            restored = sum(1 for p in papers if p["source_id"] in existing_tags)
            lost = len(existing_tags) - restored
            print(f"      → tags restored: {restored}件", end="")
            if lost:
                print(f" / lost (no longer in results): {lost}件", end="")
            print()

        print("\n" + "─" * 52)
        print("Done. Open the review file and add tags:")
        print(f"  code {review_path}")
        print("Then run Stage 2:")
        print("  python 2_summarize.py")
        return

    # ── Incremental mode (no args) ─────────────────────────────────────────────
    last_date = find_last_screened_date()
    if last_date is None:
        sys.exit(
            "[error] スクリーニング済みファイルが見つかりません。\n"
            "        初回は日付を指定して実行してください:\n"
            "          python 1_screen.py YYYY-MM-DD"
        )

    end_date = datetime.now(timezone.utc).date()
    fetch_cfg = config.get("fetch", {})
    overlap_days = fetch_cfg.get("overlap_days", 2)
    # overlap_days: 前回取得日より何日前まで遡るか（締め切り後投稿の遅延インデックス対策）
    # 例: overlap_days=2 → last_date-1 から取得（前日締め切り後投稿を翌日に取り込める）
    start_date = last_date + timedelta(days=1) - timedelta(days=overlap_days)

    new_start = last_date + timedelta(days=1)
    if new_start > end_date:
        print(f"[info] 最終取得日: {last_date} — すでに最新です。")
        return

    print(f"gleanpaper Stage 1 — incremental ({start_date} – {end_date})")
    print("─" * 52)
    print(f"[info] 最終取得日: {last_date} (overlap: {overlap_days}日前から再チェック)")

    print("\n[1/3] Fetching papers from arXiv...")
    raw, fetched = fetch_arxiv(config, start_date, end_date)
    print(f"      → {fetched} papers fetched")

    print("\n[2/3] Screening and splitting by published date...")
    by_date = screen_and_split(raw, config)
    min_score = config.get("screening", {}).get("min_score", 5)

    created = []
    overlaps_found = []
    for d in sorted(by_date.keys()):
        papers = by_date[d]
        existing_json = SCREENED_DIR / f"{d}.json"

        if d <= last_date and existing_json.exists():
            # Overlap day: 既存ファイルに漏れ論文だけ追加（上書きしない）
            with open(existing_json, encoding="utf-8") as f:
                old = json.load(f)
            old_ids = {p["source_id"] for p in old["papers"]}
            new_papers = [p for p in papers if p["source_id"] not in old_ids]
            if new_papers:
                top_n = config.get("screening", {}).get("top_n", 50)
                merged = old["papers"] + new_papers
                merged.sort(key=lambda p: p["score"], reverse=True)
                merged = merged[:top_n]
                save_screened_json(merged, d, old["fetched_count"])
                existing_tags = parse_existing_tags(REVIEW_DIR / f"{d}.md")
                save_review_md(merged, d, old["fetched_count"], config, existing_tags)
                print(f"      {d}: +{len(new_papers)}件 追加 (遅延インデックス論文を発見)")
                overlaps_found.append((d, new_papers))
            else:
                print(f"      {d}: 変更なし (overlap チェック済み)")
        else:
            print(f"      {d}: {len(papers)}件 passed")
            save_screened_json(papers, d, fetched)
            save_review_md(papers, d, fetched, config)
            created.append(d)

    if not created and not overlaps_found:
        print("      → 新規ファイルなし")
        if fetched == 0:
            print(
                f"      (arXiv インデックスが {new_start} 以降の論文を"
                f"まだ収録していない可能性があります)"
            )
        print("\n" + "─" * 52)
        return

    print(f"\n[3/3] ファイルを生成/更新しました:")
    for d in created:
        print(f"      [新規] review/{d}.md")
    for d, papers in overlaps_found:
        ids = ", ".join(p["source_id"] for p in papers)
        print(f"      [更新] review/{d}.md (+{len(papers)}件: {ids})")

    print("\n" + "─" * 52)
    print("Done. Open the review file(s) and add tags:")
    for d in created:
        print(f"  code review/{d}.md")
    for d, _ in overlaps_found:
        print(f"  code review/{d}.md  ← 追加論文あり")
    print("Then run Stage 2:")
    print("  python 2_summarize.py")


if __name__ == "__main__":
    main()
