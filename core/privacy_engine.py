import re

class PrivacyEngine:
    def __init__(self):
        # 🛡️ 用正则表达式定义核心敏感数据的匹配规则
        self.patterns = {
            "PHONE": r"1[3-9]\d{9}",                                 # 手机号
            "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", # 电子邮箱
            "IDCARD": r"\d{17}[\dXx]|\d{15}"                         # 身份证号
        }

    def anonymize(self, text: str):
        """
        【正向脱敏】
        将文本中的敏感词提取出来存入字典，并在原句中替换为占位符。
        例如："张三的电话是13812345678" -> ("张三的电话是[PHONE_0]", {"[PHONE_0]": "13812345678"})
        """
        mapping = {}
        anonymized_text = text
        
        for key, pattern in self.patterns.items():
            matches = list(set(re.findall(pattern, anonymized_text))) # 去重匹配
            for idx, match in enumerate(matches):
                placeholder = f"[{key}_{idx}]"
                mapping[placeholder] = match  # 把真实数据锁进本地“密文保险箱”
                anonymized_text = anonymized_text.replace(match, placeholder) # 在文本中打码
                
        return anonymized_text, mapping

    def deanonymize(self, text: str, mapping: dict) -> str:
        """
        【逆向还原】
        大模型回答完后，把回复里的占位符偷偷换回原本的真实敏感数据，再展示给用户。
        """
        restored_text = text
        for placeholder, original_value in mapping.items():
            restored_text = restored_text.replace(placeholder, original_value)
        return restored_text


# 🧪 这里是一段本地独立测试代码，可以直接运行看效果
if __name__ == "__main__":
    engine = PrivacyEngine()
    raw_text = "员工老王的邮箱是 test@qq.com，报销手机号是13988889999"
    print("【1. 原始输入】:", raw_text)
    
    safe_text, secret_box = engine.anonymize(raw_text)
    print("【2. 脱敏后（发给AI的文本）】:", safe_text)
    print("【3. 本地密文保险箱】:", secret_box)
    
    ai_reply = f"已收到，我会向 [EMAIL_0] 发送确认函，并短信通知 [PHONE_0]。"
    restored = engine.deanonymize(ai_reply, secret_box)
    print("【4. 最终还原（展示给用户的文本）】:", restored)