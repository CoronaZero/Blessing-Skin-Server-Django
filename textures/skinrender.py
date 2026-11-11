"""皮肤等距 3D 立绘渲染器。

忠实移植 bs-community/texture-renderer（BSD-3，见 LICENSE 注）：
- 逐像素 3D 投影多边形（Point/Polygon 数学一致）
- 正面（-45°）+ 背面（135°）双视角拼合立绘
- 头盔/帽层（放大 12.5%）、Alex 3 像素臂、HD 皮肤（hd_ratio）、
  64x32 旧皮肤左臂/左腿镜像取色、1.8 新皮肤层合并、背景色清理
静止姿态渲染（原版皮肤库预览用法），肢体旋转省略
（原版 preProject 在零角度下为恒等变换）。
"""

import io
import math

from PIL import Image, ImageDraw


class _Pt:
    __slots__ = ("x", "y", "z", "dx", "dy", "dz", "projected")

    def __init__(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)
        self.dx = self.dy = self.dz = 0.0
        self.projected = False

    def project(self, cos_a, sin_a, cos_o, sin_o, bbox):
        x, y, z = self.x, self.y, self.z
        self.dx = x * cos_o + z * sin_o
        self.dy = x * sin_a * sin_o + y * cos_a - z * sin_a * cos_o
        self.dz = -x * cos_a * sin_o + y * sin_a + z * cos_a * cos_o
        self.projected = True
        bbox[0] = min(bbox[0], self.dx)
        bbox[1] = max(bbox[1], self.dx)
        bbox[2] = min(bbox[2], self.dy)
        bbox[3] = max(bbox[3], self.dy)


class _Poly:
    __slots__ = ("pts", "color")

    def __init__(self, pts, color):
        self.pts = pts
        self.color = color  # (r, g, b, a)

    def draw(self, image, min_x, min_y, ratio):
        r, g, b, a = self.color
        if a <= 8:  # GD: vR == 0 跳过全透明
            return
        xs = [p.dx for p in self.pts]
        ys = [p.dy for p in self.pts]
        if len(set(xs)) == 1 or len(set(ys)) == 1:  # 退化多边形
            return
        polygon = [
            ((p.dx - min_x) * ratio, (p.dy - min_y) * ratio) for p in self.pts
        ]
        ImageDraw.Draw(image).polygon(polygon, fill=(r, g, b, 255))


_FACES = ("back", "right", "top", "front", "left", "bottom")


class SkinRenderer:
    """单视角渲染（对照 PHP SkinRenderer，静止肢体姿态）。"""

    def __init__(self, ratio=7, head_only=False, horizontal_rotation=145,
                 vertical_rotation=-25):
        self.ratio = ratio
        self.head_only = head_only
        self.hR = horizontal_rotation
        self.vR = vertical_rotation

    def render(self, skin_bytes: bytes, is_alex: bool) -> Image.Image:
        skin = Image.open(io.BytesIO(skin_bytes)).convert("RGBA")
        if skin.width > 256:  # 防 HD 皮肤内存放大异常
            new_h = int(skin.height / (skin.width / 256))
            skin = skin.resize((256, new_h), Image.LANCZOS)

        hd = skin.width // 64
        is_new = skin.width == skin.height
        skin = self._clean_background(skin, hd)
        if is_new:  # 1.8 层合并（原版 quick fix）
            overlay = skin.crop((0, 32, 56, 48))
            skin.alpha_composite(overlay, (0, 16))
            overlay = skin.crop((0, 48, 16, 64))
            skin.alpha_composite(overlay, (16, 48))
            overlay = skin.crop((48, 48, 64, 64))
            skin.alpha_composite(overlay, (32, 48))

        px = skin.load()
        poly = {
            p: {f: [] for f in _FACES}
            for p in ("helmet", "head", "torso", "rightArm", "leftArm",
                      "rightLeg", "leftLeg")
        }
        self._gen_head(poly, px, hd)
        self._gen_helmet(poly, px, hd)
        if not self.head_only:
            self._gen_torso(poly, px, hd)
            self._gen_arm(poly, px, hd, is_alex, is_new, right=True)
            self._gen_arm(poly, px, hd, is_alex, is_new, right=False)
            self._gen_leg(poly, px, hd, is_new, right=True)
            self._gen_leg(poly, px, hd, is_new, right=False)

        cos_a, sin_a = math.cos(math.radians(self.vR)), math.sin(math.radians(self.vR))
        cos_o, sin_o = math.cos(math.radians(self.hR)), math.sin(math.radians(self.hR))
        bbox = [0.0, 0.0, 0.0, 0.0]
        for piece in poly.values():
            for face in piece.values():
                for p in face:
                    for pt in p.pts:
                        if not pt.projected:
                            pt.project(cos_a, sin_a, cos_o, sin_o, bbox)
        return self._display(poly, bbox)

    # ---- 部件生成 ----
    def _clean_background(self, skin: Image.Image, hd: int) -> Image.Image:
        """原版 makeBackgroundTransparent：角落纯色块 → 视为背景抠透明。"""
        px = skin.load()
        base = None
        for i in range(8 * hd):
            for j in range(8 * hd):
                c = px[i, j]
                if c[3] < 128:
                    return skin
                if base is None:
                    base = c
                elif c != base:
                    return skin
        # 角块纯色不透明：把该色抠成透明
        if base is not None:
            w, h = skin.size
            data = skin.load()
            r0, g0, b0 = base[0], base[1], base[2]
            for x in range(w):
                for y in range(h):
                    c = data[x, y]
                    if c[3] >= 128 and c[0] == r0 and c[1] == g0 and c[2] == b0:
                        data[x, y] = (r0, g0, b0, 0)
        return skin

    @staticmethod
    def _vox(x0, x1, y0, y1, z0, z1, xf=None, yf=None, zf=None):
        """生成体素点阵 dict[(i,j,k)]（坐标可经函数重映射）。"""
        xf = xf or (lambda i: i)
        yf = yf or (lambda j: j)
        zf = zf or (lambda k: k)
        pts = {}
        for i in range(x0, x1 + 1):
            for j in range(y0, y1 + 1):
                for k in range(z0, z1 + 1):
                    pts[(i, j, k)] = _Pt(xf(i), yf(j), zf(k))
        return pts

    @staticmethod
    def _quad(v, a, b, c, d):
        return [v[a], v[b], v[c], v[d]]

    def _gen_head(self, poly, px, hd):
        v = self._vox(0, 8 * hd, 0, 8 * hd, -2 * hd, 6 * hd)
        for i in range(8 * hd):
            for j in range(8 * hd):
                poly["head"]["back"].append(_Poly(
                    self._quad(v, (i, j, -2 * hd), (i + 1, j, -2 * hd),
                               (i + 1, j + 1, -2 * hd), (i, j + 1, -2 * hd)),
                    px[(32 * hd - 1) - i, 8 * hd + j]))
                poly["head"]["front"].append(_Poly(
                    self._quad(v, (i, j, 6 * hd), (i + 1, j, 6 * hd),
                               (i + 1, j + 1, 6 * hd), (i, j + 1, 6 * hd)),
                    px[8 * hd + i, 8 * hd + j]))
        for j in range(8 * hd):
            for k in range(-2 * hd, 6 * hd):
                poly["head"]["right"].append(_Poly(
                    self._quad(v, (0, j, k), (0, j, k + 1), (0, j + 1, k + 1),
                               (0, j + 1, k)),
                    px[k + 2 * hd, 8 * hd + j]))
                poly["head"]["left"].append(_Poly(
                    self._quad(v, (8 * hd, j, k), (8 * hd, j, k + 1),
                               (8 * hd, j + 1, k + 1), (8 * hd, j + 1, k)),
                    px[(24 * hd - 1) - k - 2 * hd, 8 * hd + j]))
        for i in range(8 * hd):
            for k in range(-2 * hd, 6 * hd):
                poly["head"]["top"].append(_Poly(
                    self._quad(v, (i, 0, k), (i + 1, 0, k), (i + 1, 0, k + 1),
                               (i, 0, k + 1)),
                    px[8 * hd + i, k + 2 * hd]))
                poly["head"]["bottom"].append(_Poly(
                    self._quad(v, (i, 8 * hd, k), (i + 1, 8 * hd, k),
                               (i + 1, 8 * hd, k + 1), (i, 8 * hd, k + 1)),
                    px[16 * hd + i, 2 * hd + k]))

    def _gen_helmet(self, poly, px, hd):
        s = 9 / 8

        def xf(i): return i * s - 0.5 * hd
        def yf(j): return j * s - 0.5 * hd
        def zf(k): return k * s - 0.5 * hd

        v = self._vox(0, 8 * hd, 0, 8 * hd, -2 * hd, 6 * hd, xf, yf, zf)
        for i in range(8 * hd):
            for j in range(8 * hd):
                poly["helmet"]["back"].append(_Poly(
                    self._quad(v, (i, j, -2 * hd), (i + 1, j, -2 * hd),
                               (i + 1, j + 1, -2 * hd), (i, j + 1, -2 * hd)),
                    px[64 * hd - 1 - i, 8 * hd + j]))
                poly["helmet"]["front"].append(_Poly(
                    self._quad(v, (i, j, 6 * hd), (i + 1, j, 6 * hd),
                               (i + 1, j + 1, 6 * hd), (i, j + 1, 6 * hd)),
                    px[40 * hd + i, 8 * hd + j]))
        for j in range(8 * hd):
            for k in range(-2 * hd, 6 * hd):
                poly["helmet"]["right"].append(_Poly(
                    self._quad(v, (0, j, k), (0, j, k + 1), (0, j + 1, k + 1),
                               (0, j + 1, k)),
                    px[32 * hd + k + 2 * hd, 8 * hd + j]))
                poly["helmet"]["left"].append(_Poly(
                    self._quad(v, (8 * hd, j, k), (8 * hd, j, k + 1),
                               (8 * hd, j + 1, k + 1), (8 * hd, j + 1, k)),
                    px[32 * hd + (24 * hd - 1) - k - 2 * hd, 8 * hd + j]))
        for i in range(8 * hd):
            for k in range(-2 * hd, 6 * hd):
                poly["helmet"]["top"].append(_Poly(
                    self._quad(v, (i, 0, k), (i + 1, 0, k), (i + 1, 0, k + 1),
                               (i, 0, k + 1)),
                    px[40 * hd + i, k + 2 * hd]))
                poly["helmet"]["bottom"].append(_Poly(
                    self._quad(v, (i, 8 * hd, k), (i + 1, 8 * hd, k),
                               (i + 1, 8 * hd, k + 1), (i, 8 * hd, k + 1)),
                    px[48 * hd + i, 2 * hd + k]))

    def _gen_torso(self, poly, px, hd):
        v = self._vox(0, 8 * hd, 8 * hd, 20 * hd, 0, 4 * hd)
        for i in range(8 * hd):
            for j in range(12 * hd):
                poly["torso"]["back"].append(_Poly(
                    self._quad(v, (i, 8 * hd + j, 0), (i + 1, 8 * hd + j, 0),
                               (i + 1, 8 * hd + j + 1, 0), (i, 8 * hd + j + 1, 0)),
                    px[(40 * hd - 1) - i, 20 * hd + j]))
                poly["torso"]["front"].append(_Poly(
                    self._quad(v, (i, 8 * hd + j, 4 * hd), (i + 1, 8 * hd + j, 4 * hd),
                               (i + 1, 8 * hd + j + 1, 4 * hd), (i, 8 * hd + j + 1, 4 * hd)),
                    px[20 * hd + i, 20 * hd + j]))
        for j in range(12 * hd):
            for k in range(4 * hd):
                poly["torso"]["right"].append(_Poly(
                    self._quad(v, (0, 8 * hd + j, k), (0, 8 * hd + j, k + 1),
                               (0, 8 * hd + j + 1, k + 1), (0, 8 * hd + j + 1, k)),
                    px[16 * hd + k, 20 * hd + j]))
                poly["torso"]["left"].append(_Poly(
                    self._quad(v, (8 * hd, 8 * hd + j, k), (8 * hd, 8 * hd + j, k + 1),
                               (8 * hd, 8 * hd + j + 1, k + 1), (8 * hd, 8 * hd + j + 1, k)),
                    px[(32 * hd - 1) - k, 20 * hd + j]))
        for i in range(8 * hd):
            for k in range(4 * hd):
                poly["torso"]["top"].append(_Poly(
                    self._quad(v, (i, 8 * hd, k), (i + 1, 8 * hd, k),
                               (i + 1, 8 * hd, k + 1), (i, 8 * hd, k + 1)),
                    px[20 * hd + i, 16 * hd + k]))
                poly["torso"]["bottom"].append(_Poly(
                    self._quad(v, (i, 20 * hd, k), (i + 1, 20 * hd, k),
                               (i + 1, 20 * hd, k + 1), (i, 20 * hd, k + 1)),
                    px[28 * hd + i, (20 * hd - 1) - k]))

    def _gen_arm(self, poly, px, hd, is_alex, is_new, right: bool):
        aw = (3 if is_alex else 4) * hd
        ox = -4 * hd if right else 8 * hd  # right: x-4..0；left: x8..12
        piece = "rightArm" if right else "leftArm"
        v = self._vox(ox, ox + 4 * hd, 8 * hd, 20 * hd, 0, 4 * hd)
        # front/back（旧皮肤左臂从右臂镜像取色，坐标为 PHP 原式）
        for i in range(aw):
            for j in range(12 * hd):
                if right:
                    cb = px[(51 if is_alex else 56) * hd - 1 - i, 20 * hd + j]
                    cf = px[44 * hd + i, 20 * hd + j]
                elif is_alex:
                    cb = px[43 * hd + i, 52 * hd + j]
                    cf = px[36 * hd + i, 52 * hd + j]
                elif is_new:
                    cb = px[47 * hd - i, 52 * hd + j]
                    cf = px[36 * hd + i, 52 * hd + j]
                else:
                    cb = px[52 * hd + i, 20 * hd + j]          # (56hd-1)-((4hd-1)-i)
                    cf = px[(48 * hd - 1) - i, 20 * hd + j]   # 44hd+((4hd-1)-i)
                poly[piece]["back"].append(_Poly(
                    self._quad(v, (ox + i, 8 * hd + j, 0), (ox + i + 1, 8 * hd + j, 0),
                               (ox + i + 1, 8 * hd + j + 1, 0), (ox + i, 8 * hd + j + 1, 0)), cb))
                poly[piece]["front"].append(_Poly(
                    self._quad(v, (ox + i, 8 * hd + j, 4 * hd), (ox + i + 1, 8 * hd + j, 4 * hd),
                               (ox + i + 1, 8 * hd + j + 1, 4 * hd),
                               (ox + i, 8 * hd + j + 1, 4 * hd)), cf))
        # right/left
        for j in range(12 * hd):
            for k in range(4 * hd):
                if right:
                    cr = px[(47 if is_alex else 40) * hd + k, 20 * hd + j]
                    cl = px[((40 if is_alex else 52) * hd - 1) - k, 20 * hd + j]
                elif is_alex:
                    cr = px[32 * hd + k, 52 * hd + j]
                    cl = px[43 * hd - k, 52 * hd + j]
                elif is_new:
                    cr = px[32 * hd + k, 52 * hd + j]
                    cl = px[43 * hd - k, 52 * hd + j]
                else:
                    cr = px[(44 * hd - 1) - k, 20 * hd + j]  # 40hd+((4hd-1)-k)
                    cl = px[48 * hd + k, 20 * hd + j]       # (52hd-1)-((4hd-1)-k)
                poly[piece]["right"].append(_Poly(
                    self._quad(v, (ox, 8 * hd + j, k), (ox, 8 * hd + j, k + 1),
                               (ox, 8 * hd + j + 1, k + 1), (ox, 8 * hd + j + 1, k)), cr))
                poly[piece]["left"].append(_Poly(
                    self._quad(v, (ox + 4 * hd, 8 * hd + j, k), (ox + 4 * hd, 8 * hd + j, k + 1),
                               (ox + 4 * hd, 8 * hd + j + 1, k + 1),
                               (ox + 4 * hd, 8 * hd + j + 1, k)), cl))
        # top/bottom
        for i in range(aw):
            for k in range(4 * hd):
                if right:
                    ct = px[44 * hd + i, 16 * hd + k]
                    cb = px[(47 if is_alex else 48) * hd + i, 16 * hd + k]
                elif is_alex:
                    ct = px[36 * hd + i, 48 * hd + k]
                    cb = px[39 * hd + i, 48 * hd + k]
                elif is_new:
                    ct = px[36 * hd + i, 48 * hd + k]
                    cb = px[40 * hd + i, 48 * hd + k]
                else:
                    ct = px[(48 * hd - 1) - i, 16 * hd + k]        # 44hd+((4hd-1)-i)
                    cb = px[(52 * hd - 1) - i, (20 * hd - 1) - k]  # 48hd+((4hd-1)-i)
                poly[piece]["top"].append(_Poly(
                    self._quad(v, (ox + i, 8 * hd, k), (ox + i + 1, 8 * hd, k),
                               (ox + i + 1, 8 * hd, k + 1), (ox + i, 8 * hd, k + 1)), ct))
                poly[piece]["bottom"].append(_Poly(
                    self._quad(v, (ox + i, 20 * hd, k), (ox + i + 1, 20 * hd, k),
                               (ox + i + 1, 20 * hd, k + 1), (ox + i, 20 * hd, k + 1)), cb))

    def _gen_leg(self, poly, px, hd, is_new, right: bool):
        ox = 0 if right else 4 * hd  # right: x0..4；left: x4..8
        piece = "rightLeg" if right else "leftLeg"
        v = self._vox(ox, ox + 4 * hd, 20 * hd, 32 * hd, 0, 4 * hd)
        for i in range(4 * hd):
            for j in range(12 * hd):
                if right:
                    cb = px[(16 * hd - 1) - i, 20 * hd + j]
                    cf = px[4 * hd + i, 20 * hd + j]
                elif is_new:
                    cb = px[31 * hd - i, 52 * hd + j]
                    cf = px[20 * hd + i, 52 * hd + j]
                else:
                    cb = px[12 * hd + i, 20 * hd + j]            # (16hd-1)-((4hd-1)-i)
                    cf = px[(8 * hd - 1) - i, 20 * hd + j]     # 4hd+((4hd-1)-i)
                poly[piece]["back"].append(_Poly(
                    self._quad(v, (ox + i, 20 * hd + j, 0), (ox + i + 1, 20 * hd + j, 0),
                               (ox + i + 1, 20 * hd + j + 1, 0), (ox + i, 20 * hd + j + 1, 0)), cb))
                poly[piece]["front"].append(_Poly(
                    self._quad(v, (ox + i, 20 * hd + j, 4 * hd), (ox + i + 1, 20 * hd + j, 4 * hd),
                               (ox + i + 1, 20 * hd + j + 1, 4 * hd),
                               (ox + i, 20 * hd + j + 1, 4 * hd)), cf))
        for j in range(12 * hd):
            for k in range(4 * hd):
                if right:
                    cr = px[k, 20 * hd + j]
                    cl = px[(12 * hd - 1) - k, 20 * hd + j]
                elif is_new:
                    cr = px[16 * hd + k, 52 * hd + j]
                    cl = px[27 * hd - k, 52 * hd + j]
                else:
                    cr = px[(4 * hd - 1) - k, 20 * hd + j]  # 0+((4hd-1)-k)
                    cl = px[8 * hd + k, 20 * hd + j]      # (12hd-1)-((4hd-1)-k)
                poly[piece]["right"].append(_Poly(
                    self._quad(v, (ox, 20 * hd + j, k), (ox, 20 * hd + j, k + 1),
                               (ox, 20 * hd + j + 1, k + 1), (ox, 20 * hd + j + 1, k)), cr))
                poly[piece]["left"].append(_Poly(
                    self._quad(v, (ox + 4 * hd, 20 * hd + j, k), (ox + 4 * hd, 20 * hd + j, k + 1),
                               (ox + 4 * hd, 20 * hd + j + 1, k + 1),
                               (ox + 4 * hd, 20 * hd + j + 1, k)), cl))
        for i in range(4 * hd):
            for k in range(4 * hd):
                if right:
                    ct = px[4 * hd + i, 16 * hd + k]
                    cb = px[8 * hd + i, 16 * hd + k]
                elif is_new:
                    ct = px[20 * hd + i, 48 * hd + k]
                    cb = px[24 * hd + i, 48 * hd + k]
                else:
                    ct = px[(8 * hd - 1) - i, 16 * hd + k]        # 4hd+((4hd-1)-i)
                    cb = px[(12 * hd - 1) - i, (20 * hd - 1) - k]  # 8hd+((4hd-1)-i)
                poly[piece]["top"].append(_Poly(
                    self._quad(v, (ox + i, 20 * hd, k), (ox + i + 1, 20 * hd, k),
                               (ox + i + 1, 20 * hd, k + 1), (ox + i, 20 * hd, k + 1)), ct))
                poly[piece]["bottom"].append(_Poly(
                    self._quad(v, (ox + i, 32 * hd, k), (ox + i + 1, 32 * hd, k),
                               (ox + i + 1, 32 * hd, k + 1), (ox + i, 32 * hd, k + 1)), cb))

    # ---- 输出 ----
    def _display(self, poly, bbox):
        min_x, max_x, min_y, max_y = bbox
        ratio = self.ratio * 2  # 2 倍超采样抗锯齿
        src = Image.new("RGBA", (int(ratio * (max_x - min_x)) + 1,
                                 int(ratio * (max_y - min_y)) + 1), (0, 0, 0, 0))
        back_faces, front_faces = self._faces_order()
        for piece, faces in self._display_order(back_faces, front_faces):
            for f in faces:
                for p in poly[piece][f]:
                    p.draw(src, min_x, min_y, ratio)
        return src.resize((src.width // 2, src.height // 2), Image.LANCZOS)

    def _faces_order(self):
        """按视角判断背面朝向（front_faces = 全部 - back）。"""
        cos_a, sin_a = math.cos(math.radians(self.vR)), math.sin(math.radians(self.vR))
        cos_o, sin_o = math.cos(math.radians(self.hR)), math.sin(math.radians(self.hR))
        # 8 个立方体顶点，找投影深度最大者
        corner_faces = [
            ("back", "right", "top"), ("front", "right", "top"),
            ("back", "right", "bottom"), ("front", "right", "bottom"),
            ("back", "left", "top"), ("front", "left", "top"),
            ("back", "left", "bottom"), ("front", "left", "bottom"),
        ]
        best, best_d = None, None
        for (x, y, z), faces in zip(
            [(0, 0, 0), (0, 0, 1), (0, 1, 0), (0, 1, 1),
             (1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1)], corner_faces,
            strict=True,
        ):
            dz = -x * cos_a * sin_o + y * sin_a + z * cos_a * cos_o
            # PHP 语义：保留 depth 最小的顶点（其三面组合 = 背面组）
            if best_d is None or dz < best_d:
                best_d, best = dz, faces
        back = best
        front = tuple(f for f in _FACES if f not in back)
        return back, front

    def _display_order(self, back_faces, front_faces):
        """绘制顺序：远面→近面（对照 getDisplayOrder）。"""
        B, F = back_faces, front_faces

        def _part(name):
            return [(name, B), (name, F)]

        order = []
        if "top" in front_faces:
            if "right" in front_faces:
                order.extend(_part("leftLeg") + _part("rightLeg"))
                order.extend(_part("leftArm") + _part("torso") + _part("rightArm"))
            else:
                order.extend(_part("rightLeg") + _part("leftLeg"))
                order.extend(_part("rightArm") + _part("torso") + _part("leftArm"))
            order.extend([("helmet", B), ("head", B), ("head", F), ("helmet", F)])
        else:
            order.extend([("helmet", B), ("head", B), ("head", F), ("helmet", F)])
            if "right" in front_faces:
                order.extend(_part("leftArm") + _part("torso") + _part("rightArm"))
                order.extend(_part("leftLeg") + _part("rightLeg"))
            else:
                order.extend(_part("rightArm") + _part("torso") + _part("leftArm"))
                order.extend(_part("rightLeg") + _part("leftLeg"))
        if self.head_only:
            order = [o for o in order if o[0] in ("head", "helmet")]
        return order


def render_skin_portrait(skin_bytes: bytes, ratio: int = 12,
                         is_alex: bool = False) -> bytes:
    """正面+背面双视角立绘（对照 Minecraft::renderSkin）。"""
    vp, hp, ip = 15, 30, 15
    front = SkinRenderer(ratio, False, -45).render(skin_bytes, is_alex)
    back = SkinRenderer(ratio, False, 135).render(skin_bytes, is_alex)
    w, h = front.size
    canvas = Image.new("RGBA", ((hp + w + ip) * 2, vp * 2 + h), (0, 0, 0, 0))
    canvas.alpha_composite(back, (hp, vp))
    canvas.alpha_composite(front, (hp + w + ip * 2, vp))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def render_cape_portrait(cape_bytes: bytes, height: int = 200) -> bytes:
    """披风立绘（对照 CapeRenderer：裁 (1,1) 起 10:16 区域并缩放）。"""
    cape = Image.open(io.BytesIO(cape_bytes)).convert("RGBA")
    hd = cape.width // 64
    out_w = int(height / 16 * 10)
    vp, hp = 20, 40
    canvas = Image.new("RGBA", (hp * 2 + out_w, vp * 2 + height), (0, 0, 0, 0))
    region = cape.crop((1 * hd, 1 * hd, 1 * hd + cape.width * 10 // 64,
                        1 * hd + cape.height * 16 // 32))
    region = region.resize((out_w, height), Image.LANCZOS)
    canvas.alpha_composite(region, (hp, vp))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()
