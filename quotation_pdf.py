"""
报价单 PDF 生成器
用法：generate_quotation_pdf(draft, parsed_inquiry, products) -> bytes
"""
import os
import json
from datetime import datetime, timedelta
from fpdf import FPDF

# 字体候选路径：Windows → Linux（Docker 容器内安装 fonts-noto-cjk）
_FONTS = {
    # Windows
    "win_regular": r"C:/Windows/Fonts/msyh.ttc",
    "win_bold":    r"C:/Windows/Fonts/msyhbd.ttc",
    "win_simhei":  r"C:/Windows/Fonts/simhei.ttf",
    "win_arial":   r"C:/Windows/Fonts/arial.ttf",
    "win_arialbd": r"C:/Windows/Fonts/arialbd.ttf",
    # Linux / Docker (apt-get install fonts-noto-cjk)
    "noto_reg":    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "noto_bold":   "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "noto_reg2":   "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "noto_bold2":  "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "wqy":         "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
}

# ── 颜色常量 ─────────────────────────────────────────────────────────────────
_BLUE       = (13,  110, 253)
_DARK       = (30,   30,  30)
_GRAY       = (108, 117, 125)
_LIGHT_GRAY = (240, 242, 245)
_WHITE      = (255, 255, 255)
_GREEN      = (25,  135,  84)
_BORDER     = (200, 206, 212)


def _pick_font() -> tuple[str, str]:
    """返回 (regular_path, bold_path)，优先微软雅黑，降级至 Noto/WQY/内置"""
    f = _FONTS
    # Windows: 微软雅黑
    if os.path.exists(f["win_bold"]):
        return f["win_regular"], f["win_bold"]
    if os.path.exists(f["win_simhei"]):
        return f["win_regular"], f["win_simhei"]
    if os.path.exists(f["win_arialbd"]):
        return f["win_arial"], f["win_arialbd"]
    # Linux: Noto CJK（apt install fonts-noto-cjk）
    if os.path.exists(f["noto_bold"]):
        return f["noto_reg"], f["noto_bold"]
    if os.path.exists(f["noto_bold2"]):
        return f["noto_reg2"], f["noto_bold2"]
    # Linux: WQY Microhei（apt install fonts-wqy-microhei）
    if os.path.exists(f["wqy"]):
        return f["wqy"], f["wqy"]
    # 最终 fallback：FPDF2 内置 Helvetica（仅 ASCII）
    return "", ""


class _PDF(FPDF):
    def __init__(self, company_name: str, company_info: dict):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.company_name = company_name
        self.company_info = company_info
        self.set_margins(18, 18, 18)
        self.set_auto_page_break(auto=True, margin=20)

        reg, bold = _pick_font()
        if reg:
            # 字体名统一用 "F"（regular）和 "FB"（bold）
            self.add_font("F",  style="",  fname=reg)
            self.add_font("FB", style="",  fname=bold if bold else reg)
            self._use_custom_font = True
        else:
            # 无 CJK 字体时降级使用内置 Helvetica（仅 ASCII）
            self._use_custom_font = False

    def _f(self, size: float):
        """切换到 regular 字体"""
        self.set_font("F" if self._use_custom_font else "Helvetica", size=size)

    def _fb(self, size: float):
        """切换到 bold 字体"""
        self.set_font("FB" if self._use_custom_font else "Helvetica", style="B", size=size)

    # ── 页眉 ──────────────────────────────────────────────────────────────────
    def header(self):
        w = self.w - self.l_margin - self.r_margin

        # 顶部蓝色横条
        self.set_fill_color(*_BLUE)
        self.rect(0, 0, self.w, 2.5, "F")

        self.set_y(8)

        # 左：公司名
        self._fb(16)
        self.set_text_color(*_DARK)
        self.cell(w * 0.6, 8, self.company_name, ln=0)

        # 右："QUOTATION" 大字
        self._fb(22)
        self.set_text_color(*_BLUE)
        self.cell(w * 0.4, 8, "QUOTATION", align="R", ln=1)

        # 公司联系信息（小字）
        self._f(8)
        self.set_text_color(*_GRAY)
        info_parts = []
        if self.company_info.get("website"):
            info_parts.append(self.company_info["website"])
        if self.company_info.get("email"):
            info_parts.append(self.company_info["email"])
        if self.company_info.get("phone"):
            info_parts.append(self.company_info["phone"])
        if info_parts:
            self.cell(0, 5, "  |  ".join(info_parts), ln=1)

        # 分隔线
        self.set_draw_color(*_BORDER)
        self.set_line_width(0.3)
        self.line(self.l_margin, self.get_y() + 2,
                  self.w - self.r_margin, self.get_y() + 2)
        self.ln(6)

    # ── 页脚 ──────────────────────────────────────────────────────────────────
    def footer(self):
        self.set_y(-14)
        self._f(7.5)
        self.set_text_color(*_GRAY)
        self.cell(0, 5,
                  "This quotation is valid for 30 days. All prices are in USD unless otherwise stated.",
                  align="C", ln=1)
        self._f(7)
        self.cell(0, 4, f"Page {self.page_no()}", align="C")

    # ── 辅助：节标题 ──────────────────────────────────────────────────────────
    def section_title(self, text: str):
        self._fb(9)
        self.set_text_color(*_BLUE)
        self.cell(0, 6, text.upper(), ln=1)
        self.set_draw_color(*_BLUE)
        self.set_line_width(0.4)
        self.line(self.l_margin, self.get_y(),
                  self.w - self.r_margin, self.get_y())
        self.ln(3)
        self.set_text_color(*_DARK)

    # ── 辅助：KV 行 ──────────────────────────────────────────────────────────
    def kv(self, key: str, value: str, bold_val: bool = False):
        w = self.w - self.l_margin - self.r_margin
        self._f(9)
        self.set_text_color(*_GRAY)
        self.cell(38, 5.5, key + ":", ln=0)
        if bold_val:
            self._fb(9)
        self.set_text_color(*_DARK)
        self.cell(w - 38, 5.5, str(value or "—"), ln=1)


# ── 主函数 ───────────────────────────────────────────────────────────────────

def generate_quotation_pdf(draft: dict, parsed_inquiry: dict | None,
                            products: list) -> bytes:
    """
    生成报价单 PDF，返回字节流。
    draft: get_draft() 返回的 dict（含 subject, sender, email_id 等）
    parsed_inquiry: parse_inquiry() 返回的结构化询盘（可为 None）
    products: matched_products 列表
    """
    parsed = parsed_inquiry or {}

    # ── 公司信息 ────────────────────────────────────────────────────────────
    company_name = os.getenv("COMPANY_NAME", "Our Company")
    company_info = {
        "website": os.getenv("COMPANY_WEBSITE", ""),
        "email":   os.getenv("COMPANY_EMAIL", ""),
        "phone":   os.getenv("COMPANY_PHONE", ""),
        "desc":    os.getenv("COMPANY_DESC", ""),
    }

    # ── 报价单编号 & 日期 ────────────────────────────────────────────────────
    today      = datetime.now()
    qt_ref     = f"QT-{today.strftime('%Y%m%d')}-{draft.get('email_id', 0):04d}"
    qt_date    = today.strftime("%B %d, %Y")
    qt_valid   = (today + timedelta(days=30)).strftime("%B %d, %Y")

    # ── 询盘方信息 ────────────────────────────────────────────────────────────
    buyer_name    = parsed.get("customer_name") or ""
    buyer_company = parsed.get("company") or ""
    buyer_country = parsed.get("country") or ""

    # 如果 parsed_inquiry 是 JSON 字符串
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except Exception:
            parsed = {}

    reqs = parsed.get("products_requested") or []

    # ── 构建 PDF ──────────────────────────────────────────────────────────────
    pdf = _PDF(company_name, company_info)
    pdf.add_page()

    w = pdf.w - pdf.l_margin - pdf.r_margin

    # ── Ref / Date / Valid ──────────────────────────────────────────────────
    # 右对齐的元信息区块
    info_col_w = 55
    info_x = pdf.w - pdf.r_margin - info_col_w

    cur_y = pdf.get_y()
    pdf.set_xy(pdf.l_margin, cur_y)
    pdf._f(9)
    pdf.set_text_color(*_GRAY)
    pdf.cell(0, 5.5, f"Ref: {qt_ref}", ln=1)

    # 叠加右侧日期信息（回退 y，在右侧另画）
    pdf.set_xy(info_x, cur_y)
    pdf._f(8.5)
    pdf.set_text_color(*_GRAY)
    pdf.cell(info_col_w, 5, f"Date: {qt_date}", ln=1, align="R")
    pdf.set_xy(info_x, pdf.get_y())
    pdf.cell(info_col_w, 5, f"Valid until: {qt_valid}", ln=1, align="R")

    pdf.ln(4)

    # ── 买家信息 ─────────────────────────────────────────────────────────────
    pdf.section_title("Bill To")
    if buyer_name:
        pdf.kv("Contact", buyer_name, bold_val=True)
    if buyer_company:
        pdf.kv("Company", buyer_company, bold_val=True)
    if buyer_country:
        pdf.kv("Country", buyer_country)
    # 发件人邮箱
    sender_raw = draft.get("sender", "")
    import re
    m = re.search(r"<(.+?)>", sender_raw)
    buyer_email = m.group(1) if m else sender_raw.strip()
    if buyer_email:
        pdf.kv("Email", buyer_email)
    # 原始询盘主题
    orig_subj = draft.get("original_subject") or draft.get("subject", "")
    if orig_subj:
        pdf.kv("Re (inquiry)", orig_subj[:80])

    if parsed.get("delivery_deadline"):
        pdf.kv("Requested delivery", parsed["delivery_deadline"])
    if parsed.get("payment_terms") and parsed["payment_terms"] != "not mentioned":
        pdf.kv("Requested payment", parsed["payment_terms"])

    pdf.ln(5)

    # ── 产品报价表 ───────────────────────────────────────────────────────────
    pdf.section_title("Products & Pricing")

    # 列宽分配（总宽 w）
    cols = {
        "#":         8,
        "Product":   56,
        "Grade/Spec": 28,
        "MOQ":       16,
        "Unit":      12,
        "Unit Price\n(USD)": 24,
        "Lead Time\n(days)": 22,
    }
    # 剩余给 Notes
    notes_w = w - sum(cols.values())
    if notes_w > 5:
        cols["Notes"] = notes_w

    # 表头
    pdf.set_fill_color(*_BLUE)
    pdf.set_text_color(*_WHITE)
    pdf._fb(8)
    row_h = 7
    for col, cw in cols.items():
        # 多行表头：取第一行显示
        label = col.split("\n")[0]
        pdf.cell(cw, row_h, label, border=0, align="C", fill=True)
    pdf.ln(row_h)

    # 如果有双行表头（Unit Price / Lead Time），加第二行
    second_line = {col: col.split("\n")[1] for col in cols if "\n" in col}
    if second_line:
        pdf._f(7)
        for col, cw in cols.items():
            label = second_line.get(col, "")
            pdf.cell(cw, 4.5, label, border=0, align="C", fill=True)
        pdf.ln(4.5)

    # 数据行
    pdf._f(8.5)
    pdf.set_text_color(*_DARK)

    def _fmt_price(v):
        try:
            return f"$ {float(v):,.2f}" if v else "—"
        except Exception:
            return str(v)

    def _fmt_num(v, suffix=""):
        try:
            if v:
                return f"{int(v):,}{suffix}"
        except Exception:
            pass
        return "—"

    row_index = 0
    used_products = products if products else []

    # 如果没有匹配产品但 reqs 里有，生成占位行
    if not used_products and reqs:
        used_products = [
            {
                "product_name": r.get("description", "TBD"),
                "grade": r.get("grade_or_spec", ""),
                "moq": r.get("quantity", ""),
                "unit": r.get("unit", ""),
                "price_usd": r.get("target_price_usd", ""),
                "lead_time_days": "",
                "specs_summary": "",
            }
            for r in reqs
        ]

    if not used_products:
        # 空行占位
        used_products = [{"product_name": "— (Please specify product details)", "grade": "",
                          "moq": "", "unit": "", "price_usd": "", "lead_time_days": ""}]

    for p in used_products:
        row_index += 1
        fill = row_index % 2 == 0
        if fill:
            pdf.set_fill_color(*_LIGHT_GRAY)
        else:
            pdf.set_fill_color(*_WHITE)

        # 计算行高（产品名可能需要多行）
        name = str(p.get("product_name", "") or "")
        # 估算 product 列需要几行
        name_lines = max(1, len(name) // 28 + 1)
        rh = max(row_h, name_lines * 5.5)

        pdf.cell(cols["#"],       rh, str(row_index),            border=0, align="C", fill=fill)
        pdf.cell(cols["Product"], rh, name[:55],                 border=0, fill=fill)
        pdf.cell(cols["Grade/Spec"], rh, str(p.get("grade") or "")[:18], border=0, align="C", fill=fill)
        pdf.cell(cols["MOQ"],     rh, _fmt_num(p.get("moq")),   border=0, align="R", fill=fill)
        pdf.cell(cols["Unit"],    rh, str(p.get("unit") or "")[:6], border=0, align="C", fill=fill)
        pdf.cell(cols["Unit Price\n(USD)"], rh,
                 _fmt_price(p.get("price_usd")),                 border=0, align="R", fill=fill)
        pdf.cell(cols["Lead Time\n(days)"], rh,
                 _fmt_num(p.get("lead_time_days")),              border=0, align="C", fill=fill)
        if "Notes" in cols:
            specs = str(p.get("specs_summary") or "")[:30]
            pdf.cell(cols["Notes"], rh, specs,                   border=0, fill=fill)
        pdf.ln(rh)

    # 表格底部线
    pdf.set_draw_color(*_BORDER)
    pdf.set_line_width(0.3)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(6)

    # ── 付款条款 ─────────────────────────────────────────────────────────────
    pdf.section_title("Payment Terms")
    pdf._f(9)
    pdf.set_text_color(*_DARK)
    pdf.multi_cell(w, 5.5,
        "T/T: 30% deposit upon order confirmation, 70% balance before shipment.\n"
        "Other payment terms are subject to negotiation.",
        new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ── 备注 ──────────────────────────────────────────────────────────────────
    pdf.section_title("Notes")
    pdf._f(9)
    notes = [
        "All prices are FOB China unless otherwise stated.",
        "Lead time starts from receipt of deposit payment.",
        "Prices are subject to change based on raw material fluctuations.",
        "Samples are available upon request (sample cost may apply).",
    ]
    if parsed.get("special_requirements"):
        notes.append(f"Special requirement noted: {parsed['special_requirements'][:100]}")
    note_w = w - 6
    for note in notes:
        pdf.set_x(pdf.l_margin)
        pdf.cell(6, 5, "-", ln=0)
        pdf.multi_cell(note_w, 5, note, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(8)

    # ── 签名区 ────────────────────────────────────────────────────────────────
    sig_x = pdf.w - pdf.r_margin - 70
    pdf.set_xy(sig_x, pdf.get_y())
    pdf._f(9)
    pdf.set_text_color(*_GRAY)
    pdf.cell(70, 5.5, "Yours sincerely,", ln=1, align="C")
    pdf.set_xy(sig_x, pdf.get_y())
    pdf.ln(10)
    # 签名横线
    line_x = pdf.w - pdf.r_margin - 60
    pdf.set_draw_color(*_BORDER)
    pdf.set_line_width(0.4)
    pdf.line(line_x, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(3)
    pdf.set_xy(line_x, pdf.get_y())
    pdf._fb(9)
    pdf.set_text_color(*_DARK)
    pdf.cell(0, 5.5, company_name, ln=1)
    if company_info.get("email"):
        pdf.set_xy(line_x, pdf.get_y())
        pdf._f(8)
        pdf.set_text_color(*_GRAY)
        pdf.cell(0, 5, company_info["email"], ln=1)
    if company_info.get("phone"):
        pdf.set_xy(line_x, pdf.get_y())
        pdf.cell(0, 5, company_info["phone"], ln=1)

    return bytes(pdf.output())
