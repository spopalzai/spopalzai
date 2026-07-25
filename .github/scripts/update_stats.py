#!/usr/bin/env python3
"""Regenerate the stats block in README.md from the GitHub GraphQL API.

Runs inside the update-stats workflow. Uses STATS_TOKEN (a PAT, sees
private repos) when set, otherwise falls back to the workflow's
GITHUB_TOKEN, which only sees public data.
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

LOGIN = "spopalzai"
README = os.path.join(os.path.dirname(__file__), "..", "..", "README.md")
API = "https://api.github.com/graphql"
START = "<!-- STATS:START -->"
END = "<!-- STATS:END -->"
MONTHS_BACK = 24
BAR_WIDTH = 20


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


def month_ranges(n):
    """Last n calendar months (oldest first), ending with the current month."""
    now = datetime.now(timezone.utc)
    first = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    starts = []
    y, m = first.year, first.month
    for _ in range(n):
        starts.append(datetime(y, m, 1, tzinfo=timezone.utc))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    starts.reverse()
    ranges = []
    for i, s in enumerate(starts):
        e = starts[i + 1] - timedelta(seconds=1) if i + 1 < len(starts) else now
        ranges.append((s, e))
    return ranges


def fetch_commits_by_month():
    ranges = month_ranges(MONTHS_BACK)
    # contributionsCollection spans at most a year per call, so alias one
    # call per month in a single query.
    parts = []
    for i, (s, e) in enumerate(ranges):
        parts.append(
            f'm{i}: contributionsCollection(from: "{s.isoformat()}", '
            f'to: "{e.isoformat()}") {{ totalCommitContributions }}'
        )
    query = (
        'query($login: String!) { user(login: $login) { '
        + " ".join(parts)
        + " } }"
    )
    data = gql(query, {"login": LOGIN})
    user = data["user"]
    return [
        (s, user[f"m{i}"]["totalCommitContributions"])
        for i, (s, e) in enumerate(ranges)
    ]


def bar(value, max_value, width=BAR_WIDTH):
    if max_value <= 0:
        return ""
    return "█" * max(1 if value else 0, round(value / max_value * width))


def render(total, repos, monthly):
    public = sum(1 for r in repos if not r["isPrivate"])
    private = total - public

    total_commits = sum(c for _, c in monthly)
    max_commits = max((c for _, c in monthly), default=0)

    lang_bytes = {}
    for r in repos:
        if r["isFork"]:
            continue
        for e in r["languages"]["edges"]:
            lang_bytes[e["node"]["name"]] = lang_bytes.get(e["node"]["name"], 0) + e["size"]
    lang_total = sum(lang_bytes.values())

    topics = {}
    for r in repos:
        for t in r["repositoryTopics"]["nodes"]:
            name = t["topic"]["name"]
            topics[name] = topics.get(name, 0) + 1

    lines = []
    lines.append(
        f"**{total} repositories** ({public} public · {private} private) · "
        f"**{total_commits:,} commits in the last {MONTHS_BACK} months**"
    )
    lines.append("")
    lines.append("```text")
    for start, count in monthly:
        label = start.strftime("%b %Y")
        lines.append(f"{label:<9} {bar(count, max_commits):<{BAR_WIDTH}} {count}")
    lines.append("```")

    if lang_total:
        lines.append("")
        lines.append("**Languages** (all repos, including private)")
        lines.append("")
        lines.append("```text")
        top = sorted(lang_bytes.items(), key=lambda kv: -kv[1])[:8]
        for name, size in top:
            pct = size / lang_total * 100
            filled = round(pct / 100 * BAR_WIDTH)
            lines.append(
                f"{name:<13} {'█' * filled}{'░' * (BAR_WIDTH - filled)} {pct:5.1f}%"
            )
        lines.append("```")

    if topics:
        lines.append("")
        top_topics = sorted(topics.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
        joined = " · ".join(f"{name} ({count})" for name, count in top_topics)
        lines.append(f"**In the repos:** {joined}")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines.append("")
    lines.append(f"<sub>Updated automatically · {stamp}</sub>")
    return "\n".join(lines)


def main():
    total, repos = fetch_repos()
    monthly = fetch_commits_by_month()
    block = render(total, repos, monthly)

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
    print("README stats block updated")


if __name__ == "__main__":
    main()
