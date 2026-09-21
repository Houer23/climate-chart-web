#!/usr/bin/env python
"""DrawClimateChartWeb 启动入口。

用法
----
```bash
python run.py                 # 启动并自动打开浏览器（http://127.0.0.1:8765/）
python run.py --port 9000     # 换端口
python run.py --no-browser    # 不自动打开浏览器
python run.py --host 0.0.0.0  # 允许局域网访问（注意本机数据可见性）
```

依赖：matplotlib（含 numpy）+ openpyxl + PyYAML；Web 层只用标准库。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="DrawClimateChartWeb",
        description="WMO 城市气候数据网页工具：在线编辑配置、即时出图、另存配置。",
    )
    parser.add_argument("--host", default="127.0.0.1", metavar="地址",
                        help="监听地址（默认 127.0.0.1，仅本机可访问）")
    parser.add_argument("--port", type=int, default=8765, metavar="端口",
                        help="监听端口（默认 8765）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("缺少依赖 matplotlib，请先执行：pip install -r requirements.txt")
        return 2
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("缺少依赖 PyYAML，请先执行：pip install -r requirements.txt")
        return 2

    from app.server import serve

    try:
        serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    except OSError as exc:
        print(f"启动失败：端口 {args.port} 可能已被占用（{exc}）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
