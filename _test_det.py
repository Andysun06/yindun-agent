import os
import re

def _det(text):
    if "桌面" in text:
        return os.path.join(os.path.expanduser("~"), "Desktop")

    _INDICATORS_MULTI = (
        "这个", "那个", "该个",
        "目录", "文件夹",
        "下的", "里面", "里头",
        "创建", "新建", "保存", "写入", "生成", "删除", "移除", "清理",
        "文件", "项目", "帮我", "分析", "查看", "看看", "写入", "写出",
    )
    _INDICATORS_SINGLE_STRONG = {"该", "此", "下", "个"}
    _INDICATORS_SINGLE_WEAK = {"的", "中", "内", "里"}

    def _is_indicator_segment(seg):
        s = seg.strip()
        if not s:
            return True
        for ind in _INDICATORS_MULTI:
            if s == ind:
                return True
        if s in _INDICATORS_SINGLE_STRONG or s in _INDICATORS_SINGLE_WEAK:
            return True
        return False

    def _safe_segment(seg):
        if not seg:
            return False
        if re.fullmatch(r"[\w.\-]+", seg):
            return True
        if _is_indicator_segment(seg):
            return False
        for ind in _INDICATORS_MULTI:
            if seg.startswith(ind) or seg.endswith(ind):
                return False
        return True

    def _splitext_is_file(name):
        _, ext = os.path.splitext(name)
        return bool(ext) and len(ext) >= 3 and ext[1:].isalnum()

    loose_pattern = re.compile(
        r"([A-Za-z]):[/\\]{1,3}[\w\u4e00-\u9fa5.\-/\\]+",
        re.IGNORECASE,
    )
    m = loose_pattern.search(text)
    if m:
        raw = m.group(0)
        raw_norm = raw.replace("\\", "/").replace("//", "/")
        drive_letter = raw[0].upper()
        body = raw_norm[3:]

        segments = body.split("/")
        accepted = []
        stopped = False
        for seg in segments:
            if stopped:
                break
            if _is_indicator_segment(seg):
                stopped = True
                continue
            cleaned = ""
            hit = False
            i = 0
            while i < len(seg):
                matched_len = 0
                for ind in sorted(_INDICATORS_MULTI, key=len, reverse=True):
                    if seg[i:i + len(ind)] == ind:
                        matched_len = len(ind)
                        break
                if not matched_len and seg[i] in _INDICATORS_SINGLE_STRONG:
                    matched_len = 1
                if matched_len:
                    hit = True
                    break
                cleaned += seg[i]
                i += 1
            if cleaned:
                if _safe_segment(cleaned):
                    accepted.append(cleaned)
                else:
                    stopped = True
            if hit:
                stopped = True

        if not accepted:
            return os.path.abspath(f"{drive_letter}:\\")

        final_path = f"{drive_letter}:/" + "/".join(accepted)
        base = os.path.basename(final_path.replace("/", "\\"))
        if _splitext_is_file(base):
            parent = os.path.dirname(final_path)
            if len(parent) > 2:
                final_path = parent

        return os.path.abspath(os.path.normpath(final_path.replace("/", "\\")))

    drive_match = re.search(r"([A-Za-z])\s*盘", text)
    if drive_match:
        d = drive_match.group(1).upper()
        after_drive = text[drive_match.end():]

        after_drive = re.sub(r"^(?:的|这个|那个|该|此)\s*", "", after_drive)

        cn_suffix = r"(?:文件夹内|文件夹中|文件夹里|文件夹下|文件夹|目录下|目录里|目录中|目录)"
        m_en = re.search(r"([a-zA-Z0-9_\-]{1,30})" + cn_suffix, after_drive)
        if m_en:
            return os.path.abspath(f"{d}:\\{m_en.group(1)}")
        m_cn = re.search(r"([\u4e00-\u9fa5]{1,8})" + cn_suffix, after_drive)
        if m_cn:
            name = m_cn.group(1)
            if not _is_indicator_segment(name):
                return os.path.abspath(f"{d}:\\{name}")
        return os.path.abspath(f"{d}:\\")

    return None


cases = [
    r'帮我分析一下E:\yindun-agent这个目录下的项目',
    r'E:\yindun-agent这个目录下',
    r'在E:/qwen目录下创建一个老罗.txt文件',
    r'E:\yindun-agent\src 目录下',
    r'帮我在D盘的文档文件夹写一个README',
    r'E盘qwen文件夹中新建一个文件',
    r'在桌面创建test.txt',
    r'E:\\yindun-agent\\test\\file.txt 的内容',
    r'E:\projects\我的文档 里的文件',
    r'帮我分析E:\yindun-agent该目录下的项目结构',
    r'在E:\data\work下新建文件夹',
    r'E:\yindun-agent 下的代码',
    r'E:\test_project\新建 文件夹',
    r'E:\yindun-agent这个',
    r'E:\文档',
    r'E:\projects\文档 目录',
    r'E:\yindun-agent此目录下',
]
for t in cases:
    print(f'输入: {t!r}')
    print(f'解析: {_det(t)!r}')
    print()
