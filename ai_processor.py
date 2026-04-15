import os
import json
import re
import base64
import logging
import httpx

logger = logging.getLogger("inquiry")
from openai import OpenAI
from inquiry_criteria import (
    is_definite_spam, has_inquiry_signal, build_scoring_prompt,
    SCORING_CRITERIA, INQUIRY_THRESHOLD, OTHER_THRESHOLD,
)

# ── LLM 客户端工厂 ──────────────────────────────────────────────────────────

PROVIDERS = {
    "qianwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "QIANWEN_API_KEY",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "api_key_env": "ZHIPU_API_KEY",
    },
}

def _get_client():
    provider = os.getenv("LLM_PROVIDER", "qianwen")
    cfg = PROVIDERS[provider]
    api_key = os.getenv(cfg["api_key_env"])
    if not api_key:
        raise ValueError(f"缺少 API Key，请在 .env 中设置 {cfg['api_key_env']}")
    return OpenAI(api_key=api_key, base_url=cfg["base_url"])

def _model():
    return os.getenv("LLM_MODEL", "qwen-plus")


# ── AI 健康状态 ─────────────────────────────────────────────────────────────
from datetime import datetime as _dt

_ai_health = {
    "status":        "healthy",   # healthy / degraded / unavailable
    "last_ok":       None,        # ISO 时间戳
    "last_fail":     None,
    "fail_count":    0,
    "error_message": None,
}


def get_ai_health() -> dict:
    """返回当前 AI 健康状态的只读副本"""
    return dict(_ai_health)


def check_ai_health() -> tuple[str, str] | None:
    """
    轻量探针：发一个 max_tokens=1 的请求检测 LLM 可用性。
    返回 (old_status, new_status) 如果状态变化了，否则返回 None。
    """
    old = _ai_health["status"]
    try:
        client = _get_client()
        client.chat.completions.create(
            model=_model(),
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
            timeout=10,
        )
        _ai_health["last_ok"] = _dt.now().isoformat()
        _ai_health["fail_count"] = 0
        _ai_health["error_message"] = None
        _ai_health["status"] = "healthy"
    except Exception as e:
        _ai_health["last_fail"] = _dt.now().isoformat()
        _ai_health["fail_count"] += 1
        _ai_health["error_message"] = str(e)[:200]
        if _ai_health["fail_count"] >= 2:
            _ai_health["status"] = "unavailable"
        else:
            _ai_health["status"] = "degraded"

    new = _ai_health["status"]
    return (old, new) if old != new else None


def _parse_json(text):
    """从 LLM 响应中提取 JSON，兼容 markdown 代码块包裹"""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1)
    return json.loads(text)


# ── 调用 1：邮件分类（三层机制） ──────────────────────────────────────────────

def classify_email(subject: str, sender: str, body: str) -> dict:
    """
    三层分类机制：
      Layer 1 — 规则预过滤（零 API 调用）
      Layer 2 — LLM 结构化评分（8 项标准）
      Layer 3 — 加权总分决策

    返回:
    {
      "category": "valid_inquiry|spam|other",
      "reason": "...",
      "layer": 1 或 2,           # 哪一层做出决策
      "score": 7,                 # Layer 2 加权总分（Layer 1 时为 null）
      "max_score": 10,
      "criteria": {               # 每项得分（Layer 2 时有值）
        "C1": 1, "C2": 1, ...
      }
    }
    """
    # ── Layer 1：规则预过滤 ───────────────────────────────────────────────────
    if is_definite_spam(subject, sender, body):
        return {
            "category": "spam",
            "reason": "Matched definite-spam rule (subject/sender/body pattern)",
            "layer": 1,
            "score": None,
            "max_score": None,
            "criteria": {},
        }

    if not has_inquiry_signal(subject, body):
        return {
            "category": "other",
            "reason": "No product or inquiry-intent keywords detected",
            "layer": 1,
            "score": None,
            "max_score": None,
            "criteria": {},
        }

    # ── Layer 2：LLM 结构化评分 ───────────────────────────────────────────────
    criteria_desc = build_scoring_prompt()
    criteria_ids  = [c["id"] for c in SCORING_CRITERIA]
    criteria_json_template = "{" + ", ".join(f'"{c}": 0' for c in criteria_ids) + "}"

    client = _get_client()
    company_name = os.getenv("COMPANY_NAME", "our company")
    prompt = f"""You are a classification engine for {company_name}, a B2B export company.

Score the following email against each criterion (1 = met, 0 = not met):

{criteria_desc}

Respond ONLY with valid JSON in this exact format (no extra fields):
{{
  "criteria": {criteria_json_template},
  "reason": "one sentence explaining the overall classification"
}}

Email:
Subject: {subject}
From: {sender}
Body:
{body[:2000]}
"""
    resp = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        timeout=30,
    )
    try:
        raw = _parse_json(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"[classify_email] JSON parse failed model={_model()} err={e} raw={resp.choices[0].message.content[:200]!r}")
        return {"category": "other", "reason": "LLM response parse failed",
                "layer": 2, "score": 0, "max_score": None, "criteria": {}}
    scores = raw.get("criteria", {})

    # ── Layer 3：加权总分决策 ────────────────────────────────────────────────
    weight_map = {c["id"]: c["weight"] for c in SCORING_CRITERIA}
    total = sum(scores.get(cid, 0) * weight_map.get(cid, 1) for cid in criteria_ids)
    max_score = sum(c["weight"] for c in SCORING_CRITERIA)

    if total >= INQUIRY_THRESHOLD:
        category = "valid_inquiry"
    elif total >= OTHER_THRESHOLD:
        category = "other"
    else:
        category = "spam"

    return {
        "category": category,
        "reason": raw.get("reason", ""),
        "layer": 2,
        "score": total,
        "max_score": max_score,
        "criteria": scores,
    }


# ── 调用 2：询盘内容解析 ──────────────────────────────────────────────────────

def parse_inquiry(body: str) -> dict:
    """
    返回结构化询盘信息，包含检测到的语言。
    """
    client = _get_client()
    prompt = f"""You are an expert foreign trade assistant for a B2B export company.
Extract structured information from the following inquiry email.

Respond ONLY with valid JSON in this exact format:
{{
  "language": "en",
  "customer_name": "John Smith",
  "company": "ABC Mining Ltd.",
  "country": "Australia",
  "products_requested": [
    {{
      "description": "original product description from email",
      "quantity": 1000,
      "unit": "PCS",
      "target_price_usd": 2.50,
      "grade_or_spec": "SS16C or any spec mentioned"
    }}
  ],
  "delivery_deadline": "within 60 days or specific date",
  "payment_terms": "T/T or L/C or not mentioned",
  "special_requirements": "any special notes",
  "urgency": "high|medium|low",
  "competitor_mentions": [
    {{
      "company": "competitor company name or null",
      "price_usd": 2.30,
      "unit": "kg",
      "product": "the product being compared"
    }}
  ]
}}

Rules:
- "language": ISO 639-1 code of the email's language (en/zh/es/ar/fr/de/ru/pt/etc.)
- Use null for fields not mentioned in the email
- Keep "description" as close to original wording as possible
- "competitor_mentions": extract ONLY when buyer explicitly mentions another supplier's price
  (e.g. "Company X offers $2.3/kg", "I got a quote for $5 from another vendor").
  Leave as empty array [] if no competitor price is mentioned.

Inquiry email:
{body[:3000]}
"""
    resp = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        timeout=30,
    )
    try:
        return _parse_json(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"[parse_inquiry] JSON parse failed model={_model()} err={e} raw={resp.choices[0].message.content[:200]!r}")
        return {"language": "en", "products_requested": [],
                "customer_name": None, "company": None, "country": None,
                "delivery_deadline": None, "payment_terms": None,
                "special_requirements": None, "urgency": "low",
                "competitor_mentions": []}


# ── 调用 3：回复草稿生成 ──────────────────────────────────────────────────────

def generate_draft(parsed_inquiry: dict, matched_products: list) -> dict:
    """
    返回: {"subject": "...", "body": "..."}
    回复语言跟随来信语言。
    """
    client = _get_client()
    company_name    = os.getenv("COMPANY_NAME", "our company")
    company_website = os.getenv("COMPANY_WEBSITE", "")
    company_email   = os.getenv("COMPANY_EMAIL", "")
    company_phone   = os.getenv("COMPANY_PHONE", "")
    company_desc    = os.getenv("COMPANY_DESC", "a manufacturer and exporter")

    lang = parsed_inquiry.get("language", "en")
    customer_name = parsed_inquiry.get("customer_name") or "Sir/Madam"

    # 格式化产品报价信息
    if matched_products:
        quote_lines = []
        for p in matched_products:
            parts = [f"- {p['product_name']}"]
            if p.get("grade"):
                parts.append(f"Grade/Model: {p['grade']}")
            if p.get("price_usd"):
                parts.append(f"Price: USD {p['price_usd']:.2f}/{p['unit']}")
            if p.get("moq"):
                parts.append(f"MOQ: {p['moq']} {p['unit']}")
            if p.get("lead_time_days"):
                parts.append(f"Lead time: {p['lead_time_days']} days")
            if p.get("specs_summary"):
                parts.append(f"Specs: {p['specs_summary']}")
            quote_lines.append(" | ".join(parts))
        quote_text = "\n".join(quote_lines)
    else:
        quote_text = "No exact product match found — please describe the product details more specifically."

    lang_instruction = {
        "en": "Write in natural, conversational English — like a real salesperson who has actually read the email, not a template.",
        "zh": "用自然、口语化的中文回复，像真实销售人员写的，不要像模板。",
        "es": "Escribe en español natural y directo, como un vendedor real, no como una plantilla.",
        "ar": "اكتب باللغة العربية بشكل طبيعي ومباشر، كما يكتب مندوب مبيعات حقيقي.",
        "fr": "Rédigez en français naturel et direct, comme un vrai commercial, pas comme un modèle.",
        "de": "Schreiben Sie auf natürlichem, direktem Deutsch — wie ein echter Vertriebsmitarbeiter.",
        "ru": "Пишите на естественном, разговорном русском языке — как настоящий менеджер по продажам.",
        "pt": "Escreva em português natural e direto, como um vendedor real, não como um modelo.",
    }.get(lang, f"Write in natural, direct {lang} — like a real salesperson, not a template.")

    # 竞品价格感知：若买家提到竞品报价，生成有针对性的应对提示
    competitor_hint = ""
    competitors = parsed_inquiry.get("competitor_mentions") or []
    if competitors:
        lines = []
        for c in competitors:
            price = c.get("price_usd")
            unit  = c.get("unit") or ""
            comp  = c.get("company") or "another supplier"
            prod  = c.get("product") or "the product"
            if price:
                lines.append(f'- Buyer mentioned {comp} quoted ${price}/{unit} for {prod}')
            else:
                lines.append(f'- Buyer mentioned getting a quote from {comp} for {prod}')
        competitor_hint = (
            "\nCOMPETITOR PRICING ALERT — handle this tactfully:\n"
            + "\n".join(lines) + "\n"
            "Strategy: Do NOT match their price blindly. In ONE sentence max, "
            "acknowledge you've seen similar comparisons before, then pivot to your "
            "strengths (quality certifications, lead time, after-sales support, "
            "factory-direct reliability). Do not ignore this — buyers who mention "
            "competitor prices are actively comparing and need a reason to choose you.\n"
        )

    prompt = f"""You are a sales rep at {company_name}, {company_desc}. A customer just sent you an inquiry and you're writing back.

{lang_instruction}

STRICT RULES — violations will make the email look fake:
- Do NOT open with "Thank you for your inquiry" or any variation of it
- Do NOT use "I hope this email finds you well" or similar filler openers
- Do NOT use hollow phrases like "We are pleased to", "It is our pleasure to", "Please feel free to"
- Do NOT structure the email like a formal business letter with numbered sections
- Start directly — reference something specific from their inquiry (the product they asked about, their quantity, their deadline) in the first sentence
- Write short paragraphs (2-3 sentences max each)
- Sound like a real person who read their email, not a bot filling in a template
{competitor_hint}
What to cover naturally (weave in, don't list):
- Acknowledge what they asked for specifically
- Share the product quote and key specs
- Payment terms: T/T 30% deposit, 70% before shipment (mention briefly)
- Offer to send samples or discuss further
- Sign off with company info

Customer: {customer_name}
Inquiry details: {json.dumps(parsed_inquiry, ensure_ascii=False, indent=2)}

Product quote:
{quote_text}

Signature:
{company_name}
{f"Website: {company_website}" if company_website else ""}
{f"Email: {company_email}" if company_email else ""}
{f"Tel: {company_phone}" if company_phone else ""}

Respond ONLY with valid JSON:
{{"subject": "Re: [original subject]", "body": "full email body"}}
"""
    resp = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6,
        timeout=45,
    )
    try:
        return _parse_json(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"[generate_draft] JSON parse failed model={_model()} err={e} raw={resp.choices[0].message.content[:200]!r}")
        return {"subject": f"Re: {parsed_inquiry.get('products_requested', [{}])[0].get('description', 'your inquiry') if parsed_inquiry.get('products_requested') else 'your inquiry'}",
                "body": "(草稿生成失败，请手动编写回复)"}


# ── 跟进草稿生成 ──────────────────────────────────────────────────────────────

def generate_followup_draft(original_subject: str, original_body: str,
                             sent_draft_body: str, language: str,
                             customer_history: dict) -> dict:
    """
    生成跟进邮件草稿。
    customer_history: {"inquiry_count": n, "quote_count": n, "company_name": "..."}
    返回: {"subject": "...", "body": "..."}
    """
    client = _get_client()
    company_name    = os.getenv("COMPANY_NAME", "our company")
    company_website = os.getenv("COMPANY_WEBSITE", "")
    company_email   = os.getenv("COMPANY_EMAIL", "")
    company_phone   = os.getenv("COMPANY_PHONE", "")

    lang_instruction = {
        "en": "Write in professional English.",
        "zh": "用专业中文写。",
        "es": "Escribe en español profesional.",
        "ar": "اكتب باللغة العربية المهنية.",
        "fr": "Rédigez en français professionnel.",
        "de": "Schreiben Sie auf professionellem Deutsch.",
        "ru": "Напишите на профессиональном русском.",
        "pt": "Escreva em português profissional.",
    }.get(language, f"Write in {language}, professional tone.")

    history_note = ""
    if customer_history.get("inquiry_count", 0) > 1:
        history_note = f"Note: This customer has contacted us {customer_history['inquiry_count']} times before."

    prompt = f"""You are a sales rep at {company_name} writing a follow-up email.
{lang_instruction}

We sent a quotation 3 days ago with no reply. Write a short follow-up — but make it sound like a real person checking in, not an automated reminder.

Rules:
- 3-4 sentences max
- Do NOT open with "I hope this email finds you well" or "Just following up on my previous email"
- Do NOT say "I wanted to reach out" or "I am writing to"
- Start with something direct and human — reference what they were looking for or a specific detail from the original inquiry
- Do NOT repeat prices or specs
- Casual but professional tone — think colleague, not corporate bot
- {history_note}

Original inquiry subject: {original_subject}
Our quotation excerpt (first 300 chars): {sent_draft_body[:300]}

Company signature:
{company_name}
Website: {company_website}
Email: {company_email}
{f"Tel: {company_phone}" if company_phone else ""}

Respond ONLY with valid JSON:
{{"subject": "Follow-up: {original_subject}", "body": "full follow-up email body"}}
"""
    resp = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        timeout=30,
    )
    try:
        return _parse_json(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"[generate_followup_draft] JSON parse failed model={_model()} err={e} raw={resp.choices[0].message.content[:200]!r}")
        return {"subject": f"Follow-up: {original_subject}",
                "body": "(跟进草稿生成失败，请手动编写)"}


# ── 客户意图评分 ──────────────────────────────────────────────────────────────

def score_buyer_intent(parsed_inquiry: dict, email_body: str) -> int:
    """
    快速评估买家意向强度（1-5 星，无需 API 调用，纯规则）。
    """
    score = 0
    body_lower = email_body.lower()

    # 有明确数量 +2
    for req in parsed_inquiry.get("products_requested") or []:
        if req.get("quantity"):
            score += 2
            break

    # 有目标价 +1
    for req in parsed_inquiry.get("products_requested") or []:
        if req.get("target_price_usd"):
            score += 1
            break

    # 有交期要求 +1
    if parsed_inquiry.get("delivery_deadline"):
        score += 1

    # 有付款条款 +1
    if parsed_inquiry.get("payment_terms") and parsed_inquiry["payment_terms"] != "not mentioned":
        score += 1

    # 紧急词 +1
    urgent_words = ["urgent", "asap", "immediately", "rush", "deadline", "紧急", "急需"]
    if any(w in body_lower for w in urgent_words):
        score += 1

    # 企业邮箱 +1（从正文前 200 字符检测，免费邮箱域名不加分）
    free_domains = ["gmail", "hotmail", "yahoo", "qq.com", "163.com", "outlook.com", "126.com"]
    if not any(d in body_lower[:200] for d in free_domains):
        score += 1

    return min(score, 5)


# ── 背调：Serper 搜索 ────────────────────────────────────────────────────────

def _serper_search(query: str, num: int = 6) -> list:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return []
    try:
        resp = httpx.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=10,
        )
        data = resp.json()
        return [
            {"title": r.get("title", ""), "snippet": r.get("snippet", ""), "link": r.get("link", "")}
            for r in data.get("organic", [])
        ]
    except Exception as e:
        print(f"  Serper 搜索失败: {e}")
        return []


def background_check(sender: str, company_name: str, country: str, email_body: str) -> dict:
    """
    对询盘方进行背景调查：
    1. Serper 搜索公司信息（三个角度）
    2. LLM 综合分析，给出风险等级和建议
    """
    m = re.search(r"@([\w.\-]+)", sender)
    domain = m.group(1) if m else ""

    q_company = f'"{company_name}"' if company_name else domain
    queries = {
        "公司基本信息": f"{q_company} {country} company",
        "域名/邮箱可信度": f'"{domain}" company review',
        "行业匹配度": f"{q_company} buyer importer wholesale",
    }

    search_results = {}
    for label, q in queries.items():
        if q.strip():
            results = _serper_search(q, num=5)
            search_results[label] = results
            print(f"  [背调] {label}: {len(results)} 条结果")

    # 给 LLM 的搜索结果文本
    search_text = ""
    for section, items in search_results.items():
        if items:
            search_text += f"\n[{section}]\n"
            for r in items:
                search_text += f"  • {r['title']}\n    {r['snippet']}\n    {r['link']}\n"

    client = _get_client()
    seller_name = os.getenv("COMPANY_NAME", "our company")
    prompt = f"""You are a foreign trade risk analyst. Analyze the buyer below based on their inquiry and Google search results.
The seller is: {seller_name}

Buyer info:
- Email: {sender}
- Domain: {domain}
- Company: {company_name or 'Not stated'}
- Country: {country or 'Unknown'}

Inquiry excerpt:
{email_body[:600]}

Google search results:
{search_text or '(No results)'}

Respond ONLY with valid JSON:
{{
  "risk_level": "low|medium|high",
  "buyer_type": "end-user|trader|distributor|agent|unknown",
  "domain_type": "corporate|free_email|suspicious|unknown",
  "company_verified": true,
  "red_flags": ["concern 1", "concern 2"],
  "positive_signals": ["signal 1", "signal 2"],
  "recommendation": "proceed|verify_first|caution",
  "summary": "2-3 sentence plain English summary of who this buyer likely is and your recommendation"
}}

Rules:
- Free email (gmail/hotmail/yahoo/qq/163) → domain_type: free_email, risk at least medium
- Company found on LinkedIn/website → company_verified: true, lower risk
- Scam/fraud reports → risk: high
- Industry match with seller's products/sector → positive signal
"""
    resp = client.chat.completions.create(
        model=_model(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        timeout=30,
    )
    try:
        result = _parse_json(resp.choices[0].message.content)
    except Exception as e:
        logger.warning(f"[background_check] JSON parse failed model={_model()} sender={sender[:50]!r} err={e}")
        result = {"risk_level": "unknown", "buyer_type": "unknown",
                  "domain_type": "unknown", "red_flags": [],
                  "positive_signals": [], "recommendation": "verify_first",
                  "summary": "背调结果解析失败，请手动核查"}
    result["search_results"] = search_results
    return result


# ── 网站产品提取 ─────────────────────────────────────────────────────────────

def extract_products_from_url(url: str) -> dict:
    """
    抓取网页内容，用 AI 提取产品清单。
    返回: {"products": [...], "raw_count": n, "url": url, "error": ""}
    每个 product: {product_name, category, description, unit, moq, price_usd, lead_time_days}
    """
    import requests
    from bs4 import BeautifulSoup

    # 1. 抓取页面
    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; InquiryBot/1.0)"
        })
        resp.raise_for_status()
    except Exception as e:
        return {"products": [], "error": f"页面抓取失败: {e}", "url": url}

    # 2. 提取正文文本（去除脚本/样式/导航）
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    # 截取前 6000 字符（节省 token）
    text = "\n".join(line for line in text.splitlines() if line.strip())[:6000]

    # 3. AI 提取产品
    client = _get_client()
    prompt = f"""Extract a product catalog from the following website content.
For each distinct product or product category found, return a JSON object.

Return ONLY valid JSON in this format:
{{
  "products": [
    {{
      "product_name": "Product Name",
      "category": "Category or empty string",
      "description": "Brief product description or application",
      "unit": "pcs/kg/set/m/etc or empty",
      "moq": 0,
      "price_usd": 0,
      "lead_time_days": 0
    }}
  ]
}}

Rules:
- Include every distinct product/service mentioned
- If price/moq/lead_time is unknown, use 0
- Merge variants of the same product into one entry unless specs differ significantly
- Maximum 50 products

Website content:
{text}
"""
    try:
        ai_resp = client.chat.completions.create(
            model=_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            timeout=30,
        )
        result = _parse_json(ai_resp.choices[0].message.content)
        products = result.get("products", [])
        return {"products": products, "raw_count": len(products), "url": url, "error": ""}
    except Exception as e:
        return {"products": [], "error": f"AI 提取失败: {e}", "url": url}


# ── AI 局部改写 ──────────────────────────────────────────────────────────────

def rewrite_snippet(original_body: str, instruction: str, language: str = "en") -> str:
    """
    根据用户指令对草稿正文进行局部或全局改写。
    返回改写后的完整正文字符串；失败时原样返回 original_body。
    """
    client = _get_client()
    lang_hint = {
        "zh": "保持中文", "en": "keep English", "es": "mantén español",
        "ar": "حافظ على اللغة العربية", "fr": "garde le français",
        "de": "Deutsch behalten", "ru": "оставь русский", "pt": "manter português",
    }.get(language, f"keep the same language ({language})")

    prompt = f"""You are editing a business email draft. Apply the following instruction to improve it.

Instruction: {instruction}
Language rule: {lang_hint}

Rules:
- Only change what the instruction asks for; keep everything else identical
- Do NOT add explanations or meta-comments
- Return ONLY the revised email body text, nothing else

Original draft:
{original_body}
"""
    try:
        resp = client.chat.completions.create(
            model=_model(),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            timeout=30,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"[rewrite_snippet] 失败: {e}")
        return original_body


# ── 邮件图片识别（Qwen-VL） ───────────────────────────────────────────────────

def describe_email_images(images: list[dict]) -> str:
    """
    用 Qwen-VL 逐张描述邮件中的图片，返回拼接的文字描述，追加到正文后供分类/解析使用。

    仅在 LLM_PROVIDER=qianwen 时生效（zhipu 无视觉模型）。
    失败时静默返回空字符串，不影响主流程。

    参考：https://www.alibabacloud.com/help/en/model-studio/qwen-vl-compatible-with-openai
    images: list of {"content_type": str, "raw": bytes}（由 email_client._extract_images 生成）
    可通过 .env 的 LLM_VISION_MODEL 指定模型（默认 qwen-vl-plus）。
    """
    if not images or os.getenv("LLM_PROVIDER", "qianwen") != "qianwen":
        return ""

    api_key = os.getenv(PROVIDERS["qianwen"]["api_key_env"])
    if not api_key:
        return ""

    client = OpenAI(api_key=api_key, base_url=PROVIDERS["qianwen"]["base_url"])
    vision_model = os.getenv("LLM_VISION_MODEL", "qwen-vl-plus")

    descriptions = []
    for i, img in enumerate(images, 1):
        try:
            b64 = base64.b64encode(img["raw"]).decode("utf-8")
            data_uri = f"data:{img['content_type']};base64,{b64}"
            resp = client.chat.completions.create(
                model=vision_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": (
                            "This image is from a business inquiry email. "
                            "Extract and describe any product names, model numbers, "
                            "specifications, quantities, prices, company names, or other "
                            "commercially relevant information visible in the image. "
                            "If there is no relevant business content, reply with 'no relevant content'."
                        )},
                    ],
                }],
                timeout=20,
            )
            desc = resp.choices[0].message.content.strip()
            if desc and "no relevant content" not in desc.lower():
                descriptions.append(f"[Image {i}]: {desc}")
                print(f"  [图片识别] 第{i}张: {desc[:60]}...")
        except Exception as e:
            print(f"  [图片识别] 第{i}张失败（跳过）: {e}")

    return "\n".join(descriptions)

