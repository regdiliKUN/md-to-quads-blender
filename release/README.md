# 发行包 · MD 布料转四边面 · 一键助手

本目录存放 **MD 布料转四边面 · 一键助手** 的 Windows x64 可运行包体（由桌面快捷方式实际指向的安装/发布目录整理而来）。

| 项目 | 内容 |
| --- | --- |
| 版本 | `1.3.0` |
| 平台 | Windows x64 |
| 包体路径 | `release/1.3.0/md-to-quads-blender-1.3.0-win-x64/` |
| 文件数 | 16 |
| 体积 | 约 0.1 MB |

## 运行方式

双击 `一键启动.bat`（需要本机有 Python 3 或 Blender 自带 Python）

## 校验完整性

```bash
cd release
sha256sum -c SHA256SUMS.txt
```

Windows PowerShell：

```powershell
Get-Content release\SHA256SUMS.txt | ForEach-Object {
  $h, $p = $_ -split '  ', 2
  if ((Get-FileHash $p -Algorithm SHA256).Hash -ne $h.ToUpper()) { "MISMATCH $p" }
}
```

## 关于 MD 布料转四边面 · 一键助手

Blender 一键把 Marvelous Designer 导出的三角面布料转成带 UV 的四边面（内置 Quadriflow）。

## 未纳入的内容

旧版 `md_to_quads-1.3.0*.zip` 未纳入（源码与发布包已在本仓库根目录）。

以上目录属于开发环境、运行期缓存或大体积模型权重，不影响程序本体运行。
