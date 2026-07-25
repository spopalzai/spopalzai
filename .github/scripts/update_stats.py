#!/usr/bin/env python3
"""Regenerate the stats block in README.md from the GitHub GraphQL API.

Runs inside the update-stats workflow. Uses STATS_TOKEN (a PAT, sees
private repos) when set, otherwise falls back to the workflow's
GITHUB_TOKEN, which only sees public data.

Draws light/dark SVG charts into assets/stats/ and rewrites the text
between the STATS markers. Only aggregate numbers are published; private
repository names never appear in the output.
"""

import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

LOGIN = "spopalzai"
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
README = os.path.join(ROOT, "README.md")
STATS_DIR = os.path.join(ROOT, "assets", "stats")
API = "https://api.github.com/graphql"
START = "<!-- STATS:START -->"
END = "<!-- STATS:END -->"
# The account's activity starts here; the chart grows forward from it.
FIRST_MONTH = (2025, 6)

FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
THEMES = {
    "light": {"ink": "#1f2328", "muted": "#59636e", "grid": "#d1d9e0", "bar": "#1f2328"},
    "dark": {"ink": "#e6edf3", "muted": "#9198a1", "grid": "#3d444d", "bar": "#e6edf3"},
}


def gql(query, variables=None):
    token = os.environ.get("STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("No STATS_TOKEN or GITHUB_TOKEN in environment")
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    if data.get("errors"):
        sys.exit(f"GraphQL errors: {data['errors']}")
    return data["data"]


def fetch_repos():
    repos = []
    total = 0
    cursor = None
    query = """
    query($login: String!, $cursor: String) {
      user(login: $login) {
        repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER]) {
          totalCount
          pageInfo { hasNextPage endCursor }
          nodes {
            name
            isPrivate
            isFork
            pushedAt
            createdAt
            primaryLanguage { name }
            languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
              edges { size node { name } }
            }
            repositoryTopics(first: 20) { nodes { topic { name } } }
          }
        }
      }
    }
    """
    while True:
        data = gql(query, {"login": LOGIN, "cursor": cursor})
        conn = data["user"]["repositories"]
        total = conn["totalCount"]
        repos.extend(conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return total, repos


def month_starts():
    """Calendar month starts from FIRST_MONTH through the current month."""
    now = datetime.now(timezone.utc)
    y, m = FIRST_MONTH
    starts = []
    while (y, m) <= (now.year, now.month):
        starts.append(datetime(y, m, 1, tzinfo=timezone.utc))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return starts


def fetch_commits_by_month(repos):
    """Count commits on each repo's default branch, bucketed by month.

    Walks real commit history instead of the contributions API, which
    only counts commits whose author email is linked to the account.
    """
    starts = month_starts()
    since = starts[0].isoformat()
    counts = {s.strftime("%Y-%m"): 0 for s in starts}
    query = """
    query($login: String!, $name: String!, $since: GitTimestamp!, $cursor: String) {
      repository(owner: $login, name: $name) {
        defaultBranchRef {
          target {
            ... on Commit {
              history(since: $since, first: 100, after: $cursor) {
                pageInfo { hasNextPage endCursor }
                nodes { committedDate }
              }
            }
          }
        }
      }
    }
    """
    for r in repos:
        if r["isFork"]:
            continue
        cursor = None
        while True:
            data = gql(query, {
                "login": LOGIN, "name": r["name"],
                "since": since, "cursor": cursor,
            })
            ref = data["repository"]["defaultBranchRef"]
            if not ref:
                break
            history = ref["target"]["history"]
            for node in history["nodes"]:
                key = node["committedDate"][:7]
                if key in counts:
                    counts[key] += 1
            if not history["pageInfo"]["hasNextPage"]:
                break
            cursor = history["pageInfo"]["endCursor"]
    return [(s, counts[s.strftime("%Y-%m")]) for s in starts]


# --- SVG helpers -----------------------------------------------------------

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_open(width, height, title):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">'
        f'<style>text{{font-family:{FONT};}}</style>'
    )


def text(x, y, s, size, fill, anchor="start", weight="normal"):
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-weight="{weight}">{esc(s)}</text>'
    )


def svg_commits(monthly, theme):
    """Vertical bar chart of commits per month, square-root scale."""
    t = THEMES[theme]
    slot, gap = 46, 2
    left, right, top, bottom = 16, 16, 44, 40
    plot_h = 130
    n = len(monthly)
    width = left + n * slot + right
    height = top + plot_h + bottom
    max_v = max((c for _, c in monthly), default=0) or 1

    parts = [svg_open(width, height, "Commits per month")]
    parts.append(text(left, 18, "COMMITS BY MONTH", 12, t["ink"], weight="600"))
    parts.append(text(width - right, 18, "√ scale, real values labeled", 10,
                      t["muted"], anchor="end"))
    baseline = top + plot_h
    parts.append(
        f'<line x1="{left}" y1="{baseline}" x2="{width - right}" y2="{baseline}" '
        f'stroke="{t["grid"]}" stroke-width="1"/>'
    )
    for i, (start, count) in enumerate(monthly):
        cx = left + i * slot + slot / 2
        if count:
            h = max(3, round(math.sqrt(count) / math.sqrt(max_v) * plot_h))
            bw = slot - gap * 2 - 24
            parts.append(
                f'<rect x="{cx - bw / 2}" y="{baseline - h}" width="{bw}" height="{h}" '
                f'rx="2" fill="{t["bar"]}"/>'
            )
            parts.append(text(cx, baseline - h - 6, f"{count}", 11, t["ink"],
                              anchor="middle"))
        else:
            parts.append(text(cx, baseline - 6, "·", 11, t["muted"], anchor="middle"))
        label = start.strftime("%b")
        parts.append(text(cx, baseline + 16, label, 10, t["muted"], anchor="middle"))
        if start.month == 1 or i == 0:
            parts.append(text(cx, baseline + 30, start.strftime("%Y"), 10,
                              t["muted"], anchor="middle"))
    parts.append("</svg>")
    return "".join(parts)


def hbar_panel(parts, x, y, w, title, rows, t, unit=""):
    """Horizontal bar list: rows of (label, value, display)."""
    parts.append(text(x, y, title, 12, t["ink"], weight="600"))
    bar_x = x + 110
    bar_w = w - 110 - 56
    max_v = max((v for _, v, _ in rows), default=0) or 1
    ry = y + 18
    for label, value, display in rows:
        parts.append(text(x, ry + 9, label, 11, t["muted"]))
        bw = max(2, round(value / max_v * bar_w)) if value else 0
        if bw:
            parts.append(
                f'<rect x="{bar_x}" y="{ry}" width="{bw}" height="12" rx="2" '
                f'fill="{t["bar"]}"/>'
            )
        parts.append(text(bar_x + bw + 8, ry + 9, display + unit, 11, t["ink"]))
        ry += 22
    return ry


def svg_languages(langs, theme):
    """Horizontal bars of language share (percent of bytes)."""
    t = THEMES[theme]
    width = 420
    height = 34 + len(langs) * 22 + 8
    parts = [svg_open(width, height, "Language share")]
    rows = [(name, pct, f"{pct:.1f}") for name, pct in langs]
    hbar_panel(parts, 16, 22, width - 32, "LANGUAGES · ALL REPOS", rows, t, unit="%")
    parts.append("</svg>")
    return "".join(parts)


def svg_overview(by_lang, activity, theme):
    """Two panels: repos by primary language, repos by recency of work."""
    t = THEMES[theme]
    panel_w = 380
    width = panel_w * 2 + 48
    n = max(len(by_lang), len(activity))
    height = 34 + n * 22 + 8
    parts = [svg_open(width, height, "Repository overview")]
    hbar_panel(parts, 16, 22, panel_w,
               "REPOS BY PRIMARY LANGUAGE",
               [(name, v, str(v)) for name, v in by_lang], t)
    hbar_panel(parts, panel_w + 48, 22, panel_w,
               "LAST TOUCHED",
               [(name, v, str(v)) for name, v in activity], t)
    parts.append("</svg>")
    return "".join(parts)


# --- assembly --------------------------------------------------------------

def picture(name, alt):
    return (
        "<picture>\n"
        f'  <source media="(prefers-color-scheme: dark)" srcset="assets/stats/{name}-dark.svg">\n'
        f'  <img alt="{alt}" src="assets/stats/{name}-light.svg">\n'
        "</picture>"
    )


def compute(total, repos, monthly):
    public = sum(1 for r in repos if not r["isPrivate"])
    private = total - public
    total_commits = sum(c for _, c in monthly)

    lang_bytes = {}
    for r in repos:
        if r["isFork"]:
            continue
        for e in r["languages"]["edges"]:
            lang_bytes[e["node"]["name"]] = lang_bytes.get(e["node"]["name"], 0) + e["size"]
    lang_total = sum(lang_bytes.values())
    langs = [
        (name, size / lang_total * 100)
        for name, size in sorted(lang_bytes.items(), key=lambda kv: -kv[1])[:8]
    ] if lang_total else []

    by_lang = {}
    for r in repos:
        name = (r["primaryLanguage"] or {}).get("name") or "No code yet"
        by_lang[name] = by_lang.get(name, 0) + 1
    top = sorted(by_lang.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(top) > 6:
        head, tail = top[:5], top[5:]
        top = head + [("Other", sum(v for _, v in tail))]

    now = datetime.now(timezone.utc)
    buckets = [("Past month", 30), ("Past quarter", 92), ("Past year", 365)]
    activity = []
    remaining = list(repos)
    for label, days in buckets:
        cutoff = now - timedelta(days=days)
        hit = [r for r in remaining if r["pushedAt"] and
               datetime.fromisoformat(r["pushedAt"].replace("Z", "+00:00")) >= cutoff]
        activity.append((label, len(hit)))
        remaining = [r for r in remaining if r not in hit]
    activity.append(("Earlier", len(remaining)))

    topics = {}
    for r in repos:
        for node in r["repositoryTopics"]["nodes"]:
            name = node["topic"]["name"]
            topics[name] = topics.get(name, 0) + 1

    return {
        "public": public, "private": private, "total_commits": total_commits,
        "langs": langs, "by_lang": top, "activity": activity, "topics": topics,
    }


def render(total, monthly, c):
    os.makedirs(STATS_DIR, exist_ok=True)
    for theme in THEMES:
        charts = {
            "commits": svg_commits(monthly, theme),
            "languages": svg_languages(c["langs"], theme),
            "overview": svg_overview(c["by_lang"], c["activity"], theme),
        }
        for name, svg in charts.items():
            path = os.path.join(STATS_DIR, f"{name}-{theme}.svg")
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg)

    start_label = datetime(*FIRST_MONTH, 1).strftime("%B %Y")
    lines = []
    lines.append(
        f"**{total} repositories** ({c['public']} public · {c['private']} private) · "
        f"**{c['total_commits']:,} commits since {start_label}**"
    )
    lines.append("")
    lines.append(picture("commits", "Commits per month"))
    lines.append("")
    lines.append(picture("overview", "Repository overview"))
    lines.append("")
    lines.append(picture("languages", "Language share across all repos"))
    if c["topics"]:
        top_topics = sorted(c["topics"].items(), key=lambda kv: (-kv[1], kv[0]))[:12]
        joined = " · ".join(f"{name} ({count})" for name, count in top_topics)
        lines.append("")
        lines.append(f"**In the repos:** {joined}")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines.append("")
    lines.append(f"<sub>Updated automatically · {stamp}</sub>")
    return "\n".join(lines)


def main():
    total, repos = fetch_repos()
    monthly = fetch_commits_by_month(repos)
    block = render(total, monthly, compute(total, repos, monthly))

    with open(README, encoding="utf-8") as f:
        readme = f.read()
    if START not in readme or END not in readme:
        sys.exit("Stats markers not found in README.md")
    new = re.sub(
        re.escape(START) + r".*?" + re.escape(END),
        START + "\n" + block + "\n" + END,
        readme,
        flags=re.S,
    )
    with open(README, "w", encoding="utf-8") as f:
        f.write(new)
    print("README stats block and SVG charts updated")


if __name__ == "__main__":
    main()
