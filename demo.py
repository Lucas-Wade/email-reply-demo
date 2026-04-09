"""
外贸询盘自动回复 Demo
运行方式: python demo.py
"""
import sys
import os
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
from dotenv import load_dotenv

load_dotenv()

from database import init_db, save_email, save_draft, update_email_status
from email_client import fetch_unread_emails
from ai_processor import classify_email, parse_inquiry, generate_draft
from product_matcher import load_products, match_products

SEPARATOR = "=" * 60


def process_email(raw: dict) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  发件人: {raw['sender']}")
    print(f"  主 题: {raw['subject']}")
    print(f"  时 间: {raw['received_at']}")
    print(SEPARATOR)

    # ── Step 1: 分类 ──────────────────────────────────────────
    print("\n[1/4] 正在分类邮件...")
    classification = classify_email(raw["subject"], raw["sender"], raw["body_text"])
    category = classification.get("category", "other")
    reason = classification.get("reason", "")
    print(f"  分类结果: {category.upper()}")
    print(f"  分类原因: {reason}")

    if category != "valid_inquiry":
        print(f"\n  ⏭  跳过（非有效询盘），已标记为 [{category}]")
        save_email(
            uid=raw["uid"], subject=raw["subject"], sender=raw["sender"],
            received_at=raw["received_at"], body_text=raw["body_text"],
            language="unknown", category=category,
        )
        return

    # ── Step 2: 解析询盘内容 ───────────────────────────────────
    print("\n[2/4] 正在解析询盘内容...")
    parsed = parse_inquiry(raw["body_text"])
    language = parsed.get("language", "en")
    print(f"  检测语言: {language}")
    print(f"  客户名称: {parsed.get('customer_name') or '未识别'}")
    print(f"  客户公司: {parsed.get('company') or '未识别'}")
    print(f"  来自国家: {parsed.get('country') or '未识别'}")
    print(f"  紧急程度: {parsed.get('urgency', 'medium')}")
    products_requested = parsed.get("products_requested") or []
    print(f"  询盘产品数: {len(products_requested)} 种")
    for i, p in enumerate(products_requested, 1):
        qty = f"{p.get('quantity')} {p.get('unit', 'PCS')}" if p.get("quantity") else "数量未指定"
        print(f"    [{i}] {p.get('description', '未描述')}  |  {qty}  |  牌号: {p.get('grade_or_spec') or '未指定'}")

    # ── Step 3: 产品匹配 ───────────────────────────────────────
    print("\n[3/4] 正在匹配产品库...")
    matched = match_products(products_requested, top_n=2)
    if matched:
        print(f"  匹配到 {len(matched)} 个产品建议：")
        for m in matched:
            print(f"    ✓ {m['product_name']} [{m['grade']}]  USD {m['price_usd']:.2f}/{m['unit']}  交期 {m['lead_time_days']} 天")
    else:
        print("  ⚠  未找到匹配产品，将生成通用询价回复")

    # ── Step 4: 生成草稿 ───────────────────────────────────────
    print("\n[4/4] 正在生成回复草稿...")
    draft = generate_draft(parsed, matched)
    draft_subject = draft.get("subject", f"Re: {raw['subject']}")
    draft_body = draft.get("body", "")

    print(f"\n{'─'*60}")
    print(f"  草稿主题: {draft_subject}")
    print(f"{'─'*60}")
    print(draft_body)
    print(f"{'─'*60}")

    # ── 持久化 ─────────────────────────────────────────────────
    email_id = save_email(
        uid=raw["uid"], subject=raw["subject"], sender=raw["sender"],
        received_at=raw["received_at"], body_text=raw["body_text"],
        language=language, category=category,
    )
    draft_id = save_draft(email_id, draft_subject, draft_body, matched)
    update_email_status(email_id, "drafted")

    print(f"\n  ✅ 草稿已保存  邮件ID={email_id}  草稿ID={draft_id}")


def main():
    print("=" * 60)
    print("   外贸询盘自动回复系统 — Demo 模式")
    print("=" * 60)

    # 初始化数据库
    init_db()
    print("✓ 数据库初始化完成")

    # 加载产品表
    products = load_products()
    print(f"✓ 产品表加载完成，共 {len(products)} 条产品")

    # 拉取邮件
    print("\n正在连接邮件服务器，拉取未读邮件（最多 5 封）...")
    try:
        emails = fetch_unread_emails(max_count=5)
    except ValueError as e:
        print(f"\n❌ 邮件配置错误: {e}")
        print("请复制 .env.example 为 .env 并填写 IMAP 配置")
        return
    except Exception as e:
        print(f"\n❌ 邮件连接失败: {e}")
        return

    if not emails:
        print("\n📭 没有未读邮件")
        return

    print(f"\n📬 获取到 {len(emails)} 封未读邮件，开始处理...\n")

    for raw in emails:
        try:
            process_email(raw)
        except Exception as e:
            print(f"\n❌ 处理邮件时出错: {e}")
            import traceback
            traceback.print_exc()

    print(f"\n{SEPARATOR}")
    print("  全部处理完成")
    print(SEPARATOR)


if __name__ == "__main__":
    main()
