"""
询盘判定标准

分三层：
  Layer 1 — Python 关键词预过滤（免 API 调用）
  Layer 2 — LLM 结构化评分（8 项标准，每项 0/1）
  Layer 3 — 分数阈值决策

最终分类：
  valid_inquiry : 评分 >= INQUIRY_THRESHOLD
  other         : 评分 >= OTHER_THRESHOLD 且 < INQUIRY_THRESHOLD
  spam          : 评分 < OTHER_THRESHOLD，或 Layer 1 直接判定
"""

# ══ Layer 1：确定垃圾邮件（命中任意一条 → 直接 spam，不调用 LLM） ══════════

# 主题关键词（小写，完整词或短语）
SPAM_SUBJECT_PATTERNS = [
    "verification code", "验证码", "your code is", "otp", "one-time password",
    "confirm your email", "activate your account", "password reset",
    "unsubscribe", "newsletter", "weekly digest", "monthly update",
    "trial ends", "your subscription", "invoice #", "payment receipt",
    "job application", "resume", "curriculum vitae",
]

# 发件人域名（已知营销/通知类域名）
SPAM_SENDER_DOMAINS = [
    "noreply@", "no-reply@", "donotreply@", "mailer@", "bounce@",
    "notifications@", "updates@", "digest@", "newsletter@",
    "tm.openai.com", "announcements@figma.com", "mail.wispr.ai",
]

# 正文关键词（命中 2+ 条则判 spam）
SPAM_BODY_PATTERNS = [
    "click here to unsubscribe", "to unsubscribe from", "manage your preferences",
    "you are receiving this", "this email was sent to", "email preferences",
    "view in browser", "add us to your address book",
]

# ══ Layer 1：确定询盘信号（命中任意一条 → 跳过 spam 预判，进入 LLM 评分） ═

# 产品相关关键词（命中即可能是询盘）
PRODUCT_KEYWORDS = [
    # 硬质合金类
    "carbide", "tungsten", "cemented", "wc", "sintered",
    # 产品名称
    "shield teeth", "tbm", "spherical button", "dth button", "drill bit",
    "tunneling teeth", "coal pick", "mining pick", "milling tip",
    "carbide rod", "carbide strip", "carbide blank", "rotary bit",
    "insert", "cutter", "pick", "tip", "button", "tooth",
    # 行业词
    "mining", "drilling", "tunneling", "roadheader", "shearer", "milling",
    # 规格词
    "ss12c", "ss16c", "ss18c", "ss20c", "sr11c", "sr13c",
    "su10", "su20", "su25", "yg10",
    "hra", "hardness", "density", "grain size", "trs",
    # 应用场景
    "dtH", "rock drill", "shield machine", "coal seam", "asphalt", "pavement",
]

# 询价意图关键词
INQUIRY_INTENT_KEYWORDS = [
    "inquiry", "enquiry", "quotation", "quote", "rfq", "request for quotation",
    "price", "pricing", "cost", "offer", "catalogue", "catalog",
    "availability", "stock", "lead time", "delivery time", "moq",
    "minimum order", "specification", "spec", "datasheet", "sample",
    "trial order", "test order", "purchase", "sourcing", "procurement",
    "supplier", "manufacturer", "factory", "oem", "odm",
    # 中文
    "询价", "报价", "采购", "求购", "需要", "规格", "参数", "价格",
]

# ══ Layer 2：LLM 评分标准（每项 1 分，共 8 分） ════════════════════════════

SCORING_CRITERIA = [
    {
        "id": "C1",
        "name": "产品相关性",
        "en": "The email mentions any specific product, material, goods, or commodity (e.g. carbide, tungsten, chemicals, machinery parts, raw materials, industrial products, or any tradable item)",
        "weight": 2,   # 最重要，double weight
    },
    {
        "id": "C2",
        "name": "明确询价意图",
        "en": "The email explicitly asks for price, quotation, RFQ, or commercial terms",
        "weight": 2,
    },
    {
        "id": "C3",
        "name": "数量或规格",
        "en": "The email mentions a quantity (with unit like pcs/kg/tons) or technical specification",
        "weight": 1,
    },
    {
        "id": "C4",
        "name": "交期或付款",
        "en": "The email mentions delivery time, lead time, or payment terms (T/T, L/C, etc.)",
        "weight": 1,
    },
    {
        "id": "C5",
        "name": "企业邮箱",
        "en": "The sender uses a corporate email domain (not gmail/hotmail/yahoo/qq/163/outlook personal)",
        "weight": 1,
    },
    {
        "id": "C6",
        "name": "公司或应用场景",
        "en": "The email mentions the buyer's company name, or describes the end-use application",
        "weight": 1,
    },
    {
        "id": "C7",
        "name": "正式商务格式",
        "en": "The email has a professional business structure: greeting + body + signature (not just a one-liner or forwarded chain)",
        "weight": 1,
    },
    {
        "id": "C8",
        "name": "行业匹配度",
        "en": "The sender appears to be from a relevant industry: mining, drilling, construction, oil & gas, road maintenance, tool manufacturing, chemical processing, automotive, machinery, hardware, or any B2B manufacturing/trading context",
        "weight": 1,
    },
]

# ══ Layer 3：分数阈值 ═════════════════════════════════════════════════════════

INQUIRY_THRESHOLD = 3    # 加权总分 >= 3 → valid_inquiry
OTHER_THRESHOLD   = 1    # 加权总分 >= 1 → other；< 1 → spam

MAX_SCORE = sum(c["weight"] for c in SCORING_CRITERIA)  # = 10


# ══ Layer 1 工具函数 ══════════════════════════════════════════════════════════

def _norm(text: str) -> str:
    return (text or "").lower()


def is_definite_spam(subject: str, sender: str, body: str) -> bool:
    """Layer 1：规则直接判垃圾"""
    s = _norm(subject)
    snd = _norm(sender)
    b = _norm(body)

    # 主题命中
    if any(p in s for p in SPAM_SUBJECT_PATTERNS):
        return True

    # 发件人命中
    if any(p in snd for p in SPAM_SENDER_DOMAINS):
        return True

    # 正文命中 2 条以上
    body_hits = sum(1 for p in SPAM_BODY_PATTERNS if p in b)
    if body_hits >= 2:
        return True

    return False


def has_inquiry_signal(subject: str, body: str) -> bool:
    """Layer 1：是否含有任何询盘信号（决定是否调用 LLM）"""
    text = _norm(subject) + " " + _norm(body)
    has_product = any(k in text for k in PRODUCT_KEYWORDS)
    has_intent  = any(k in text for k in INQUIRY_INTENT_KEYWORDS)
    return has_product or has_intent


def build_scoring_prompt() -> str:
    """生成给 LLM 的评分标准说明"""
    lines = []
    for c in SCORING_CRITERIA:
        lines.append(f'  "{c["id"]}": 1 if {c["en"]}, else 0  (weight: {c["weight"]})')
    return "\n".join(lines)
