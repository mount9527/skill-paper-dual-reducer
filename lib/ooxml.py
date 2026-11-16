"""OOXML 读写层：只用标准库（zipfile + ElementTree）直接操作 WordprocessingML。

为什么不用 python-docx
----------------------
本项目第一验收项是「格式零漂移」——改写前后文档的格式属性 diff 必须为空。
任何高层封装在写回时都会重建 <w:r>/<w:rPr>，反而扩大漂移面；
这里直接深拷贝原始节点、只替换 <w:t> 的文本，是保真度最高的做法。
零依赖还带来一个副产品：这个 skill 在没装 pip 包的机器上也能跑。

边界
----
- 只改 word/document.xml；其余部件（样式、编号、页眉页脚、关系表）逐字节原样回写。
- 不解析图片与公式的语义，只把它们识别为「锁定区」，永不参与改写。
- 不支持默认命名空间（xmlns=）写法的 document.xml，遇到会明确报错而不是静默出错。
"""

from __future__ import annotations

import re
import zipfile
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Iterator

import xml.etree.ElementTree as ET

# --------------------------------------------------------------------------
# 命名空间
# --------------------------------------------------------------------------

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
}

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def q(name: str) -> str:
    """'w:p' -> '{ns}p'"""
    prefix, local = name.split(":", 1)
    return "{%s}%s" % (NS[prefix], local)


W_P = q("w:p")
W_R = q("w:r")
W_T = q("w:t")
W_PPR = q("w:pPr")
W_RPR = q("w:rPr")
W_BODY = q("w:body")
W_TBL = q("w:tbl")
W_TR = q("w:tr")
W_TC = q("w:tc")
W_SDT = q("w:sdt")
W_SDTCONTENT = q("w:sdtContent")
W_TXBXCONTENT = q("w:txbxContent")
W_CUSTOMXML = q("w:customXml")
W_SMARTTAG = q("w:smartTag")
W_HYPERLINK = q("w:hyperlink")
W_INS = q("w:ins")
W_DEL = q("w:del")
W_SECTPR = q("w:sectPr")
W_STYLE = q("w:style")
W_STYLES = q("w:styles")
W_NAME = q("w:name")
W_LSDEXCEPTION = q("w:lsdException")
W_COMMENT = q("w:comment")
W_FOOTNOTE = q("w:footnote")
W_ENDNOTE = q("w:endnote")
MC_ALTERNATE = q("mc:AlternateContent")
MC_FALLBACK = q("mc:Fallback")

#: 段落内出现这些标签即视为「不可改写」，值为锁定原因
LOCK_TAGS: dict[str, str] = {
    q("m:oMath"): "formula",
    q("m:oMathPara"): "formula",
    q("w:drawing"): "media",
    q("w:pict"): "media",
    q("w:object"): "media",
    q("w:footnoteReference"): "note",
    q("w:endnoteReference"): "note",
    q("w:fldChar"): "field",
    q("w:instrText"): "field",
    q("w:fldSimple"): "field",
    q("w:txbxContent"): "textbox",
    q("w:hyperlink"): "hyperlink",
    q("w:ins"): "revision",
    q("w:del"): "revision",
    q("w:br"): "inline_break",
    q("w:tab"): "inline_break",
    q("w:sym"): "symbol",
    q("w:commentReference"): "comment",
}

#: 可能包含子段落的块级容器（用于按文档顺序遍历）
BLOCK_CONTAINERS = (
    W_BODY,
    W_TBL,
    W_TR,
    W_TC,
    W_SDT,
    W_SDTCONTENT,
    W_CUSTOMXML,
    W_SMARTTAG,
    W_FOOTNOTE,
    W_ENDNOTE,
    W_COMMENT,
    MC_ALTERNATE,
    MC_FALLBACK,
)

#: run 可能被包在这些标签里（w:hyperlink / w:ins 等会被锁定，但仍需能取到文本）
RUN_CONTAINERS = (W_HYPERLINK, W_INS, W_SDT, W_SDTCONTENT, W_SMARTTAG, W_CUSTOMXML)


class DocxError(RuntimeError):
    """输入文档无法按预期解析时抛出，附带可操作的提示。"""


# --------------------------------------------------------------------------
# 命名空间探测与保留
# --------------------------------------------------------------------------

_NS_DECL = re.compile(r'xmlns:([A-Za-z_][\w.\-]*)\s*=\s*"([^"]+)"')


def _scan_ns(raw: bytes) -> tuple[dict[str, str], bool]:
    head = raw[:8192].decode("utf-8", "ignore")
    mapping = {m.group(1): m.group(2) for m in _NS_DECL.finditer(head)}
    has_default = bool(re.search(r'xmlns\s*=\s*"', head))
    return mapping, has_default


def _reinject_ns(xml: bytes, nsmap: dict[str, str]) -> bytes:
    """把原始根标签上的 xmlns 声明补回去。

    ElementTree 只输出「实际用到」的命名空间，而 mc:Ignorable="w14 wp14" 这类
    属性是通过前缀名引用命名空间的——前缀一旦丢失，Word 会判定文件损坏。
    """
    start = xml.find(b"<", xml.find(b"?>") + 2 if b"?>" in xml else 0)
    end = xml.find(b">", start)
    if start < 0 or end < 0:
        return xml
    tag = xml[start:end]
    missing = []
    for prefix, uri in nsmap.items():
        marker = ('xmlns:%s=' % prefix).encode()
        if marker not in tag:
            missing.append((' xmlns:%s="%s"' % (prefix, uri)).encode("utf-8"))
    if not missing:
        return xml
    return xml[:end] + b"".join(missing) + xml[end:]


# --------------------------------------------------------------------------
# run / 段落格式签名
# --------------------------------------------------------------------------

def _val(el: ET.Element, attr: str = "w:val") -> str | None:
    return el.get(q(attr))


def _toggle(parent: ET.Element, name: str) -> bool:
    el = parent.find(q(name))
    if el is None:
        return False
    v = _val(el)
    return v is None or v not in ("0", "false", "off")


def _twips_to_pt(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v) / 20.0, 2)
    except ValueError:
        return None


def run_format(r: ET.Element) -> dict:
    """读取 run 上**显式声明**的字符格式。未声明即记为 None（表示继承样式）。"""
    rpr = r.find(W_RPR)
    fmt = {
        "bold": None,
        "italic": None,
        "strike": None,
        "underline": None,
        "size_pt": None,
        "font_ascii": None,
        "font_eastasia": None,
        "color": None,
        "vert_align": None,
    }
    if rpr is None:
        return fmt
    if rpr.find(q("w:b")) is not None:
        fmt["bold"] = _toggle(rpr, "w:b")
    if rpr.find(q("w:i")) is not None:
        fmt["italic"] = _toggle(rpr, "w:i")
    if rpr.find(q("w:strike")) is not None:
        fmt["strike"] = _toggle(rpr, "w:strike")
    u = rpr.find(q("w:u"))
    if u is not None:
        uv = _val(u)
        fmt["underline"] = None if uv in (None, "none") else uv
    sz = rpr.find(q("w:sz"))
    if sz is not None:
        v = _val(sz)
        if v and v.isdigit():
            fmt["size_pt"] = round(int(v) / 2.0, 2)
    fonts = rpr.find(q("w:rFonts"))
    if fonts is not None:
        fmt["font_ascii"] = _val(fonts, "w:ascii")
        fmt["font_eastasia"] = _val(fonts, "w:eastAsia")
    color = rpr.find(q("w:color"))
    if color is not None:
        cv = _val(color)
        if cv and cv.lower() != "auto":
            fmt["color"] = cv.upper()
    va = rpr.find(q("w:vertAlign"))
    if va is not None:
        fmt["vert_align"] = _val(va)
    return fmt


def _fmt_code(fmt: dict) -> str:
    parts = []
    for k in ("bold", "italic", "strike", "underline", "size_pt", "font_ascii",
              "font_eastasia", "color", "vert_align"):
        v = fmt.get(k)
        if v is not None:
            parts.append("%s=%s" % (k, v))
    return "|".join(parts) if parts else "inherit"


def run_signature(r: ET.Element) -> str:
    return _fmt_code(run_format(r))


def paragraph_format(p: ET.Element) -> dict:
    ppr = p.find(W_PPR)
    out = {
        "style_id": None,
        "align": None,
        "first_line_indent_pt": None,
        "left_indent_pt": None,
        "right_indent_pt": None,
        "space_before_pt": None,
        "space_after_pt": None,
        "line_spacing": None,
        "line_rule": None,
        "outline_level": None,
        "num_id": None,
        "ilvl": None,
    }
    if ppr is None:
        return out
    st = ppr.find(q("w:pStyle"))
    if st is not None:
        out["style_id"] = _val(st)
    jc = ppr.find(q("w:jc"))
    if jc is not None:
        out["align"] = _val(jc)
    ind = ppr.find(q("w:ind"))
    if ind is not None:
        out["first_line_indent_pt"] = _twips_to_pt(_val(ind, "w:firstLine"))
        out["left_indent_pt"] = _twips_to_pt(_val(ind, "w:left"))
        out["right_indent_pt"] = _twips_to_pt(_val(ind, "w:right"))
    sp = ppr.find(q("w:spacing"))
    if sp is not None:
        out["space_before_pt"] = _twips_to_pt(_val(sp, "w:before"))
        out["space_after_pt"] = _twips_to_pt(_val(sp, "w:after"))
        line = _val(sp, "w:line")
        out["line_rule"] = _val(sp, "w:lineRule")
        if line and line.isdigit():
            lv = int(line)
            out["line_spacing"] = round(lv / 240.0, 3) if out["line_rule"] in (None, "auto") else round(lv / 20.0, 2)
    ol = ppr.find(q("w:outlineLvl"))
    if ol is not None:
        v = _val(ol)
        out["outline_level"] = int(v) if v and v.isdigit() else None
    num = ppr.find(q("w:numPr"))
    if num is not None:
        nid = num.find(q("w:numId"))
        ilvl = num.find(q("w:ilvl"))
        if nid is not None:
            out["num_id"] = _val(nid)
        if ilvl is not None:
            out["ilvl"] = _val(ilvl)
    return out


def paragraph_signature(p: ET.Element) -> str:
    f = paragraph_format(p)
    return "|".join("%s=%s" % (k, f[k]) for k in sorted(f) if f[k] is not None) or "inherit"


# --------------------------------------------------------------------------
# Run / Paragraph 包装
# --------------------------------------------------------------------------

def _iter_runs(el: ET.Element) -> Iterator[ET.Element]:
    for child in el:
        if child.tag == W_R:
            yield child
        elif child.tag in RUN_CONTAINERS:
            yield from _iter_runs(child)


@dataclass
class Run:
    el: ET.Element
    rid: str = ""

    @property
    def text(self) -> str:
        return "".join(t.text or "" for t in self.el.findall(W_T))

    def set_text(self, text: str) -> None:
        """清空本 run 的所有 <w:t>，写入单一文本节点；rPr 原样保留。"""
        for t in self.el.findall(W_T):
            self.el.remove(t)
        t = ET.SubElement(self.el, W_T)
        t.text = text
        if text != text.strip() or "  " in text:
            t.set(XML_SPACE, "preserve")

    @property
    def fmt(self) -> dict:
        return run_format(self.el)

    @property
    def signature(self) -> str:
        return _fmt_code(self.fmt)

    def json(self) -> dict:
        f = self.fmt
        return {"rid": self.rid, "text": self.text, "sig": _fmt_code(f), "fmt": f}


@dataclass
class Paragraph:
    el: ET.Element
    index: int = 0
    in_table: bool = False
    in_textbox: bool = False
    pid: str = ""
    parent_el: ET.Element | None = None

    # ---- 文本与 run ----
    def runs(self) -> list[Run]:
        return [Run(el=r) for r in _iter_runs(self.el)]

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs())

    @property
    def chars(self) -> int:
        return len(self.text.strip())

    # ---- 格式 ----
    @property
    def fmt(self) -> dict:
        return paragraph_format(self.el)

    @property
    def signature(self) -> str:
        return paragraph_signature(self.el)

    @property
    def style_id(self) -> str | None:
        return self.fmt["style_id"]

    # ---- 锁定判定 ----
    def lock_reason(self) -> str | None:
        if self.in_table:
            return "table"
        if self.in_textbox:
            return "textbox"
        stack = [c for c in self.el if c.tag != W_PPR]
        while stack:
            el = stack.pop()
            hit = LOCK_TAGS.get(el.tag)
            if hit:
                return hit
            if el.tag == W_PPR:
                continue
            stack.extend(list(el))
        return None


# --------------------------------------------------------------------------
# 文档
# --------------------------------------------------------------------------

@dataclass
class Styles:
    """styles.xml 里 styleId -> 名称 的映射，用于识别标题样式。"""

    names: dict[str, str] = field(default_factory=dict)
    outline: dict[str, int] = field(default_factory=dict)

    def name(self, style_id: str | None) -> str | None:
        if not style_id:
            return None
        return self.names.get(style_id) or self.names.get(style_id.lower())

    def is_heading(self, style_id: str | None) -> bool:
        if not style_id:
            return False
        nm = self.name(style_id) or ""
        if re.match(r"^(标题|Heading)\s*[1-9]$", nm.strip(), re.I):
            return True
        if re.match(r"^(Title|题目|标题)$", nm.strip(), re.I):
            return True
        return self.outline.get(style_id, 99) < 9


class Docx:
    """一个 .docx 的只读视图 + document.xml 的可写句柄。"""

    def __init__(self, path: str):
        self.path = str(path)
        try:
            with zipfile.ZipFile(self.path) as z:
                self.infos = z.infolist()
                self.parts = {i.filename: z.read(i.filename) for i in self.infos}
        except FileNotFoundError:
            raise DocxError("找不到文件：%s" % self.path)
        except zipfile.BadZipFile:
            raise DocxError(
                "不是有效的 .docx（可能是 .doc 或已加密）：%s\n"
                "提示：请用 Word/WPS 另存为 .docx 后重试。" % self.path
            )
        if "word/document.xml" not in self.parts:
            raise DocxError("缺少 word/document.xml，不是 Word 文档：%s" % self.path)
        raw = self.parts["word/document.xml"]
        self.nsmap, has_default = _scan_ns(raw)
        if has_default:
            raise DocxError(
                "document.xml 使用了默认命名空间（xmlns=），当前实现不支持。\n"
                "提示：用 Word 另存一次即可转为 w: 前缀形式。"
            )
        for prefix, uri in self.nsmap.items():
            try:
                ET.register_namespace(prefix, uri)
            except ValueError:
                pass
        try:
            self.root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise DocxError("document.xml 解析失败：%s" % exc)
        self.styles = self._load_styles()

    # ---- 样式 ----
    def _load_styles(self) -> Styles:
        st = Styles()
        raw = self.parts.get("word/styles.xml")
        if not raw:
            return st
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return st
        for s in root.findall(W_STYLE):
            sid = s.get(q("w:styleId"))
            if not sid:
                continue
            nm = s.find(W_NAME)
            if nm is not None:
                st.names[sid] = nm.get(q("w:val")) or ""
            ppr = s.find(W_PPR)
            if ppr is not None:
                ol = ppr.find(q("w:outlineLvl"))
                if ol is not None:
                    v = _val(ol)
                    if v and v.isdigit():
                        st.outline[sid] = int(v)
        return st

    # ---- 段落遍历 ----
    def paragraphs(self) -> list[Paragraph]:
        out: list[Paragraph] = []
        body = self.root.find(W_BODY)
        if body is None:
            raise DocxError("document.xml 缺少 w:body")

        def walk(el: ET.Element, in_table: bool, in_textbox: bool) -> None:
            for child in el:
                if child.tag == W_P:
                    out.append(Paragraph(el=child, in_table=in_table, in_textbox=in_textbox))
                elif child.tag == W_TXBXCONTENT:
                    walk(child, in_table, True)
                elif child.tag in BLOCK_CONTAINERS:
                    walk(child, in_table or child.tag in (W_TBL, W_TR, W_TC), in_textbox)

        walk(body, False, False)
        for i, p in enumerate(out, 1):
            p.index = i
            p.pid = "p%04d" % i
        return out

    def section_count(self) -> int:
        return len(self.root.findall(".//" + W_SECTPR))

    def table_count(self) -> int:
        return len(self.root.findall(".//" + W_TBL))

    def text_all(self, limit: int | None = None) -> str:
        parts = [p.text for p in self.paragraphs()]
        s = "\n".join(parts)
        return s[:limit] if limit else s

    def sha256(self) -> str:
        import hashlib

        return hashlib.sha256(self.parts["word/document.xml"]).hexdigest()

    # ---- 写回 ----
    def document_bytes(self) -> bytes:
        xml = ET.tostring(self.root, encoding="utf-8")
        if xml.startswith(b"<?xml"):
            xml = xml[xml.index(b"?>") + 2:].lstrip()
        xml = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + xml
        return _reinject_ns(xml, self.nsmap)

    def save(self, out_path: str) -> str:
        import os

        out_path = str(out_path)
        if os.path.abspath(out_path) == os.path.abspath(self.path):
            raise DocxError("输出路径不能与输入相同，请另存为新文件。")
        data = self.document_bytes()
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with zipfile.ZipFile(self.path) as zin, zipfile.ZipFile(out_path, "w") as zout:
            for info in zin.infolist():
                payload = data if info.filename == "word/document.xml" else zin.read(info.filename)
                zout.writestr(info, payload)
        return out_path

    # ---- 供 anchor 回滚用 ----
    def snapshot(self, p: Paragraph) -> list[ET.Element]:
        return [deepcopy(c) for c in p.el]

    @staticmethod
    def restore(p: Paragraph, snapshot: list[ET.Element]) -> None:
        for c in list(p.el):
            p.el.remove(c)
        for c in snapshot:
            p.el.append(c)
