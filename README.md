# 外贸询盘自动回复 Demo

## 快速启动（5分钟）

### 1. 安装依赖
```bash
cd email-reply-demo
python -m venv venv
venv\Scripts\activate       # Windows
pip install -r requirements.txt
```

### 2. 配置环境变量
```bash
copy .env.example .env
```
用记事本打开 `.env`，填入：
- `QIANWEN_API_KEY` — 你的千问 API Key
- `IMAP_HOST` — 邮件服务器地址（如 mail.yourdomain.com）
- `IMAP_USER` — 邮箱账号
- `IMAP_PASS` — 邮箱密码
- `COMPANY_*` — 公司信息（写入邮件签名）

### 3. 填写产品价格
打开 `products.csv`，在 `price_usd` 列填入真实价格（目前是示例价格）。

### 4. 运行
```bash
python demo.py
```

## 项目结构

```
email-reply-demo/
├── demo.py            # 主入口，运行这个
├── email_client.py    # IMAP 邮件读取
├── ai_processor.py    # 千问 API（分类/解析/生成）
├── product_matcher.py # 产品匹配逻辑
├── database.py        # SQLite 存储
├── products.csv       # 产品表（需填真实价格）
├── .env               # 配置文件（不提交git）
├── .env.example       # 配置模板
└── requirements.txt   # 依赖
```

## 切换到智谱 AI

修改 `.env`：
```
LLM_PROVIDER=zhipu
LLM_MODEL=glm-4-flash
ZHIPU_API_KEY=你的智谱Key
```

## Demo 运行结果示例

```
============================================================
   外贸询盘自动回复系统 — Demo 模式
============================================================
✓ 数据库初始化完成
✓ 产品表加载完成，共 27 条产品

正在连接邮件服务器，拉取未读邮件（最多 5 封）...

📬 获取到 1 封未读邮件，开始处理...

============================================================
  发件人: john@miningco.com
  主  题: Inquiry for Carbide Buttons
============================================================

[1/4] 正在分类邮件...
  分类结果: VALID_INQUIRY
  分类原因: Customer asking for carbide button specifications and pricing

[2/4] 正在解析询盘内容...
  检测语言: en
  客户公司: ABC Mining Ltd.
  来自国家: Australia
  询盘产品数: 1 种
    [1] Spherical carbide buttons for DTH bit  |  2000 PCS  |  牌号: SS16C

[3/4] 正在匹配产品库...
  匹配到 2 个产品建议：
    ✓ Spherical Gear Inserts [SS16C]  USD 2.50/PCS  交期 25 天
    ✓ Spherical Gear Inserts [SS20C]  USD 2.30/PCS  交期 25 天

[4/4] 正在生成回复草稿...
────────────────────────────────────────────────────────────
  草稿主题: Re: Inquiry for Carbide Buttons - Quotation
────────────────────────────────────────────────────────────
Dear John,

Thank you for reaching out to Ruixin Tungsten Carbide...
[完整草稿]
────────────────────────────────────────────────────────────

  ✅ 草稿已保存  邮件ID=1  草稿ID=1
```
