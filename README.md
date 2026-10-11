# MD布料转四边面（Blender 一键版 / md_to_quads）

把 **Marvelous Designer** 导出的三角面布料，在 **Blender** 里直接变成带正确 UV 的完美四边面 —— 不用 Maya，也不用任何付费/破解的重拓扑器。

> Blender port of the Maya tool "MD布料转四边面" (原脚本作者: 离山笔记侠 / 高山小师).

## 特性
- 🪄 **一键全自动**：导入 MD 的两个 OBJ → 自动识别平板(01)/衣服(03) → 内置 Quadriflow 重拓扑成四边面(02) → 传 UV + 传顶点位置。
- 🔒 **无外部依赖**：重拓扑用 Blender 内置 Quadriflow（2.79+ 自带），不捆绑任何商业/破解二进制。
- 🌐 **全版本 / 全平台**：支持 Blender **2.80 / 2.9x / 3.x / 4.x（含 4.2+）**，Windows / macOS / Linux。
- 🧼 **安全**：纯 Blender Python，无网络、无 subprocess、无混淆。

## 安装
1. 下载本仓库的 `__init__.py`（Release 里的 `md_to_quads-1.1.0-傻瓜版.zip` 内含 `__init__.py` 与 `使用说明.txt`）。
2. Blender → 编辑 → 偏好设置 → 插件 → **从磁盘安装 / Install from Disk** → 选 `__init__.py` → 勾选启用。
3. 3D 视图按 `N` → 侧栏最右标签 **「MD转四边面」**。

## 使用（3 步）
1. **Marvelous Designer**：导出「平铺板片」和「3D 衣服」两个 OBJ，**都勾选「统一UV坐标」**。
2. 面板 **① 导入** → 「导入 MD 的 OBJ」→ 选这两个文件。
3. 面板 **② 一键转换** → 「🪄 一键 MD→四边面（全自动）」。

完成。详见仓库内 `使用说明.txt`。

## 原理
- UV 传递：世界空间最近点采样，把 01 的全部 UV 层传给 02（等价 Maya `transferUVs=2`）。
- 位置传递：按 02 的 UV 在 03 的 UV 空间采样，把 02 套到衣服形状（等价 Maya `transferPositions=2, sampleSpace=3`）。

## 许可证
GPL-3.0-or-later（与原 Maya 脚本同源衍生）。
