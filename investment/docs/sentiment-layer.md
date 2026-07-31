# 舆情与另类数据（Sentiment / Alt-Data）

[← 文档索引](README.md) · 数据入口见 [data-layer.md](data-layer.md) · 因子侧见 [strategy-layer.md](strategy-layer.md) · [quant.md](quant.md)

**目标**：把「中东局势紧张」这类**模糊叙事**，变成模型可读的数值（如地缘 / 情绪风险分 0～1），再作为 **另类因子** 进入研究或（远期）Alpha。

本仓库 **已有**：个股资讯**标题**拉取（`news` Skill · AkShare）；观察页舆情面板（标题缓存 · 规则情绪徽章 · 页内扫描告警）。  
**没有**：金融 NLP 情感模型、地缘风险分历史面板、自动写入 `score_bars`、外推送（邮件/钉钉）。

LLM 可**解读** `news` 返回的标题；**不得编造**未出现在 JSON 里的涨跌归因数字。舆情数值因子若落地，须由确定性模块计算，与 score/stance 同一契约。观察页规则分**仅展示与提醒**，**不**改 stance。

---

## 在架构中的位置

```text
外部资讯源（标题 / 正文 / 社媒 …）
        ↓
   采集 · 关键词过滤 ·（可选）NLP 打分
        ↓
   情绪/风险分（另类因子）──► 特征表 / 研究模型
        ↓
   可选：并入 Alpha 权重或 Risk 输入（人工合并配置）
```

| 层 | 关系 |
|----|------|
| [data-layer](data-layer.md) | 资讯属另类数据源；当前按需拉取、弱缓存、非 PIT 面板 |
| Alpha / [strategy-layer](strategy-layer.md) | 成熟形态：风险分作为特征列，与价量/基本面一起训练或加权 |
| [risk-layer](risk-layer.md) | 高舆情风险 → 降仓 / 提高风险分（防守侧） |
| 投顾 | `news` 供 LLM 写「资讯观察」；**不参与** `stance` 主算 |

---

## 成熟流程：新闻 → 量化因子

### 1. 采集与过滤

- 拉标题（或正文）；先用关键词收窄（如「原油 / 中东 / OPEC / 石油」），减少噪声与算力。  
- 本仓库今天：`skills/news/engine.py` → `stock_news_em`，按标的名/代码查询，输出 `title` / `time` / `source` / `url`。

### 2. NLP 打分（研究示意，未入库）

常用路径：开源中文情感模型（如 `transformers` pipeline）对标题批量推断，再聚合：

```text
risk_score ≈ 负面标题数 / 标题总数    ∈ [0, 1]
无新闻 → 中性 0.5（或缺失标记，勿假装有信号）
```

示意（文档级，**不**加入默认 `requirements.txt`）：

```python
# 研究用草图 —— 非生产依赖
# pip install transformers torch  # 仅实验环境
from transformers import pipeline

analyzer = pipeline(
    "sentiment-analysis",
    model="uer/roberta-base-finetuned-jd-binary-chinese",
)

def geopolitical_risk_score(news_titles: list[str]) -> float:
    if not news_titles:
        return 0.5
    results = analyzer(news_titles)
    neg = sum(1 for r in results if "negative" in str(r.get("label", "")).lower())
    return neg / len(news_titles)
```

通用电商情感模型对财经术语不敏感；进阶应：**金融语料标注 → fine-tune**，或换金融领域模型。

### 3. 时效衰减

突发权重高于旧闻，例如按发布时间指数衰减后再加权平均，避免三天前标题与今日突发事件同权。

### 4. 写入特征表（研究）

| 日期 | 标的 | 价量/资金… | **地缘或情绪风险分** | Label（如未来 N 日收益） |
|------|------|------------|----------------------|---------------------------|
| … | 601857 | … | 0.80 | … |

再交给 XGBoost / LSTM 等**研究模型**学非线性关系。  
生产是否并入 `signal_config`：**人工 diff 合并**，与因子 IC 建议同一原则。

---

## 本仓库对照

| 能力 | 现状 |
|------|------|
| 个股新闻标题 | **有** · `news` Skill |
| 观察页标题面板 | **有** · `/watching` · `GET /api/watching/sentiment*` · 缓存 `data/store/news/` |
| 关键词主题池（宏观/地缘） | **无**（仅跟股票查询串） |
| 规则情绪分（利好/利空词） | **有** · `core/sentiment.score_headlines` · 词典 `data/sentiment_lexicon.json` |
| 情感 / 地缘 NLP risk_score | **无** |
| 时间衰减聚合 | **无** |
| 历史舆情面板 + PIT | **无**（资讯实时拉、不作回测面板） |
| 观察页内扫描告警 | **有** · `schedule kind=sentiment_scan` · 无外推送 |
| 进入 `score_bars` / stance | **否**（解读层可选；观察规则分不进主算） |
| transformers / torch | **非**默认依赖 |

`quant.md` 因子表「另类 / 事件」行仍为未接入研究特征；本页为演进说明书。

---

## 演进建议（与数据层一致）

```text
① 稳定 news 标题质量与缓存（可选落盘）—— 观察面板已落地
② 主题过滤 + 规则情绪（关键词利空/利好计数）—— 观察徽章已落地
③ 可选：实验目录跑 NLP / 微调；产出 risk_score 序列做 IC
④ OOS 验证后再考虑并入研究特征或 Risk 输入；不自动改 stance
⑤ 宏观「地缘」需独立新闻源与主题词典，不能只靠单票 stock_news_em
⑥ 外推送（邮件/钉钉）远期；当前仅页内告警条
```

**合规与预期**：舆情因子噪声大、易被标题党污染；只能作辅助特征，不能单独构成「保证涨跌」话术。

---

## 相关代码速查

| 能力 | 路径 |
|------|------|
| 资讯拉取与 normalize | `skills/news/engine.py` · `build_news` |
| Skill 入口 | `skills/news/handler.py` |
| 观察舆情聚合 | `core/sentiment.py` |
| 调度扫描 | `core/schedule_jobs.run_sentiment_scan` |
| Web API | `web/routers/watching.py` · `/api/watching/sentiment*` |
| 数据层中的位置 | [data-layer.md](data-layer.md) |

```bash
python3 -c "from skills.news.handler import NewsHandler; print(NewsHandler().execute({'parameters':{'stock_code':'茅台','limit':5}}))"
```

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [data-layer.md](data-layer.md) | 另类数据在五模块中的位置 |
| [strategy-layer.md](strategy-layer.md) | 因子如何进策略 |
| [risk-layer.md](risk-layer.md) | 高风险分可作防守输入 |
| [skills.md](skills.md) | `news` 与买入决策的关系（不参与 stance 主算） |
| [quant.md](quant.md) | 因子表 · `alt_sentiment` 已小权重接入 |
| [roadmap.md](roadmap.md) | `news` 能力画像 |
