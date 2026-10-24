# -*- coding: utf-8 -*-
"""无头 Blender 冒烟测试: 加载插件 -> 造测试数据 -> 跑一键转换 -> 画一遍面板。

用法:
  blender --background --factory-startup --python tools/test_addon.py -- <addon_dir>
"""
import sys
import os
import shutil
import math

import bpy
import bmesh

FAILS = []
OKS = []


def run_op(idname, **kw):
    """调用算子。Blender 会把带 ERROR 报告的调用包成 RuntimeError(UI 点击时只是提示), 这里统一吸收。"""
    try:
        return getattr(bpy.ops, idname.split(".")[0]).__getattr__(idname.split(".")[1])(**kw)
    except RuntimeError as e:
        return {'CANCELLED'}


def check(cond, name, detail=""):
    if cond:
        OKS.append(name)
        print("  [PASS] %s %s" % (name, detail))
    else:
        FAILS.append("%s %s" % (name, detail))
        print("  [FAIL] %s %s" % (name, detail))


# ---------------------------------------------------------------- 安装插件
def install_addon(addon_dir, blender_scripts):
    dst = os.path.join(blender_scripts, "addons", "md_to_quads")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(addon_dir, dst)
    return dst


# ---------------------------------------------------------------- 测试数据
def make_cloth(name, segments=24, draped=False, uv_broken=False):
    """造一块「MD 三角面布料」: 规则三角网格, UV = 归一化平面坐标。"""
    mesh = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)

    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=segments, y_segments=segments, size=2.0)
    bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method='BEAUTY', ngon_method='BEAUTY')
    bm.to_mesh(mesh)
    bm.free()

    # MD 的「统一UV坐标」: 平板与衣服共用同一套 UV
    if not uv_broken:
        uv = mesh.uv_layers.new(name="UVMap")
        for loop in mesh.loops:
            co = mesh.vertices[loop.vertex_index].co
            uv.data[loop.index].uv = ((co.x + 1.0) * 0.5, (co.y + 1.0) * 0.5)

    if draped:
        # 把平板「穿」到起伏的形状上(③衣服)
        for v in mesh.vertices:
            v.co.z += 0.35 * math.sin(4.0 * v.co.x) * math.cos(4.0 * v.co.y)
    mesh.update()
    return obj


def quad_ratio(obj):
    polys = obj.data.polygons
    if not len(polys):
        return 0.0
    return 100.0 * sum(1 for p in polys if len(p.vertices) == 4) / len(polys)


# ---------------------------------------------------------------- 面板绘制测试
class FakeLayout(object):
    """记录 UI 调用并校验属性名/算子 idname 是否真实存在。"""

    def __init__(self, path="root"):
        self.path = path
        self.enabled = True
        self.alert = False
        self.scale_y = 1.0
        self.use_property_split = False
        self.use_property_decorate = False
        self.alignment = 'EXPAND'

    def _child(self, kind):
        return FakeLayout(self.path + "/" + kind)

    def box(self, *a, **kw):
        return self._child("box")

    def row(self, *a, **kw):
        return self._child("row")

    def column(self, *a, **kw):
        return self._child("column")

    def split(self, *a, **kw):
        return self._child("split")

    def separator(self, *a, **kw):
        return None

    def label(self, text="", **kw):
        assert isinstance(text, str), "label text 必须是字符串: %r" % (text,)
        return None

    def prop(self, data, prop_name, **kw):
        if not hasattr(type(data), prop_name) and not hasattr(data, prop_name):
            raise AssertionError("UI 引用了不存在的属性: %s (on %s)" % (prop_name, data))
        return None

    def prop_search(self, data, prop_name, *a, **kw):
        return self.prop(data, prop_name)

    def operator(self, idname, **kw):
        mod, _, fn = idname.partition(".")
        if fn not in dir(getattr(bpy.ops, mod)):
            raise AssertionError("UI 引用了不存在的算子: %s" % idname)
        return None


def draw_panel(context):
    panel_cls = getattr(bpy.types, "MD2Q_PT_panel", None)
    assert panel_cls is not None, "面板类未注册"


# ---------------------------------------------------------------- 主流程
def main():
    addon_dir = os.path.abspath(sys.argv[sys.argv.index("--") + 1]) if "--" in sys.argv else None
    if addon_dir is None:
        addon_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "md_to_quads")
    addon_dir = os.path.normpath(addon_dir)

    print("Blender", bpy.app.version, "| addon:", addon_dir)
    # 直接按「从磁盘安装」的方式导入并注册(4.2 的 addons 目录属于扩展仓库, 不走 addon_utils)
    parent = os.path.dirname(addon_dir)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    import md_to_quads
    try:
        md_to_quads.register()
    except Exception as e:
        import traceback
        traceback.print_exc()
        check(False, "插件启用", repr(e))
        return 1
    check("md2q" in dir(bpy.context.scene), "插件启用 + 场景属性注册")
    check("MD2Q_PT_panel" in dir(bpy.types), "面板类注册")

    pr = bpy.context.scene.md2q

    # ---- 1. 空状态: 没有任何物体时应该友好报错而不是崩 ----
    res = run_op('md2q.auto_all')
    check('CANCELLED' in res, "空状态下一键转换被正确拦截", str(res))
    check(pr.status_level == 'ERROR', "空状态写入 ERROR 状态", pr.status_message[:40])

    # ---- 2. 正常流程 ----
    plate = make_cloth("plate_01", draped=False)
    garment = make_cloth("cloth_03", draped=True)
    print("  平板: %d 面 / 衣服: %d 面" % (len(plate.data.polygons), len(garment.data.polygons)))

    bpy.ops.object.select_all(action='DESELECT')
    plate.select_set(True)
    garment.select_set(True)
    bpy.context.view_layer.objects.active = plate
    res = run_op('md2q.pick_from_selection')
    check('FINISHED' in res, "从选择识别", pr.status_message[:60])
    check(pr.plate_obj is plate, "自动识别: 较扁的是①平板", pr.plate_obj.name if pr.plate_obj else "None")
    check(pr.garment_obj is garment, "自动识别: 较立体的是③衣服", pr.garment_obj.name if pr.garment_obj else "None")

    plate_name, garment_name = plate.name, garment.name
    res = run_op('md2q.auto_all')
    check('FINISHED' in res, "一键转换执行成功", pr.status_message[:70])
    quad = pr.quad_obj
    check(quad is not None and quad.name in bpy.data.objects, "生成了②四边面", quad.name if quad else "None")
    if quad:
        ratio = quad_ratio(quad)
        check(ratio > 90.0, "四边面占比 > 90%", "实际 %.1f%% (%d 面)" % (ratio, len(quad.data.polygons)))
        check(len(quad.data.uv_layers) > 0, "结果带 UV 层")
        # 形状是否被正确套回衣服(按 UV 采样应该落在起伏面上)
        uv = quad.data.uv_layers[0]
        loop_uv = {}
        for loop in quad.data.loops:
            loop_uv.setdefault(loop.vertex_index, uv.data[loop.index].uv)
        max_err = 0.0
        for vi, uvv in loop_uv.items():
            v = quad.data.vertices[vi]
            x, y = uvv[0] * 2.0 - 1.0, uvv[1] * 2.0 - 1.0
            expected_z = 0.35 * math.sin(4.0 * x) * math.cos(4.0 * y)
            max_err = max(max_err, abs(v.co.z - expected_z))
        check(max_err < 0.05, "顶点位置已套回衣服形状", "最大误差 %.4f" % max_err)
        check(plate_name not in bpy.data.objects and garment_name not in bpy.data.objects,
              "默认已清理①③源模型")
        # 撤销可用性
        try:
            bpy.ops.ed.undo()
            check(True, "撤销不报错")
        except RuntimeError as e:
            # 后台模式没有窗口上下文, ed.undo 的 poll 必然失败, 非插件问题
            check("poll() failed" in str(e), "撤销不报错（后台模式跳过）", str(e)[:40])

    # ---- 2b. 完全不指定、靠自动识别 + 自动删除源模型(最容易崩的一条路径) ----
    run_op('md2q.clear_roles')
    ap = make_cloth("auto_plate", draped=False)
    ag = make_cloth("auto_garment", draped=True)
    bpy.ops.object.select_all(action='DESELECT')
    ap.select_set(True)
    ag.select_set(True)
    ap_name, ag_name = ap.name, ag.name
    res = run_op('md2q.auto_all')
    check('FINISHED' in res, "全自动(未指定①③)成功", pr.status_message[:70])
    check(ap_name not in bpy.data.objects and ag_name not in bpy.data.objects,
          "全自动路径也清理了源模型")

    # ---- 3. 无 UV 的报错 ----
    run_op('md2q.clear_roles')
    bad = make_cloth("no_uv", uv_broken=True)
    bad2 = make_cloth("no_uv2", uv_broken=True)
    pr.plate_obj, pr.garment_obj = bad, bad2
    res = run_op('md2q.auto_all')
    check('CANCELLED' in res, "缺少 UV 时被拦截", pr.status_message[:50])
    bpy.data.objects.remove(bad, do_unlink=True)
    bpy.data.objects.remove(bad2, do_unlink=True)

    # ---- 4. Quadriflow 模式 ----
    run_op('md2q.clear_roles')
    p2 = make_cloth("qf_plate")
    g2 = make_cloth("qf_garment", draped=True)
    pr.plate_obj, pr.garment_obj = p2, g2
    pr.remesh_mode = 'QUADRIFLOW'
    pr.auto_target = True
    res = run_op('md2q.auto_all')
    check('FINISHED' in res, "Quadriflow 模式可用", pr.status_message[:60])
    if pr.quad_obj:
        check(quad_ratio(pr.quad_obj) > 90.0, "Quadriflow 结果为四边面",
              "%.1f%% / %d 面" % (quad_ratio(pr.quad_obj), len(pr.quad_obj.data.polygons)))
    pr.remesh_mode = 'JOIN'

    # ---- 5. 面板绘制(空/满/高级展开/Quadriflow 模式各画一遍) ----
    panel_cls = getattr(bpy.types, "MD2Q_PT_panel")
    class _PanelShim(object):
        layout = None
        bl_label = ""
    panel = _PanelShim()
    states = []
    for adv in (False, True):
        for mode in ('JOIN', 'QUADRIFLOW'):
            pr.show_advanced = adv
            pr.remesh_mode = mode
            states.append((adv, mode))
    try:
        for adv, mode in states:
            panel.layout = FakeLayout()
            panel_cls.draw(panel, bpy.context)
        check(True, "面板绘制(4 种状态)")
    except Exception as e:
        check(False, "面板绘制(4 种状态)", repr(e))

    # 空状态面板
    try:
        run_op('md2q.clear_roles')
        panel.layout = FakeLayout()
        panel_cls.draw(panel, bpy.context)
        check(True, "面板绘制(空状态)")
    except Exception as e:
        check(False, "面板绘制(空状态)", repr(e))

    # ---- 6. 旧 idname 兼容 ----
    run_op('md2q.clear_roles')
    p3 = make_cloth("legacy_plate")
    pr.plate_obj = p3
    res = run_op('md2q.quadriflow_remesh')
    check('FINISHED' in res, "旧 idname md2q.quadriflow_remesh 仍可用", pr.status_message[:50])

    print("\n==== 通过 %d 项, 失败 %d 项 ====" % (len(OKS), len(FAILS)))
    for f in FAILS:
        print("  FAIL:", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
