#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
印象笔记「原生 Markdown 笔记」构造工具（跨平台，无外部服务依赖）

背景 / 为什么要这个模块：
  1) 普通 EDAM createNote 塞 ENML 只能得到「富文本笔记」，没有源码概念；
  2) 官方 RESTful createNoteFromMCP 会渲染成「超级笔记」(contentClass=yinxiang.peso.superNote)，
     同样没有 Markdown 源码视图；
  3) 只有 attributes.contentClass == 'yinxiang.markdown' + 正文里带隐藏 <center> 的
     URL 编码源码，客户端才会识别为「可切换 编辑/预览、查看原始 Markdown 源码」的原生 Markdown 笔记。

原生 Markdown 笔记正文格式（实测反解自客户端 yinxiang.win32 创建的真·Markdown 笔记）：
  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">
  <en-note>
    <div style="font-size: 14px; margin: 0; padding: 0; width: 100%;"> {markdown 渲染后的 HTML} </div>
    <center style='display:none !important;visibility:collapse !important;height:0 !important;
                   white-space:nowrap;width:100%;overflow:hidden'> {URL 编码的 markdown 源码} </center>
  </en-note>
"""
import urllib.parse
from xml.sax.saxutils import escape as xescape
from html.parser import HTMLParser

from zibuyu_evernote.edam.type.ttypes import Note, NoteAttributes
from zibuyu_evernote.edam.error.ttypes import EDAMSystemException, EDAMErrorCode

try:
    import markdown as _markdown
except ImportError:  # 兜底：无 markdown 库时退回最简渲染
    _markdown = None

ENML_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">')
DIV_STYLE = "font-size: 14px; margin: 0; padding: 0; width: 100%;"
CENTER_STYLE = ("display:none !important;visibility:collapse !important;height:0 !important;"
                "white-space:nowrap;width:100%;overflow:hidden")

CONTENT_CLASS_MD = "yinxiang.markdown"                 # 原生 Markdown 笔记（可切源码/预览）
CONTENT_CLASS_SUPERNOTE = "yinxiang.peso.superNote"    # 「超级笔记」（块状富文本）
CONTENT_CLASS = CONTENT_CLASS_MD                       # 兼容别名
SOURCE_APP = "yinxiang.mcp"                            # 标识由本连接器写入

VOID_TAGS = {"br", "hr", "img", "meta", "link", "input", "col", "source",
             "area", "base", "embed", "param", "track", "wbr"}

# ENML 的 DTD 严格校验属性：class / id 等不被允许（如 <code class="language-python">
# 或 <a id="..."> 会被服务器以 errorCode=11 拒收），这里只保留 ENML 合法属性。
ENML_ALLOWED_ATTRS = {"href", "src", "alt", "title", "colspan", "rowspan",
                      "start", "type", "target", "rel", "value"}


class ENMLNormalizer(HTMLParser):
    """把任意 HTML 归一化为合法 XML（ENML）：空元素自闭合、属性与文本转义、剔除非法属性。"""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []

    @staticmethod
    def _attrs(attrs):
        parts = []
        for k, v in attrs:
            if k.lower() not in ENML_ALLOWED_ATTRS:
                continue  # 丢弃 ENML DTD 不支持的属性（class/id/style 等）
            if v is None:
                parts.append(k)
            else:
                parts.append('%s="%s"' % (k, xescape(v, {'"': "&quot;"})))
        return (" " + " ".join(parts)) if parts else ""

    def handle_starttag(self, tag, attrs):
        if tag in VOID_TAGS:
            self.out.append("<%s%s/>" % (tag, self._attrs(attrs)))
        else:
            self.out.append("<%s%s>" % (tag, self._attrs(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.out.append("<%s%s/>" % (tag, self._attrs(attrs)))

    def handle_endtag(self, tag):
        if tag not in VOID_TAGS:
            self.out.append("</%s>" % tag)

    def handle_data(self, data):
        self.out.append(xescape(data))

    def handle_entityref(self, name):
        self.out.append("&%s;" % name)

    def handle_charref(self, name):
        self.out.append("&#%s;" % name)

    def result(self):
        return "".join(self.out)


def render_html_xml(md_text):
    """markdown -> 合法 ENML 片段。"""
    if _markdown is not None:
        html = _markdown.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists", "nl2br"])
    else:
        html = "<pre>%s</pre>" % xescape(md_text)
    p = ENMLNormalizer()
    p.feed(html)
    p.close()
    return p.result()


def md_to_native_markdown_content(md_text):
    """构造原生 Markdown 笔记正文（可见 HTML + 隐藏的 URL 编码源码）。"""
    body = render_html_xml(md_text)
    encoded = urllib.parse.quote(md_text, safe="")
    return (ENML_HEAD
            + '<en-note><div style="%s">' % DIV_STYLE
            + body
            + "</div>"
            + "<center style='%s'>" % CENTER_STYLE
            + encoded
            + "</center></en-note>")


def build_markdown_note(title, md_text, notebook_guid=""):
    """构造一个「原生 Markdown 笔记」的 Note 对象（contentClass='yinxiang.markdown'）。"""
    note = Note()
    note.title = (title or "")[:240]
    note.content = md_to_native_markdown_content(md_text)
    if notebook_guid:
        note.notebookGuid = notebook_guid
    note.attributes = NoteAttributes()
    note.attributes.contentClass = CONTENT_CLASS_MD
    note.attributes.sourceApplication = SOURCE_APP
    return note


def wrap_enml(body):
    """把正文片段包成完整 ENML（若已是完整 ENML 则原样返回）。"""
    b = body.lstrip()
    if b.startswith("<?xml") or "<en-note" in b[:200]:
        return body
    return ENML_HEAD + "<en-note>" + body + "</en-note>"


def build_supernote_note(title, enml_body, notebook_guid="", resources=None):
    """构造一个「超级笔记」Note 对象（contentClass='yinxiang.peso.superNote'）。
    用于非 Markdown 内容的笔记（如文件附件、图片汇总）。可携带 resources（附件）。
    """
    note = Note()
    note.title = (title or "")[:240]
    note.content = wrap_enml(enml_body)
    if resources:
        note.resources = resources
    if notebook_guid:
        note.notebookGuid = notebook_guid
    note.attributes = NoteAttributes()
    note.attributes.contentClass = CONTENT_CLASS_SUPERNOTE
    note.attributes.sourceApplication = SOURCE_APP
    return note


def create_markdown_note(client, title, md_text, notebook_guid="", attempts=4, sleep=__import__("time").sleep):
    """通过 EDAM createNote 建「原生 Markdown 笔记」，带限流重试。"""
    note = build_markdown_note(title, md_text, notebook_guid)
    last = None
    for i in range(attempts):
        try:
            return client.note_store.createNote(note)
        except EDAMSystemException as e:
            last = e
            code = getattr(e, "errorCode", None)
            wait = (getattr(e, "rateLimitDuration", 1) or 1) + 1 if code == EDAMErrorCode.RATE_LIMIT_REACHED else 2 * (i + 1)
            sleep(wait)
    raise last
