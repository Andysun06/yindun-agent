# -*- coding: utf-8 -*-
"""
隐盾 V2.0 — 📂 离线机密文档解析舱
专门处理离线 PDF、Word (docx)、Excel (xlsx, xls) 及文本文件的全自动内容读取
"""
import os

def extract_file_text(filepath):
    """
    自适应跨平台离线文件文本提取矩阵
    支持：TXT, MD, CSV, PDF, DOCX, XLSX, XLS
    """
    name = filepath.lower()
    try:
        # 1. 普通文本流格式 (.txt / .md / .csv) — 支持多重字符编码自适应探测
        if name.endswith((".txt", ".md", ".csv")):
            for enc in ["utf-8", "gbk", "gb2312", "latin-1"]:
                try:
                    with open(filepath, "r", encoding=enc) as f: 
                        return f.read()
                except: 
                    continue
            # 若全部失败，启动柔性兜底读取，强行替换非法字符
            with open(filepath, "r", encoding="utf-8", errors="replace") as f: 
                return f.read()
                
        # 2. 离线标准 PDF 格式解析舱
        if name.endswith(".pdf"):
            from PyPDF2 import PdfReader
            return "\n".join(p.extract_text() or "" for p in PdfReader(filepath).pages)
            
        # 3. 离线微软 Word 格式解析舱
        if name.endswith(".docx"):
            from docx import Document
            return "\n".join(p.text for p in Document(filepath).paragraphs)
            
        # 4. 离线微软 Excel 电子表格解析舱 (支持多工作表全透视扫描)
        if name.endswith((".xlsx", ".xls")):
            import openpyxl
            # 以只读且读取公式计算结果的方式安全打开
            wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets:
                lines.append(f"=== 工作表: {ws.title} ===")
                for row in ws.iter_rows(values_only=True): 
                    # 用管道符 | 拼装每一行单元格的数据，保持数据表格结构
                    lines.append(" | ".join(str(c) if c else "" for c in row))
            wb.close()
            return "\n".join(lines)
            
        # 5. 无法识别的后缀，默认按普通文本流进行强行脱敏兜底读取
        with open(filepath, "r", encoding="utf-8", errors="replace") as f: 
            return f.read()
            
    except Exception as e: 
        return f"[离线文档网关读取失败: {type(e).__name__} -> {e}]"