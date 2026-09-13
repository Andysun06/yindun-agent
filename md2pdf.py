# -*- coding: utf-8 -*-
"""md2pdf.py <in.md> <out.pdf> —— Markdown → 排版 HTML → Edge 无头渲染 PDF"""
import sys, subprocess, markdown, os
md_path, pdf_path = sys.argv[1], sys.argv[2]
body = markdown.markdown(open(md_path, encoding="utf-8").read(),
                         extensions=["tables", "fenced_code", "sane_lists"])
css = """
:root{--ink:#1a2332;--prim:#0e7a4f;--acc:#e8590c;--mut:#6b7a72;--line:#dde4ec;--tint:#f2f7f4;}
*{box-sizing:border-box;}
body{font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:var(--ink);line-height:1.85;font-size:11.5pt;margin:0;}
.page{max-width:820px;margin:0 auto;padding:0 8mm;}
h1{font-size:24pt;color:var(--prim);border-bottom:3px solid var(--prim);padding-bottom:8px;margin:0 0 4px;}
h1+p{color:var(--mut);font-size:10.5pt;margin:0 0 6px;}
blockquote{margin:14px 0;padding:10px 18px;background:var(--tint);border-left:4px solid var(--prim);color:#2a3444;border-radius:0 6px 6px 0;}
blockquote p{margin:2px 0;}
h2{font-size:16pt;color:var(--prim);margin:26px 0 10px;padding-left:10px;border-left:5px solid var(--prim);}
h3{font-size:13pt;color:#124a30;margin:16px 0 6px;}
p{margin:8px 0;} a{color:var(--acc);text-decoration:none;word-break:break-all;}
ul,ol{margin:6px 0;padding-left:22px;} li{margin:4px 0;}
code{background:#eef2f0;color:#0a5c3b;padding:1px 6px;border-radius:4px;font-family:Consolas,monospace;font-size:10pt;}
pre{background:#0f172a;color:#d7e3ee;padding:12px 16px;border-radius:8px;overflow-x:auto;line-height:1.6;}
pre code{background:none;color:inherit;padding:0;}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:10.5pt;}
th{background:var(--prim);color:#fff;text-align:left;padding:8px 12px;}
td{border-bottom:1px solid var(--line);padding:7px 12px;vertical-align:top;}
tr:nth-child(even) td{background:#fafcfa;} hr{border:none;border-top:1px solid var(--line);margin:20px 0;}
strong{color:#0a5c3b;} @page{size:A4;margin:16mm 12mm;} h2{page-break-after:avoid;} table,pre,blockquote{page-break-inside:avoid;}
"""
html = f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>doc</title><style>{css}</style></head><body><div class="page">{body}</div></body></html>'
html_path = os.path.splitext(md_path)[0] + ".html"
open(html_path, "w", encoding="utf-8").write(html)
edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
subprocess.run([edge, "--headless", "--disable-gpu", f"--print-to-pdf={pdf_path}",
                "--no-pdf-header-footer", "file:///" + os.path.abspath(html_path).replace("\\", "/")])
print("PDF:", pdf_path)
