#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MD布料转四边面 · 一键助手

给下载了本插件的人用的小工具：双击即可完成「找 Blender → 装插件 → 启用 → 启动 Blender」，
也可以一键在桌面创建快捷方式（快捷方式 = 以后真正的一键启动）。

用法（命令行）:
    python md_launcher.py                 交互式菜单（推荐，双击 .bat 就是这个）
    python md_launcher.py --auto          一键：安装插件 + 启动 Blender
    python md_launcher.py --install-only  只安装并启用插件，不启动 Blender
    python md_launcher.py --launch        只启动 Blender
    python md_launcher.py --shortcut      只创建桌面快捷方式
    python md_launcher.py --all           创建快捷方式 + 安装 + 启动
    python md_launcher.py --blender PATH  手动指定 blender 可执行文件路径

只用标准库，Windows / macOS / Linux 通用。
"""

import os
import shutil
import subprocess
import sys

ADDON_MODULE = "md_to_quads"
PANEL_HINT = u"3D视图右侧边栏（按 N 打开）→ 「MD转四边面」标签页"

# 本文件位于 <包根目录>/tools/ 下, 包根目录里应有 md_to_quads/ 与 一键助手.bat
_HERE = os.path.dirname(os.path.abspath(__file__))
PKG_ROOT = os.path.dirname(_HERE)
ADDON_SRC = os.path.join(PKG_ROOT, ADDON_MODULE)


def _use_utf8_console():
    """让中文在 Windows 控制台里不乱码（.bat 里已 chcp 65001）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg=""):
    print(msg, flush=True)


def line(ch="-", n=58):
    log(ch * n)


def ask(prompt, default=""):
    try:
        raw = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return raw or default


# ------------------------------------------------------------ 找 Blender

PATH_FILE = os.path.join(PKG_ROOT, ".blender_path")


def recall_blender_path():
    """上次用过的 Blender 路径（装在非默认位置时尤其有用）。"""
    try:
        with open(PATH_FILE, "r", encoding="utf-8") as f:
            p = f.read().strip()
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    return None


def remember_blender_path(path):
    try:
        with open(PATH_FILE, "w", encoding="utf-8") as f:
            f.write(path)
    except Exception:
        pass


def forget_blender_path():
    try:
        os.remove(PATH_FILE)
    except Exception:
        pass


def _blender_candidates():
    """按「新版本优先」的顺序枚举可能的 Blender 可执行文件。"""
    env = os.environ.get("BLENDER_EXE", "").strip()
    if env and os.path.isfile(env):
        yield env, u"环境变量 BLENDER_EXE"

    remembered = recall_blender_path()
    if remembered:
        yield remembered, u"上次用过的路径"

    system = sys.platform
    found_via_path = shutil.which("blender")
    if found_via_path:
        yield found_via_path, u"系统 PATH"

    if system.startswith("win"):
        roots = [os.environ.get("PROGRAMFILES") or r"C:\Program Files",
                 os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)"]
        for root in roots:
            bf = os.path.join(root, "Blender Foundation")
            if os.path.isdir(bf):
                for name in sorted(os.listdir(bf), reverse=True):
                    p = os.path.join(bf, name, "blender.exe")
                    if os.path.isfile(p):
                        yield p, u"已安装的 %s" % name
            steam = os.path.join(root, "Steam", "steamapps", "common", "Blender", "blender.exe")
            if os.path.isfile(steam):
                yield steam, u"Steam 版"
        scoop = os.path.join(os.path.expanduser("~"), "scoop", "apps", "blender", "current", "blender.exe")
        if os.path.isfile(scoop):
            yield scoop, u"Scoop 版"
    elif system == "darwin":
        apps = "/Applications"
        if os.path.isdir(apps):
            for name in sorted(os.listdir(apps), reverse=True):
                if name.startswith("Blender"):
                    p = os.path.join(apps, name, "Contents", "MacOS", "Blender")
                    if os.path.isfile(p):
                        yield p, u"应用程序 %s" % name
        hp = os.path.join(os.path.expanduser("~"), "Applications", "Blender.app", "Contents", "MacOS", "Blender")
        if os.path.isfile(hp):
            yield hp, u"用户应用程序目录"
    else:
        for p in ("/usr/bin/blender", "/usr/local/bin/blender",
                  "/snap/bin/blender", os.path.join(os.path.expanduser("~"), "blender", "blender")):
            if os.path.isfile(p):
                yield p, u"系统路径 %s" % p


def find_blender():
    for path, source in _blender_candidates():
        if os.path.isfile(path):
            return path, source
    return None, None


def ask_blender_path():
    """没找到 Blender 时, 让用户手动指定一次。"""
    log()
    log(u"没有自动找到 Blender。")
    log(u"可以这样做：")
    log(u"  1) 直接把 blender.exe / Blender 拖进这个窗口后回车")
    log(u"  2) 或手动粘贴完整路径后回车")
    log(u"  3) 直接回车 = 放弃，先去 blender.org 下载安装")
    raw = ask(u"\nBlender 路径 > ").strip().strip('"').strip("'")
    if not raw:
        return None
    if os.path.isdir(raw):
        for sub in (("blender.exe",), ("Contents", "MacOS", "Blender"), ("blender",)):
            p = os.path.join(raw, *sub)
            if os.path.isfile(p):
                raw = p
                break
    if os.path.isfile(raw) and os.access(raw, os.X_OK):
        return os.path.abspath(raw)
    log(u"× 这个路径不是可执行文件：%s" % raw)
    return None


# ------------------------------------------------------------ 调 Blender

def run_blender(blender, args, timeout=300):
    """以无头方式跑一段 Blender 命令, 返回合并后的输出文本。"""
    cmd = [blender] + list(args)
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return u"__TIMEOUT__"
    except Exception as e:
        return u"__ERROR__%s" % e
    text = r.stdout or b""
    try:
        return text.decode("utf-8", errors="replace")
    except Exception:
        return str(text)


def blender_version(blender):
    out = run_blender(blender, ["--background", "--factory-startup",
                                "--python-expr", "import bpy;print('VER', bpy.app.version_string)"],
                      timeout=120)
    for ln in out.splitlines():
        if ln.startswith("VER "):
            return ln[4:].strip()
    return u"未知版本"


def blender_addons_dir(blender):
    """问 Blender 自己：用户级 addons 目录在哪（不同版本/系统路径不一样，别硬编码）。"""
    out = run_blender(blender, ["--background", "--factory-startup", "--python-expr",
                                "import bpy;print('ADDONS_DIR', bpy.utils.user_resource('SCRIPTS', path='addons'))"],
                      timeout=120)
    for ln in reversed(out.splitlines()):
        ln = ln.strip()
        if ln.startswith("ADDONS_DIR"):
            return ln.split("ADDONS_DIR", 1)[1].strip()
    return None


# ------------------------------------------------------------ 安装 / 启用

def _set_addon_enabled(blender, enable=True):
    """启用/禁用插件。

    后台模式下 Blender 不会把偏好设置落盘, 必须显式 save_userpref(),
    否则「装完看起来成功、下次打开又没了」——这是个很容易漏掉的坑。
    """
    code = ("import bpy, addon_utils;"
            "ok = addon_utils.enable(%r, default_set=True, persistent=True) if %s "
            "else addon_utils.disable(%r, default_set=True, persistent=True);"
            "print('ENABLE_RESULT', bool(ok));"
            "bpy.ops.wm.save_userpref();"
            "print('PREFS_SAVED')"
            % (ADDON_MODULE, "True" if enable else "False", ADDON_MODULE))
    out = run_blender(blender, ["--background", "--python-expr", code], timeout=180)
    return "ENABLE_RESULT True" in out, out


def is_addon_enabled(blender):
    code = ("import bpy;"
            "print('ADDON_ENABLED', %r in bpy.context.preferences.addons)" % ADDON_MODULE)
    out = run_blender(blender, ["--background", "--python-expr", code], timeout=180)
    return "ADDON_ENABLED True" in out


def blender_is_running():
    """Blender 开着时装插件, 退出时会用旧偏好设置覆盖掉我们的改动 —— 提前提醒。"""
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq blender.exe"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
            txt = (r.stdout or b"").decode("utf-8", errors="replace").lower()
            return "blender.exe" in txt
        r = subprocess.run(["pgrep", "-x", "blender"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
        return (r.stdout or b"").strip() != b""
    except Exception:
        return False


def install_addon(blender):
    """把 md_to_quads/ 拷进 Blender 的 addons 目录并在偏好设置里启用。"""
    if blender_is_running():
        log(u"! 检测到 Blender 正在运行。")
        log(u"  它会用自己内存里的旧设置覆盖本助手刚写入的偏好，装完可能看不到面板。")
        log(u"  建议：先关掉 Blender，再重新运行本助手。")
        if not ask(u"  仍要继续吗？(y/N) > ", "N").lower().startswith("y"):
            log(u"已取消。关掉 Blender 后再来一次即可。")
            return False

    if not os.path.isdir(ADDON_SRC):
        log(u"× 在本包里找不到插件目录：%s" % ADDON_SRC)
        log(u"  请确认 md_to_quads/ 与 tools/ 在同一个文件夹下")
        return False

    addons_dir = blender_addons_dir(blender)
    if not addons_dir:
        log(u"× 问不到 Blender 的 addons 目录，改为手动安装：")
        log(u"  Blender → 编辑 → 偏好设置 → 插件 → 安装… → 选择 md_to_quads-1.3.0.zip")
        return False

    dst = os.path.join(addons_dir, ADDON_MODULE)
    try:
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        elif os.path.exists(dst):
            os.remove(dst)
        os.makedirs(addons_dir, exist_ok=True)
        shutil.copytree(ADDON_SRC, dst,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    except Exception as e:
        log(u"× 复制插件失败：%s" % e)
        log(u"  常见原因：Blender 正在运行占用文件，或没有该目录的写入权限")
        return False
    log(u"√ 插件已复制到：%s" % dst)

    ok, out = _set_addon_enabled(blender, True)
    if not ok:
        log(u"× 启用插件失败，Blender 输出：")
        log(u"  " + (out or u"(空)").replace("\n", "\n  ")[-800:])
        return False
    if not is_addon_enabled(blender):
        log(u"× 插件已复制但偏好设置里没启用成功")
        return False
    log(u"√ 插件已在 Blender 偏好设置中启用（下次打开也在）")
    return True


# ------------------------------------------------------------ 启动

def launch_blender(blender):
    """独立进程启动 Blender，不阻塞本脚本退出。"""
    kwargs = {"stdin": subprocess.DEVNULL,
              "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL}
    # 必须断开标准流: 否则 Blender 会一直持有父进程的管道,
    # 让调用方(命令行/脚本)以为命令没结束而一直卡住。
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([blender], **kwargs)
    except Exception as e:
        log(u"× 启动 Blender 失败：%s" % e)
        return False
    return True


# ------------------------------------------------------------ 桌面快捷方式

def desktop_dir():
    if sys.platform.startswith("win"):
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "[Environment]::GetFolderPath('Desktop')"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
            txt = (r.stdout or b"").decode("utf-8", errors="replace").strip().splitlines()
            if txt and os.path.isdir(txt[-1]):
                return txt[-1]
        except Exception:
            pass
        home = os.path.expanduser("~")
        for cand in (os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "Desktop")):
            if os.path.isdir(cand):
                return cand
        return os.path.join(home, "Desktop")
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, "Desktop"), os.path.join(home, "桌面")):
        if os.path.isdir(cand):
            return cand
    return home


def _bat_path():
    """快捷方式要指向「本身就是一键启动」的 bat —— 不给快捷方式传参数,
    因为 WScript.Shell 的 Arguments 在部分机器上写不进去, 会导致双击后
    弹出菜单而不是直接启动。"""
    direct = os.path.join(PKG_ROOT, u"一键启动.bat")
    if os.path.isfile(direct):
        return direct
    return os.path.join(PKG_ROOT, u"一键助手.bat")


def _ps_quote(s):
    return "'" + s.replace("'", "''") + "'"


def _win_make_shortcut(ps_lines):
    tmp_ps1 = os.path.join(os.environ.get("TEMP", PKG_ROOT), "md2q_mkshortcut.ps1")
    with open(tmp_ps1, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\r\n".join(ps_lines))
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", tmp_ps1], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=60)
        if r.returncode != 0:
            log(u"× 创建快捷方式失败：%s" % (r.stdout or b"").decode("utf-8", errors="replace")[:300])
            return False
    except Exception as e:
        log(u"× 调用 PowerShell 失败：%s" % e)
        return False
    finally:
        try:
            os.remove(tmp_ps1)
        except Exception:
            pass
    return True


def create_windows_shortcut(blender=None):
    """返回 (一键助手快捷方式, 文件目录快捷方式)，失败项为 None。"""
    desktop = desktop_dir()
    launcher_lnk = os.path.join(desktop, u"MD布料转四边面 · 一键助手.lnk")
    folder_lnk = os.path.join(desktop, u"MD布料转四边面 · 文件目录.lnk")

    icon = ""
    if blender and os.path.isfile(blender):
        icon = "%s,0" % blender

    ps = ["$ws = New-Object -ComObject WScript.Shell"]

    # ① 一键启动：双击 = 自动安装/更新插件 + 启动 Blender（不依赖参数）
    target = _bat_path()
    ps += [
        "$sc = $ws.CreateShortcut(%s)" % _ps_quote(launcher_lnk),
        "$sc.TargetPath = %s" % _ps_quote(target),
        "$sc.Arguments = ''",
        "$sc.WorkingDirectory = %s" % _ps_quote(PKG_ROOT),
        "$sc.Description = %s" % _ps_quote(u"一键把 MD布料转四边面 装进 Blender 并启动"),
        ("$sc.IconLocation = %s" % _ps_quote(icon)) if icon else "$sc.IconLocation = ''",
        "$sc.Save()",
    ]
    # ② 文件目录：方便翻使用说明 / 拿纯净插件包
    ps += [
        "$sc2 = $ws.CreateShortcut(%s)" % _ps_quote(folder_lnk),
        "$sc2.TargetPath = %s" % _ps_quote(PKG_ROOT),
        "$sc2.Description = %s" % _ps_quote(u"打开 MD布料转四边面 的文件目录（使用说明 / 插件包）"),
        "$sc2.IconLocation = '%SystemRoot%\\system32\\imageres.dll,3'",
        "$sc2.Save()",
    ]
    if not _win_make_shortcut(ps):
        return None, None
    return (launcher_lnk if os.path.isfile(launcher_lnk) else None,
            folder_lnk if os.path.isfile(folder_lnk) else None)


def create_posix_shortcut():
    desktop = desktop_dir()
    if sys.platform == "darwin":
        path = os.path.join(desktop, u"MD布料转四边面 一键助手.command")
        body = ('#!/bin/bash\ncd %s\nexec python3 tools/md_launcher.py --auto\n'
                % ('"%s"' % PKG_ROOT))
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(path, 0o755)
        folder = os.path.join(desktop, u"MD布料转四边面 文件目录")
        try:
            if not os.path.islink(folder) and not os.path.exists(folder):
                os.symlink(PKG_ROOT, folder)
        except Exception:
            folder = None
        return path, folder

    entry = "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        "Name=MD布料转四边面 · 一键助手",
        "Comment=一键把 MD布料转四边面 装进 Blender 并启动",
        'Exec=python3 "%s" --auto' % os.path.join(PKG_ROOT, "tools", "md_launcher.py"),
        "Path=%s" % PKG_ROOT,
        "Terminal=true",
        "Icon=blender",
        "Categories=Graphics;3DGraphics;",
        "",
    ])
    path = os.path.join(desktop, "md-to-quads-一键助手.desktop")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(entry)
        os.chmod(path, 0o755)
    except Exception as e:
        log(u"× 创建快捷方式失败：%s" % e)
        return None
    apps = os.path.join(os.path.expanduser("~"), ".local", "share", "applications")
    try:
        os.makedirs(apps, exist_ok=True)
        with open(os.path.join(apps, "md-to-quads-launcher.desktop"), "w", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass

    folder = os.path.join(desktop, "md-to-quads-文件目录")
    try:
        if not os.path.islink(folder) and not os.path.exists(folder):
            os.symlink(PKG_ROOT, folder)
    except Exception:
        folder = None
    return path, folder


def create_shortcut(blender=None):
    if sys.platform.startswith("win"):
        return create_windows_shortcut(blender)
    return create_posix_shortcut()


# ------------------------------------------------------------ 流程

def resolve_blender(interactive=True):
    blender, source = find_blender()
    if blender:
        log(u"√ 找到 Blender：%s（%s）" % (blender, source))
        remember_blender_path(blender)
        return blender
    if interactive:
        blender = ask_blender_path()
        if blender:
            log(u"√ 使用你指定的 Blender：%s" % blender)
            remember_blender_path(blender)
            return blender
        forget_blender_path()
    return None


def do_auto(interactive=True):
    """一键：安装 + 启动。"""
    blender = resolve_blender(interactive)
    if not blender:
        return False
    log(u"  Blender %s" % blender_version(blender))
    if not install_addon(blender):
        return False
    log(u"√ 正在启动 Blender …")
    if not launch_blender(blender):
        return False
    log()
    log(u"完成！接下来在 Blender 里：%s" % PANEL_HINT)
    return True


def report_shortcuts(pair):
    launcher_lnk, folder_lnk = pair
    if not launcher_lnk:
        log(u"! 快捷方式没创建成功")
        return False
    log(u"√ 桌面快捷方式已创建：")
    log(u"  ① %s" % launcher_lnk)
    log(u"     双击 = 自动安装/更新插件 + 启动 Blender")
    if folder_lnk:
        log(u"  ② %s" % folder_lnk)
        log(u"     一键打开本文件夹（使用说明 / 纯净插件包都在这里）")
    return True


def do_all(interactive=True):
    """快捷方式 + 安装 + 启动。"""
    blender = resolve_blender(interactive)
    if not blender:
        return False
    report_shortcuts(create_shortcut(blender))
    log()
    return do_auto(False) or True


def do_shortcut():
    blender, _src = find_blender()
    return report_shortcuts(create_shortcut(blender))


def menu():
    line("=")
    log(u"  MD布料转四边面 · 一键助手")
    line("=")
    log(u"  本包位置：%s" % PKG_ROOT)
    log()
    log(u"  [1] 一键安装插件并启动 Blender        （推荐，第一次用选这个）")
    log(u"  [2] 一键创建桌面快捷方式")
    log(u"  [3] 两个合起来：创建快捷方式 + 安装并启动")
    log(u"  [4] 只启动 Blender")
    log(u"  [5] 只安装/更新插件，不启动 Blender")
    log(u"  [6] 忘记记住的 Blender 路径（换一个 Blender 时用）")
    log(u"  [0] 退出")
    log()
    choice = ask(u"请输入序号后回车（默认 1）> ", "1")

    if choice == "1":
        return do_auto()
    if choice == "2":
        return do_shortcut()
    if choice == "3":
        return do_all()
    if choice == "4":
        blender = resolve_blender()
        if not blender:
            return False
        log(u"√ 正在启动 Blender …")
        launch_blender(blender)
        log(u"  面板位置：%s" % PANEL_HINT)
        return True
    if choice == "5":
        blender = resolve_blender()
        return install_addon(blender) if blender else False
    if choice == "6":
        forget_blender_path()
        log(u"√ 已清除记住的路径，下次会重新查找 / 询问")
        return True
    log(u"已退出")
    return True


def main(argv):
    _use_utf8_console()
    args = [a for a in argv[1:]]
    forced_blender = None
    if "--blender" in args:
        i = args.index("--blender")
        if i + 1 < len(args):
            forced_blender = os.path.abspath(args[i + 1].strip().strip('"'))
            os.environ["BLENDER_EXE"] = forced_blender
            del args[i:i + 2]

    flags = set(a for a in args if a.startswith("-"))
    if "--auto" in flags:
        ok = do_auto()
    elif "--all" in flags or "--shortcut-and-run" in flags:
        ok = do_all()
    elif "--shortcut" in flags:
        ok = do_shortcut()
    elif "--install-only" in flags:
        blender = resolve_blender()
        ok = install_addon(blender) if blender else False
        if ok:
            log(u"  面板位置：%s" % PANEL_HINT)
    elif "--launch" in flags:
        blender = resolve_blender()
        ok = bool(blender) and launch_blender(blender)
    elif "--forget" in flags:
        forget_blender_path()
        log(u"√ 已清除记住的 Blender 路径")
        ok = True
    else:
        ok = menu()

    if not ok:
        log()
        log(u"没有成功完成。可以手动安装：Blender → 编辑 → 偏好设置 → 插件 → 安装…")
        log(u"选择本包里的 md_to_quads-1.3.0.zip（纯净插件包）即可。")
    if sys.platform.startswith("win") and "--no-pause" not in flags:
        try:
            subprocess.run(["pause"], shell=True)
        except Exception:
            pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
