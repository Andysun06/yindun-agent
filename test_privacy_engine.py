# -*- coding: utf-8 -*-
"""
PrivacyEngine 强化版测试脚本

测试方式：
1. 同时加载旧版（备份文件）和新版（强化版）
2. 对同一批测试用例做对比，直观展示强化前后的差异
3. 验证接口兼容性：anonymize/deanonymize 行为一致
4. 验证还原准确性：deanonymize(anonymize(text)) 应还原回原文

运行方式：
    python test_privacy_engine.py
"""
import sys
import os
from importlib.machinery import SourceFileLoader

# ──────────────────────────────────────────
# 动态加载旧版（备份）和新版（强化版）两个版本
# ──────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OLD_PATH = os.path.join(BASE_DIR, "yindun", "core", "privacy_engine_old.py.bak")
NEW_PATH = os.path.join(BASE_DIR, "yindun", "core", "privacy_engine.py")


def _load_class(path, module_name):
    """从指定路径动态加载 PrivacyEngine 类（支持 .bak 等非标准扩展名）。"""
    loader = SourceFileLoader(module_name, path)
    mod = loader.load_module()
    return mod.PrivacyEngine


OldEngine = _load_class(OLD_PATH, "old_engine")
NewEngine = _load_class(NEW_PATH, "new_engine")

# ──────────────────────────────────────────
# 测试用例
# ──────────────────────────────────────────
TEST_CASES = [
    {
        "name": "测试1：原有3类实体（回归测试）",
        "desc": "验证强化后不破坏对手机号/邮箱/身份证的识别",
        "text": "员工老王的邮箱是 test@qq.com，报销手机号是13988889999，身份证号110101199001011234。",
    },
    {
        "name": "测试2：新增6类正则实体",
        "desc": "验证银行卡/IP/金额/API密钥/微信号/地址的识别",
        "text": (
            "服务器IP是192.168.1.100，API密钥sk-abcdef1234567890abcdef1234567890，"
            "合同金额85万元，乙方账号6222020200112345678，"
            "对接微信zhangwei_88，地址北京市海淀区中关村大街1号。"
        ),
    },
    {
        "name": "测试3：人名识别（上下文触发）",
        "desc": "验证甲方/乙方等触发词后的人名识别，且不误伤地名",
        "text": "甲方张伟与乙方李明签约，王芳华经理见证，赵强主任审批。",
    },
    {
        "name": "测试4：综合办公文档（最接近真实场景）",
        "desc": "一份合同文本，包含多种敏感信息混合",
        "text": (
            "甲方张伟（手机13812345678，邮箱zhangwei@qq.com），"
            "合同金额85万元，乙方账号6222020200112345678，"
            "地址北京市海淀区中关村大街1号，对接微信zhangwei_88，"
            "身份证110101199001011234。"
        ),
    },
    {
        "name": "测试5：误伤检测（不带触发词不应误伤）",
        "desc": "北京/上海等地名不带触发词时不应被当作人名",
        "text": "北京是首都，上海是魔都，广州是花城，深圳是鹏城。",
    },
    {
        "name": "测试6：空文本与边界情况",
        "desc": "空字符串、无敏感信息文本的处理",
        "text": "今天天气不错，适合出去散步。",
    },
]


# ──────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────
def _count_placeholders(text):
    """统计文本中占位符的数量，如 [PHONE_0] [NAME_1]"""
    import re
    return len(re.findall(r"\[[A-Z]+_\d+\]", text))


def _count_real_sensitive_in_output(text, mapping):
    """检查脱敏后文本中是否还残留真实敏感值。返回残留数量。"""
    leaked = 0
    for real_value in mapping.values():
        if real_value in text:
            leaked += 1
    return leaked


def _check_restore(original, restored):
    """检查还原后是否与原文一致。返回是否一致。"""
    return original == restored


# ──────────────────────────────────────────
# 执行测试
# ──────────────────────────────────────────
def run_test(case):
    """执行单个测试用例，返回 (旧版结果, 新版结果, 是否通过)。"""
    text = case["text"]
    print("\n" + "=" * 70)
    print(f"【{case['name']}】")
    print(f"  说明：{case['desc']}")
    print(f"  原文：{text}")
    print("-" * 70)

    # === 旧版 ===
    old_eng = OldEngine()
    old_safe, old_box = old_eng.anonymize(text)
    old_restored = old_eng.deanonymize(old_safe, old_box)
    old_count = _count_placeholders(old_safe)
    old_leak = _count_real_sensitive_in_output(old_safe, old_box)

    print(f"\n  【旧版】脱敏后：{old_safe}")
    print(f"  【旧版】占位符数：{old_count}，残留敏感值：{old_leak}")
    print(f"  【旧版】还原准确：{'✅' if _check_restore(text, old_restored) else '❌'}")

    # === 新版 ===
    new_eng = NewEngine()
    new_safe, new_box = new_eng.anonymize(text)
    new_restored = new_eng.deanonymize(new_safe, new_box)
    new_count = _count_placeholders(new_safe)
    new_leak = _count_real_sensitive_in_output(new_safe, new_box)
    new_stats = new_eng.get_last_stats()

    print(f"\n  【新版】脱敏后：{new_safe}")
    print(f"  【新版】占位符数：{new_count}，残留敏感值：{new_leak}")
    print(f"  【新版】脱敏统计：{new_stats}")
    print(f"  【新版】还原准确：{'✅' if _check_restore(text, new_restored) else '❌'}")

    # === 判定 ===
    # 通过条件：
    # 1. 新版占位符数 >= 旧版（识别更多或同等）
    # 2. 新版残留敏感值 = 0（无泄露）
    # 3. 还原准确
    passed = (
        new_count >= old_count
        and new_leak == 0
        and _check_restore(text, new_restored)
    )

    # 测试5（误伤检测）：新版不应产生占位符
    if "误伤" in case["name"]:
        passed = (new_count == 0 and old_count == 0)

    # 测试6（空文本/无敏感）：不应产生占位符
    if "空文本" in case["name"] or "无敏感" in case["desc"]:
        passed = (new_count == 0)

    print(f"\n  【结论】{'✅ 通过' if passed else '❌ 未通过'}")
    print(f"  【对比】旧版识别 {old_count} 个 → 新版识别 {new_count} 个")

    return passed


def main():
    print("=" * 70)
    print("PrivacyEngine 强化版 vs 旧版 对比测试")
    print("=" * 70)
    print(f"旧版文件：{OLD_PATH}")
    print(f"新版文件：{NEW_PATH}")

    results = []
    for case in TEST_CASES:
        try:
            passed = run_test(case)
            results.append((case["name"], passed))
        except Exception as e:
            print(f"\n【{case['name']}】执行出错：{type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            results.append((case["name"], False))

    # === 汇总 ===
    print("\n" + "=" * 70)
    print("【测试汇总】")
    print("=" * 70)
    passed_count = sum(1 for _, p in results if p)
    total = len(results)
    for name, passed in results:
        print(f"  {'✅' if passed else '❌'} {name}")
    print(f"\n通过率：{passed_count}/{total}")
    print("=" * 70)

    # === 额外验证：接口兼容性 ===
    print("\n【接口兼容性验证】")
    try:
        new_eng = NewEngine()
        # 验证 anonymize 返回 (str, dict)
        result = new_eng.anonymize("电话13812345678")
        assert isinstance(result, tuple) and len(result) == 2, "anonymize 返回格式错误"
        assert isinstance(result[0], str), "anonymize 第一返回值应为 str"
        assert isinstance(result[1], dict), "anonymize 第二返回值应为 dict"
        # 验证 deanonymize 返回 str
        restored = new_eng.deanonymize(result[0], result[1])
        assert isinstance(restored, str), "deanonymize 返回值应为 str"
        # 验证 destroy / get_last_stats 存在
        new_eng.destroy()
        stats = new_eng.get_last_stats()
        assert isinstance(stats, dict), "get_last_stats 返回值应为 dict"
        # 验证 add_custom_names
        new_eng.add_custom_names(["张三", "李四"])
        print("  ✅ anonymize(text) -> (str, dict) 格式正确")
        print("  ✅ deanonymize(text, dict) -> str 格式正确")
        print("  ✅ destroy() / get_last_stats() / add_custom_names() 可用")
        print("  ✅ 接口完全兼容，调用方代码无需修改")
    except Exception as e:
        print(f"  ❌ 接口兼容性验证失败：{e}")

    print("\n" + "=" * 70)
    print("测试完成。")
    return 0 if passed_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
