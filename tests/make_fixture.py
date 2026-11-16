#!/usr/bin/env python3
"""make_fixture.py —— 构造测试样本（不依赖 python-docx）

产出的样本刻意覆盖这些情形：
- 标题 / 摘要 / 正文 / 表格 / 参考文献（分别对应 heading、table、reference 锁定）
- 纯单 run 段落（最简单情形）
- 多 run 但格式一致
- 含斜体《书名》的混合格式段（格式锚点应能保住斜体）
- 含加粗小标题的混合格式段（一旦改写把小标题改掉，必须整段拒绝）
- 数字密集段（信息具体度高，风险应偏低）
- 两段高度重复的正文（触发 13 字指纹）

同时产出 tampered_*.docx，用来验证 verify.py 真的能抓到格式漂移与实体丢失。

用法
----
    python tests/make_fixture.py
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import xml.etree.ElementTree as ET

import inspect_docx
from lib.ooxml import NS, W_RPR, Docx, q

W = NS["w"]
R = NS["r"]
MC = NS["mc"]
W14 = NS["w14"]

OUT = ROOT / "tests" / "fixtures"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run(text: str, bold: bool = False, italic: bool = False, size: int = 24) -> str:
    rpr = []
    if bold:
        rpr.append("<w:b/>")
    if italic:
        rpr.append("<w:i/>")
    rpr.append('<w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体"/>')
    rpr.append('<w:sz w:val="%d"/>' % size)
    return ('<w:r><w:rPr>%s</w:rPr><w:t xml:space="preserve">%s</w:t></w:r>'
            % ("".join(rpr), esc(text)))


def run_mono(text: str, size: int = 18) -> str:
    """等宽字体的 run —— 用来造「代码块/目录树」段落。"""
    return ('<w:r><w:rPr><w:rFonts w:ascii="Consolas" w:eastAsia="Consolas"/>'
            '<w:sz w:val="%d"/></w:rPr><w:t xml:space="preserve">%s</w:t></w:r>'
            % (size, esc(text)))


def para(runs: str, style: str | None = None, jc: str = "both",
         first_line: int | None = 480) -> str:
    ppr = []
    if style:
        ppr.append('<w:pStyle w:val="%s"/>' % style)
    if first_line:
        ppr.append('<w:ind w:firstLine="%d"/>' % first_line)
    ppr.append('<w:spacing w:line="360" w:lineRule="auto"/>')
    if jc:
        ppr.append('<w:jc w:val="%s"/>' % jc)
    return "<w:p><w:pPr>%s</w:pPr>%s</w:p>" % ("".join(ppr), runs)


def heading(text: str, level: int = 1) -> str:
    return ('<w:p><w:pPr><w:pStyle w:val="Heading%d"/></w:pPr>%s</w:p>'
            % (level, run(text, bold=True, size=28 if level == 1 else 26)))


def table(rows: list[list[str]], widths: list[int]) -> str:
    grid = "".join('<w:gridCol w:w="%d"/>' % w for w in widths)
    body = []
    for ri, row in enumerate(rows):
        cells = []
        for ci, cell in enumerate(row):
            bold = ri == 0
            cells.append(
                '<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/></w:tcPr>%s</w:tc>'
                % (widths[ci], para(run(cell, bold=bold), jc="center", first_line=None))
            )
        body.append("<w:tr>%s</w:tr>" % "".join(cells))
    borders = "".join(
        '<w:%s w:val="single" w:sz="4" w:space="0" w:color="000000"/>' % side
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    return ('<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>'
            '<w:tblW w:w="0" w:type="auto"/><w:tblBorders>%s</w:tblBorders></w:tblPr>'
            '<w:tblGrid>%s</w:tblGrid>%s</w:tbl>' % (borders, grid, "".join(body)))


# --------------------------------------------------------------------------
# 正文内容
# --------------------------------------------------------------------------

TITLE = "生成式人工智能对高校写作教学的影响研究"
AUTHORS = "张三　李四　（某某大学 教育科学学院，北京 100875）"

ABSTRACT = (
    "随着生成式人工智能技术的快速发展，高校写作教学正在经历深刻变革。"
    "首先，智能写作工具能够为学生提供实时的语言支持，有效提升表达效率。"
    "其次，这类工具的普及也带来了学术诚信方面的新问题，值得教育研究者深入思考。"
    "此外，如何在教学中合理引导技术使用，成为当前亟待解决的重要问题。"
    "因此，本研究试图从教师视角出发，探讨人工智能工具在写作教学中的合理定位。"
)

INTRO = (
    "随着生成式人工智能技术的快速普及，高校写作教学面临着前所未有的机遇与挑战。"
    "首先，人工智能工具能够为学生提供即时的语言支持，有效提升写作效率。"
    "其次，技术的普及也带来了学术诚信方面的新问题，值得教育研究者深入思考。"
    "此外，已有研究（如《教育研究》2023 年的专题讨论）表明，学生对这类工具的依赖程度正在加深[1]。"
    "因此，如何在教学中合理引导技术使用，成为当前亟待解决的重要问题。"
)

ITALIC_A = "近年来，围绕学术写作规范的研究不断积累，"
ITALIC_B = "《教育研究》"
ITALIC_C = (
    "等期刊发表的成果显示，写作者的语言习惯与其学术训练经历密切相关。"
    "这一判断在多项实证研究中得到支持，说明写作能力的形成具有明显的阶段性特征。"
)

BOLD_LEAD = "研究背景"
BOLD_BODY = (
    "　自 2022 年以来，国内高校陆续在写作课程中引入人工智能辅助工具。"
    "相关调查显示，超过六成的受访学生曾在课程作业中使用此类工具，其中约三分之一的学生承认未加说明。"
    "这一现象引发了关于学术规范边界的持续讨论。"
)

REVIEW = (
    "已有研究主要沿着两条路径展开。一条路径关注技术接受度，试图解释学生为何愿意使用智能写作工具。"
    "另一条路径关注写作质量，比较人机协作与纯人工写作的文本差异。"
    "两条路径各有侧重，但都存在共同的局限，即较少讨论教师在教学过程中的能动作用。"
    "这一局限使得现有研究难以回答一个更根本的问题，即技术介入之后写作教学的目标应当如何重新界定。"
)

METHOD_DUP = (
    "在研究方法层面，已有成果大多采用问卷调查与访谈相结合的方式，样本规模从数十人到数百人不等。"
    "这种设计虽然能够覆盖较多的研究对象，但在因果推断方面存在明显局限。"
)

DESIGN = (
    "本研究采用混合研究方法，以某高校 2023 级本科生为研究对象，共发放问卷 480 份，"
    "回收有效问卷 412 份，有效回收率为 85.8%。"
    "同时对 12 名任课教师进行半结构化访谈，以补充量化数据的解释力度。"
)

REF1 = "[1] 李明. 生成式人工智能与学术写作规范[J]. 教育研究, 2023, 45(6): 12-20."
REF2 = "[2] 王芳. 高校写作教学的困境与出路[J]. 高等教育研究, 2022, 43(3): 55-62."

TABLE_ROWS = [
    ["维度", "传统写作教学", "人机协作写作"],
    ["教学目标", "语言规范", "语言规范与思维训练"],
    ["评价方式", "结果性评价", "过程性评价与结果性评价"],
]

#: 代码块行：论文里的目录树/代码清单。样式为空、字体全等宽，长度也过得了 min_chars，
#: 因此只有「等宽字体」这条规则能把它拦下来——这正是要验的那条。
CODE_LINE = "│   ├── inspect_docx.py      # 解析 → doc.json（段落、锚点、保护区、锁定标记）"


def build_document_xml() -> str:
    body = [
        para(run(TITLE, bold=True, size=36), style="Title", jc="center", first_line=None),
        para(run(AUTHORS, size=21), style="Normal", jc="center", first_line=None),
        heading("摘要"),
        para(run(ABSTRACT)),
        para(run("关键词：生成式人工智能；写作教学；学术规范；高校", size=21), first_line=None),
        heading("一、引言"),
        para(run(INTRO)),
        para(run(ITALIC_A) + run(ITALIC_B, italic=True) + run(ITALIC_C)),
        para(run(BOLD_LEAD, bold=True) + run(BOLD_BODY)),
        heading("二、文献综述"),
        para(run(REVIEW)),
        para(run(METHOD_DUP)),
        para(run("此外，" + METHOD_DUP)),
        heading("三、研究设计"),
        para(run(DESIGN)),
        table(TABLE_ROWS, [1800, 2200, 2400]),
        para(run_mono(CODE_LINE), first_line=None),
        heading("参考文献"),
        para(run(REF1, size=21), first_line=None),
        para(run(REF2, size=21), first_line=None),
        ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
         '<w:pgMar w:top="1440" w:right="1418" w:bottom="1440" w:left="1418" '
         'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>'),
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="%s" xmlns:r="%s" xmlns:mc="%s" xmlns:w14="%s" mc:Ignorable="w14">'
        "<w:body>%s</w:body></w:document>" % (W, R, MC, W14, "".join(body))
    )


CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="%s">
<w:docDefaults>
<w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体"/><w:sz w:val="24"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:line="360" w:lineRule="auto"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>
<w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>
<w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>
<w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/>
<w:tblPr><w:tblBorders>
<w:top w:val="single" w:sz="4" w:space="0" w:color="000000"/>
<w:left w:val="single" w:sz="4" w:space="0" w:color="000000"/>
<w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/>
<w:right w:val="single" w:sz="4" w:space="0" w:color="000000"/>
<w:insideH w:val="single" w:sz="4" w:space="0" w:color="000000"/>
<w:insideV w:val="single" w:sz="4" w:space="0" w:color="000000"/>
</w:tblBorders></w:tblPr></w:style>
</w:styles>""" % W


def write_docx(path: Path, document_xml: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("word/document.xml", document_xml)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)
        z.writestr("word/styles.xml", STYLES)


# --------------------------------------------------------------------------
# 改写清单：2 条应当通过，5 条应当被各自的闸门拒绝
# --------------------------------------------------------------------------

EDITS_SPEC = [
    # (定位用的文本片段, 新文本, 期望, 覆盖 sha1 与否)
    (
        "随着生成式人工智能技术的快速发展，高校写作教学正在经历深刻变革",
        "智能写作工具进入高校课堂后，写作教学的目标与手段都在被重新定义。它带来效率，也带来争议。"
        "学生在几秒内就能得到一段通顺文字，教师却难以判断这段文字出自谁手。学界对此已有不少讨论，"
        "但多集中在技术层面；教师如何在课堂中安置这类工具，讨论仍显不足。本研究因此把视角转向教师。",
        "applied", True,
    ),
    (
        "围绕学术写作规范的研究不断积累",
        "学术写作规范的研究已积累多年。《教育研究》等期刊的成果反复说明一件事："
        "一个人的语言习惯，与其接受学术训练的经历高度相关。多项实证研究支持这一判断。"
        "写作能力的形成因此呈现出明显的阶段性。",
        "applied", True,
    ),
    (
        "自 2022 年以来，国内高校陆续在写作课程中引入人工智能辅助工具",
        "研究缘起　自 2022 年以来，国内高校陆续在写作课程中引入人工智能辅助工具。"
        "调查显示，超过六成的受访学生曾在作业中使用此类工具，约三分之一承认未加说明。"
        "学术规范的边界，因此被反复讨论。",
        "format_anchor_lost", True,
    ),
    (
        "随着生成式人工智能技术的快速普及",
        "我认为，" + INTRO,
        "subjective_added", False,
    ),
    (
        "在研究方法层面，已有成果大多采用问卷调查与访谈相结合",
        "在研究方法层面，已有成果大多采用问卷调查与访谈相结合的方式，样本规模从数十人到数百人不等，"
        "约 60% 的研究样本量不足 300 人。这种设计覆盖面广，但在因果推断上存在明显局限。",
        "entity_added", True,
    ),
    (
        "本研究采用混合研究方法",
        "本研究以某高校 2023 级本科生为对象，发放问卷 480 份，回收有效问卷 412 份。"
        "为解释量化结果，另对 12 名任课教师做了半结构化访谈。",
        "entity_lost", True,
    ),
    (
        "一、引言",
        "一、问题的提出",
        "locked", False,
    ),
    (
        "已有研究主要沿着两条路径展开",
        "已有研究主要沿着两条路径展开：一条关注技术接受度，一条关注写作质量。",
        "stale_sha1", "bad",
    ),
    (
        "此外，在研究方法层面",
        "研究方法以问卷为主。",
        "length_out_of_range", False,
    ),
]


def make_edits(doc_path: Path, out_path: Path) -> list[dict]:
    doc = inspect_docx.build(str(doc_path))
    rows = doc["paragraphs"]
    lines = []
    for needle, new_text, expect, sha_mode in EDITS_SPEC:
        hit = next((r for r in rows if needle in r["text"]), None)
        if hit is None:
            raise SystemExit("样本中找不到定位文本：%s" % needle)
        item = {"pid": hit["pid"], "text": new_text, "op": "O4", "expect": expect}
        if sha_mode is True:
            item["expected_sha1"] = hit["anchor"]["sha1"]
        elif sha_mode == "bad":
            item["expected_sha1"] = "0" * 40
        lines.append(item)
    lines.append({"pid": "p9999", "text": "不存在的段落。", "op": "O4", "expect": "unknown_pid"})
    out_path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                        encoding="utf-8")
    return lines


# --------------------------------------------------------------------------
# 篡改样本：用于证明 verify.py 抓得住问题
# --------------------------------------------------------------------------

def make_tampered_format(src: Path, dst: Path) -> None:
    """给一个 run 偷偷加粗 —— 文本一字未改，只有格式变了。"""
    docx = Docx(str(src))
    for p in docx.paragraphs():
        if p.text.startswith("随着生成式人工智能技术的快速普及"):
            r = p.runs()[0]
            rpr = r.el.find(W_RPR)
            if rpr is None:
                rpr = ET.Element(q("w:rPr"))
                r.el.insert(0, rpr)
            ET.SubElement(rpr, q("w:b"))
            break
    else:
        raise SystemExit("找不到用于篡改的段落")
    docx.save(str(dst))


def make_tampered_entity(src: Path, dst: Path) -> None:
    """删掉正文里的 [1] —— 引用标记蒸发。"""
    docx = Docx(str(src))
    for p in docx.paragraphs():
        if "[1]" in p.text:
            for r in p.runs():
                if "[1]" in r.text:
                    r.set_text(r.text.replace("[1]", ""))
            break
    else:
        raise SystemExit("找不到含 [1] 的段落")
    docx.save(str(dst))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    sample = OUT / "sample.docx"
    write_docx(sample, build_document_xml())

    edits = make_edits(sample, OUT / "edits.jsonl")
    make_tampered_format(sample, OUT / "tampered_format.docx")
    make_tampered_entity(sample, OUT / "tampered_entity.docx")

    doc = inspect_docx.build(str(sample))
    print("[fixture] %s" % sample)
    print("[fixture] 段落 %d ｜ 锁定 %d ｜ 可改写 %d"
          % (doc["stats"]["paragraphs"], doc["stats"]["locked"], doc["stats"]["rewritable"]))
    print("[fixture] 锁定构成：" + json.dumps(doc["lock_reasons"], ensure_ascii=False))
    print("[fixture] edits %d 条（期望通过 %d 条）"
          % (len(edits), sum(1 for e in edits if e["expect"] == "applied")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
