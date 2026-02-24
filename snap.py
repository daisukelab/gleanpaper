#!/usr/bin/env python3
"""
gleanpaper — snap

Fetch and summarize a single paper from its URL.
Tags are auto-assigned from config/interests.yaml (top-N by keyword match score).

Supported sources:
  arXiv      https://arxiv.org/abs/2502.XXXXX
  OpenReview https://openreview.net/forum?id=XXXXX
  IEEE Xplore https://ieeexplore.ieee.org/document/XXXXXXX

Usage:
  python snap.py <URL>
  python snap.py <URL> --tags audio-repr,ssl-audio   # manual tag override
  python snap.py <URL> --top 3                        # use top-3 auto tags (default: 5)
  python snap.py <URL> --dry-run                      # show tags only, no API call
  python snap.py <URL> --force                        # re-summarize if digest exists
  python snap.py <URL> --skip-pdf                     # abstract only, skip PDF download

Output:
  digest/YYYY-MM/{source}_{id}.md  (same format as Stage 2)

Requires:
  ANTHROPIC_API_KEY environment variable (or .env file)
"""

from __future__ import annotations

import argparse
import base64
import datetime
import os
import re
import sys
import urllib.request
from pathlib import Path

import anthropic
import requests
import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import arxiv as _arxiv

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
INTERESTS_CONFIG_PATH = BASE_DIR / "config" / "interests.yaml"
SUMMARIZE_CONFIG_PATH = BASE_DIR / "config" / "summarize.yaml"
DIGEST_DIR = BASE_DIR / "digest"

TITLE_WEIGHT_MULTIPLIER = 2

DEFAULT_SUMMARIZE_CONFIG = {
    "model": "claude-sonnet-4-6",
    "max_tokens": 1500,
}

# ── Load configs ──────────────────────────────────────────────────────────────

def load_interests() -> dict:
    if not INTERESTS_CONFIG_PATH.exists():
        sys.exit(
            f"[error] interests.yaml not found: {INTERESTS_CONFIG_PATH}\n"
            f"        Create config/interests.yaml based on config/examples/interests.yaml.example"
        )
    with open(INTERESTS_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_summarize_config() -> dict:
    cfg = dict(DEFAULT_SUMMARIZE_CONFIG)
    if SUMMARIZE_CONFIG_PATH.exists():
        with open(SUMMARIZE_CONFIG_PATH, encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
            cfg.update({k: v for k, v in user_cfg.items() if v is not None})
    return cfg


# ── URL detection ─────────────────────────────────────────────────────────────

def detect_source_and_id(url: str) -> tuple:
    """Returns (source, id) or raises ValueError."""
    url = url.strip()

    # arXiv: abs or pdf URL, with optional version suffix
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)", url)
    if m:
        return "arxiv", re.sub(r"v\d+$", "", m.group(1))

    # OpenReview: forum or pdf URL
    m = re.search(r"openreview\.net/(?:forum|pdf)\?.*\bid=([^\s&]+)", url)
    if m:
        return "openreview", m.group(1)

    # IEEE Xplore
    m = re.search(r"ieeexplore\.ieee\.org/document/(\d+)", url)
    if m:
        return "ieee", m.group(1)

    raise ValueError(
        f"Unsupported URL: {url}\n"
        f"  Supported: arxiv.org, openreview.net, ieeexplore.ieee.org"
    )


# ── Fetchers ──────────────────────────────────────────────────────────────────

def fetch_arxiv_paper(arxiv_id: str) -> dict:
    client = _arxiv.Client(num_retries=3, delay_seconds=3)
    search = _arxiv.Search(id_list=[arxiv_id])
    try:
        result = next(client.results(search))
    except StopIteration:
        raise ValueError(f"arXiv paper not found: {arxiv_id}")

    raw_id = result.entry_id.split("/")[-1]
    clean_id = re.sub(r"v\d+$", "", raw_id)

    authors = [a.name for a in result.authors[:5]]
    if len(result.authors) > 5:
        authors.append("et al.")

    return {
        "source": "arxiv",
        "source_id": clean_id,
        "title": result.title,
        "authors": authors,
        "abstract": result.summary.replace("\n", " ").strip(),
        "categories": list(result.categories),
        "url": f"https://arxiv.org/abs/{clean_id}",
        "pdf_url": f"https://arxiv.org/pdf/{clean_id}",
        "date_published": str(result.published.date()),
        "score": 0.0,
    }


_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def fetch_openreview_paper(paper_id: str) -> dict:
    api_url = f"https://api2.openreview.net/notes?id={paper_id}"
    resp = requests.get(api_url, headers={"User-Agent": _BROWSER_UA}, timeout=15)
    if resp.status_code == 404:
        raise ValueError(f"OpenReview paper not found: {paper_id}")
    if resp.status_code == 429:
        raise RuntimeError("OpenReview API rate limit exceeded. Please retry in a moment.")
    resp.raise_for_status()
    notes = resp.json().get("notes", [])
    if not notes:
        raise ValueError(f"OpenReview paper not found: {paper_id}")

    note = notes[0]
    content = note.get("content", {})

    def extract(field):
        v = content.get(field, {})
        return v.get("value", "") if isinstance(v, dict) else (v or "")

    title = extract("title")
    authors_raw = extract("authors") or []
    if isinstance(authors_raw, str):
        authors_raw = [authors_raw]
    authors = authors_raw[:5] + (["et al."] if len(authors_raw) > 5 else [])

    abstract = extract("abstract")
    venue = extract("venue") or extract("venueid") or ""
    keywords = extract("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]

    # Timestamp (ms) → date
    ts = note.get("pdate") or note.get("cdate") or note.get("tcdate")
    pub_date = (
        datetime.datetime.fromtimestamp(ts / 1000, tz=datetime.timezone.utc).date()
        if ts else datetime.date.today()
    )

    # Pseudo-categories: venue + first few keywords (for scoring purposes)
    categories = ([venue] if venue else []) + keywords[:3]

    return {
        "source": "openreview",
        "source_id": paper_id,
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "categories": categories,
        "url": f"https://openreview.net/forum?id={paper_id}",
        "pdf_url": f"https://openreview.net/pdf?id={paper_id}",
        "date_published": str(pub_date),
        "score": 0.0,
    }


def _meta(html: str, name: str) -> str:
    """Extract a single <meta name="..." content="..."> value."""
    for pattern in [
        rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]*)"',
        rf'<meta\s+content="([^"]*)"\s+name="{re.escape(name)}"',
    ]:
        m = re.search(pattern, html, re.I)
        if m:
            return m.group(1).strip()
    return ""


def _metas(html: str, name: str) -> list:
    """Extract all <meta name="..." content="..."> values for a given name."""
    results = []
    for pattern in [
        rf'<meta\s+name="{re.escape(name)}"\s+content="([^"]*)"',
        rf'<meta\s+content="([^"]*)"\s+name="{re.escape(name)}"',
    ]:
        results = re.findall(pattern, html, re.I)
        if results:
            break
    return results


def fetch_ieee_paper(doc_id: str) -> dict:
    import json as _json

    url = f"https://ieeexplore.ieee.org/document/{doc_id}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    html = resp.text

    # IEEE embeds full metadata in: xplGlobal.document.metadata = {...};
    meta = {}
    m = re.search(
        r"xplGlobal\.document\.metadata\s*=\s*(\{.*?\});\s*</script>",
        html, re.S
    )
    if m:
        try:
            meta = _json.loads(m.group(1))
        except _json.JSONDecodeError:
            pass

    # Title
    title = meta.get("title", "")
    if not title:
        m2 = re.search(r'property="(?:og|twitter):title"\s+content="([^"]+)"', html, re.I)
        if m2:
            title = m2.group(1).strip()
    if not title:
        raise ValueError(f"Could not extract title from IEEE page: {url}")

    # Authors: list of dicts with "name" key
    authors_raw = [a.get("name", "") for a in meta.get("authors", []) if a.get("name")]
    if not authors_raw:
        authors_raw = _metas(html, "citation_author")

    # Abstract
    abstract = meta.get("abstract", "")
    if not abstract:
        abstract = _meta(html, "description")
    if not abstract:
        m3 = re.search(r'property="(?:og|twitter):description"\s+content="([^"]+)"', html, re.I)
        if m3:
            abstract = m3.group(1).strip()

    # DOI and PDF
    doi = meta.get("doi", "") or _meta(html, "citation_doi")
    pdf_url = (
        f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={doc_id}"
        if doi else ""
    )

    # Publication date: IEEE typically only has year
    pub_year = str(meta.get("publicationDate") or meta.get("publicationYear") or "")
    pub_date = datetime.date.today()
    for fmt in ["%Y/%m/%d", "%Y/%m", "%Y"]:
        try:
            pub_date = datetime.datetime.strptime(pub_year, fmt).date()
            break
        except ValueError:
            continue

    return {
        "source": "ieee",
        "source_id": doc_id,
        "title": title,
        "authors": (authors_raw[:5] + ["et al."]) if len(authors_raw) > 5 else authors_raw,
        "abstract": abstract or "(アブストラクトをページから取得できませんでした)",
        "categories": [doi] if doi else [],
        "url": url,
        "pdf_url": pdf_url,
        "date_published": str(pub_date),
        "score": 0.0,
    }


def fetch_paper(url: str) -> dict:
    source, paper_id = detect_source_and_id(url)
    if source == "arxiv":
        return fetch_arxiv_paper(paper_id)
    elif source == "openreview":
        return fetch_openreview_paper(paper_id)
    elif source == "ieee":
        return fetch_ieee_paper(paper_id)


# ── Scoring / auto-tagging ────────────────────────────────────────────────────

def score_paper(title: str, abstract: str, categories: list, config: dict) -> tuple:
    """
    Returns (total_score, matched_topics).
    Each entry in matched_topics has a 'topic_score' key for ranking.
    """
    title_lower = title.lower()
    abstract_lower = abstract.replace("\n", " ").lower()

    for excl in config.get("exclude_keywords", []):
        if re.search(r"\b" + re.escape(excl.lower()) + r"(?:es|s)?\b",
                     f"{title_lower} {abstract_lower}"):
            return 0.0, []

    primary_cats = set(config.get("fetch", {}).get("categories", {}).get("primary", []))
    secondary_cats = set(config.get("fetch", {}).get("categories", {}).get("secondary", []))
    paper_cats = set(categories)

    if paper_cats & primary_cats:
        category_bonus = 1.0
    elif paper_cats & secondary_cats:
        category_bonus = 0.7
    else:
        category_bonus = 1.0

    total_score = 0.0
    matched_topics = []

    for topic in config.get("topics", []):
        if not topic.get("enabled", True):
            continue
        weight = topic.get("weight", 1)
        topic_score = 0.0
        matched_kw = []

        for kw in topic.get("keywords", []):
            pat = re.compile(r"\b" + re.escape(kw.lower()) + r"(?:es|s)?\b", re.IGNORECASE)
            title_hits = len(pat.findall(title_lower))
            abstract_hits = len(pat.findall(abstract_lower))
            if title_hits or abstract_hits:
                matched_kw.append(kw)
                contribution = weight * (title_hits * TITLE_WEIGHT_MULTIPLIER + abstract_hits)
                topic_score += contribution
                total_score += contribution

        if matched_kw:
            matched_topics.append({
                "topic": topic["name"],
                "tag": topic.get("tag", topic["name"].lower().replace(" ", "-")),
                "weight": weight,
                "matched_keywords": matched_kw,
                "topic_score": round(topic_score * category_bonus, 1),
            })

    return round(total_score * category_bonus, 1), matched_topics


def auto_tag(paper: dict, config: dict, top_n: int = 5) -> list:
    """Return top-N tag names, ranked by per-topic relevance score."""
    score, matched = score_paper(
        paper["title"], paper["abstract"], paper.get("categories", []), config
    )
    paper["score"] = score
    ranked = sorted(matched, key=lambda t: t["topic_score"], reverse=True)
    return [t["tag"] for t in ranked[:top_n]]


# ── Prompt / summarize ────────────────────────────────────────────────────────

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

PROMPT_TEMPLATE_FULLPDF = """\
添付の論文PDFを、指定された6項目でまとめてください。

## 注意事項
- この論文に書いてある内容のみを記述してください。論文に記載のない情報を類推・推測して付け加えることは厳禁です。
- 各項目は、できるだけ1行で簡潔に記述してください。
- 重要な点が複数ある場合は、それぞれ1行で列挙してください。
- その項目で利用・言及された既存モデル・データセット・手法は、関連する行の直後に「  - 名称」の形式で箇条書きで記載してください。
- 情報が論文に記載されていない場合は「（論文に記載なし）」と記してください。

## 論文情報
タイトル: {title}
著者: {authors}
関連トピック: {tags}

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


def build_prompt_fullpdf(paper: dict, tags: list) -> str:
    return PROMPT_TEMPLATE_FULLPDF.format(
        title=paper["title"],
        authors=", ".join(paper["authors"]),
        tags=", ".join(tags),
    )


def download_pdf(url: str, timeout: int = 30) -> bytes | None:
    """Download PDF from URL. Returns bytes or None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "gleanpaper/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        print(f"       [warn] PDF download failed: {e}")
        return None


def summarize(paper: dict, tags: list, cfg: dict, client: anthropic.Anthropic,
              use_full_pdf: bool = True) -> str:
    if use_full_pdf and paper.get("pdf_url"):
        pdf_bytes = download_pdf(paper["pdf_url"])
        if pdf_bytes:
            prompt_text = build_prompt_fullpdf(paper, tags)
            message = client.messages.create(
                model=cfg["model"],
                max_tokens=cfg["max_tokens"],
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.standard_b64encode(pdf_bytes).decode("utf-8"),
                            },
                        },
                        {"type": "text", "text": prompt_text},
                    ],
                }],
            )
            return message.content[0].text.strip()
        else:
            print("       [warn] Falling back to abstract-only.")

    # abstract-only (default or fallback)
    prompt = build_prompt(paper, tags)
    message = client.messages.create(
        model=cfg["model"],
        max_tokens=cfg["max_tokens"],
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


# ── Citation count ────────────────────────────────────────────────────────────

def get_citation_count(title: str) -> int | None:
    """Fetch citation count from Google Scholar via scholarly.

    Returns the citation count as an integer, or None on failure.
    Failure is silently logged (not a fatal error).
    """
    try:
        from scholarly import scholarly as _scholarly
        results = _scholarly.search_pubs(title)
        pub = next(results, None)
        if pub:
            return pub.get("num_citations")
    except ImportError:
        print("       [warn] scholarly not installed: pip install scholarly")
    except Exception as e:
        print(f"       [warn] Citation count unavailable: {e}")
    return None


# ── Write digest ──────────────────────────────────────────────────────────────

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


def write_digest(
    paper: dict,
    tags: list,
    summary: str,
    gleaned_date: datetime.date,
    citation_count: int | None = None,
) -> Path:
    month_dir = DIGEST_DIR / gleaned_date.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)

    filename = digest_filename(paper)
    path = month_dir / filename

    authors_yaml = "\n".join(f'  - "{a}"' for a in paper["authors"])
    tags_yaml = "\n".join(f"  - {t}" for t in tags)
    tags_inline = " ".join(f"`{t}`" for t in tags)
    pdf_line = f"**PDF**: {paper['pdf_url']}  " if paper.get("pdf_url") else ""
    citation_yaml = str(citation_count) if citation_count is not None else "null"
    citation_line = (
        f"**Citations**: {citation_count}  "
        if citation_count is not None
        else "**Citations**: 取得不可  "
    )

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
citation_count: {citation_yaml}
tags:
{tags_yaml}
score: {paper['score']}
---

# {paper['title']}

**Authors**: {', '.join(paper['authors'])}
**Published**: {paper.get('date_published', str(gleaned_date))}
{citation_line}
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


# ── Digest path helper ────────────────────────────────────────────────────────

def digest_path_for(paper: dict, gleaned_date: datetime.date) -> Path:
    return DIGEST_DIR / gleaned_date.strftime("%Y-%m") / digest_filename(paper)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="gleanpaper snap — fetch and summarize a single paper from URL"
    )
    parser.add_argument("url", help="Paper URL (arXiv, OpenReview, or IEEE Xplore)")
    parser.add_argument(
        "--tags",
        help="Comma-separated tags to assign (overrides auto-detection from interests.yaml)",
    )
    parser.add_argument(
        "--top", type=int, default=5, metavar="N",
        help="Number of auto-detected tags to assign (default: 5)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch metadata and show auto-tags, but do not call the API",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-summarize even if a digest file already exists",
    )
    parser.add_argument(
        "--skip-pdf", action="store_true",
        help="Use abstract only, skip PDF download (faster, fewer tokens)",
    )
    args = parser.parse_args()

    interests = load_interests()
    sum_cfg = load_summarize_config()

    print("gleanpaper snap")
    print("─" * 52)

    # ── Step 1: Fetch metadata ────────────────────────────────────────────────
    print(f"\n[1/4] Fetching paper metadata...")
    print(f"      URL: {args.url}")
    try:
        paper = fetch_paper(args.url)
    except ValueError as e:
        sys.exit(f"[error] {e}")
    except requests.HTTPError as e:
        sys.exit(f"[error] HTTP {e.response.status_code}: {e.response.url}")
    except Exception as e:
        sys.exit(f"[error] {e}")

    print(f"      Source  : {paper['source']} / {paper['source_id']}")
    print(f"      Title   : {paper['title']}")
    print(f"      Authors : {', '.join(paper['authors'])}")
    print(f"      Date    : {paper.get('date_published', '—')}")
    if paper.get("pdf_url"):
        print(f"      PDF     : {paper['pdf_url']}")

    # ── Step 2: Tags ──────────────────────────────────────────────────────────
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        paper["score"] = score_paper(
            paper["title"], paper["abstract"], paper.get("categories", []), interests
        )[0]
        print(f"\n[2/4] Tags (manual): {', '.join(tags)}")
    else:
        print(f"\n[2/4] Auto-tagging from interests.yaml (top {args.top})...")
        tags = auto_tag(paper, interests, top_n=args.top)
        if tags:
            print(f"      Tags  : {', '.join(tags)}")
            print(f"      Score : {paper['score']}")
        else:
            print("      [warn] No matching topics found in interests.yaml.")
            print("             Use --tags to assign tags manually.")

    if args.dry_run:
        print("\n[dry-run] No API call made.")
        return

    # ── Check for existing digest ─────────────────────────────────────────────
    today = datetime.date.today()
    dp = digest_path_for(paper, today)
    if dp.exists() and not args.force:
        print(f"\n[info] Digest already exists: {dp}")
        print("       Use --force to re-summarize.")
        return

    # ── Step 3: Citation count ────────────────────────────────────────────────
    print(f"\n[3/4] Fetching citation count from Google Scholar...")
    citation_count = get_citation_count(paper["title"])
    if citation_count is not None:
        print(f"      Citations: {citation_count}")
    else:
        print("      Citations: unavailable")

    # ── Step 4: Summarize ─────────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit(
            "\n[error] ANTHROPIC_API_KEY is not set.\n"
            "        export ANTHROPIC_API_KEY=sk-ant-...\n"
            "        or add it to a .env file in this directory."
        )

    client = anthropic.Anthropic(api_key=api_key)

    print(f"\n[4/4] Summarizing via {sum_cfg['model']} (full-pdf: {not args.skip_pdf})...")
    try:
        summary = summarize(paper, tags, sum_cfg, client, use_full_pdf=not args.skip_pdf)
        path = write_digest(paper, tags, summary, today, citation_count=citation_count)
    except Exception as e:
        sys.exit(f"[error] {e}")

    print("\n" + "─" * 52)
    print(f"Done.  → {path}")


if __name__ == "__main__":
    main()
