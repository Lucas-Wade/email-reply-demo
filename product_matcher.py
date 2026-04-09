"""
通用产品匹配模块（行业无关）
CSV 只需要 product_name 列，其余列均为可选。
行业专属词典从与 products.csv 同目录的 synonyms.json 加载（若存在）。
"""
import csv
import json
import os
import re

_PRODUCTS: list[dict] = []

# 可选：用户自定义的同义词扩展（空字典 = 仅凭关键词重叠匹配）
SYNONYMS: dict[str, list[str]] = {}

# 可选：产品 display_name → 强区分词集合（空字典 = 不使用）
CATEGORY_HINTS: dict[str, set] = {}


# ── CSV 加载 ─────────────────────────────────────────────────────────────────

def _load_synonyms(csv_path: str):
    """从与 CSV 同目录的 synonyms.json 加载同义词和类别区分词（若文件存在）"""
    global SYNONYMS, CATEGORY_HINTS
    syn_path = os.path.join(os.path.dirname(os.path.abspath(csv_path)), "synonyms.json")
    if not os.path.exists(syn_path):
        return
    try:
        with open(syn_path, encoding="utf-8") as f:
            data = json.load(f)
        SYNONYMS = data.get("synonyms", {})
        raw_hints = data.get("category_hints", {})
        CATEGORY_HINTS = {k: set(v) for k, v in raw_hints.items()}
        print(f"  [产品库] 已加载同义词 {len(SYNONYMS)} 条、类别区分词 {len(CATEGORY_HINTS)} 类（来源: {syn_path}）")
    except Exception as e:
        print(f"  [产品库] synonyms.json 加载失败（不影响主流程）: {e}")


def load_products() -> list[dict]:
    global _PRODUCTS
    path = os.getenv("PRODUCTS_CSV", "products.csv")
    if not os.path.exists(path):
        _PRODUCTS = []
        print(f"  [产品库] 文件不存在: {path}，请在设置页上传产品 CSV")
        return _PRODUCTS
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        _PRODUCTS = list(reader)
    _load_synonyms(path)
    print(f"  [产品库] 已加载 {len(_PRODUCTS)} 条产品（来源: {path}）")
    return _PRODUCTS


def reload_products() -> list[dict]:
    """运行时热重载，上传新 CSV 后调用"""
    return load_products()


def get_products() -> list[dict]:
    return _PRODUCTS


# ── 产品字段通用 getter（兼容多种列名）─────────────────────────────────────

def _get(p: dict, *keys, default="") -> str:
    """依次尝试多个列名，返回第一个非空值"""
    for k in keys:
        v = p.get(k, "").strip()
        if v:
            return v
    return default


def _get_display_name(p: dict) -> str:
    return _get(p, "display_name", "product_name", "name")


def _get_grade(p: dict) -> str:
    return _get(p, "grade", "model", "spec", "variant", "sku")


def _get_description(p: dict) -> str:
    return _get(p, "application", "description", "desc", "usage", "note")


def _build_specs_summary(p: dict) -> str:
    """从非空字段动态构建规格摘要，兼容任意行业 CSV"""
    parts = []
    spec_fields = [
        ("grade",           "Grade"),
        ("model",           "Model"),
        ("spec",            "Spec"),
        ("density",         "Density"),
        ("hardness_hra",    "Hardness HRA"),
        ("hardness_hrc",    "Hardness HRC"),
        ("bending_strength_mpa", "TRS MPa"),
        ("grain_size_um",   "Grain μm"),
        ("material",        "Material"),
        ("size",            "Size"),
        ("weight",          "Weight"),
        ("voltage",         "Voltage"),
        ("power",           "Power"),
        ("capacity",        "Capacity"),
        ("color",           "Color"),
    ]
    for key, label in spec_fields:
        val = p.get(key, "").strip()
        if val and val not in ("-", "N/A", "n/a", ""):
            parts.append(f"{label}: {val}")
    return " | ".join(parts) if parts else ""


# ── 匹配算法 ─────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", text.lower())


def _expand_query(tokens: list[str]) -> set[str]:
    expanded = set(tokens)
    for t in tokens:
        if t in SYNONYMS:
            expanded.update(SYNONYMS[t])
    return expanded


def _score(product: dict, description: str, grade_hint: str) -> float:
    tokens = _tokenize(description)
    query_expanded = _expand_query(tokens)
    score = 0.0

    # 1. 型号/牌号精确匹配（最高权重）
    grade = _get_grade(product)
    if grade_hint and grade:
        g, pg = grade_hint.strip().upper(), grade.upper()
        if pg == g:
            score += 50
        elif g in pg or pg in g:
            score += 25

    # 2. display_name 词汇重叠
    display_tokens = set(_tokenize(_get_display_name(product)))
    name_overlap = query_expanded & display_tokens
    score += len(name_overlap) * 8

    # 3. CATEGORY_HINTS（用户自定义区分词，空时跳过）
    cat_hints = CATEGORY_HINTS.get(_get_display_name(product), set())
    if cat_hints:
        score += len(query_expanded & cat_hints) * 6

    # 4. 产品描述/应用场景关键词重叠
    prod_kw = (
        set(_tokenize(product.get("product_name", "")))
        | set(_tokenize(product.get("category", "")))
        | set(_tokenize(_get_description(product)))
    )
    score += len(query_expanded & prod_kw) * 2

    return score


def match_products(products_requested: list, top_n: int = 3) -> list:
    if not _PRODUCTS:
        load_products()
    if not _PRODUCTS:
        return []

    results = []
    for req in products_requested:
        description = req.get("description", "")
        grade_hint  = req.get("grade_or_spec", "") or ""
        quantity    = req.get("quantity")

        scored = [(s, p) for p in _PRODUCTS
                  if (s := _score(p, description, grade_hint)) > 0]
        scored.sort(key=lambda x: -x[0])

        # 按 display_name 去重，每种产品类型最多取一条
        seen: dict = {}
        deduped = []
        for s, p in scored:
            name = _get_display_name(p)
            if name not in seen:
                seen[name] = True
                deduped.append((s, p))
            if len(deduped) >= top_n:
                break

        for s, p in deduped:
            results.append({
                "product_code":        p.get("product_code", ""),
                "product_name":        _get_display_name(p),
                "grade":               _get_grade(p),
                "category":            p.get("category", ""),
                "unit":                p.get("unit", "pcs"),
                "moq":                 _safe_int(p.get("moq", "1")),
                "price_usd":           _safe_float(p.get("price_usd", "0")),
                "lead_time_days":      _safe_int(p.get("lead_time_days", "0")),
                "specs_summary":       _build_specs_summary(p),
                "application":         _get_description(p),
                "inquiry_description": description,
                "requested_quantity":  quantity,
                "match_score":         round(s, 1),
            })

    return results


def _safe_int(v, default=0) -> int:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return default


def _safe_float(v, default=0.0) -> float:
    try:
        return float(str(v).strip())
    except Exception:
        return default
