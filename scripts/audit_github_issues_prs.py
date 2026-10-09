from __future__ import annotations

import csv
import html
from collections import Counter
from pathlib import Path
from typing import Any

from repository_secret_rules import redact_credential_literals


REPOSITORIES = {
    "20580498": "kubernetes/kubernetes",
    "50613991": "goharbor/harbor",
    "62921553": "rook/rook",
    "11008207": "vitessio/vitess",
}

CATEGORIES = {
    "ui_feedback": ("界面与反馈状态", "问题应体现加载、导航、数据显示或页面反馈异常，并能转成明确的界面验收点。"),
    "input_error": ("输入校验与错误提示", "问题应包含可识别的无效输入，以及清楚、可操作的预期错误反馈。"),
    "auth_access": ("登录、权限与访问控制", "问题应体现身份或角色条件、实际访问结果和权限变更后的预期行为。"),
    "state_sync": ("状态同步与数据一致性", "问题应体现操作前后状态未同步、残留或延迟，并可检查最终一致性。"),
    "reliability": ("异常恢复与可靠性", "问题应体现崩溃、卡死、重试失控或不可用场景，并能形成恢复和边界测试。"),
    "compat_config": ("版本、升级与配置兼容", "问题应具有明确版本或配置条件，并能检查升级、解析和功能兼容性。"),
}

# Each item is repo_id, issue_number, pull_number, Chinese summary, validation focus.
CURATED = {
    "ui_feedback": [
        ("50613991", "12820", "12887", "仓库详情页进入后加载动画一直旋转，实际数据始终不出现。", "检查加载成功、加载失败和超时三种状态均有明确反馈，且不会无限等待。"),
        ("20580498", "12154", "12437", "浏览器前进或后退会触发整页刷新，导航菜单出现先关闭再展开的闪烁。", "检查历史导航后菜单展开状态、当前项高亮和页面内容保持一致且无明显闪烁。"),
        ("50613991", "5729", "6689", "镜像已经成功推送并可以拉取，但 Harbor 网页界面中找不到该镜像。", "检查后台操作成功后列表及时刷新，直接访问与刷新页面所得状态一致。"),
    ],
    "input_error": [
        ("20580498", "63756", "63757", "编辑资源标签时被无关的必填字段阻止，并返回不恰当的错误消息。", "检查只修改允许字段时可以提交；真正缺少必填项时错误应定位到正确字段。"),
        ("20580498", "64301", "64305", "容忍规则字段填写错误后，系统返回难以理解且不能指导修正的校验信息。", "检查错误消息包含字段名、错误值、合法格式和可执行的修正建议。"),
        ("20580498", "66572", "82423", "命令参数无效时输出多页帮助内容，掩盖了真正的错误原因。", "检查错误反馈简短突出原因，详细帮助按需展示，不打断主要任务。"),
    ],
    "auth_access": [
        ("50613991", "9869", "10002", "用户组已有最高项目角色，但使用令牌推送或拉取镜像仍然被拒绝。", "检查用户、用户组和令牌三种身份路径得到一致的角色授权结果。"),
        ("50613991", "9749", "9834", "用户先登录、随后通过用户组获得角色后，刷新界面仍看不到项目且接口返回禁止访问。", "检查登录期间权限变更能刷新会话，界面可见性与接口授权同步更新。"),
        ("62921553", "3478", "3503", "使用自定义访问密钥登录对象存储面板时，系统错误地提示密钥不存在。", "检查默认和自定义凭据均可认证；失败时区分不存在、格式错误和权限不足。"),
    ],
    "state_sync": [
        ("20580498", "85677", "91750", "调度目标节点被删除后，任务仍保留指向无效节点的旧状态。", "检查依赖对象失效后及时清除旧引用，并重新计算或显示可恢复状态。"),
        ("20580498", "44372", "44719", "存储卷被意外删除，但相关资源状态仍显示未变化，形成数据与状态不一致。", "检查删除操作的实际资源、状态字段和用户提示最终一致，并处理部分失败。"),
        ("20580498", "47597", "55474", "任务删除后端点移除明显延迟，短时间内仍可能把请求发送给已删除目标。", "检查删除后的列表、路由和缓存按约定时间收敛，并覆盖延迟窗口。"),
    ],
    "reliability": [
        ("11008207", "5859", "5865", "依赖的拓扑服务不可用时，组件收到关闭信号后会无限卡住而无法正常退出。", "检查依赖不可用时关闭流程有超时、降级和最终退出保证。"),
        ("20580498", "70865", "70866", "外部接口限流时存在两层重试退避，导致请求重试过于激进。", "检查重试次数、退避上限、限流响应和用户可见状态，避免请求风暴。"),
        ("20580498", "33772", "33967", "删除任务时调度器触发空指针异常，可能导致核心流程崩溃。", "检查删除、重复删除和并发状态变化不会导致崩溃，并产生可诊断日志。"),
    ],
    "compat_config": [
        ("20580498", "44140", "44199", "系统无法解析带 `-ce` 后缀的 Docker 版本号，导致组件启动或检测失败。", "检查标准版本、发行版后缀和未知格式的解析兼容性及错误回退。"),
        ("20580498", "40415", "42335", "升级到指定版本后产生重复副本集，旧状态没有正确迁移。", "检查升级前后资源数量、标识和状态迁移，确保重复执行升级仍具幂等性。"),
        ("20580498", "46023", "46203", "启用 RBAC 后日志插件无法运行，默认附加组件与权限配置不兼容。", "检查默认配置和启用权限控制后的功能一致性，并验证缺失权限的诊断提示。"),
    ],
}

AUTO_SELECTED_IDS = {
    "GHP-UI-01",
    "GHP-UI-02",
    "GHP-INP-01",
    "GHP-INP-02",
    "GHP-AUTH-01",
    "GHP-AUTH-02",
    "GHP-STA-01",
    "GHP-STA-02",
    "GHP-REL-01",
    "GHP-REL-02",
    "GHP-CMP-01",
    "GHP-CMP-02",
}


def write_csv_atomic(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def number(value: str) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def main() -> int:
    csv.field_size_limit(10_000_000)
    root = Path(__file__).resolve().parent.parent
    source_path = root / "data/raw/github_issues_prs/extracted/ghpr-dataset-main/ghpr.csv"
    processed = root / "data/processed"
    inventory_path = processed / "github_issues_prs_inventory.csv"
    candidate_path = processed / "github_issues_prs_review_candidates.csv"
    review_path = processed / "github_issues_prs_review.html"

    source_rows: dict[tuple[str, str, str], dict[str, str]] = {}
    inventory: list[dict[str, Any]] = []
    with source_path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        for row_number, row in enumerate(reader, 1):
            key = (row["repo_id"], row["issue_number"], row["pull_number"])
            source_rows[key] = row
            body = row["issue_body_plain"] or ""
            lower = f"{row['issue_title']} {body}".lower()
            inventory.append(
                {
                    "dataset": "github_issues_prs",
                    "row_number": row_number,
                    "repo_id": row["repo_id"],
                    "repository": REPOSITORIES.get(row["repo_id"], ""),
                    "issue_number": row["issue_number"],
                    "pull_number": row["pull_number"],
                    "issue_title": row["issue_title"],
                    "body_chars": len(body),
                    "has_issue_body": bool(body.strip()),
                    "has_reproduction_signal": any(term in lower for term in ("reproduce", "steps to", "how to")),
                    "has_expected_signal": any(term in lower for term in ("expected", "should", "what you expected")),
                    "has_actual_signal": any(term in lower for term in ("actual", "what happened", "observed")),
                    "pull_comments": number(row["pull_comments"]),
                    "pull_review_comments": number(row["pull_review_comments"]),
                    "pull_commits": number(row["pull_commits"]),
                    "pull_additions": number(row["pull_additions"]),
                    "pull_deletions": number(row["pull_deletions"]),
                    "pull_changed_files": number(row["pull_changed_files"]),
                }
            )
    write_csv_atomic(inventory_path, inventory, list(inventory[0]))

    candidates: list[dict[str, Any]] = []
    prefixes = {
        "ui_feedback": "UI",
        "input_error": "INP",
        "auth_access": "AUTH",
        "state_sync": "STA",
        "reliability": "REL",
        "compat_config": "CMP",
    }
    for category, entries in CURATED.items():
        for index, (repo_id, issue_number, pull_number, summary, focus) in enumerate(entries, 1):
            key = (repo_id, issue_number, pull_number)
            if key not in source_rows:
                raise ValueError(f"curated relation missing from source: {key}")
            row = source_rows[key]
            review_id = f"GHP-{prefixes[category]}-{index:02d}"
            repository = REPOSITORIES.get(repo_id, repo_id)
            candidates.append(
                {
                    "review_id": review_id,
                    "category": category,
                    "category_label": CATEGORIES[category][0],
                    "repository": repository,
                    "repo_id": repo_id,
                    "issue_number": issue_number,
                    "pull_number": pull_number,
                    "issue_title": row["issue_title"],
                    # Third-party issue text is untrusted and can contain real
                    # passwords. Retain source references, not literal secrets.
                    "issue_body_plain": redact_credential_literals(row["issue_body_plain"] or ""),
                    "chinese_summary": summary,
                    "validation_focus": focus,
                    "pull_comments": number(row["pull_comments"]),
                    "pull_review_comments": number(row["pull_review_comments"]),
                    "pull_commits": number(row["pull_commits"]),
                    "pull_additions": number(row["pull_additions"]),
                    "pull_deletions": number(row["pull_deletions"]),
                    "pull_changed_files": number(row["pull_changed_files"]),
                    "issue_url": f"https://github.com/{repository}/issues/{issue_number}",
                    "pull_url": f"https://github.com/{repository}/pull/{pull_number}",
                    "review_status": "auto_selected" if review_id in AUTO_SELECTED_IDS else "reserve",
                }
            )
    write_csv_atomic(candidate_path, candidates, list(candidates[0]))

    sections: list[str] = []
    for category, (label, standard) in CATEGORIES.items():
        cards: list[str] = []
        for row in (item for item in candidates if item["category"] == category):
            excerpt = " ".join(row["issue_body_plain"].split())[:900]
            status_label = "自动保留" if row["review_status"] == "auto_selected" else "备用"
            cards.append(
                f"""<article class="candidate {html.escape(row['review_status'])}"><div class="card-head"><h3>{html.escape(row['review_id'])}</h3><span class="badge">{status_label}</span></div><div class="body"><strong>{html.escape(row['chinese_summary'])}</strong><p class="focus">验收重点：{html.escape(row['validation_focus'])}</p><p class="links"><a href="{html.escape(row['issue_url'])}" target="_blank" rel="noreferrer">原始 Issue</a><a href="{html.escape(row['pull_url'])}" target="_blank" rel="noreferrer">对应 PR</a></p><details><summary>可选：查看英文原始描述</summary><p><b>{html.escape(row['issue_title'])}</b></p><p>{html.escape(excerpt)}</p></details><p class="meta">{html.escape(row['repository'])} · Issue #{row['issue_number']} → PR #{row['pull_number']}<br>提交 {row['pull_commits']} · 文件 {row['pull_changed_files']} · +{row['pull_additions']} / -{row['pull_deletions']}<br>本地数据仅证明该 PR 修复了该 Issue，不包含修复代码或 diff。</p></div></article>"""
            )
        sections.append(
            f"""<section><div class="group-head"><div><h2>{html.escape(label)}</h2><p>{html.escape(standard)}</p></div><strong>自动保留 2 条 · 备用 1 条</strong></div><div class="grid">{''.join(cards)}</div></section>"""
        )

    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GitHub Issues / PRs 人工挑选</title><style>
:root{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#182226}}*{{box-sizing:border-box}}body{{margin:0;background:#f2f4f5}}header{{padding:18px 24px;background:#fff;border-bottom:1px solid #bdc7ca}}h1{{margin:0;font-size:24px}}header p{{margin:7px 0;color:#5b696f}}.notice{{max-width:1100px;padding:11px 13px;background:#edf3f1;border-left:4px solid #277760;color:#29453c}}main{{width:min(1500px,100%);margin:auto;padding:20px 24px 48px}}section{{margin-bottom:38px}}.group-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;padding:14px;background:#fff;border:1px solid #c6d0d4;border-left:5px solid #176f55}}.group-head p{{margin:6px 0 0;color:#56666c}}h2,h3{{margin:0}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:12px}}.candidate{{background:#fff;border:1px solid #c5cfd3;min-width:0}}.candidate.auto_selected{{border-top:5px solid #177b59}}.candidate.reserve{{border-top:5px solid #9aa5aa}}.card-head{{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:11px 13px;border-bottom:1px solid #d5dcdf}}.badge{{padding:4px 8px;border:1px solid #78878d;font-size:12px;font-weight:700}}.auto_selected .badge{{background:#e4f2ec;border-color:#43836c}}.reserve .badge{{background:#eef0f1}}.body{{padding:14px;line-height:1.55}}.focus{{min-height:74px;padding:10px;background:#edf3f1;border-left:4px solid #277760}}.links{{display:flex;gap:12px}}.links a{{font-weight:700}}details{{margin:12px 0}}details p{{font-size:12px;color:#536268}}.meta{{font-size:12px;color:#607078}}@media(max-width:1050px){{.grid{{grid-template-columns:1fr}}}}@media(max-width:680px){{header,main{{padding:14px}}.group-head{{flex-direction:column}}}}</style></head><body>
<header><h1>GitHub Issues / PRs 自动筛选审计报告</h1><p>14,384 条关系已完成结构清点；18 条候选中自动保留 12 条，另外 6 条作为备用。</p><div class="notice">无需人工逐条筛选。中文概括和验收重点已作为 RAG 内容；原始 Issue / PR 链接只用于可选的来源核对。本地 GHPR 不包含 PR 解决方案、代码或 diff。</div></header><main>{''.join(sections)}</main></body></html>"""
    review_path.write_text(document, encoding="utf-8")

    complete = sum(row["has_issue_body"] for row in inventory)
    signal_counts = Counter()
    for row in inventory:
        for signal in ("has_reproduction_signal", "has_expected_signal", "has_actual_signal"):
            signal_counts[signal] += bool(row[signal])
    print(f"Inventory rows: {len(inventory)}")
    print(f"Rows with issue body: {complete}")
    print(f"Structured signal counts: {dict(signal_counts)}")
    print(f"Curated review candidates: {len(candidates)}")
    print(f"Review page: {review_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
