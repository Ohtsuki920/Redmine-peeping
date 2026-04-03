# Redmine MCP Server

Redmine プロジェクト管理システムに接続し、チケット情報の検索・分析・レポート生成を行う **MCP (Model Context Protocol) サーバー**です。  
AIエージェント（GitHub Copilot、Claude 等）がこのサーバーのツールを使って、担当者の作業状況を細かく確認し、レポートを作成できます。

## Features

### 🔍 検索・一覧
| ツール | 説明 |
|--------|------|
| `list_projects` | プロジェクト一覧を取得 |
| `search_users` | ユーザーを名前で検索（担当者ID特定用） |
| `list_issues` | チケット一覧を多機能フィルタ付きで取得 |
| `get_issue_detail` | チケット詳細＋更新履歴（journals）を取得 |

### 📊 分析・レポート
| ツール | 説明 |
|--------|------|
| `get_user_activity` | 担当者の活動状況（チケット更新・作業時間）を集計 |
| `get_weekly_report` | 週報を一括生成（ユーザー検索→チケット→詳細→レポート） |
| `get_project_summary` | プロジェクト統計（ステータス別・担当者別・期限超過） |
| `get_overdue_issues` | 期限超過チケットを超過日数付きで一覧 |

### 📅 スケジュール分析

| ツール                 | 説明                                                                 |
|------------------------|----------------------------------------------------------------------|
| `get_schedule_status`  | 担当者/プロジェクトのスケジュール状況を分析（超過・今週・来週・将来・未設定に分類） |
| `get_gantt_data`       | ガントチャート用の日程データ取得（開始日・期日・進捗・親子関係）     |

### 🔔 通知・アラート

| ツール                 | 説明                                                     |
|------------------------|----------------------------------------------------------|
| `get_stalled_issues`   | 一定期間更新がない停滞チケットを検出（デフォルト14日）   |
| `get_unassigned_issues`| 担当者が未割当のオープンチケット一覧                     |

### 📈 トレンド・傾向分析

| ツール              | 説明                                                             |
|---------------------|------------------------------------------------------------------|
| `get_issue_history` | チケットのステータス遷移・変更履歴を時系列表示                   |
| `get_velocity`      | チケットのクローズ速度を週単位で測定（トレンド・残量消化予測付き） |

### ✏️ 更新

| ツール         | 説明                                                     |
|----------------|----------------------------------------------------------|
| `update_issue` | チケットのステータス・進捗率・日程・コメントを更新       |

### 📝 プロンプト

| プロンプト        | 説明                           |
|-------------------|------------------------------------|
| `weekly_report`   | 担当者の週報分析プロンプト         |
| `issue_review`    | チケット状況レビュープロンプト       |

## Requirements

- Python 3.12+
- Redmine REST API アクセス（APIキー）

## Install

```bash
pip install -e .
```

## Setup

### 1. APIキーの設定

以下のいずれかの方法でRedmine APIキーを設定してください：

```bash
# 方法1: 環境変数
export REDMINE_API_KEY="your-api-key"
export REDMINE_BASE_URL="https://your-redmine.example.com"  # 省略時はデフォルト値を使用

# 方法2: ファイル
echo "your-api-key" > .redmine_api_key
```

APIキーは Redmine の「個人設定」(`/my/account`) から取得できます。

### 2. VS Code での利用

`.vscode/mcp.json` が同梱されています。VS Code を再起動すると、GitHub Copilot 等から自動的にMCPサーバーが利用可能になります。

```jsonc
// .vscode/mcp.json
{
    "servers": {
        "redmine-agent": {
            "command": "${workspaceFolder}/.venv/Scripts/python.exe",
            "args": ["-m", "redmine_peeping.mcp_server"]
        }
    }
}
```

## Usage

### AI エージェントから（推奨）

VS Code の Copilot Chat 等で、自然言語で指示するだけです：

- 「--の週報を作成して」
- 「期限超過のチケットを一覧表示して」
- 「チケット #13304 の状況を分析して」
- 「プロジェクト の概要を教えて」
- 「2週間以上更新のないチケットを探して」
- 「担当者未割当のチケットを一覧して」
- 「直近8週間のチケット消化速度を教えて」
### 手動起動

```bash
# stdio モード（MCP クライアントから接続）
python -m redmine_peeping.mcp_server
```

## Architecture

```
AI Agent (Copilot / Claude)
    │
    │  MCP Protocol (stdio)
    ▼
┌────────────────────────────────┐
│  Redmine Agent MCP Server      │
│  src/redmine_peeping/          │
│      mcp_server.py             │
│                                │
│  15 Tools + 1 Resource         │
│  + 2 Prompts                   │
└────────────┬───────────────────┘
             │  httpx (async)
             ▼
       Redmine REST API
```

## Project Structure

```
.
├── .vscode/mcp.json                 # VS Code MCP設定
├── src/redmine_peeping/
│   ├── __init__.py
│   └── mcp_server.py               # MCPサーバー本体
├── .redmine_api_key.example         # APIキーファイルのサンプル
├── pyproject.toml
└── README.md
```

## License

MIT
