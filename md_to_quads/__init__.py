# SPDX-License-Identifier: GPL-3.0-or-later
# md_to_quads — Blender port of the Maya tool "MD布料转四边面"
# (原脚本作者: 离山笔记侠 / 高山小师, Maya 端逻辑同款)
#
# 傻瓜式一键版 (v1.1.0):
#   - 导入: 面板「导入 MD 的 OBJ」直接打开 Blender 的 OBJ 导入窗口
#   - 一键: 「🪄 一键 MD→四边面」自动识别 平板(01)/衣服(03),
#           用内置 Quadriflow 生成四边面(02), 再传 UV + 传顶点位置
#   - 也可在「高级/手动」里逐项指定与分步执行
#
# 版本: 传统插件(legacy)格式, bl_info 最低 (2,80,0)
#       -> 支持 Blender 2.80 / 2.9x / 3.x / 4.x(含 4.2+)；全平台；无需任何外部重拓扑器。
import bpy

try:
    import numpy as np
except ImportError:
    raise ImportError(
        "本插件需要 numpy。官方版 Blender(2.79 起)均已内置 numpy；"
        "如你使用的是缺失 numpy 的精简/便携版, 请改用官方完整版 Blender。"
    )

from mathutils import Vector
from mathutils.bvhtree import BVHTree

bl_info = {
    "name": "MD布料转四边面 (MD Fabric to Quads)",
    "author": "Hermes (based on 离山笔记侠 Maya script)",
    "version": (1, 1, 0),
    "blender": (2, 80, 0),
    "location": "3D视图 > 侧边栏(N) > MD转四边面",
    "description": "MD三角面布料→完美四边面：一键导入+重拓扑+传UV+传位置，支持 Blender 2.80+",
    "category": "Mesh",
}

# ---------------------------------------------------------------- 基础工具

def _loops_vertex_index(me):
    lvi = np.empty(len(me.loops), dtype=np.int64)
    me.loops.foreach_get('vertex_index', lvi)
    return lvi


def _world_coords(obj):
    me = obj.data
    n = len(me.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get('co', co)
    co = co.reshape(n, 3)
    mw = np.array(obj.matrix_world, dtype=np.float64)
    return co @ mw[:3, :3].T + mw[:3, 3]


def _object_world_bbox(obj):
    me = obj.data
    n = len(me.vertices)
    if n == 0:
        return None
    co = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get('co', co)
    co = co.reshape(n, 3)
    mw = np.array(obj.matrix_world, dtype=np.float64)
    wc = co @ mw[:3, :3].T + mw[:3, 3]
    return wc.min(axis=0), wc.max(axis=0)


def _loop_world(obj, wco=None):
    if wco is None:
        wco = _world_coords(obj)
    lvi = _loops_vertex_index(obj.data)
    return wco[lvi], lvi, wco


def _tris(obj):
    me = obj.data
    me.calc_loop_triangles()
    arr = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get('loops', arr)
    return arr.reshape(-1, 3)


def _uv_arrays(obj):
    me = obj.data
    out = []
    n = len(me.loops)
    for uvl in me.uv_layers:
        a = np.empty(n * 2, dtype=np.float64)
        uvl.data.foreach_get('uv', a)
        out.append((uvl.name, a.reshape(-1, 2)))
    return out


def _bvh_from(verts, tris):
    vlist = [Vector((float(p[0]), float(p[1]), float(p[2]))) for p in verts]
    tlist = [tuple(int(i) for i in t) for t in tris]
    return BVHTree.FromPolygons(vlist, tlist, all_triangles=True)


def _bary2d(p, a, b, c):
    d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(d) < 1e-20:
        return 1.0, 0.0, 0.0
    w1 = ((b[1] - c[1]) * (p[0] - c[0]) + (c[0] - b[0]) * (p[1] - c[1])) / d
    w2 = ((c[1] - a[1]) * (p[0] - c[0]) + (a[0] - c[0]) * (p[1] - c[1])) / d
    return w1, w2, 1.0 - w1 - w2


def _bary3d(p, T):
    a, b, c = T[0], T[1], T[2]
    v0 = b - a
    v1 = c - a
    v2 = p - a
    d00 = float(np.dot(v0, v0))
    d01 = float(np.dot(v0, v1))
    d11 = float(np.dot(v1, v1))
    d20 = float(np.dot(v2, v0))
    d21 = float(np.dot(v2, v1))
    den = d00 * d11 - d01 * d01
    if abs(den) < 1e-20:
        return 1.0, 0.0, 0.0
    v = (d11 * d20 - d01 * d21) / den
    w = (d00 * d21 - d01 * d20) / den
    return 1.0 - v - w, v, w


# ---------------------------------------------------------------- 核心操作

def transfer_uvs(base, uv_src):
    """世界空间最近点采样: 把 uv_src 的全部UV层传递给 base (等价 transferUVs=2)。"""
    src_layers = _uv_arrays(uv_src)
    if not src_layers:
        raise ValueError(u'UV来源(01.三角面平板)没有UV层')
    sw, slvi, swco = _loop_world(uv_src)
    stris = _tris(uv_src)
    if len(stris) == 0:
        raise ValueError(u'UV来源(01.三角面平板)没有面')
    tree = _bvh_from(sw, stris)

    bw, blvi, bwco = _loop_world(base)
    n = len(bw)
    tri_id = np.full(n, -1, dtype=np.int64)
    barys = np.zeros((n, 3), dtype=np.float64)
    hit_count = 0
    for i in range(n):
        hit = tree.find_nearest(Vector((bw[i, 0], bw[i, 1], bw[i, 2])))
        if hit is None:
            continue
        loc, _nrm, idx, _d = hit
        if idx is None or idx < 0 or loc is None:
            continue
        T = sw[stris[idx]]
        w = _bary3d(np.array(loc, dtype=np.float64), T)
        tri_id[i] = idx
        barys[i] = w
        hit_count += 1

    me = base.data
    for lname, sarr in src_layers:
        tgt = me.uv_layers.get(lname)
        if tgt is None:
            tgt = me.uv_layers.new(name=lname)
        out = np.empty(n * 2, dtype=np.float64)
        tgt.data.foreach_get('uv', out)
        out = out.reshape(-1, 2)
        for i in range(n):
            ti = tri_id[i]
            if ti < 0:
                continue
            t3 = stris[ti]
            w1, w2, w3 = barys[i]
            out[i] = w1 * sarr[t3[0]] + w2 * sarr[t3[1]] + w3 * sarr[t3[2]]
        tgt.data.foreach_set('uv', out.ravel())
    me.update()
    return hit_count, n


def _pick_uv_names(base, src):
    me = base.data
    b_active = me.uv_layers.active
    s_layers = list(src.data.uv_layers)
    if b_active is not None:
        for l in s_layers:
            if l.name == b_active.name:
                return b_active.name, b_active.name
    b_name = b_active.name if b_active is not None else (me.uv_layers[0].name if len(me.uv_layers) else None)
    s_name = s_layers[0].name if s_layers else None
    return b_name, s_name


def transfer_positions(base, pos_src):
    """顶点位置: 对 base 每个顶点, 用其UV在 pos_src 的UV空间找最近点, 采样 pos_src 顶点位置。"""
    b_name, s_name = _pick_uv_names(base, pos_src)
    if s_name is None:
        raise ValueError(u'位置来源(03.三角面衣服模型)没有UV层')
    if b_name is None:
        raise ValueError(u'目标(02.四边面平板)还没有UV层(应先执行UV传递)')

    s_layers = dict(_uv_arrays(pos_src))
    suv = s_layers[s_name]
    stris = _tris(pos_src)
    if len(stris) == 0:
        raise ValueError(u'位置来源(03.三角面衣服模型)没有面')
    vs = np.concatenate([suv, np.zeros((len(suv), 1))], axis=1)
    tree = _bvh_from(vs, stris)

    gw, glvi, _ = _loop_world(pos_src)

    me = base.data
    b_layers = dict(_uv_arrays(base))
    buv = b_layers[b_name]
    blvi = _loops_vertex_index(me)
    nv = len(me.vertices)
    vert_uv = np.zeros((nv, 2), dtype=np.float64)
    uniq, idx = np.unique(blvi, return_index=True)
    vert_uv[uniq] = buv[idx]
    covered = np.zeros(nv, dtype=bool)
    covered[uniq] = True

    co = np.empty(nv * 3, dtype=np.float64)
    me.vertices.foreach_get('co', co)
    co = co.reshape(nv, 3)

    mw_inv = np.array(base.matrix_world.inverted(), dtype=np.float64)
    hit_count = 0
    for i in range(nv):
        if not covered[i]:
            continue
        q = Vector((vert_uv[i, 0], vert_uv[i, 1], 0.0))
        hit = tree.find_nearest(q)
        if hit is None:
            continue
        loc, _nrm, ti, _d = hit
        if ti is None or ti < 0 or loc is None:
            continue
        t3 = stris[ti]
        P = suv[t3]
        w1, w2, w3 = _bary2d((loc[0], loc[1]), tuple(P[0]), tuple(P[1]), tuple(P[2]))
        pw = w1 * gw[t3[0]] + w2 * gw[t3[1]] + w3 * gw[t3[2]]
        co[i] = pw @ mw_inv[:3, :3].T + mw_inv[:3, 3]
        hit_count += 1
    me.vertices.foreach_set('co', co.ravel())
    me.update()
    return hit_count, nv


def do_all(base, uv_src, pos_src, delete_sources=True):
    if base is None or uv_src is None or pos_src is None:
        raise ValueError(u'请先指定三个模型')
    if len({base.name, uv_src.name, pos_src.name}) != 3:
        raise ValueError(u'三个模型不能重复')
    for o in (base, uv_src, pos_src):
        if o.type != 'MESH':
            raise ValueError(u'%s 不是网格物体' % o.name)
    if base.data.users > 1:
        base.data = base.data.copy()

    ok1, n1 = transfer_uvs(base, uv_src)
    ok2, n2 = transfer_positions(base, pos_src)

    msg = u'完成: UV %d/%d, 顶点位置 %d/%d' % (ok1, n1, ok2, n2)
    if delete_sources:
        for o in (uv_src, pos_src):
            bpy.data.objects.remove(o, do_unlink=True)
        msg += u' (已删除两个源模型)'
    return msg


def quadriflow_remesh(obj, target_faces, preserve_sharp, preserve_boundary):
    """对 obj 做一份四边面副本(保持原位/变换, 与原件重叠), 返回新对象。"""
    src = obj
    if src.data.users > 1:
        src = src.copy()
        src.data = src.data.copy()
    bpy.ops.object.select_all(action='DESELECT')
    src.select_set(True)
    bpy.context.view_layer.objects.active = src
    bpy.ops.object.duplicate()
    new_obj = bpy.context.view_layer.objects.active
    new_obj.name = (src.name + "_quads")[:64]
    bpy.ops.object.quadriflow_remesh(
        use_preserve_sharp=preserve_sharp,
        use_preserve_boundary=preserve_boundary,
        use_mesh_curvature=False,
        preserve_paint_mask=False,
        smooth_normals=True,
        target_faces=target_faces,
    )
    return new_obj


def _auto_detect(context):
    """从选中(或场景中)的网格里自动识别 平板(01, 最扁) 与 衣服(03, 最立体)。"""
    sel = [o for o in context.selected_objects if o.type == 'MESH']
    cands = sel if len(sel) >= 2 else [o for o in context.scene.objects if o.type == 'MESH']
    meshes = [o for o in cands if o.type == 'MESH' and len(o.data.vertices) > 0]
    if len(meshes) < 2:
        return None, None
    info = []
    for o in meshes:
        bb = _object_world_bbox(o)
        if bb is None:
            continue
        mn, mx = bb
        dims = mx - mn
        thickness = float(np.min(dims))
        info.append((thickness, o))
    if len(info) < 2:
        return None, None
    info.sort(key=lambda x: x[0])            # 最扁的排最前
    flat = info[0][1]
    draped = max(info[1:], key=lambda x: x[0])[1]   # 其余里最立体的 = 衣服
    return flat, draped


# ---------------------------------------------------------------- 操作器

class MD2Q_OT_import(bpy.types.Operator):
    bl_idname = "md2q.import_obj"
    bl_label = "导入 MD 的 OBJ（可多选）"
    bl_description = "打开 Blender 的 OBJ 导入窗口，选中 MD 导出的平板+衣服两个 OBJ"
    bl_options = {'REGISTER'}

    def execute(self, context):
        try:
            bpy.ops.wm.obj_import('INVOKE_DEFAULT')
        except Exception:
            try:
                bpy.ops.import_scene.obj('INVOKE_DEFAULT')
            except Exception as e:
                self.report({'ERROR'}, u'未找到 OBJ 导入器: %s' % e)
                return {'CANCELLED'}
        self.report({'INFO'}, u'在弹窗里选中两个 OBJ（平板+衣服），确认导入后点「一键转换」')
        return {'FINISHED'}


class MD2Q_OT_auto(bpy.types.Operator):
    bl_idname = "md2q.auto_all"
    bl_label = "🪄 一键 MD→四边面（全自动）"
    bl_description = "自动识别平板(01)与衣服(03)，生成四边面(02)并传递UV+顶点位置"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        uv_src, pos_src = pr.uv_src, pr.pos_src
        if uv_src is None or pos_src is None:
            uv_src, pos_src = _auto_detect(context)
        if uv_src is None or pos_src is None:
            self.report({'ERROR'}, u'请先导入 MD 的两个 OBJ（平板+衣服），或手动在下方指定 01/03')
            return {'CANCELLED'}
        if uv_src.type != 'MESH' or pos_src.type != 'MESH':
            self.report({'ERROR'}, u'01/03 必须是网格物体')
            return {'CANCELLED'}
        if not _uv_arrays(uv_src):
            self.report({'ERROR'}, u'01 没有UV：请确认 MD 导出时勾选了「统一UV坐标」')
            return {'CANCELLED'}
        if not _uv_arrays(pos_src):
            self.report({'ERROR'}, u'03 没有UV：请确认 MD 导出时勾选了「统一UV坐标」')
            return {'CANCELLED'}

        try:
            base = quadriflow_remesh(uv_src, pr.quad_target,
                                     pr.quad_preserve_sharp, pr.quad_preserve_boundary)
        except Exception as e:
            self.report({'ERROR'}, u'Quadriflow 重拓扑失败: %s' % e)
            return {'CANCELLED'}
        pr.uv_src = uv_src
        pr.base = base
        pr.pos_src = pos_src
        try:
            msg = do_all(base, uv_src, pos_src, pr.delete_sources)
        except Exception as e:
            self.report({'ERROR'}, u'传递失败: %s' % e)
            return {'CANCELLED'}
        self.report({'INFO'}, u'一键完成 → ' + msg)
        return {'FINISHED'}


class MD2Q_OT_quadriflow(bpy.types.Operator):
    bl_idname = "md2q.quadriflow_remesh"
    bl_label = "用 Quadriflow 重拓扑 01→02（手动）"
    bl_description = "把选中的三角面平板(01)复制并重拓扑为四边面(02)，保持原位重叠"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        if context.object is None or context.object.type != 'MESH':
            self.report({'ERROR'}, u'请先选中三角面平板(01)再点此按钮')
            return {'CANCELLED'}
        if context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            new_obj = quadriflow_remesh(context.object, pr.quad_target,
                                         pr.quad_preserve_sharp, pr.quad_preserve_boundary)
        except Exception as e:
            self.report({'ERROR'}, u'Quadriflow 失败: %s' % e)
            return {'CANCELLED'}
        pr.uv_src = context.object
        pr.base = new_obj
        self.report({'INFO'}, u'已生成四边面: %s (目标面数 %d)' % (new_obj.name, pr.quad_target))
        return {'FINISHED'}


class MD2Q_OT_all(bpy.types.Operator):
    bl_idname = "md2q.transfer_all"
    bl_label = "传递 UV + 顶点位置（手动）"
    bl_description = "一键完成: UV传递(平板→底板) + 顶点位置传递(衣服→底板)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        err = _check_ctx_objects(pr.base, pr.uv_src, pr.pos_src)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        try:
            msg = do_all(pr.base, pr.uv_src, pr.pos_src, pr.delete_sources)
        except Exception as e:
            self.report({'ERROR'}, u'出错: %s' % e)
            return {'CANCELLED'}
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class MD2Q_OT_uv(bpy.types.Operator):
    bl_idname = "md2q.transfer_uvs_only"
    bl_label = "只传递 UV"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        err = _check_ctx_objects(pr.base, pr.uv_src, pr.pos_src)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        try:
            ok, n = transfer_uvs(pr.base, pr.uv_src)
        except Exception as e:
            self.report({'ERROR'}, u'出错: %s' % e)
            return {'CANCELLED'}
        self.report({'INFO'}, u'UV传递完成 %d/%d' % (ok, n))
        return {'FINISHED'}


class MD2Q_OT_pos(bpy.types.Operator):
    bl_idname = "md2q.transfer_pos_only"
    bl_label = "只传递 顶点位置"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        err = _check_ctx_objects(pr.base, pr.uv_src, pr.pos_src)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        try:
            ok, n = transfer_positions(pr.base, pr.pos_src)
        except Exception as e:
            self.report({'ERROR'}, u'出错: %s' % e)
            return {'CANCELLED'}
        self.report({'INFO'}, u'顶点位置传递完成 %d/%d' % (ok, n))
        return {'FINISHED'}


# ---------------------------------------------------------------- 面板

def _check_ctx_objects(base, uv_src, pos_src):
    if base is None or uv_src is None or pos_src is None:
        return u'请先在面板里指定 01/02/03 三个模型'
    if len({base.name, uv_src.name, pos_src.name}) != 3:
        return u'三个模型不能重复'
    return None


class MD2Q_Props(bpy.types.PropertyGroup):
    uv_src: bpy.props.PointerProperty(type=bpy.types.Object, name=u"01 三角面平板(UV来源)")
    base: bpy.props.PointerProperty(type=bpy.types.Object, name=u"02 四边面平板(接受方)")
    pos_src: bpy.props.PointerProperty(type=bpy.types.Object, name=u"03 三角面衣服模型(位置来源)")
    delete_sources: bpy.props.BoolProperty(name=u"完成后删除两个源模型", default=True)
    quad_target: bpy.props.IntProperty(name=u"四边面目标面数", default=4000, min=100, max=2000000,
                                       description=u"Quadriflow 目标四边形数量, 越大越密")
    quad_preserve_sharp: bpy.props.BoolProperty(name=u"保留硬边", default=True,
                                                description=u"Quadriflow 保留锐利边缘")
    quad_preserve_boundary: bpy.props.BoolProperty(name=u"保留边界", default=True,
                                                   description=u"Quadriflow 保留外边界形状")


class MD2Q_PT_panel(bpy.types.Panel):
    bl_idname = "MD2Q_PT_panel"
    bl_label = "MD布料转四边面"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "MD转四边面"

    def draw(self, context):
        layout = self.layout
        pr = context.scene.md2q

        # ① 导入
        box = layout.box()
        box.label(text=u"① 导入：MD 导出的两个 OBJ（勾选「统一UV坐标」）")
        box.operator("md2q.import_obj", icon='IMPORT')
        box.label(text=u"    在弹窗里选 平板 + 衣服 两个 OBJ，确认导入", icon='INFO')

        # ② 一键
        box2 = layout.box()
        box2.label(text=u"② 一键转换（自动：重拓扑→传UV→传位置）")
        box2.operator("md2q.auto_all", icon='PLAY')
        box2.prop(pr, "delete_sources")

        # 高级/手动
        layout.separator()
        adv = layout.box()
        adv.label(text=u"高级 / 手动（一般不用改）")
        adv.prop(pr, "quad_target")
        r = adv.row(align=True)
        r.prop(pr, "quad_preserve_sharp")
        r.prop(pr, "quad_preserve_boundary")
        adv.operator("md2q.quadriflow_remesh", icon='MOD_REMESH')
        adv.label(text=u"手动指定 01 / 02 / 03：")
        col = adv.column(align=True)
        col.prop(pr, "uv_src")
        col.prop(pr, "base")
        col.prop(pr, "pos_src")
        row = adv.row(align=True)
        row.operator("md2q.transfer_uvs_only")
        row.operator("md2q.transfer_pos_only")
        adv.operator("md2q.transfer_all")

        layout.separator()
        layout.label(text=u"支持 Blender 2.80 / 2.9x / 3.x / 4.x（含 4.2+），全平台")


# ---------------------------------------------------------------- 注册

_classes = (MD2Q_OT_import, MD2Q_OT_auto, MD2Q_OT_quadriflow,
            MD2Q_OT_all, MD2Q_OT_uv, MD2Q_OT_pos, MD2Q_Props, MD2Q_PT_panel)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.md2q = bpy.props.PointerProperty(type=MD2Q_Props)


def unregister():
    if hasattr(bpy.types.Scene, 'md2q'):
        del bpy.types.Scene.md2q
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
