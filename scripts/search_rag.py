from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from req2web_rag.corpus import ROLE_ORDER  # noqa: E402
from req2web_rag.index import TfidfIndex  # noqa: E402


DEFAULT_QUERY = (
    "我想做一个移动端电商应用，支持登录、搜索筛选商品、查看详情、加入购物车和结算，"
    "需要响应式页面、清楚的点击流程，以及输入错误和权限异常的验收。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search the local Req2Web RAG index.")
    parser.add_argument("query", nargs="?", default=DEFAULT_QUERY)
    parser.add_argument("--index-dir", type=Path, default=ROOT / "data/processed/rag")
    parser.add_argument("--top-k", type=int, default=2)
    parser.add_argument("--role", choices=ROLE_ORDER)
    parser.add_argument("--all", action="store_true", help="Return one mixed ranking instead of five role groups.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    index = TfidfIndex.load(args.index_dir.resolve())
    if args.role:
        result = {args.role: index.search(args.query, top_k=args.top_k, roles=[args.role])}
    elif args.all:
        result = {"all": index.search(args.query, top_k=args.top_k)}
    else:
        result = index.search_by_role(args.query, top_k=args.top_k)
    print(json.dumps({"query": args.query, "results": result}, ensure_ascii=False, indent=2))
    return 0 if all(result.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
