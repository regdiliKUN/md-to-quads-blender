# SPDX-License-Identifier: GPL-3.0-or-later
# md_to_quads — Blender port of the Maya tool "MD布料转四边面"
# (原脚本作者: 离山笔记侠 / 高山小师, Maya 端逻辑同款)
#
# 把一个 Marvelous Designer 的三角面布料变成带正确 UV 的四边面衣服:
#   ① 平板(三角面, MD 平铺状态导出) —— 提供 UV, 也是生成四边面的输入
#   ② 四边面(由①生成)               —— 最终结果的载体
#   ③ 衣服(三角面, MD 穿着状态导出)  —— 提供最终形状
#
# 面板三步: ① 准备(导入/识别) → ② 生成四边面 → ③ 完成转换(传UV + 套形状)
#
# 四边面生成有两种方式:
#   · 三角面配对(默认, 全版本可用): 自研贪心配对算法, 保留原拓扑与 UV
#   · Quadriflow 重拓扑(可选): 按目标面数重新分布, 但 4.2 起官方可能移除
#
# 版本: 传统插件(legacy)格式, bl_info 最低 (2,80,0)
#       -> 支持 Blender 2.80 / 2.9x / 3.x / 4.x(含 4.2+)；全平台；无需任何外部重拓扑器。
import bpy
import bmesh
import math
import time
from math import radians

try:
    import numpy as np
except ImportError:
    raise ImportError(
        "本插件需要 numpy。官方版 Blender(2.79 起)均已内置 numpy；"
        "如你使用的是缺失 numpy 的精简/便携版, 请改用官方完整版 Blender。"
    )

from mathutils import Vector
from mathutils.bvhtree import BVHTree

# ---------------------------------------------------------------- 环境能力检测
# Blender 4.2 起官方移除了 object.quadriflow_remesh, 旧版(<=4.1)仍然自带。
# 插件在启动时检测可用能力, 面板据此显示/隐藏对应入口, 而不是点了才报错。
_BLENDER_VERSION = getattr(bpy.app, "version", (2, 80, 0))


def _op_prop_names(module, name):
    """读取某个 operator 实际支持的参数名(不同 Blender 版本差异很大)。"""
    try:
        rna = getattr(module, name).get_rna_type()
        return set(p.identifier for p in rna.properties)
    except Exception:
        return set()


def _op_exists(module, name):
    """可靠判断某个 operator 是否存在。

    注意: bpy.ops 的属性访问对任何名字都不报错(hasattr 恒为 True),
    必须用 dir() 检查; 否则插件会在旧版 Blender 上调用到不存在的操作符。
    """
    try:
        return name in dir(module)
    except Exception:
        return False


def _has_quadriflow():
    return _op_exists(bpy.ops.object, "quadriflow_remesh")


def _has_modern_obj_import():
    """Blender 4.0+ 的新版 OBJ 导入器。"""
    return _op_exists(bpy.ops.wm, "obj_import")


def _has_legacy_obj_import():
    """Blender 3.x 及更早的 OBJ 导入器。"""
    try:
        return _op_exists(getattr(bpy.ops, "import_scene", None), "obj")
    except Exception:
        return False


bl_info = {
    "name": "MD布料转四边面 (MD Fabric to Quads)",
    "author": "Hermes (based on 离山笔记侠 Maya script)",
    "version": (1, 3, 0),
    "blender": (2, 80, 0),
    "location": "3D视图 > 侧边栏(N) > MD转四边面",
    "description": "MD三角面布料→四边面：导入/识别 → 生成四边面 → 传UV + 套回衣服形状，支持 Blender 2.80+",
    "category": "Mesh",
    "doc_url": "https://github.com/regdiliKUN/md-to-quads-blender",
    "tracker_url": "https://github.com/regdiliKUN/md-to-quads-blender/issues",
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

def transfer_uvs(base, uv_src, progress=None):
    """世界空间最近点采样: 把 uv_src 的全部UV层传递给 base (等价 transferUVs=2)。

    性能: BVH 查询只对「唯一顶点」做(而不是每个 UV 点), 一个顶点通常对应 6 个 UV 点,
    查询量直接降到约 1/6; UV 插值改为 numpy 向量化, 不再逐点循环。
    """
    src_layers = _uv_arrays(uv_src)
    if not src_layers:
        raise ValueError(u'UV来源(①平板)没有UV层')
    sw, slvi, swco = _loop_world(uv_src)
    stris = _tris(uv_src)
    if len(stris) == 0:
        raise ValueError(u'UV来源(①平板)没有面')
    tree = _bvh_from(sw, stris)
    if tree is None:
        raise ValueError(u'无法从①平板建立空间索引（网格可能已损坏）')

    bw, blvi, bwco = _loop_world(base)
    n = len(bw)
    if n == 0:
        raise ValueError(u'②四边面上没有可写入的 UV 点')

    uniq, inv = np.unique(blvi, return_inverse=True)
    nv = len(uniq)
    tri_id = np.full(nv, -1, dtype=np.int64)
    barys = np.zeros((nv, 3), dtype=np.float64)
    step = max(1, nv // 50)
    for i in range(nv):
        p = bw[uniq[i]]
        hit = tree.find_nearest(Vector((p[0], p[1], p[2])))
        if hit is not None:
            loc, _nrm, idx, _d = hit
            if idx is not None and idx >= 0 and loc is not None:
                T = sw[stris[idx]]
                tri_id[i] = idx
                barys[i] = _bary3d(np.array(loc, dtype=np.float64), T)
        if progress is not None and (i % step == 0 or i == nv - 1):
            progress(float(i + 1) / float(nv))

    loop_tri = tri_id[inv]
    loop_bary = barys[inv]
    valid = loop_tri >= 0

    me = base.data
    for lname, sarr in src_layers:
        tgt = me.uv_layers.get(lname)
        if tgt is None:
            tgt = me.uv_layers.new(name=lname)
        out = np.empty(n * 2, dtype=np.float64)
        tgt.data.foreach_get('uv', out)
        out = out.reshape(-1, 2)
        if valid.any():
            idx3 = stris[np.where(valid, loop_tri, 0)]       # (n, 3) 三个角的 loop 下标
            w3 = loop_bary[:, :, None]                        # (n, 3, 1) 重心坐标
            sampled = (sarr[idx3] * w3).sum(axis=1)           # (n, 2) 向量化插值
            out[valid] = sampled[valid]
        tgt.data.foreach_set('uv', out.ravel())
    me.update()
    return int(valid.sum()), n


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


def transfer_positions(base, pos_src, progress=None):
    """顶点位置: 对 base 每个顶点, 用其UV在 pos_src 的UV空间找最近点, 采样 pos_src 顶点位置。"""
    b_name, s_name = _pick_uv_names(base, pos_src)
    if s_name is None:
        raise ValueError(u'位置来源(③衣服)没有UV层')
    if b_name is None:
        raise ValueError(u'目标(②四边面)还没有UV层（应先执行 UV 传递）')

    s_layers = dict(_uv_arrays(pos_src))
    suv = s_layers[s_name]
    stris = _tris(pos_src)
    if len(stris) == 0:
        raise ValueError(u'位置来源(③衣服)没有面')
    vs = np.concatenate([suv, np.zeros((len(suv), 1))], axis=1)
    tree = _bvh_from(vs, stris)
    if tree is None:
        raise ValueError(u'无法在③衣服的 UV 空间建立索引（UV 可能完全重叠或已损坏）')

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
    step = max(1, nv // 50)
    for i in range(nv):
        if covered[i]:
            q = Vector((vert_uv[i, 0], vert_uv[i, 1], 0.0))
            hit = tree.find_nearest(q)
            if hit is not None:
                loc, _nrm, ti, _d = hit
                if ti is not None and ti >= 0 and loc is not None:
                    t3 = stris[ti]
                    P = suv[t3]
                    w1, w2, w3 = _bary2d((loc[0], loc[1]), tuple(P[0]), tuple(P[1]), tuple(P[2]))
                    pw = w1 * gw[t3[0]] + w2 * gw[t3[1]] + w3 * gw[t3[2]]
                    co[i] = pw @ mw_inv[:3, :3].T + mw_inv[:3, 3]
                    hit_count += 1
        if progress is not None and (i % step == 0 or i == nv - 1):
            progress(float(i + 1) / float(nv))
    me.vertices.foreach_set('co', co.ravel())
    me.update()
    return hit_count, nv


def do_all(base, uv_src, pos_src, delete_sources=True):
    """一次性完成 UV + 顶点位置传递（旧版对外函数名保留, 供脚本调用）。

    参数名沿用旧版: base=②四边面, uv_src=①平板, pos_src=③衣服。
    """
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


def _unique_object_name(name):
    """生成场景内不冲突的物体名(避免多次运行出现 .001/.002 混乱)。"""
    base = name[:56]
    candidate = base
    i = 1
    while candidate in bpy.data.objects:
        candidate = "%s.%03d" % (base, i)
        i += 1
    return candidate


def _linked_copy(obj, new_name):
    """复制物体(含独立网格数据)并真正链接进原物体所在的集合。

    修复: 旧实现里 obj.copy() 之后没有 link, 复制体不在任何集合中,
    后续 duplicate/重拓扑会作用于错误的对象甚至静默失败。
    """
    new_obj = obj.copy()
    new_obj.data = obj.data.copy()
    linked = False
    for coll in list(obj.users_collection):
        coll.objects.link(new_obj)
        linked = True
    if not linked:
        bpy.context.scene.collection.objects.link(new_obj)
    new_obj.name = _unique_object_name(new_name)
    try:
        # 标记「本插件生成的中间体」, 重复运行时可以安全清理, 不会误删用户自己的模型
        new_obj["md2q_generated"] = True
    except Exception:
        pass
    return new_obj


def _activate_only(obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def _triangle_normal(a, b, c):
    n = (b - a).cross(c - a)
    length = n.length
    if length < 1e-12:
        return None
    return n / length


def _face_winding_normal(face):
    """按面的 loop 顺序求法线。

    直接用顶点集合排序会得到反向法线(两个面的法线点积变成 -1),
    导致所有候选边都被判为「折角过大」而被跳过。
    """
    vs = [l.vert for l in face.loops]
    if len(vs) != 3:
        return None
    return _triangle_normal(vs[0].co, vs[1].co, vs[2].co)


def _pair_quality(e, angle_limit_rad):
    """评估一条内部边「能否合并成好四边面」。

    返回 (总偏离, 最大偏离)；超过阈值或退化时返回 None。
    偏离 = 合并后四边面四个内角与 90° 的差值，越小越方正。
    """
    f1, f2 = e.link_faces[0], e.link_faces[1]
    v0, v1 = e.verts[0], e.verts[1]
    op1 = [v for v in f1.verts if v is not v0 and v is not v1]
    op2 = [v for v in f2.verts if v is not v0 and v is not v1]
    if len(op1) != 1 or len(op2) != 1:
        return None
    o1, o2 = op1[0], op2[0]

    # 两面夹角(折角)过大就不合并, 避免把折痕抹平
    n1 = _face_winding_normal(f1)
    n2 = _face_winding_normal(f2)
    if n1 is None or n2 is None:
        return None
    if n1.dot(n2) < math.cos(min(angle_limit_rad, math.radians(89.0))):
        return None

    # 合并后的四边形环序: o1 -> v0 -> o2 -> v1
    cycle = [o1.co, v0.co, o2.co, v1.co]
    total = 0.0
    worst = 0.0
    for i in range(4):
        prev, cur, nxt = cycle[(i - 1) % 4], cycle[i], cycle[(i + 1) % 4]
        va, vb = prev - cur, nxt - cur
        if va.length < 1e-12 or vb.length < 1e-12:
            return None
        cosang = max(-1.0, min(1.0, va.normalized().dot(vb.normalized())))
        dev = abs(math.acos(cosang) - math.pi * 0.5)
        total += dev
        worst = max(worst, dev)
    if worst > angle_limit_rad:
        return None
    return total, worst


def _pair_triangles_to_quads(bm, angle_limit_rad, progress=None):
    """贪心把三角面两两合并成四边面: 先合并质量最好的, 每个面只用一次。

    自研实现而非调用 bmesh.ops.join_triangles —— 后者在 Blender 4.x 上参数改名
    且实际不再产生任何合并, 用它在 4.2+ 会得到「0% 四边面」的假成功。
    """
    edges = bm.edges
    total_edges = max(1, len(edges))
    step = max(1, total_edges // 50)
    candidates = []
    for i, e in enumerate(edges):
        if len(e.link_faces) != 2:
            continue
        f1, f2 = e.link_faces[0], e.link_faces[1]
        if len(f1.verts) != 3 or len(f2.verts) != 3:
            continue
        q = _pair_quality(e, angle_limit_rad)
        if q is None:
            continue
        candidates.append((q[0], q[1], e))
        if progress is not None and (i % step == 0):
            progress(min(1.0, float(i) / float(total_edges)))

    candidates.sort(key=lambda x: (x[0], x[1]))
    joined = 0
    used = set()
    for _total, _worst, e in candidates:
        if not e.is_valid:
            continue
        faces = [f for f in e.link_faces if f.is_valid and len(f.verts) == 3]
        if len(faces) != 2 or faces[0] in used or faces[1] in used:
            continue
        try:
            result = bmesh.utils.face_join(faces)
        except Exception:
            result = None
        if result is None:
            continue
        used.add(faces[0])
        used.add(faces[1])
        joined += 1
    return joined


def join_triangles_to_quads(obj, angle_limit_deg=45.0, progress=None):
    """方案A(默认, 全版本可用): 把三角面按夹角阈值两两合并成四边面。

    MD 导出的板片本身是规则三角网格, 配对合并几乎能还原成完整四边面,
    且天然保留原UV, 无需外部重拓扑器, Blender 4.2+ 移除 Quadriflow 后依然可用。
    """
    new_obj = _linked_copy(obj, obj.name + "_quads")
    me = new_obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    try:
        _pair_triangles_to_quads(bm, radians(angle_limit_deg), progress=progress)
        bm.to_mesh(me)
    finally:
        bm.free()
    me.update()
    return new_obj


def quadriflow_remesh(obj, target_faces, preserve_sharp, preserve_boundary):
    """方案B: 对 obj 做一份四边面副本(保持原位/变换, 与原件重叠), 返回新对象。

    注意: 该操作符在部分 Blender 版本(如 4.2+)已无对应参数或被移除,
    调用前请先用 _has_quadriflow() 判断, 否则应退回 join_triangles_to_quads。
    """
    new_obj = _linked_copy(obj, obj.name + "_quads")
    _activate_only(new_obj)

    # 只传当前版本真实存在的参数: 4.2 已移除 use_mesh_curvature / preserve_paint_mask
    props = _op_prop_names(bpy.ops.object, "quadriflow_remesh")
    kwargs = {}
    if 'target_faces' in props:
        kwargs['target_faces'] = int(target_faces)
    if 'use_preserve_sharp' in props:
        kwargs['use_preserve_sharp'] = bool(preserve_sharp)
    if 'use_preserve_boundary' in props:
        kwargs['use_preserve_boundary'] = bool(preserve_boundary)
    if 'use_mesh_curvature' in props:      # 旧版本参数
        kwargs['use_mesh_curvature'] = False
    if 'preserve_paint_mask' in props:     # 旧版本参数
        kwargs['preserve_paint_mask'] = False
    if 'smooth_normals' in props:
        kwargs['smooth_normals'] = True
    if 'mode' in props:
        # 新版本新增了 mode, 必须显式指定按「面数」为目标, 否则 target_faces 不生效
        try:
            ids = [i.identifier for i in getattr(bpy.ops.object, "quadriflow_remesh")
                   .get_rna_type().properties['mode'].enum_items]
            if 'FACES' in ids:
                kwargs['mode'] = 'FACES'
        except Exception:
            pass
    bpy.ops.object.quadriflow_remesh(**kwargs)
    return new_obj


def _detect_from_list(meshes):
    """在一组网格里挑出 平板(最扁) 与 衣服(最立体)。"""
    info = []
    for o in meshes:
        if o is None or o.type != 'MESH' or len(o.data.vertices) == 0:
            continue
        bb = _object_world_bbox(o)
        if bb is None:
            continue
        mn, mx = bb
        dims = mx - mn
        info.append((float(np.min(dims)), o))
    if len(info) < 2:
        return None, None
    info.sort(key=lambda x: x[0])            # 最扁的排最前
    flat = info[0][1]
    draped = max(info[1:], key=lambda x: x[0])[1]   # 其余里最立体的 = 衣服
    return flat, draped


def _auto_detect(context):
    """从选中(或场景中)的网格里自动识别 平板(①, 最扁) 与 衣服(③, 最立体)。"""
    sel = [o for o in context.selected_objects if o.type == 'MESH']
    cands = sel if len(sel) >= 2 else [o for o in context.scene.objects if o.type == 'MESH']
    return _detect_from_list([o for o in cands if len(o.data.vertices) > 0])


# ---------------------------------------------------------------- 导入后自动识别
# 导入窗口是异步的(确认后才会真正加进场景), 用一个短轮询在导入完成时自动回填 ①③,
# 省掉用户「导入完还要再点一次识别」的操作。
_IMPORT_WATCH = {"names": set(), "tries": 0}
_IMPORT_TIMER = None


def _stop_import_watch():
    global _IMPORT_TIMER
    if _IMPORT_TIMER is not None:
        try:
            bpy.app.timers.unregister(_IMPORT_TIMER)
        except Exception:
            pass
        _IMPORT_TIMER = None


def _import_watch_timer():
    """轮询等待 OBJ 导入完成, 然后自动把新进来的两个网格填成 ①③。"""
    global _IMPORT_TIMER
    try:
        new_meshes = [o for o in bpy.data.objects
                      if o.type == 'MESH' and o.name not in _IMPORT_WATCH["names"]
                      and len(o.data.vertices) > 0]
        if len(new_meshes) >= 2:
            flat, draped = _detect_from_list(new_meshes)
            _IMPORT_TIMER = None
            if flat is not None and draped is not None:
                pr = bpy.context.scene.md2q
                pr.plate_obj = flat
                pr.garment_obj = draped
                _set_status(bpy.context,
                            u"已自动识别：①%s（平板）/ ③%s（衣服），接下来点「一键生成四边面衣服」"
                            % (flat.name, draped.name), 'SUCCESS')
            return None
        _IMPORT_WATCH["tries"] += 1
        if _IMPORT_WATCH["tries"] > 60:      # 约 30 秒后放弃, 不留后台任务
            _IMPORT_TIMER = None
            return None
        return 0.5
    except Exception:
        _IMPORT_TIMER = None
        return None


def _start_import_watch():
    global _IMPORT_TIMER
    _stop_import_watch()
    _IMPORT_WATCH["names"] = set(o.name for o in bpy.data.objects)
    _IMPORT_WATCH["tries"] = 0
    try:
        bpy.app.timers.register(_import_watch_timer, first_interval=0.8, persistent=False)
        _IMPORT_TIMER = _import_watch_timer
    except Exception:
        _IMPORT_TIMER = None


def _estimate_target_faces(pr, plate):
    """按原网格面数估算目标四边面数(约三角面数的一半), 避免默认值与实际模型严重不匹配。"""
    if not getattr(pr, "auto_target", True):
        return max(100, int(pr.quad_target))
    try:
        n = len(plate.data.polygons)
    except Exception:
        n = 4000
    est = int(n // 2)
    return max(100, min(est, 200000))


def _make_quads(pr, plate, progress=None):
    """按面板选择的方式生成 ②四边面。统一入口, 失败时给出可读原因。"""
    if plate is None:
        raise ValueError(u"没有指定 ①平板")
    if pr.remesh_mode == 'QUADRIFLOW':
        if not _has_quadriflow():
            raise RuntimeError(u"当前 Blender 没有 Quadriflow 操作符（4.2+ 已被官方移除）")
        return quadriflow_remesh(plate, _estimate_target_faces(pr, plate),
                                 pr.preserve_sharp, pr.preserve_boundary)
    return join_triangles_to_quads(plate, pr.join_angle, progress=progress)


def _face_stats(obj):
    """统计面数 / 四边面比例, 用于结果反馈。"""
    try:
        polys = obj.data.polygons
    except Exception:
        return 0, 0, 0.0
    total = len(polys)
    quads = sum(1 for p in polys if len(p.vertices) == 4)
    tris = sum(1 for p in polys if len(p.vertices) == 3)
    ratio = (float(quads) / float(total) * 100.0) if total else 0.0
    return total, tris, ratio


# ---------------------------------------------------------------- 反馈与校验

def _show_popup(title, lines, icon='INFO'):
    """弹窗提示。无窗口环境(后台渲染/命令行)自动降级为打印, 避免报错。"""
    lines = [l for l in lines if l]
    if getattr(bpy.app, "background", False):
        print(u"[md_to_quads] %s: %s" % (title, u" / ".join(lines)))
        return
    def draw(self, context):
        layout = self.layout
        for line in lines:
            layout.label(text=line)
    try:
        bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)
    except Exception:
        pass


def _op_fail(op, context, title, lines, hint=None):
    """统一的失败出口: 状态栏 + 面板状态条 + 弹窗, 三处都能看到错误。"""
    lines = [l for l in lines if l]
    if hint:
        lines.append(u"→ " + hint)
    message = u" ".join(lines)
    _set_status(context, message, 'ERROR')
    if op is not None:
        op.report({'ERROR'}, message)
    _show_popup(title, lines, 'ERROR')
    return {'CANCELLED'}


def _op_done(op, context, message, stats=None):
    _set_status(context, message, 'SUCCESS')
    if stats:
        try:
            context.scene.md2q.last_stats = stats
        except Exception:
            pass
    if op is not None:
        op.report({'INFO'}, message)
    return {'FINISHED'}


def _ensure_object_mode(context):
    """传递/重拓扑都必须在物体模式下进行, 编辑态下静默切回。"""
    try:
        if context.object is not None and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass


def _is_usable(obj):
    """物体是否仍然有效: 未被删除、是网格、且有顶点。"""
    if obj is None:
        return False
    try:
        if obj.name not in bpy.data.objects:
            return False
    except Exception:
        return False
    return obj.type == 'MESH' and len(obj.data.vertices) > 0


def _check_sources(plate, garment):
    """校验 ①平板 / ③衣服 两个来源是否可用, 返回错误信息(无错返回 None)。"""
    if plate is None or garment is None:
        missing = []
        if plate is None:
            missing.append(u"①平板")
        if garment is None:
            missing.append(u"③衣服")
        return (u"还缺：" + u"、".join(missing)
                + u"。请先导入 MD 的两个 OBJ，或选中它们后点「从选择识别」")
    if not _is_usable(plate):
        return u"①平板无效（已被删除或不是网格），请重新指定"
    if not _is_usable(garment):
        return u"③衣服无效（已被删除或不是网格），请重新指定"
    if plate is garment:
        return u"①平板和③衣服不能是同一个物体"
    if len(plate.data.uv_layers) == 0:
        return u"①平板没有 UV"
    if len(garment.data.uv_layers) == 0:
        return u"③衣服没有 UV"
    return None


def _risk_warnings(plate, garment):
    """开始前给出「不致命但值得知道」的风险提示, 避免用户以为卡死或结果不对。"""
    warns = []
    try:
        for o, tag in ((plate, u"①"), (garment, u"③")):
            if _is_usable(o) and len(o.data.polygons) > 200000:
                warns.append(u"%s%s 有 %d 面，处理可能需要几十秒到几分钟"
                             % (tag, o.name, len(o.data.polygons)))
        if _is_usable(plate) and _is_usable(garment):
            p_names = set(l.name for l in plate.data.uv_layers)
            g_names = set(l.name for l in garment.data.uv_layers)
            if p_names and g_names and not (p_names & g_names):
                warns.append(u"①③的 UV 层名不同（%s / %s），将按活动 UV 层对齐，"
                             u"若形状错位请改回同一套「统一UV坐标」"
                             % (u"/".join(sorted(p_names)), u"/".join(sorted(g_names))))
    except Exception:
        pass
    return warns


def _check_quad(quad):
    if quad is None:
        return u"还缺：②四边面。点「生成 ② 四边面」或直接一键转换即可自动创建"
    if not _is_usable(quad):
        return u"②四边面无效（已被删除或不是网格），请重新生成"
    return None


# ---------------------------------------------------------------- 操作器

class MD2Q_OT_import(bpy.types.Operator):
    bl_idname = "md2q.import_obj"
    bl_label = u"导入 MD 的 OBJ（可多选）"
    bl_description = (u"打开 Blender 的 OBJ 导入窗口，一次选中 MD 导出的「平板 + 衣服」两个 OBJ。"
                      u"导入完成后插件会自动把这两个模型填进 ① 和 ③")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if _has_modern_obj_import():          # Blender 4.0+
            bpy.ops.wm.obj_import('INVOKE_DEFAULT')
        elif _has_legacy_obj_import():        # Blender 3.x 及更早
            bpy.ops.import_scene.obj('INVOKE_DEFAULT')
        else:
            return _op_fail(self, context, u"没有 OBJ 导入器",
                            [u"当前 Blender 里找不到 OBJ 导入功能。"],
                            hint=u"请确认使用的是官方完整版 Blender（精简/便携构建可能裁剪了 IO 插件）")
        _start_import_watch()
        _set_status(context, u"在弹窗里选中「平板 + 衣服」两个 OBJ；导入完成后会自动填好 ①③", 'INFO')
        return {'FINISHED'}


class MD2Q_OT_auto(bpy.types.Operator):
    bl_idname = "md2q.auto_all"
    bl_label = u"一键生成四边面衣服"
    bl_description = (u"全自动：自动识别①平板与③衣服 → 生成②四边面 → 传 UV → 按 UV 套回衣服形状。"
                      u"没有手动指定时会按「最扁的是平板、最立体的是衣服」判断")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        wm = context.window_manager
        _ensure_object_mode(context)
        t0 = time.time()

        plate, garment = pr.plate_obj, pr.garment_obj
        auto_picked = False
        if plate is None or garment is None:
            plate, garment = _auto_detect(context)
            auto_picked = plate is not None
        err = _check_sources(plate, garment)
        if err:
            return _op_fail(self, context, u"无法开始转换", [err],
                            hint=u"MD 导出两个 OBJ 时都要勾选「统一UV坐标」")

        # 自动识别的结果回填到面板, 用户能立刻看到插件的判定并可手动纠正
        pr.plate_obj, pr.garment_obj = plate, garment
        # 名字要先存下来: 开启「删除源模型」后对象会被移除, 之后再读 .name 会抛 ReferenceError
        plate_name, garment_name = plate.name, garment.name

        # 重复运行时清掉上一次插件自己生成的②, 避免场景里越攒越多
        stale = pr.quad_obj
        if (_is_usable(stale) and stale is not plate and stale is not garment
                and stale.get("md2q_generated")):
            bpy.data.objects.remove(stale, do_unlink=True)
            pr.quad_obj = None

        try:
            wm.progress_begin(0, 100)
            wm.progress_update(5)
            try:
                quad = _make_quads(pr, plate,
                                   progress=lambda f: wm.progress_update(5 + int(35 * f)))
            except Exception as e:
                return _op_fail(self, context, u"生成四边面失败", [u"%s" % e],
                                hint=u"方式选「Quadriflow 重拓扑」时，请确认当前 Blender 仍自带该功能；否则改用「三角面配对」")
            finally:
                wm.progress_update(40)
            pr.quad_obj = quad
            # 结果用③衣服的名字命名更易认(它就是那件衣服的四边面版本)
            if garment is not None:
                try:
                    quad.name = _unique_object_name(garment.name + "_quads")
                except Exception:
                    pass

            total_faces, tris_left, quad_ratio = _face_stats(quad)
            uv_note = u""
            uv_hits = uv_total = 0
            if pr.remesh_mode == 'JOIN' and pr.keep_uv_when_present and len(quad.data.uv_layers):
                # 配对方式下②是从①复制来的, UV 已经完全一致, 不需要再采样一次
                uv_note = u"沿用原有 UV（未重新采样）"
            else:
                try:
                    uv_hits, uv_total = transfer_uvs(
                        quad, plate,
                        progress=lambda f: wm.progress_update(40 + int(30 * f)))
                    uv_note = u"UV 传递 %d/%d" % (uv_hits, uv_total)
                except Exception as e:
                    return _op_fail(self, context, u"UV 传递失败", [u"%s" % e],
                                    hint=u"②四边面已生成并保留在场景中，可手动指定物体后重试")

            wm.progress_update(70)
            try:
                pos_hits, pos_total = transfer_positions(
                    quad, garment,
                    progress=lambda f: wm.progress_update(70 + int(25 * f)))
            except Exception as e:
                return _op_fail(self, context, u"顶点位置传递失败", [u"%s" % e],
                                hint=u"②四边面已生成并保留在场景中，可手动指定物体后重试")
            wm.progress_update(95)

            # 清理源模型前显式压入一步撤销, 保证 Ctrl+Z 一定能回来
            deleted = []
            if pr.delete_sources:
                try:
                    bpy.ops.ed.undo_push(message="MD转四边面：删除源模型")
                except Exception:
                    pass
                for o in (plate, garment):
                    if _is_usable(o):
                        deleted.append(o.name)
                        bpy.data.objects.remove(o, do_unlink=True)
                pr.plate_obj = None
                pr.garment_obj = None

            _activate_only(quad)
            wm.progress_update(100)
        finally:
            wm.progress_end()

        secs = time.time() - t0
        stats = (u"物体: %s | 面数 %d | 四边面 %.0f%%（剩余三角面 %d） | UV: %s | 位置 %d/%d | 用时 %.1fs"
                 % (quad.name, total_faces, quad_ratio, tris_left, uv_note,
                    pos_hits, pos_total, secs))
        warns = _risk_warnings(plate, garment)
        if warns:
            stats += u" | 提示: " + u"；".join(warns)
        if quad_ratio < 60.0 and pr.remesh_mode == 'JOIN':
            stats += u" | 四边面占比偏低，可在「②生成四边面」里调大配对角度阈值"
        msg = u"完成：%s%s" % (quad.name,
                              u"（已删除 %s）" % u"、".join(deleted) if deleted else u"")
        if auto_picked:
            msg += u" 〔自动识别：①%s / ③%s〕" % (plate_name, garment_name)
        return _op_done(self, context, msg, stats)


class MD2Q_OT_make_quads(bpy.types.Operator):
    bl_idname = "md2q.make_quads"
    bl_label = u"生成 ② 四边面"
    bl_description = (u"把①平板复制一份并转成四边面（原物体保持不变）。"
                      u"①没指定时，默认用当前选中的网格")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        _ensure_object_mode(context)
        plate = pr.plate_obj if _is_usable(pr.plate_obj) else None
        if plate is None and _is_usable(context.object):
            plate = context.object
        if plate is None:
            return _op_fail(self, context, u"没有可用的平板",
                            [u"请先指定①平板，或选中一个网格物体再点此按钮。"])
        pr.plate_obj = plate
        try:
            quad = _make_quads(pr, plate)
        except Exception as e:
            return _op_fail(self, context, u"生成四边面失败", [u"%s" % e],
                            hint=u"Quadriflow 方式在 Blender 4.2+ 可能已不可用，可改用「三角面配对」")
        pr.quad_obj = quad
        _activate_only(quad)
        total, tris, ratio = _face_stats(quad)
        src_total, src_tris, _src_ratio = _face_stats(plate)
        stats = u"剩余三角面 %d" % tris
        if src_tris == 0:
            stats = (u"①%s 本身没有三角面（%d 面），无需配对；"
                     u"如果这不是你要的结果，请确认①是 MD 导出的三角面网格"
                     % (plate.name, src_total))
        elif ratio < 60.0:
            stats += u"；四边面占比偏低，可调大配对角度阈值或改用 Quadriflow"
        return _op_done(self, context,
                        u"已生成 %s：%d 面，四边面占比 %.0f%%" % (quad.name, total, ratio),
                        stats=stats)


class MD2Q_OT_make_quads_legacy(bpy.types.Operator):
    """旧版 idname 的兼容入口, 避免老用户保存的快捷键/脚本失效。"""
    bl_idname = "md2q.quadriflow_remesh"
    bl_label = u"生成四边面（旧入口）"
    bl_description = u"旧版本入口，等价于「生成 ② 四边面」，保留以兼容已有的快捷键与脚本"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return bpy.ops.md2q.make_quads()


class MD2Q_OT_transfer_all(bpy.types.Operator):
    bl_idname = "md2q.transfer_all"
    bl_label = u"传 UV + 套回衣服形状"
    bl_description = u"把①的 UV 传给②，再按 UV 把②套到③的衣服形状上。②缺失时会自动先生成"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        wm = context.window_manager
        _ensure_object_mode(context)
        err = _check_sources(pr.plate_obj, pr.garment_obj)
        if err:
            return _op_fail(self, context, u"无法传递", [err])

        quad = pr.quad_obj
        if not _is_usable(quad):
            # 流程简化: 用户只指定了①③时, 这里自动补上生成②这一步
            try:
                quad = _make_quads(pr, pr.plate_obj)
                pr.quad_obj = quad
            except Exception as e:
                return _op_fail(self, context, u"自动生成②四边面失败", [u"%s" % e])

        try:
            wm.progress_begin(0, 100)
            wm.progress_update(10)
            uv_note = u""
            if pr.remesh_mode == 'JOIN' and pr.keep_uv_when_present and len(quad.data.uv_layers):
                uv_note = u"沿用原有 UV"
            else:
                uv_hits, uv_total = transfer_uvs(
                    quad, pr.plate_obj,
                    progress=lambda f: wm.progress_update(10 + int(50 * f)))
                uv_note = u"UV %d/%d" % (uv_hits, uv_total)
            wm.progress_update(60)
            pos_hits, pos_total = transfer_positions(
                quad, pr.garment_obj,
                progress=lambda f: wm.progress_update(60 + int(30 * f)))
            wm.progress_update(90)
            deleted = []
            if pr.delete_sources:
                try:
                    bpy.ops.ed.undo_push(message="MD转四边面：删除源模型")
                except Exception:
                    pass
                for o in (pr.plate_obj, pr.garment_obj):
                    if _is_usable(o):
                        deleted.append(o.name)
                        bpy.data.objects.remove(o, do_unlink=True)
                pr.plate_obj = None
                pr.garment_obj = None
            _activate_only(quad)
            wm.progress_update(100)
        except Exception as e:
            return _op_fail(self, context, u"传递失败", [u"%s" % e])
        finally:
            wm.progress_end()

        msg = u"完成：%s（%s，位置 %d/%d）" % (quad.name, uv_note, pos_hits, pos_total)
        if deleted:
            msg += u"，已删除 %s" % u"、".join(deleted)
        return _op_done(self, context, msg,
                        stats=u"目标物体 %s | %s | 顶点位置 %d/%d" % (quad.name, uv_note, pos_hits, pos_total))


class MD2Q_OT_uv(bpy.types.Operator):
    bl_idname = "md2q.transfer_uvs_only"
    bl_label = u"只传 UV"
    bl_description = u"只做 UV 传递：按世界空间最近点，把①平板的全部 UV 层采样到②四边面上"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        _ensure_object_mode(context)
        if not _is_usable(pr.plate_obj):
            return _op_fail(self, context, u"缺少①平板", [u"请先指定①三角面平板（UV 来源）。"])
        err = _check_quad(pr.quad_obj)
        if err:
            return _op_fail(self, context, u"缺少②四边面", [err])
        try:
            ok, n = transfer_uvs(pr.quad_obj, pr.plate_obj)
        except Exception as e:
            return _op_fail(self, context, u"UV 传递失败", [u"%s" % e])
        return _op_done(self, context, u"UV 传递完成：%d/%d 个 UV 点已写入 %s" % (ok, n, pr.quad_obj.name),
                        stats=u"采样命中率 %.0f%%" % (100.0 * ok / n if n else 0.0))


class MD2Q_OT_pos(bpy.types.Operator):
    bl_idname = "md2q.transfer_pos_only"
    bl_label = u"只套回衣服形状"
    bl_description = u"只做顶点位置传递：按②的 UV 在③的 UV 空间里采样，把②套到衣服形状上（需先有 UV）"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        _ensure_object_mode(context)
        if not _is_usable(pr.garment_obj):
            return _op_fail(self, context, u"缺少③衣服", [u"请先指定③三角面衣服模型（形状来源）。"])
        err = _check_quad(pr.quad_obj)
        if err:
            return _op_fail(self, context, u"缺少②四边面", [err])
        if len(pr.quad_obj.data.uv_layers) == 0:
            return _op_fail(self, context, u"②还没有 UV",
                            [u"顶点位置是「按 UV 对齐」的，②上必须有 UV。"],
                            hint=u"请先点「只传 UV」，或直接用一键转换")
        try:
            ok, n = transfer_positions(pr.quad_obj, pr.garment_obj)
        except Exception as e:
            return _op_fail(self, context, u"位置传递失败", [u"%s" % e])
        return _op_done(self, context, u"形状已套回：%d/%d 个顶点" % (ok, n),
                        stats=u"覆盖顶点 %d/%d" % (ok, n))


class MD2Q_OT_pick_from_selection(bpy.types.Operator):
    bl_idname = "md2q.pick_from_selection"
    bl_label = u"从选择识别"
    bl_description = (u"选中两个网格后点此按钮：自动判断较扁的是①平板、较立体的是③衣服。"
                      u"选中三个时按选择顺序填入 ①②③")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        sel = [o for o in context.selected_objects if _is_usable(o)]
        if len(sel) >= 3:
            pr.plate_obj, pr.quad_obj, pr.garment_obj = sel[0], sel[1], sel[2]
            msg = u"已按顺序填入：①%s / ②%s / ③%s" % (sel[0].name, sel[1].name, sel[2].name)
        elif len(sel) == 2:
            flat, draped = _auto_detect(context)
            if flat is None or draped is None:
                return _op_fail(self, context, u"识别失败",
                                [u"选中的两个网格无法区分平板与衣服。"],
                                hint=u"也可以手动用每个字段右侧的吸管分别指定")
            pr.plate_obj, pr.garment_obj = flat, draped
            msg = u"已识别：①%s（较扁）/ ③%s（较立体）" % (flat.name, draped.name)
        else:
            return _op_fail(self, context, u"选中数量不对",
                            [u"当前选中了 %d 个可用网格，需要 2 个（平板+衣服）。" % len(sel)],
                            hint=u"按住 Shift 加选第二个物体")
        return _op_done(self, context, msg)


class MD2Q_OT_clear(bpy.types.Operator):
    bl_idname = "md2q.clear_roles"
    bl_label = u"清空"
    bl_description = u"清空已指定的 ①②③ 与状态提示（只清面板引用，不会删除场景里的物体）"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        pr = context.scene.md2q
        pr.plate_obj = None
        pr.quad_obj = None
        pr.garment_obj = None
        pr.last_stats = ""
        return _op_done(self, context, u"已清空，可以重新开始")


# ---------------------------------------------------------------- 面板

def _on_quad_target_changed(self, context):
    """用户手动改了目标面数, 就认为他不想再用自动估算。"""
    try:
        self.auto_target = False
    except Exception:
        pass


class MD2Q_Props(bpy.types.PropertyGroup):
    # ---- 三个角色物体: 命名改为「角色」而非「内部算法变量名」, 面板读数即懂 ----
    plate_obj: bpy.props.PointerProperty(
        type=bpy.types.Object,
        name=u"① 平板（三角面）",
        description=u"MD 里「平铺状态」导出的三角面布料。它是 UV 的来源，也会被重拓扑成四边面",
    )
    quad_obj: bpy.props.PointerProperty(
        type=bpy.types.Object,
        name=u"② 四边面（结果）",
        description=u"由①重拓扑得到的四边面网格，是最终接受 UV 与形状的物体。一般自动生成，无需手填",
    )
    garment_obj: bpy.props.PointerProperty(
        type=bpy.types.Object,
        name=u"③ 衣服（三角面）",
        description=u"MD 里「穿着/3D 状态」导出的三角面衣服。它只提供形状（顶点位置）",
    )

    # ---- 四边面生成方式 ----
    remesh_mode: bpy.props.EnumProperty(
        name=u"生成方式",
        description=u"选择把三角面变成四边面的算法",
        items=(
            ('JOIN', u"三角面配对", u"两两合并三角面：最快、完整保留 UV，MD 规则网格几乎可 100% 还原四边面（全版本可用，推荐）"),
            ('QUADRIFLOW', u"Quadriflow 重拓扑", u"按目标面数重新分布四边面：可自由控制密度，但会改变拓扑与顶点位置（Blender 4.2 起官方可能移除）"),
        ),
        default='JOIN',
    )
    join_angle: bpy.props.FloatProperty(
        name=u"配对角度阈值",
        description=u"两个三角面合并成四边面时允许的最大夹角。MD 规则网格用默认 45° 即可；结果里三角面多就调大",
        default=45.0, min=1.0, max=180.0, step=100, precision=1,
        subtype='ANGLE' if _BLENDER_VERSION >= (2, 81, 0) else 'NONE',
    )
    quad_target: bpy.props.IntProperty(
        name=u"目标四边面数",
        description=u"Quadriflow 重拓扑的目标四边形数量，越大越密、越慢（仅「Quadriflow 重拓扑」方式生效）",
        default=4000, min=100, max=200000, soft_max=20000,
        update=_on_quad_target_changed,
    )
    auto_target: bpy.props.BoolProperty(
        name=u"按原网格自动估算",
        description=u"按①的面数自动估算一个合理的目标四边面数（约 1/2），手改数值会自动关闭",
        default=True,
    )
    preserve_sharp: bpy.props.BoolProperty(
        name=u"保留硬边",
        description=u"Quadriflow 保留锐利边缘，让板片折角不被抹平",
        default=True,
    )
    preserve_boundary: bpy.props.BoolProperty(
        name=u"保留边界",
        description=u"Quadriflow 保留外边界轮廓，避免板片边缘被吃掉",
        default=True,
    )

    # ---- 收尾与流程 ----
    delete_sources: bpy.props.BoolProperty(
        name=u"完成后删除 ①③ 源模型",
        description=u"转换成四边面后自动删除两个三角面源模型，保持场景干净；可用 Ctrl+Z 撤销",
        default=True,
    )
    keep_uv_when_present: bpy.props.BoolProperty(
        name=u"已有 UV 时跳过 UV 传递",
        description=u"三角面配对方式下②已自带与原网格一致的 UV，跳过采样传递可更快也更精确",
        default=True,
    )
    show_advanced: bpy.props.BoolProperty(
        name=u"高级选项",
        description=u"展开参数微调与手动分步执行（日常使用无需展开）",
        default=False,
    )

    # ---- 反馈状态（持久化到场景，面板随时可见，不依赖一闪而过的提示条）----
    status_message: bpy.props.StringProperty(name=u"状态", default="")
    status_level: bpy.props.EnumProperty(
        name=u"状态级别",
        items=(('NONE', u"无", u""), ('INFO', u"信息", u""), ('SUCCESS', u"成功", u""),
               ('WARNING', u"警告", u""), ('ERROR', u"错误", u"")),
        default='NONE',
    )
    last_stats: bpy.props.StringProperty(name=u"上次结果", default="")


def _set_status(context, message, level='INFO'):
    """把结果/错误写进场景属性，面板顶部常驻显示。"""
    try:
        pr = context.scene.md2q
        pr.status_message = message or ""
        pr.status_level = level if message else 'NONE'
    except Exception:
        pass


def _status_icon(level):
    return {'SUCCESS': 'CHECKMARK', 'WARNING': 'INFO', 'ERROR': 'ERROR'}.get(level, 'INFO')


def _obj_info(obj):
    """面板自检信息: 面数 + 是否有 UV(没有 UV 一定会失败, 提前告诉用户)。"""
    if not _is_usable(obj):
        return None
    try:
        me = obj.data
        return u"%d 面 · %s" % (len(me.polygons), u"有 UV" if len(me.uv_layers) else u"⚠ 无 UV")
    except Exception:
        return None


def _scene_mesh_count(context):
    try:
        return sum(1 for o in context.scene.objects if o.type == 'MESH' and len(o.data.vertices))
    except Exception:
        return 0


class MD2Q_PT_panel(bpy.types.Panel):
    bl_idname = "MD2Q_PT_panel"
    bl_label = u"MD布料转四边面"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = u"MD转四边面"

    def draw(self, context):
        layout = self.layout
        pr = context.scene.md2q
        # 这两个属性在部分老版本上不存在, 失败就退回默认布局而不是让整个面板报错
        try:
            layout.use_property_split = True
            layout.use_property_decorate = False
        except Exception:
            pass

        plate_ok = _is_usable(pr.plate_obj)
        garment_ok = _is_usable(pr.garment_obj)
        quad_ok = _is_usable(pr.quad_obj)
        ready = plate_ok and garment_ok

        # ---------- 常驻状态条: 结果/错误不再只靠一闪而过的提示条 ----------
        if pr.status_message:
            sbox = layout.box()
            srow = sbox.row()
            srow.alert = pr.status_level in ('ERROR', 'WARNING')
            srow.label(text=pr.status_message, icon=_status_icon(pr.status_level))
            if pr.last_stats:
                sbox.label(text=pr.last_stats, icon='DOT')

        # ---------- ① 准备 ----------
        box = layout.box()
        box.label(text=u"① 准备：MD 的两个 OBJ", icon='IMPORT')
        row = box.row(align=True)
        row.operator("md2q.import_obj", icon='IMPORT')
        row.operator("md2q.pick_from_selection", icon='EYEDROPPER')

        if not ready:
            # 空状态: 明确告知缺什么、下一步该做什么
            hint = box.box()
            hint.label(text=u"还没有可以转换的模型", icon='INFO')
            n_scene = _scene_mesh_count(context)
            if n_scene >= 2:
                hint.label(text=u"· 场景里已有 %d 个网格：选中两个 → 点「从选择识别」" % n_scene)
                hint.label(text=u"· 也可以直接点「一键生成四边面衣服」让它自动猜")
            else:
                hint.label(text=u"· 点「导入 MD 的 OBJ」，一次选中平板 + 衣服两个文件")
                hint.label(text=u"· MD 导出时两个 OBJ 都要勾选「统一UV坐标」")

        col = box.column(align=True)
        for name, ok, tip in (("plate_obj", plate_ok, u"①=平铺状态的三角面平板, UV 来源"),
                              ("garment_obj", garment_ok, u"③=穿着状态的三角面衣服, 只提供形状")):
            if ok:
                col.prop(pr, name)
                info = _obj_info(getattr(pr, name))
                if info:
                    col.label(text=u"  " + info, icon='DOT')
            else:
                r = col.row(align=True)
                r.alert = True
                r.prop(pr, name)
                col.label(text=u"  " + tip, icon='DOT')

        # ---------- ② 生成四边面 ----------
        box = layout.box()
        box.label(text=u"② 生成四边面", icon='MOD_REMESH')
        box.prop(pr, "remesh_mode", expand=True)
        if pr.remesh_mode == 'JOIN':
            box.prop(pr, "join_angle")
            if quad_ok:
                _t, tris, ratio = _face_stats(pr.quad_obj)
                box.label(text=u"当前②: %s · 四边面 %.0f%%（三角面 %d）"
                          % (pr.quad_obj.name, ratio, tris), icon='DOT')
        else:
            if not _has_quadriflow():
                warn = box.box()
                warn.alert = True
                warn.label(text=u"当前 Blender 没有 Quadriflow（4.2 起被移除）", icon='ERROR')
                warn.label(text=u"请改用「三角面配对」方式", icon='DOT')
            row = box.row(align=True)
            row.prop(pr, "auto_target")
            sub = box.row(align=True)
            sub.enabled = not pr.auto_target
            sub.prop(pr, "quad_target")
            row2 = box.row(align=True)
            row2.prop(pr, "preserve_sharp")
            row2.prop(pr, "preserve_boundary")
        box.operator("md2q.make_quads", icon='MOD_REMESH')

        # ---------- ③ 完成转换 ----------
        box = layout.box()
        box.label(text=u"③ 完成转换", icon='PLAY')
        col = box.column()
        col.scale_y = 1.4
        col.operator("md2q.auto_all", icon='PLAY')
        box.prop(pr, "delete_sources")
        if not ready:
            box.label(text=u"未指定①③时, 会自动按「最扁=平板」猜测", icon='QUESTION')

        # ---------- 高级选项(默认折叠, 不干扰主流程) ----------
        adv = layout.box()
        adv.prop(pr, "show_advanced", toggle=True, icon='SETTINGS')
        if pr.show_advanced:
            col = adv.column(align=True)
            col.prop(pr, "quad_obj")
            col.prop(pr, "keep_uv_when_present")
            row = adv.row(align=True)
            row.operator("md2q.transfer_uvs_only", icon='UV_DATA')
            row.operator("md2q.transfer_pos_only", icon='MESH_DATA')
            adv.operator("md2q.transfer_all", icon='UV_DATA')
            adv.operator("md2q.clear_roles", icon='TRASH')

        foot = layout.row()
        foot.label(text=u"Blender %d.%d · %s"
                   % (_BLENDER_VERSION[0], _BLENDER_VERSION[1],
                      u"Quadriflow 可用" if _has_quadriflow() else u"仅三角面配对可用"),
                   icon='DOT')


# ---------------------------------------------------------------- 注册

_classes = (
    MD2Q_Props,
    MD2Q_OT_import,
    MD2Q_OT_pick_from_selection,
    MD2Q_OT_clear,
    MD2Q_OT_make_quads,
    MD2Q_OT_make_quads_legacy,   # 旧 idname 兼容入口
    MD2Q_OT_uv,
    MD2Q_OT_pos,
    MD2Q_OT_transfer_all,
    MD2Q_OT_auto,
    MD2Q_PT_panel,
)


def register():
    for c in _classes:
        try:
            bpy.utils.register_class(c)
        except Exception as e:
            # 单个类注册失败不应让整个插件不可用(例如老版本缺少某个属性)
            print(u"[md_to_quads] 注册 %s 失败: %s" % (getattr(c, "__name__", c), e))
    bpy.types.Scene.md2q = bpy.props.PointerProperty(type=MD2Q_Props)


def unregister():
    _stop_import_watch()          # 不留后台定时器
    try:
        del bpy.types.Scene.md2q
    except Exception:
        pass
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
