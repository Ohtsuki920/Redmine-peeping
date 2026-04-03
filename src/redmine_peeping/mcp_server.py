"""Redmine MCP Server – AIエージェントがRedmine情報を細かく確認・レポートするMCPサーバー.

Tools:
  ── 検索・一覧 ──
  - list_projects          : プロジェクト一覧
  - search_users           : ユーザー検索
  - list_issues            : チケット一覧（フィルタ付き）
  - get_issue_detail       : チケット詳細（journals付き）

  ── 分析・レポート ──
  - get_user_activity      : 担当者の活動状況
  - get_weekly_report      : 週報生成
  - get_project_summary    : プロジェクト概要
  - get_overdue_issues     : 期限超過チケット一覧

  ── スケジュール分析 ──
  - get_schedule_status    : 担当者のスケジュール状況（超過/今週/来週/将来を分類）
  - get_gantt_data         : ガントチャート用の日程データ取得

  ── 通知・アラート ──
  - get_stalled_issues     : 一定期間更新がない停滞チケットを検出
  - get_unassigned_issues  : 担当者未割当のチケット一覧

  ── トレンド・傾向分析 ──
  - get_issue_history      : チケットのステータス遷移履歴を時系列表示
  - get_velocity           : 期間内のチケットクローズ速度を測定

  ── 更新 ──
  - update_issue           : チケット更新（ステータス・進捗率・日程・コメント）

Resources:
  - redmine://config       : 現在の接続設定

Prompts:
  - weekly_report          : 週報分析プロンプト
  - issue_review           : チケットレビュープロンプト
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

# ── Redmine HTTPクライアント (async) ──────────────────────────


@dataclass(frozen=True)
class RedmineConfig:
    """Redmine接続設定."""

    base_url: str
    api_key: str
    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    verify_tls: bool = True

    @classmethod
    def from_env(cls) -> RedmineConfig:
        """環境変数 or .redmine_api_key から設定を読み込む."""
        base_url = os.environ.get(
            "REDMINE_BASE_URL",
            "https://sm-eqp-design.cloud.redmine.jp",
        ).strip().rstrip("/")

        api_key = os.environ.get("REDMINE_API_KEY", "").strip()
        if not api_key:
            for path in [Path(".redmine_api_key"), Path(__file__).parent / ".redmine_api_key"]:
                if path.exists():
                    api_key = path.read_text(encoding="utf-8").strip()
                    break

        if not api_key:
            raise RuntimeError(
                "REDMINE_API_KEY が見つかりません。"
                "環境変数 REDMINE_API_KEY を設定するか .redmine_api_key ファイルを作成してください。"
            )
        return cls(base_url=base_url, api_key=api_key)


class AsyncRedmineClient:
    """Redmine REST API 非同期クライアント."""

    def __init__(self, config: RedmineConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={
                "X-Redmine-API-Key": config.api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(
                connect=config.connect_timeout,
                read=config.read_timeout,
                write=config.read_timeout,
                pool=config.connect_timeout,
            ),
            verify=config.verify_tls,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def put(self, path: str, json_body: dict[str, Any]) -> int:
        resp = await self._client.put(path, json=json_body)
        resp.raise_for_status()
        return resp.status_code

    async def get_all_pages(
        self, path: str, key: str, params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """ページネーション付きで全件取得."""
        params = dict(params or {})
        params.setdefault("limit", 100)
        all_items: list[dict[str, Any]] = []
        offset = 0
        while True:
            params["offset"] = offset
            data = await self.get(path, params)
            items = data.get(key, [])
            all_items.extend(items)
            if len(items) < params["limit"]:
                break
            offset += params["limit"]
        return all_items


# ── App Context (lifespan) ──────────────────────────────────


@dataclass
class AppContext:
    client: AsyncRedmineClient
    config: RedmineConfig


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """サーバー起動時にRedmineクライアントを初期化."""
    config = RedmineConfig.from_env()
    client = AsyncRedmineClient(config)
    try:
        yield AppContext(client=client, config=config)
    finally:
        await client.close()


# ── MCP Server ──────────────────────────────────────────────

mcp = FastMCP(
    "Redmine Agent",
    instructions=(
        "Redmine プロジェクト管理システムに接続し、チケット情報の検索・分析・レポート生成を行うMCPサーバーです。"
        "AIエージェントはこのサーバーのツールを使って担当者の作業状況を細かく確認し、レポートを作成できます。"
    ),
    lifespan=app_lifespan,
)


def _get_app(ctx: Context[ServerSession, AppContext]) -> AppContext:
    return ctx.request_context.lifespan_context


# ────────────────────────────────────────────────────────────
# Resources
# ────────────────────────────────────────────────────────────


@mcp.resource("redmine://config")
def get_config_resource() -> str:
    """現在のRedmine接続設定を返す（APIキーは非表示）."""
    try:
        config = RedmineConfig.from_env()
        return json.dumps({
            "base_url": config.base_url,
            "connect_timeout": config.connect_timeout,
            "read_timeout": config.read_timeout,
            "verify_tls": config.verify_tls,
            "api_key": "***REDACTED***",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ────────────────────────────────────────────────────────────
# Tools : 検索・一覧
# ────────────────────────────────────────────────────────────


@mcp.tool()
async def list_projects(ctx: Context[ServerSession, AppContext]) -> str:
    """Redmineのプロジェクト一覧を取得する。

    Returns:
        プロジェクトの一覧（JSON形式: id, name, identifier, description, status）
    """
    app = _get_app(ctx)
    await ctx.info("プロジェクト一覧を取得中...")

    projects = await app.client.get_all_pages("/projects.json", "projects")

    result = []
    for p in projects:
        result.append({
            "id": p["id"],
            "name": p.get("name"),
            "identifier": p.get("identifier"),
            "description": (p.get("description") or "")[:100],
            "status": p.get("status"),
        })

    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def search_users(
    ctx: Context[ServerSession, AppContext],
    name: str,
) -> str:
    """ユーザーを名前で検索する。

    Args:
        name: 検索するユーザー名（姓名の一部でOK）

    Returns:
        マッチしたユーザーの一覧（JSON形式: id, login, firstname, lastname, mail）
    """
    app = _get_app(ctx)
    await ctx.info(f"ユーザー '{name}' を検索中...")

    data = await app.client.get("/users.json", params={"name": name, "limit": 25})
    users = data.get("users", [])

    result = []
    for u in users:
        result.append({
            "id": u["id"],
            "login": u.get("login"),
            "firstname": u.get("firstname"),
            "lastname": u.get("lastname"),
            "mail": u.get("mail"),
            "name": f'{u.get("lastname", "")}{u.get("firstname", "")}',
        })

    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def list_issues(
    ctx: Context[ServerSession, AppContext],
    project_id: int | None = None,
    assigned_to_id: int | None = None,
    status: str = "open",
    tracker_id: int | None = None,
    updated_since: str | None = None,
    updated_until: str | None = None,
    limit: int = 50,
) -> str:
    """チケット一覧を条件付きで取得する。

    Args:
        project_id: プロジェクトID（省略時: 全プロジェクト）
        assigned_to_id: 担当者のユーザーID
        status: "open", "closed", "*"（全て）のいずれか
        tracker_id: トラッカーID
        updated_since: 更新日の開始 (YYYY-MM-DD)
        updated_until: 更新日の終了 (YYYY-MM-DD)
        limit: 最大取得件数（デフォルト50、最大200）

    Returns:
        チケットの一覧（JSON形式）
    """
    app = _get_app(ctx)

    params: dict[str, Any] = {
        "limit": min(limit, 200),
        "sort": "updated_on:desc",
    }
    if project_id:
        params["project_id"] = project_id
    if assigned_to_id:
        params["assigned_to_id"] = assigned_to_id
    if tracker_id:
        params["tracker_id"] = tracker_id

    # ステータスフィルタ
    if status == "open":
        params["status_id"] = "open"
    elif status == "closed":
        params["status_id"] = "closed"
    else:
        params["status_id"] = "*"

    # 更新日フィルタ
    if updated_since and updated_until:
        params["updated_on"] = f"><{updated_since}|{updated_until}"
    elif updated_since:
        params["updated_on"] = f">={updated_since}"
    elif updated_until:
        params["updated_on"] = f"<={updated_until}"

    await ctx.info("チケット一覧を取得中...")

    if limit <= 100:
        data = await app.client.get("/issues.json", params)
        issues = data.get("issues", [])
        total = data.get("total_count", len(issues))
    else:
        issues = await app.client.get_all_pages("/issues.json", "issues", params)
        total = len(issues)

    result = []
    for iss in issues[:limit]:
        result.append({
            "id": iss["id"],
            "subject": iss.get("subject"),
            "project": iss.get("project", {}).get("name"),
            "tracker": iss.get("tracker", {}).get("name"),
            "status": iss.get("status", {}).get("name"),
            "priority": iss.get("priority", {}).get("name"),
            "assigned_to": iss.get("assigned_to", {}).get("name") if iss.get("assigned_to") else None,
            "done_ratio": iss.get("done_ratio"),
            "start_date": iss.get("start_date"),
            "due_date": iss.get("due_date"),
            "updated_on": iss.get("updated_on"),
        })

    return json.dumps({"total_count": total, "issues": result}, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_issue_detail(
    ctx: Context[ServerSession, AppContext],
    issue_id: int,
    include_journals: bool = True,
) -> str:
    """チケットの詳細情報を取得する（更新履歴・コメント含む）。

    Args:
        issue_id: チケット番号
        include_journals: 更新履歴(journals)を含めるか（デフォルト: True）

    Returns:
        チケットの詳細情報（JSON形式: 基本情報 + journals）
    """
    app = _get_app(ctx)
    await ctx.info(f"チケット #{issue_id} の詳細を取得中...")

    params = {}
    if include_journals:
        params["include"] = "journals"

    data = await app.client.get(f"/issues/{issue_id}.json", params)
    issue = data.get("issue", {})

    # 基本情報を整形
    detail: dict[str, Any] = {
        "id": issue.get("id"),
        "subject": issue.get("subject"),
        "description": issue.get("description"),
        "project": issue.get("project", {}).get("name"),
        "tracker": issue.get("tracker", {}).get("name"),
        "status": issue.get("status", {}).get("name"),
        "priority": issue.get("priority", {}).get("name"),
        "assigned_to": issue.get("assigned_to", {}).get("name") if issue.get("assigned_to") else None,
        "author": issue.get("author", {}).get("name") if issue.get("author") else None,
        "done_ratio": issue.get("done_ratio"),
        "start_date": issue.get("start_date"),
        "due_date": issue.get("due_date"),
        "estimated_hours": issue.get("estimated_hours"),
        "spent_hours": issue.get("spent_hours"),
        "created_on": issue.get("created_on"),
        "updated_on": issue.get("updated_on"),
        "closed_on": issue.get("closed_on"),
        "custom_fields": issue.get("custom_fields"),
    }

    # 更新履歴を整形
    if include_journals:
        journals_raw = issue.get("journals", [])
        journals = []
        for j in journals_raw:
            journal_entry: dict[str, Any] = {
                "id": j.get("id"),
                "user": j.get("user", {}).get("name"),
                "created_on": j.get("created_on"),
                "notes": j.get("notes") or None,
            }
            # フィールド変更
            changes = []
            for d in j.get("details", []):
                changes.append({
                    "field": d.get("name"),
                    "old_value": d.get("old_value"),
                    "new_value": d.get("new_value"),
                })
            if changes:
                journal_entry["changes"] = changes
            journals.append(journal_entry)
        detail["journals"] = journals

    return json.dumps(detail, ensure_ascii=False, indent=2)


# ────────────────────────────────────────────────────────────
# Tools : 分析・レポート
# ────────────────────────────────────────────────────────────


FIELD_LABELS: dict[str, str] = {
    "status_id": "ステータス",
    "done_ratio": "進捗率",
    "assigned_to_id": "担当者",
    "start_date": "開始日",
    "due_date": "期日",
    "priority_id": "優先度",
    "subject": "題名",
    "tracker_id": "トラッカー",
    "fixed_version_id": "対象バージョン",
    "category_id": "カテゴリ",
    "parent_id": "親チケット",
    "estimated_hours": "予定工数",
}


@mcp.tool()
async def get_user_activity(
    ctx: Context[ServerSession, AppContext],
    user_id: int,
    since: str | None = None,
    until: str | None = None,
    days: int = 7,
) -> str:
    """指定ユーザーの活動状況（チケット更新・作業時間）を取得する。

    Args:
        user_id: ユーザーID（search_usersで事前に検索）
        since: 開始日 (YYYY-MM-DD)、省略時はdays前
        until: 終了日 (YYYY-MM-DD)、省略時は今日
        days: since省略時の遡り日数（デフォルト7）

    Returns:
        ユーザーの活動サマリ（JSON形式）
    """
    app = _get_app(ctx)

    today = datetime.date.today()
    if not until:
        until = today.isoformat()
    if not since:
        since = (today - datetime.timedelta(days=days)).isoformat()

    await ctx.info(f"ユーザー {user_id} の活動状況を取得中 ({since} 〜 {until})...")

    # チケット取得
    issues = await app.client.get_all_pages("/issues.json", "issues", {
        "assigned_to_id": user_id,
        "updated_on": f"><{since}|{until}",
        "status_id": "*",
        "sort": "updated_on:desc",
        "limit": 100,
    })

    # 作業時間取得
    time_entries = await app.client.get_all_pages("/time_entries.json", "time_entries", {
        "user_id": user_id,
        "from": since,
        "to": until,
        "limit": 100,
    })

    # ステータス集計
    status_count: dict[str, int] = {}
    for iss in issues:
        st = iss.get("status", {}).get("name", "不明")
        status_count[st] = status_count.get(st, 0) + 1

    # 作業時間集計
    total_hours = sum(e.get("hours", 0) for e in time_entries)
    daily_hours: dict[str, float] = {}
    for e in time_entries:
        day = e.get("spent_on", "")
        daily_hours[day] = daily_hours.get(day, 0) + e.get("hours", 0)

    result = {
        "user_id": user_id,
        "period": {"from": since, "to": until},
        "summary": {
            "total_issues_updated": len(issues),
            "status_breakdown": status_count,
            "total_hours": round(total_hours, 1),
            "daily_hours": dict(sorted(daily_hours.items())),
        },
        "issues": [
            {
                "id": iss["id"],
                "subject": iss.get("subject"),
                "status": iss.get("status", {}).get("name"),
                "done_ratio": iss.get("done_ratio"),
                "updated_on": iss.get("updated_on"),
                "due_date": iss.get("due_date"),
            }
            for iss in issues
        ],
        "time_entries": [
            {
                "spent_on": e.get("spent_on"),
                "issue_id": e.get("issue", {}).get("id"),
                "hours": e.get("hours"),
                "activity": e.get("activity", {}).get("name"),
                "comments": e.get("comments") or None,
            }
            for e in time_entries
        ],
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_weekly_report(
    ctx: Context[ServerSession, AppContext],
    user_name: str,
    weeks_ago: int = 0,
) -> str:
    """担当者の週報を生成する。ユーザー検索→チケット取得→詳細取得→レポート生成を一括実行。

    Args:
        user_name: 担当者名（例: "大月"）
        weeks_ago: 何週間前の週報か（0=今週、1=先週）

    Returns:
        Markdown形式の週報テキスト
    """
    app = _get_app(ctx)

    # 期間算出
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday() + 7 * weeks_ago)
    friday = monday + datetime.timedelta(days=4)
    since = monday.isoformat()
    until = friday.isoformat()

    # ユーザー検索
    await ctx.info(f"ユーザー '{user_name}' を検索中...")
    user_data = await app.client.get("/users.json", params={"name": user_name, "limit": 25})
    users = user_data.get("users", [])
    user_id = None
    for u in users:
        full = f'{u.get("lastname", "")}{u.get("firstname", "")}'
        if user_name in (u.get("login"), u.get("lastname"), u.get("firstname"), full, u.get("name", "")):
            user_id = u["id"]
            break
    if user_id is None and users:
        user_id = users[0]["id"]
    if user_id is None:
        return f"❌ ユーザー '{user_name}' が見つかりません。"

    await ctx.report_progress(0.1, 1.0, "チケット取得中")

    # チケット取得
    issues = await app.client.get_all_pages("/issues.json", "issues", {
        "assigned_to_id": user_id,
        "updated_on": f"><{since}|{until}",
        "status_id": "*",
        "sort": "updated_on:desc",
        "limit": 100,
    })

    await ctx.report_progress(0.3, 1.0, "チケット詳細取得中")

    # 各チケットの詳細取得
    issue_details: dict[int, dict[str, Any]] = {}
    for i, iss in enumerate(issues):
        iid = iss["id"]
        try:
            data = await app.client.get(f"/issues/{iid}.json", {"include": "journals"})
            issue_details[iid] = data.get("issue", {})
        except Exception:
            pass
        await ctx.report_progress(0.3 + 0.4 * (i + 1) / max(len(issues), 1), 1.0, f"#{iid} 取得完了")

    # 作業時間取得
    await ctx.report_progress(0.7, 1.0, "作業時間取得中")
    time_entries = await app.client.get_all_pages("/time_entries.json", "time_entries", {
        "user_id": user_id,
        "from": since,
        "to": until,
        "limit": 100,
    })

    await ctx.report_progress(0.9, 1.0, "レポート生成中")

    # レポート生成
    lines: list[str] = []
    lines.append(f"# 週報 - {user_name}")
    lines.append(f"**対象期間:** {since} 〜 {until}")
    lines.append(f"**担当者ID:** {user_id}")
    lines.append("")

    # チケット一覧
    lines.append(f"## 対応チケット一覧（{len(issues)} 件）")
    lines.append("")
    if issues:
        lines.append("| # | プロジェクト | トラッカー | ステータス | 進捗率 | 題名 |")
        lines.append("|---|------------|----------|----------|--------|------|")
        for iss in issues:
            lines.append(
                f"| #{iss['id']} "
                f"| {iss.get('project', {}).get('name', '-')} "
                f"| {iss.get('tracker', {}).get('name', '-')} "
                f"| {iss.get('status', {}).get('name', '-')} "
                f"| {iss.get('done_ratio', 0)}% "
                f"| {iss.get('subject', '')} |"
            )
    lines.append("")

    # ステータス別集計
    status_count: dict[str, int] = {}
    for iss in issues:
        st = iss.get("status", {}).get("name", "不明")
        status_count[st] = status_count.get(st, 0) + 1
    if status_count:
        lines.append("## ステータス別集計")
        lines.append("")
        lines.append("| ステータス | 件数 |")
        lines.append("|----------|------|")
        for st, cnt in sorted(status_count.items()):
            lines.append(f"| {st} | {cnt} |")
        lines.append("")

    # チケット別詳細
    lines.append("## チケット別詳細")
    lines.append("")
    for iss in issues:
        iid = iss["id"]
        subj = iss.get("subject", "")
        status = iss.get("status", {}).get("name", "-")
        done = iss.get("done_ratio", 0)

        lines.append(f"### #{iid} {subj}")
        lines.append("")
        lines.append(f"- **ステータス:** {status}")
        lines.append(f"- **進捗率:** {done}%")
        lines.append(f"- **開始日:** {iss.get('start_date') or '-'}")
        lines.append(f"- **期日:** {iss.get('due_date') or '-'}")
        lines.append("")

        detail = issue_details.get(iid)
        if detail:
            journals = detail.get("journals", [])
            period_journals = [
                j for j in journals
                if since <= (j.get("created_on", "")[:10]) <= until
            ]
            if period_journals:
                lines.append("**期間内の更新履歴:**")
                lines.append("")
                for j in period_journals:
                    ts = j.get("created_on", "")[:16].replace("T", " ")
                    author = j.get("user", {}).get("name", "不明")
                    notes = (j.get("notes") or "").strip()
                    changes = []
                    for d in j.get("details", []):
                        label = FIELD_LABELS.get(d.get("name", ""), d.get("name", ""))
                        changes.append(f"{label}: {d.get('old_value')} → {d.get('new_value')}")

                    parts = [f"- **{ts}** ({author})"]
                    if changes:
                        parts.append(f"  変更: {'; '.join(changes)}")
                    if notes:
                        note_short = notes.replace("\r\n", " ").replace("\n", " ")[:200]
                        parts.append(f"  コメント: {note_short}")
                    lines.append("\n".join(parts))
                lines.append("")
        lines.append("---")
        lines.append("")

    # 作業時間
    total_hours = sum(e.get("hours", 0) for e in time_entries)
    lines.append(f"## 作業時間（合計: {total_hours:.1f} 時間）")
    lines.append("")
    if time_entries:
        lines.append("| 日付 | チケット | 活動 | 時間 | コメント |")
        lines.append("|------|---------|------|------|---------|")
        for e in sorted(time_entries, key=lambda x: x.get("spent_on", "")):
            lines.append(
                f"| {e.get('spent_on', '-')} "
                f"| #{e.get('issue', {}).get('id', '-')} "
                f"| {e.get('activity', {}).get('name', '-')} "
                f"| {e.get('hours', 0):.1f}h "
                f"| {e.get('comments') or ''} |"
            )
    else:
        lines.append("_対象期間に作業時間の記録はありません。_")
    lines.append("")
    lines.append("---")
    lines.append(f"_生成日時: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}_")

    await ctx.report_progress(1.0, 1.0, "完了")
    return "\n".join(lines)


@mcp.tool()
async def get_project_summary(
    ctx: Context[ServerSession, AppContext],
    project_id: int,
) -> str:
    """プロジェクトの概要（チケット統計・メンバー活動）を取得する。

    Args:
        project_id: プロジェクトID

    Returns:
        プロジェクト概要（JSON形式: ステータス別件数、担当者別件数、期限超過件数）
    """
    app = _get_app(ctx)
    await ctx.info(f"プロジェクト {project_id} の概要を取得中...")

    issues = await app.client.get_all_pages("/issues.json", "issues", {
        "project_id": project_id,
        "status_id": "*",
        "limit": 100,
    })

    today = datetime.date.today().isoformat()

    # 集計
    status_count: dict[str, int] = {}
    assignee_count: dict[str, int] = {}
    overdue_count = 0
    no_due_date_count = 0

    for iss in issues:
        # ステータス別
        st = iss.get("status", {}).get("name", "不明")
        status_count[st] = status_count.get(st, 0) + 1

        # 担当者別
        assignee = iss.get("assigned_to", {}).get("name", "未割当") if iss.get("assigned_to") else "未割当"
        assignee_count[assignee] = assignee_count.get(assignee, 0) + 1

        # 期限超過
        due = iss.get("due_date")
        is_closed = iss.get("status", {}).get("is_closed", False)
        if due and due < today and not is_closed:
            overdue_count += 1
        if not due:
            no_due_date_count += 1

    result = {
        "project_id": project_id,
        "total_issues": len(issues),
        "status_breakdown": dict(sorted(status_count.items())),
        "assignee_breakdown": dict(sorted(assignee_count.items(), key=lambda x: -x[1])),
        "overdue_count": overdue_count,
        "no_due_date_count": no_due_date_count,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_overdue_issues(
    ctx: Context[ServerSession, AppContext],
    project_id: int | None = None,
    assigned_to_id: int | None = None,
) -> str:
    """期限超過のチケット一覧を取得する。

    Args:
        project_id: プロジェクトID（省略時: 全プロジェクト）
        assigned_to_id: 担当者ID（省略時: 全担当者）

    Returns:
        期限超過チケットの一覧（JSON形式: 超過日数付き）
    """
    app = _get_app(ctx)
    await ctx.info("期限超過チケットを検索中...")

    today = datetime.date.today()
    params: dict[str, Any] = {
        "status_id": "open",
        "due_date": f"<={today.isoformat()}",
        "sort": "due_date:asc",
        "limit": 100,
    }
    if project_id:
        params["project_id"] = project_id
    if assigned_to_id:
        params["assigned_to_id"] = assigned_to_id

    issues = await app.client.get_all_pages("/issues.json", "issues", params)

    result = []
    for iss in issues:
        due_str = iss.get("due_date")
        overdue_days = (today - datetime.date.fromisoformat(due_str)).days if due_str else 0
        result.append({
            "id": iss["id"],
            "subject": iss.get("subject"),
            "project": iss.get("project", {}).get("name"),
            "assigned_to": iss.get("assigned_to", {}).get("name") if iss.get("assigned_to") else None,
            "status": iss.get("status", {}).get("name"),
            "due_date": due_str,
            "overdue_days": overdue_days,
            "done_ratio": iss.get("done_ratio"),
        })

    return json.dumps({
        "total": len(result),
        "overdue_issues": sorted(result, key=lambda x: -x["overdue_days"]),
    }, ensure_ascii=False, indent=2)


# ────────────────────────────────────────────────────────────
# Tools : スケジュール分析
# ────────────────────────────────────────────────────────────


@mcp.tool()
async def get_schedule_status(
    ctx: Context[ServerSession, AppContext],
    user_id: int | None = None,
    project_id: int | None = None,
) -> str:
    """担当者またはプロジェクトのスケジュール状況を分析する。

    チケットを「期限超過」「今週期限」「来週期限」「将来」「期日未設定」に分類し、
    進捗率分布とともに返す。

    Args:
        user_id: 担当者のユーザーID（省略時: project_idが必須）
        project_id: プロジェクトID（省略時: user_idが必須）

    Returns:
        スケジュール状況の分析結果（JSON形式）
    """
    app = _get_app(ctx)
    if not user_id and not project_id:
        return "user_id または project_id のいずれかを指定してください。"

    await ctx.info("スケジュール状況を分析中...")

    params: dict[str, Any] = {
        "status_id": "open",
        "sort": "due_date:asc",
        "limit": 100,
    }
    if user_id:
        params["assigned_to_id"] = user_id
    if project_id:
        params["project_id"] = project_id

    issues = await app.client.get_all_pages("/issues.json", "issues", params)

    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    friday = monday + datetime.timedelta(days=4)
    next_monday = monday + datetime.timedelta(days=7)
    next_friday = next_monday + datetime.timedelta(days=4)

    categories: dict[str, list[dict[str, Any]]] = {
        "overdue": [],
        "this_week": [],
        "next_week": [],
        "future": [],
        "no_due_date": [],
    }

    for iss in issues:
        entry = {
            "id": iss["id"],
            "subject": iss.get("subject"),
            "tracker": iss.get("tracker", {}).get("name"),
            "status": iss.get("status", {}).get("name"),
            "done_ratio": iss.get("done_ratio", 0),
            "start_date": iss.get("start_date"),
            "due_date": iss.get("due_date"),
            "project": iss.get("project", {}).get("name"),
            "assigned_to": iss.get("assigned_to", {}).get("name") if iss.get("assigned_to") else None,
        }

        due_str = iss.get("due_date")
        if not due_str:
            categories["no_due_date"].append(entry)
            continue

        due = datetime.date.fromisoformat(due_str)
        if due < today:
            entry["overdue_days"] = (today - due).days
            categories["overdue"].append(entry)
        elif due <= friday:
            entry["days_remaining"] = (due - today).days
            categories["this_week"].append(entry)
        elif due <= next_friday:
            entry["days_remaining"] = (due - today).days
            categories["next_week"].append(entry)
        else:
            entry["days_remaining"] = (due - today).days
            categories["future"].append(entry)

    # 進捗率分布
    ratio_dist = {"0%": 0, "1-30%": 0, "31-60%": 0, "61-90%": 0, "91-99%": 0, "100%": 0}
    for iss in issues:
        d = iss.get("done_ratio", 0)
        if d == 0:
            ratio_dist["0%"] += 1
        elif d <= 30:
            ratio_dist["1-30%"] += 1
        elif d <= 60:
            ratio_dist["31-60%"] += 1
        elif d <= 90:
            ratio_dist["61-90%"] += 1
        elif d < 100:
            ratio_dist["91-99%"] += 1
        else:
            ratio_dist["100%"] += 1

    # 超過チケットを超過日数で降順ソート
    categories["overdue"].sort(key=lambda x: -x.get("overdue_days", 0))

    result = {
        "analysis_date": today.isoformat(),
        "period": {
            "this_week": f"{monday.isoformat()} 〜 {friday.isoformat()}",
            "next_week": f"{next_monday.isoformat()} 〜 {next_friday.isoformat()}",
        },
        "summary": {
            "total_open": len(issues),
            "overdue": len(categories["overdue"]),
            "this_week": len(categories["this_week"]),
            "next_week": len(categories["next_week"]),
            "future": len(categories["future"]),
            "no_due_date": len(categories["no_due_date"]),
        },
        "progress_distribution": ratio_dist,
        "categories": categories,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_gantt_data(
    ctx: Context[ServerSession, AppContext],
    project_id: int | None = None,
    assigned_to_id: int | None = None,
    months: int = 3,
) -> str:
    """ガントチャート用の日程データを取得する。

    チケットの開始日・期日・進捗率・親子関係を含むデータを返す。

    Args:
        project_id: プロジェクトID（省略時: 全プロジェクト）
        assigned_to_id: 担当者ID（省略時: 全担当者）
        months: 取得する月数（デフォルト3ヶ月）

    Returns:
        ガントチャート用データ（JSON形式）
    """
    app = _get_app(ctx)
    await ctx.info("ガントチャートデータを取得中...")

    today = datetime.date.today()
    range_start = today - datetime.timedelta(days=30)
    range_end = today + datetime.timedelta(days=30 * months)

    params: dict[str, Any] = {
        "status_id": "*",
        "sort": "start_date:asc",
        "limit": 100,
    }
    if project_id:
        params["project_id"] = project_id
    if assigned_to_id:
        params["assigned_to_id"] = assigned_to_id

    issues = await app.client.get_all_pages("/issues.json", "issues", params)

    gantt_items = []
    for iss in issues:
        start_str = iss.get("start_date")
        due_str = iss.get("due_date")

        # 期間内のチケットのみ
        if start_str and due_str:
            start = datetime.date.fromisoformat(start_str)
            due = datetime.date.fromisoformat(due_str)
            if due < range_start or start > range_end:
                continue
            duration_days = (due - start).days + 1
        elif start_str:
            start = datetime.date.fromisoformat(start_str)
            if start > range_end:
                continue
            duration_days = None
        elif due_str:
            due = datetime.date.fromisoformat(due_str)
            if due < range_start:
                continue
            duration_days = None
        else:
            continue  # 日付がないチケットはスキップ

        parent = iss.get("parent")
        gantt_items.append({
            "id": iss["id"],
            "subject": iss.get("subject"),
            "tracker": iss.get("tracker", {}).get("name"),
            "status": iss.get("status", {}).get("name"),
            "assigned_to": iss.get("assigned_to", {}).get("name") if iss.get("assigned_to") else None,
            "start_date": start_str,
            "due_date": due_str,
            "duration_days": duration_days,
            "done_ratio": iss.get("done_ratio", 0),
            "parent_id": parent.get("id") if isinstance(parent, dict) else None,
            "is_overdue": bool(due_str and datetime.date.fromisoformat(due_str) < today
                               and iss.get("status", {}).get("is_closed") is not True),
        })

    return json.dumps({
        "range": {"from": range_start.isoformat(), "to": range_end.isoformat()},
        "total_items": len(gantt_items),
        "items": gantt_items,
    }, ensure_ascii=False, indent=2)


# ────────────────────────────────────────────────────────────
# Tools : 通知・アラート
# ────────────────────────────────────────────────────────────


@mcp.tool()
async def get_stalled_issues(
    ctx: Context[ServerSession, AppContext],
    project_id: int | None = None,
    assigned_to_id: int | None = None,
    stalled_days: int = 14,
) -> str:
    """一定期間更新がない停滞チケットを検出する。

    指定日数以上更新がないオープンチケットを抽出し、停滞日数の降順で返す。

    Args:
        project_id: プロジェクトID（省略時: 全プロジェクト）
        assigned_to_id: 担当者ID（省略時: 全担当者）
        stalled_days: 停滞とみなす日数（デフォルト14日）

    Returns:
        停滞チケットの一覧（JSON形式: 停滞日数付き）
    """
    app = _get_app(ctx)
    await ctx.info(f"{stalled_days}日以上更新がない停滞チケットを検索中...")

    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=stalled_days)

    params: dict[str, Any] = {
        "status_id": "open",
        "updated_on": f"<={cutoff.isoformat()}",
        "sort": "updated_on:asc",
        "limit": 100,
    }
    if project_id:
        params["project_id"] = project_id
    if assigned_to_id:
        params["assigned_to_id"] = assigned_to_id

    issues = await app.client.get_all_pages("/issues.json", "issues", params)

    result = []
    for iss in issues:
        updated_str = iss.get("updated_on", "")[:10]
        if updated_str:
            last_update = datetime.date.fromisoformat(updated_str)
            days_since = (today - last_update).days
        else:
            days_since = 999

        result.append({
            "id": iss["id"],
            "subject": iss.get("subject"),
            "project": iss.get("project", {}).get("name"),
            "tracker": iss.get("tracker", {}).get("name"),
            "status": iss.get("status", {}).get("name"),
            "assigned_to": iss.get("assigned_to", {}).get("name") if iss.get("assigned_to") else None,
            "done_ratio": iss.get("done_ratio", 0),
            "due_date": iss.get("due_date"),
            "last_updated": updated_str,
            "stalled_days": days_since,
            "is_overdue": bool(iss.get("due_date") and iss["due_date"] < today.isoformat()),
        })

    result.sort(key=lambda x: -x["stalled_days"])

    return json.dumps({
        "threshold_days": stalled_days,
        "total": len(result),
        "stalled_issues": result,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_unassigned_issues(
    ctx: Context[ServerSession, AppContext],
    project_id: int | None = None,
) -> str:
    """担当者が未割当のオープンチケット一覧を取得する。

    Args:
        project_id: プロジェクトID（省略時: 全プロジェクト）

    Returns:
        未割当チケットの一覧（JSON形式）
    """
    app = _get_app(ctx)
    await ctx.info("担当者未割当のチケットを検索中...")

    today = datetime.date.today()

    # Redmine API: assigned_to_id=!* は「担当者なし」を意味する場合がある
    # ただしRedmine APIの実装によって異なるため、全取得してフィルタする
    params: dict[str, Any] = {
        "status_id": "open",
        "sort": "created_on:desc",
        "limit": 100,
    }
    if project_id:
        params["project_id"] = project_id

    issues = await app.client.get_all_pages("/issues.json", "issues", params)

    unassigned = []
    for iss in issues:
        if not iss.get("assigned_to"):
            due_str = iss.get("due_date")
            unassigned.append({
                "id": iss["id"],
                "subject": iss.get("subject"),
                "project": iss.get("project", {}).get("name"),
                "tracker": iss.get("tracker", {}).get("name"),
                "status": iss.get("status", {}).get("name"),
                "priority": iss.get("priority", {}).get("name"),
                "done_ratio": iss.get("done_ratio", 0),
                "start_date": iss.get("start_date"),
                "due_date": due_str,
                "created_on": iss.get("created_on", "")[:10],
                "is_overdue": bool(due_str and due_str < today.isoformat()),
            })

    return json.dumps({
        "total": len(unassigned),
        "unassigned_issues": unassigned,
    }, ensure_ascii=False, indent=2)


# ────────────────────────────────────────────────────────────
# Tools : トレンド・傾向分析
# ────────────────────────────────────────────────────────────


@mcp.tool()
async def get_issue_history(
    ctx: Context[ServerSession, AppContext],
    issue_id: int,
) -> str:
    """チケットのステータス遷移・変更履歴を時系列で表示する。

    journals からステータス変更、進捗率変更、担当者変更、日程変更を抽出し、
    チケットのライフサイクルを時系列で可視化する。

    Args:
        issue_id: チケット番号

    Returns:
        ステータス遷移と主要フィールド変更の時系列（JSON形式）
    """
    app = _get_app(ctx)
    await ctx.info(f"チケット #{issue_id} の変更履歴を取得中...")

    data = await app.client.get(f"/issues/{issue_id}.json", {"include": "journals"})
    issue = data.get("issue", {})
    journals = issue.get("journals", [])

    TRACKED_FIELDS = {"status_id", "done_ratio", "assigned_to_id", "start_date", "due_date", "priority_id"}

    timeline: list[dict[str, Any]] = []

    # チケット作成イベント
    timeline.append({
        "date": issue.get("created_on", "")[:16].replace("T", " "),
        "event": "作成",
        "user": issue.get("author", {}).get("name"),
        "details": {
            "status": issue.get("status", {}).get("name"),
            "assigned_to": issue.get("assigned_to", {}).get("name") if issue.get("assigned_to") else None,
            "start_date": issue.get("start_date"),
            "due_date": issue.get("due_date"),
        },
    })

    for j in journals:
        details = j.get("details", [])
        tracked_changes = [d for d in details if d.get("name") in TRACKED_FIELDS]
        notes = (j.get("notes") or "").strip()

        if not tracked_changes and not notes:
            continue

        entry: dict[str, Any] = {
            "date": j.get("created_on", "")[:16].replace("T", " "),
            "user": j.get("user", {}).get("name"),
        }

        if tracked_changes:
            changes = {}
            for d in tracked_changes:
                name = d.get("name", "")
                label = FIELD_LABELS.get(name, name)
                changes[label] = {
                    "from": d.get("old_value"),
                    "to": d.get("new_value"),
                }
            entry["event"] = "変更"
            entry["changes"] = changes

        if notes:
            entry["event"] = entry.get("event", "コメント")
            entry["comment"] = notes[:200]

        timeline.append(entry)

    # 現在の状態サマリ
    today = datetime.date.today()
    created = issue.get("created_on", "")[:10]
    age_days = (today - datetime.date.fromisoformat(created)).days if created else None

    summary = {
        "issue_id": issue.get("id"),
        "subject": issue.get("subject"),
        "current_status": issue.get("status", {}).get("name"),
        "current_progress": issue.get("done_ratio"),
        "created_on": created,
        "age_days": age_days,
        "total_updates": len(journals),
        "status_changes": sum(
            1 for j in journals
            for d in j.get("details", [])
            if d.get("name") == "status_id"
        ),
    }

    return json.dumps({
        "summary": summary,
        "timeline": timeline,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_velocity(
    ctx: Context[ServerSession, AppContext],
    project_id: int | None = None,
    assigned_to_id: int | None = None,
    weeks: int = 4,
) -> str:
    """チケットのクローズ速度（ベロシティ）を週単位で測定する。

    過去N週間の各週でクローズされたチケット数を集計し、
    トレンド（増加/減少/横ばい）を分析する。

    Args:
        project_id: プロジェクトID（省略時: 全プロジェクト）
        assigned_to_id: 担当者ID（省略時: 全担当者）
        weeks: 集計する週数（デフォルト4週間）

    Returns:
        週別クローズ数とトレンド分析（JSON形式）
    """
    app = _get_app(ctx)
    await ctx.info(f"過去{weeks}週間のベロシティを測定中...")

    today = datetime.date.today()
    start_date = today - datetime.timedelta(weeks=weeks)

    params: dict[str, Any] = {
        "status_id": "closed",
        "closed_on": f">={start_date.isoformat()}",
        "sort": "closed_on:desc",
        "limit": 100,
    }
    if project_id:
        params["project_id"] = project_id
    if assigned_to_id:
        params["assigned_to_id"] = assigned_to_id

    issues = await app.client.get_all_pages("/issues.json", "issues", params)

    # 週別に集計
    weekly_data: dict[str, dict[str, Any]] = {}
    for w in range(weeks):
        week_start = today - datetime.timedelta(weeks=weeks - w, days=today.weekday())
        week_end = week_start + datetime.timedelta(days=6)
        week_label = f"{week_start.isoformat()} 〜 {week_end.isoformat()}"
        weekly_data[week_label] = {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "closed_count": 0,
            "issues": [],
        }

    for iss in issues:
        closed_str = iss.get("closed_on") or iss.get("updated_on", "")
        if not closed_str:
            continue
        closed_date = datetime.date.fromisoformat(closed_str[:10])

        for label, wdata in weekly_data.items():
            ws = datetime.date.fromisoformat(wdata["week_start"])
            we = datetime.date.fromisoformat(wdata["week_end"])
            if ws <= closed_date <= we:
                wdata["closed_count"] += 1
                wdata["issues"].append({
                    "id": iss["id"],
                    "subject": iss.get("subject"),
                    "closed_on": closed_str[:10],
                })
                break

    # トレンド計算
    counts = [wdata["closed_count"] for wdata in weekly_data.values()]
    total_closed = sum(counts)
    avg_per_week = round(total_closed / max(weeks, 1), 1)

    if len(counts) >= 2:
        first_half = sum(counts[:len(counts) // 2])
        second_half = sum(counts[len(counts) // 2:])
        if second_half > first_half * 1.2:
            trend = "加速（↑）"
        elif second_half < first_half * 0.8:
            trend = "減速（↓）"
        else:
            trend = "横ばい（→）"
    else:
        trend = "データ不足"

    # オープン数も取得して残量を把握
    open_params: dict[str, Any] = {"status_id": "open", "limit": 1}
    if project_id:
        open_params["project_id"] = project_id
    if assigned_to_id:
        open_params["assigned_to_id"] = assigned_to_id
    open_data = await app.client.get("/issues.json", open_params)
    open_count = open_data.get("total_count", 0)

    # 残量消化予測
    if avg_per_week > 0:
        weeks_to_clear = round(open_count / avg_per_week, 1)
        estimated_clear_date = (today + datetime.timedelta(weeks=int(weeks_to_clear + 0.5))).isoformat()
    else:
        weeks_to_clear = None
        estimated_clear_date = None

    result = {
        "period": f"{start_date.isoformat()} 〜 {today.isoformat()}",
        "weeks_analyzed": weeks,
        "summary": {
            "total_closed": total_closed,
            "avg_per_week": avg_per_week,
            "trend": trend,
            "current_open": open_count,
            "weeks_to_clear": weeks_to_clear,
            "estimated_clear_date": estimated_clear_date,
        },
        "weekly_breakdown": weekly_data,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


# ────────────────────────────────────────────────────────────
# Tools : 更新
# ────────────────────────────────────────────────────────────


@mcp.tool()
async def update_issue(
    ctx: Context[ServerSession, AppContext],
    issue_id: int,
    status_id: int | None = None,
    done_ratio: int | None = None,
    start_date: str | None = None,
    due_date: str | None = None,
    notes: str | None = None,
    assigned_to_id: int | None = None,
) -> str:
    """チケットを更新する。

    Args:
        issue_id: チケット番号
        status_id: 新しいステータスID
        done_ratio: 進捗率 (0-100)
        start_date: 開始日 (YYYY-MM-DD)
        due_date: 期日 (YYYY-MM-DD)
        notes: コメント
        assigned_to_id: 担当者のユーザーID

    Returns:
        更新結果のメッセージ
    """
    app = _get_app(ctx)
    await ctx.info(f"チケット #{issue_id} を更新中...")

    issue_body: dict[str, Any] = {}
    if status_id is not None:
        issue_body["status_id"] = status_id
    if done_ratio is not None:
        issue_body["done_ratio"] = done_ratio
    if start_date is not None:
        issue_body["start_date"] = start_date
    if due_date is not None:
        issue_body["due_date"] = due_date
    if notes is not None:
        issue_body["notes"] = notes
    if assigned_to_id is not None:
        issue_body["assigned_to_id"] = assigned_to_id

    if not issue_body:
        return "更新する項目が指定されていません。"

    try:
        status_code = await app.client.put(f"/issues/{issue_id}.json", {"issue": issue_body})
        fields = ", ".join(f"{k}={v}" for k, v in issue_body.items())
        return f"✅ チケット #{issue_id} を更新しました (HTTP {status_code}): {fields}"
    except httpx.HTTPStatusError as e:
        return f"❌ 更新に失敗しました: HTTP {e.response.status_code} - {e.response.text}"
    except Exception as e:
        return f"❌ 更新に失敗しました: {e}"


# ────────────────────────────────────────────────────────────
# Prompts
# ────────────────────────────────────────────────────────────


@mcp.prompt()
def weekly_report_prompt(user_name: str, weeks_ago: str = "0") -> str:
    """担当者の週報を分析するプロンプト.

    Args:
        user_name: 担当者名
        weeks_ago: 何週前か（"0"=今週）
    """
    return (
        f"以下の手順で {user_name} の週報を作成してください:\n\n"
        f"1. まず `search_users` で「{user_name}」を検索し、ユーザーIDを特定してください\n"
        f"2. `get_weekly_report` ツールを使って週報を生成してください（user_name=\"{user_name}\", weeks_ago={weeks_ago}）\n"
        f"3. 生成された週報の内容を分析し、以下の観点でコメントしてください:\n"
        f"   - 今週の主な成果\n"
        f"   - 進行中の作業と次週の予定\n"
        f"   - リスクや懸念事項（期限超過、停滞チケットなど）\n"
        f"   - チーム内コミュニケーションの状況\n\n"
        f"週報はMarkdown形式で読みやすく整形してください。"
    )


@mcp.prompt()
def issue_review_prompt(issue_id: str) -> str:
    """チケットの状況をレビューするプロンプト.

    Args:
        issue_id: チケット番号
    """
    return (
        f"チケット #{issue_id} の状況を詳しくレビューしてください:\n\n"
        f"1. `get_issue_detail` で詳細情報を取得してください（issue_id={issue_id}）\n"
        f"2. 以下の観点で分析してください:\n"
        f"   - 現在のステータスと進捗状況\n"
        f"   - 最近の更新内容とコメントの要約\n"
        f"   - 期限に対する進捗の妥当性\n"
        f"   - 次に必要なアクション\n"
        f"   - 関係者への連絡事項\n\n"
        f"結果を簡潔にまとめてください。"
    )


# ── エントリーポイント ──────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
