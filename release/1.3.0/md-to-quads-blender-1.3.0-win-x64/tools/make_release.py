#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包发布物：

1) md_to_quads-<ver>.zip          —— 纯净插件包，Blender 里「偏好设置 → 插件 → 安装…」用
2) md_to_quads-<ver>-完整包.zip   —— 给普通用户：双击 一键启动.bat 就能装好并启动 Blender

用法: python tools/make_release.py
"""

import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = os.path.join(ROOT, "md_to_quads")
SKIP_NAMES = {"__pycache__", ".blender_path"}
SKIP_SUFFIX = (".pyc",)


def read_version():
    path = os.path.join(ADDON_DIR, "__init__.py")
    text = open(path, "r", encoding="utf-8").read()
    m = re.search(r'"version"\s*:\s*\((\d+),\s*(\d+),\s*(\d+)\)', text)
    if not m:
        return "1.3.0"
    return "%s.%s.%s" % m.groups()


def add_tree(zf, src_dir, arc_dir, skip_files=()):
    """把 src_dir 整个目录写进 zip 的 arc_dir/ 下。"""
    for dirpath, dirnames, filenames in os.walk(src_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES]
        for name in sorted(filenames):
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIX):
                continue
            if name in skip_files:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, src_dir)
            zf.write(full, os.path.join(arc_dir, rel).replace(os.sep, "/"))


def main():
    ver = read_version()
    pure_name = "md_to_quads-%s.zip" % ver
    full_name = "md_to_quads-%s-完整包.zip" % ver

    # ---- 1. 纯净插件包 ----
    pure_path = os.path.join(ROOT, pure_name)
    with zipfile.ZipFile(pure_path, "w", zipfile.ZIP_DEFLATED) as z:
        add_tree(z, ADDON_DIR, "md_to_quads")
    print(u"√ 纯净插件包：%s (%.1f KB)" % (pure_name, os.path.getsize(pure_path) / 1024.0))

    # ---- 2. 完整包（含一键启动器）----
    full_path = os.path.join(ROOT, full_name)
    root_files = [u"一键启动.bat", u"一键助手.bat", u"创建桌面快捷方式.bat", u"一键助手.sh",
                  u"README.md", u"CHANGELOG.md", u"LICENSE", u"使用说明.txt"]
    missing = []

    with zipfile.ZipFile(full_path, "w", zipfile.ZIP_DEFLATED) as z:
        add_tree(z, ADDON_DIR, "md_to_quads")
        # 启动器本体
        launcher = os.path.join(ROOT, "tools", "md_launcher.py")
        if os.path.isfile(launcher):
            z.write(launcher, "tools/md_launcher.py")
        else:
            missing.append("tools/md_launcher.py")
        # 纯净插件包也放进完整包, 方便走「Blender 内手动安装」
        z.write(pure_path, pure_name)
        # 说明与批处理
        for name in root_files:
            p = os.path.join(ROOT, name)
            if os.path.isfile(p):
                z.write(p, name)
            else:
                p2 = os.path.join(ADDON_DIR, name)
                if os.path.isfile(p2):
                    z.write(p2, name)
                else:
                    missing.append(name)
    print(u"√ 完整包：%s (%.1f KB)" % (full_name, os.path.getsize(full_path) / 1024.0))
    if missing:
        print(u"! 以下文件不存在，已跳过：%s" % u"、".join(missing))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
