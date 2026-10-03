# -*- coding: utf-8 -*-
"""
PrivacyEngine 强化版测试脚本

测试方式：
1. 加载新版（强化版）并对同一批测试用例断言：无泄露 / 还原准确 / 不误伤
2. 若存在旧版备份（privacy_engine_old.py.bak），顺带做强化前后对比
3. 验证接口兼容性：anonymize/deanonymize 行为一致
4. 验证 nonce 防劫持专项行为

说明：旧版备份文件已在仓库整理中移除，缺失时自动跳过"新旧对比"，
      不影响新版断言与退出码（退出码 0 = 全部通过）。

运行方式：
    python test_privacy_engine.py
"""
import importlib.util
import sys
import os

# ──────────────────────────────────────────
# 加载新版（强化版）；旧版备份存在时一并加载用于对比
# ──────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OLD_PATH = os.path.join(BASE_DIR, "yindun", "core", "privacy_engine_old.py.bak")
NEW_PATH = os.path.join(BASE_DIR, "yindun", "core", "privacy_engine.py")


def _load_class_from_file(path, module_name):
    """从任意扩展名的文件路径加载 PrivacyEngine 类（支持 .bak 等非标准扩展名）。

    注意：不使用已废弃的 SourceFileLoader.load_module()（Python 3.12+ 已移除），
    改用 spec_from_file_location + exec_module。
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PrivacyEngine


sys.path.insert(0, BASE_DIR)
from yindun.core.privacy_engine import PrivacyEngine as NewEngine  # noqa: E402

OldEngine = None
if os.path.exists(OLD_PATH):
    OldEngine = _load_class_from_file(OLD_PATH, "old_engine")

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
            "合同金额85万元，乙方账号6228480402564890018，"
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
            "合同金额85万元，乙方账号6228480402564890018，"
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
    """统计文本中占位符的数量，兼容 [PHONE_0]（旧）与 [PHONE_0_a3f9]（新，含 nonce）"""
    import re
    return len(re.findall(r"\[[A-Z]+_\d+(?:_[a-z0-9]+)?\]", text))


def _count_real_sensitive_in_output(text, mapping):
    """检查脱敏后文本中是否还残留真实敏感值。返回残留数量。

    注意：mapping 的 value 是 Fernet 密文而非明文，直接用密文比对永远"不残留"
    （等于空转断言）。这里先解密出真实值再比对，才是有效的泄露检查。
    """
    from yindun.core.secret_manager import SecretManager
    sm = SecretManager.get_instance()
    leaked = 0
    for encrypted_value in mapping.values():
        try:
            real_value = sm.decrypt(encrypted_value)
        except Exception:
            real_value = encrypted_value  # 旧版明文 mapping 兼容
        if real_value and real_value in text:
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

    # === 旧版（备份存在时才做强化前后对比）===
    old_count = 0
    if OldEngine is not None:
        old_eng = OldEngine()
        old_safe, old_box = old_eng.anonymize(text)
        old_restored = old_eng.deanonymize(old_safe, old_box)
        old_count = _count_placeholders(old_safe)
        old_leak = _count_real_sensitive_in_output(old_safe, old_box)

        print(f"\n  【旧版】脱敏后：{old_safe}")
        print(f"  【旧版】占位符数：{old_count}，残留敏感值：{old_leak}")
        print(f"  【旧版】还原准确：{'✅' if _check_restore(text, old_restored) else '❌'}")
    else:
        print("\n  【旧版】未找到备份文件 privacy_engine_old.py.bak，"
              "跳过新旧对比（仅验证新版断言）")

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
    print("PrivacyEngine 强化版 断言测试" + ("（含新旧对比）" if OldEngine else "（无旧版备份，仅测新版）"))
    print("=" * 70)
    print(f"新版文件：{NEW_PATH}")
    print(f"旧版文件：{OLD_PATH if OldEngine else '（不存在，跳过对比）'}")

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
    interface_ok = True
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
        interface_ok = False
        print(f"  ❌ 接口兼容性验证失败：{e}")

    # === nonce 专项验证（防"还原劫持"）===
    print("\n【nonce 占位符专项验证】")
    nonce_checks = [
        ("原文含字面量占位符不应被误还原", run_nonce_anti_hijack_test),
        ("同批 nonce 不重复", run_nonce_uniqueness_test),
        ("新格式解密失败保留占位符", run_new_format_failure_test),
    ]
    nonce_failed = []
    for desc, fn in nonce_checks:
        try:
            fn()
        except Exception as e:
            nonce_failed.append(desc)
            print(f"  ❌ {desc} 失败：{type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 70)
    all_ok = (passed_count == total) and interface_ok and not nonce_failed
    if all_ok:
        print(f"✅ 全部通过：用例 {passed_count}/{total}，接口兼容，nonce 专项 {len(nonce_checks)} 项")
    else:
        if passed_count != total:
            print(f"❌ 用例通过 {passed_count}/{total}")
        if not interface_ok:
            print("❌ 接口兼容性验证未通过")
        for desc in nonce_failed:
            print(f"❌ nonce 专项未通过：{desc}")
    print("=" * 70)
    return 0 if all_ok else 1


def run_nonce_anti_hijack_test():
    """验证：原文含字面量占位符（如 [PHONE_0_a3f9]）时，deanonymize 不应误还原。"""
    import re
    engine = NewEngine()
    text = "我的手机是13812345678，占位符样例[PHONE_0_a3f9]"
    anon, box = engine.anonymize(text)
    assert "13812345678" not in anon, "真实手机号应被替换"
    assert "[PHONE_0_a3f9]" in anon, "原文字面量占位符应保持不变"
    ph = next(p for p in box if p.startswith("[PHONE_"))
    assert re.match(r"\[PHONE_\d+_[a-z0-9]{4}\]", ph), f"占位符应为含 nonce 新格式: {ph}"
    restored = engine.deanonymize(anon, box)
    assert restored == text, f"还原后应与原文一致: {restored}"
    print(f"  脱敏后: {anon}")
    print(f"  还原后: {restored}")
    print("  ✅ 字面量占位符未被误还原（nonce 已将碰撞概率降为不可行）")


def run_nonce_uniqueness_test():
    """验证：同一批 anonymize 内 nonce 不重复。"""
    import re
    engine = NewEngine()
    text = "手机号13812345678，备用13987654321，工作13711112222"
    anon, box = engine.anonymize(text)
    ph_list = [p for p in box if p.startswith("[PHONE_")]
    assert len(ph_list) == 3, f"应识别 3 个手机号: {ph_list}"
    nonces = [re.search(r"_([a-z0-9]{4})\]$", p).group(1) for p in ph_list]
    assert len(nonces) == len(set(nonces)), f"同批 nonce 不应重复: {nonces}"
    print(f"  同批 {len(ph_list)} 个 PHONE 占位符 nonce 互不相同: {nonces}")
    print("  ✅ 同批 nonce 去重通过")


def run_new_format_failure_test():
    """验证：新格式占位符解密失败时保留占位符，不写入密文垃圾。"""
    engine = NewEngine()
    text = "电话是[PHONE_0_a3f9]，请回拨。"
    mapping = {"[PHONE_0_a3f9]": "not-a-valid-ciphertext"}
    default_restored = engine.deanonymize(text, mapping)
    strict_restored = engine.deanonymize(text, mapping, strict=True)
    assert default_restored == text, f"默认模式应保留占位符: {default_restored}"
    assert strict_restored == text, f"strict 模式应保留占位符: {strict_restored}"
    print("  ✅ 默认与 strict 均保留占位符，不写入密文垃圾")


if __name__ == "__main__":
    sys.exit(main())
