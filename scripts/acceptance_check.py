from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MODULES = {
    "requests": "requests",
    "feedparser": "feedparser",
    "bs4": "bs4",
    "lxml": "lxml",
    "yaml": "yaml",
    "dotenv": "dotenv",
    "jinja2": "jinja2",
}


def check_modules() -> list[str]:
    missing = []
    for display_name, module_name in REQUIRED_MODULES.items():
        if importlib.util.find_spec(module_name) is None:
            missing.append(display_name)
    return missing


def check_files() -> list[Path]:
    required = [
        ROOT / "src" / "pipeline.py",
        ROOT / "src" / "emailer.py",
        ROOT / "config" / "venues.yaml",
        ROOT / "templates" / "digest.html",
        ROOT / "requirements.txt",
        ROOT / ".env.example",
    ]
    return [path for path in required if not path.exists()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local acceptance checks without sending email by default.")
    parser.add_argument("--network", action="store_true", help="Also run a network dry-run without sending email.")
    args = parser.parse_args()

    missing_modules = check_modules()
    if missing_modules:
        print("缺少依赖：" + ", ".join(missing_modules))
        print("请先执行：pip install -r requirements.txt")
        return 2

    missing_files = check_files()
    if missing_files:
        print("缺少文件：")
        for path in missing_files:
            print("  " + str(path))
        return 3

    settings = yaml.safe_load((ROOT / "config" / "venues.yaml").read_text(encoding="utf-8")) or {}
    venues = settings.get("venues", [])
    if not venues:
        print("配置文件中没有期刊会议白名单")
        return 4
    if int(settings.get("defaults", {}).get("interval_days", 0)) != 10:
        print("interval_days 不是 10")
        return 5

    from src.emailer import render_digest
    from src.models import Paper
    from src.storage import PaperStore

    sample = Paper(
        title="Acceptance Test Paper",
        authors=["Test Author"],
        venue="Journal of the ACM",
        venue_type="journal",
        category="theory",
        publication_date=date.today(),
        doi="10.1000/acceptance",
        url="https://example.com/paper",
        abstract="This is a local acceptance test.",
        sources={"acceptance"},
    )
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "acceptance.db"
        store = PaperStore(database)
        if store.last_success_at() is not None:
            print("新数据库不应包含成功时间")
            return 6
        store.mark_success()
        if store.last_success_at() is None:
            print("成功时间写入失败")
            return 7
        plain, html = render_digest([sample], ROOT / "templates")
        if sample.title not in plain or sample.title not in html:
            print("邮件模板渲染失败")
            return 8

    if args.network:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.pipeline",
                "--dry-run",
                "--lookback-days",
                "10",
                "--limit",
                "10",
            ],
            cwd=ROOT,
        )
        if result.returncode != 0:
            print("联网 dry-run 失败")
            return result.returncode

    print("LOCAL_ACCEPTANCE_OK")
    print("期刊会议数量：" + str(len(venues)))
    if not args.network:
        print("未执行网络访问；加入 --network 可执行不发送邮件的抓取测试。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
