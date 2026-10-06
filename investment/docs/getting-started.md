# 手把手：从 clone 到跑通一次回测

照着做完，会先在研究枢纽拉日线和 5 分钟 K、拟合三张研究套并启用，再在「历史回测」跑出调仓回测的累计收益。全程是本地研究与模拟，不连券商、不下实盘单。

对话 / Agent 才需要通义千问 key。这条路径**不用**配 `.env`。

命令在 macOS / Linux 终端里执行。仓库是整仓 `workspace_gaokm`，量化代码在子目录 `investment/`。下面每一步都先确认当前目录是 `investment`。

---

## 0. 开始前

| 需要 | 怎么确认 |
|------|----------|
| Git | `git --version` 能打出版本号 |
| Python 3.10 或更高 | `python3 --version` |
| 外网 | 研究枢纽要拉日线（约 600 个交易日）和 5 分钟 K |

Python 低于 3.10 时先装 3.10+，不要用系统自带的旧 `python`。

---

## 1. Clone

```bash
git clone git@github.com:KaimingGao/workspace_gaokm.git
cd workspace_gaokm/investment
```

没有 SSH key 时用 HTTPS：

```bash
git clone https://github.com/KaimingGao/workspace_gaokm.git
cd workspace_gaokm/investment
```

此时还没有 `data/watching.json` 和 `data/paper.json`。这两份在 `.gitignore` 里，clone 不会带下来。仓库里只有模板 `data/watching.example.json`（约 500 只，`sources` 为空）。

---

## 2. 虚拟环境与依赖

在 `investment/` 下：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -U pip
python3 -m pip install -r requirements.txt
```

做完后提示符前面应有 `(.venv)`。`pandas`、`akshare`、`lightgbm` 体积大，第一次安装常常要几分钟，等它自己结束。

以后每开一个新终端，都要重新进入目录并激活，否则会用到系统 Python，缺包装不上：

```bash
cd workspace_gaokm/investment
source .venv/bin/activate
```

`.venv/` 不要提交。

---

## 3. 先放一份小观察名单

回测读的是 `data/watching.json`，不是 example。调仓回测会为名单里**每一只**拉日线和 5 分钟 K。模板大约 500 只，第一次直接拿整池跑，下载和计算都会很久。

第一次只放 3 只。在 `investment/` 下执行（文件已存在时不要覆盖，见文末「已经初始化过」）：

```bash
cat > data/watching.json << 'EOF'
{
  "version": 1,
  "name": "default",
  "max_size": 500,
  "sources": [],
  "watchlist": ["600519", "600036", "000858"]
}
EOF
```

这三只是贵州茅台、招商银行、五粮液。`sources` 留空表示手动名单，刷新不会把别的股票加进来。

以后想改名单：改 `data/watching.json` 的 `watchlist`，或在 Web「数据中心」里改。不要改 `watching.example.json`，那是仓库模板。

---

## 4. 初始化纸面账户，并核对名单

```bash
bash scripts/setup_quant.sh
```

`watching.json` 已经存在时，脚本会打印 `skip: data/watching.json 已存在`，**不会**用 500 只模板覆盖。接着它会刷新名称（空 `sources` 只补名称），并在没有 `data/paper.json` 时从 `paper.example.json` 建一个空持仓的模拟账户。

核对：

```bash
python3 research/watching_run.py --show
python3 research/paper_run.py --status
```

`--show` 应列出 3 只（或带中文名）。`--status` 能打出净值即可，持仓为空是正常的。调仓回测不要求先有持仓。

---

## 5. 启动 Web

这个终端留着，不要关：

```bash
python3 run_web.py
```

终端会出现一块横幅，其中有：

```text
打开 http://127.0.0.1:8000
```

看到 `Uvicorn running` 后，用浏览器打开这个地址。根路径会进仪表盘。服务没起来时页面打不开。

端口被占用时，停掉旧进程，或换端口再开：

```bash
WEB_PORT=8001 python3 run_web.py
```

然后打开对应端口。第一次不必设 `WEB_RELOAD`。平时用默认 **8000**。

---

## 6. 拉数据

左侧点 **研究枢纽**（`/quant`）。页顶的「观察池分档」先不要点，那要等模型启用之后。往下找到两块缓存：

**日线**

1. 点 **增量补齐**（不要点「强更日 K」。强更是整窗重拉，仓坏了或要覆盖时再用）。
2. 它按观察池写入约 **600 个交易日**的日 K，不改名单、不写 5 分钟 K、不算 ŷ。
3. 3 只股票通常要几分钟。状态条离开「加载覆盖…」，**Missing** 变成 0，**Coverage** 能看到这 3 只。

**分钟线**（同一页再往下）

1. 点 **增量补齐**（不要点「强更 5m」或「东财补缺」）。
2. 它给观察池补 5 分钟 K。调仓回测的成交价、ŷ_τc 都读这份缓存。
3. 同样等到 **Missing** 为 0。3 只股票会比日线更久；约 500 只的整池会非常久，所以第一次只用 3 只。

缓存落在本地 `data/store/`，下次不用整池重拉。

---

## 7. 拟合模型

还在 **研究枢纽**。调仓回测的「模型 = 研究」读的是这里启用的研究套，不是页面上临时算出的草稿。每张卡都是：参数保持默认 → **拟合** → 系数表出来后点 **启用研究**。**启用执行**是给交易执行用的，第一次不要点。

拟合结束前「启用研究」是灰的。点 **状态** 能确认研究套已经落盘。

| 卡片 | 保持 | 作用 |
|------|------|------|
| **ŷ_oo** | Holdout `20`，训练窗 `600` | 主排序，标签是次日开盘相对今日开盘 |
| **ŷ_τc** | Holdout `20`，训练窗 `120` | 成交钟到收盘的副轴；要用上一步的 5 分钟 K |
| **ŷ_co** | Holdout `20`，训练窗 `600` | 隔夜缺口，会叠进调仓打分 |

三张都点完「启用研究」再往下。ŷ_τ30 及更后面的卡片默认折着，Tree 那几张也先不要拟合。

「观察池分档」用的是刚刚启用的研究套 ŷ_oo。第一次回测可以不跑它：下一步会把分档勾选全部取消。

---

## 8. 在页面上跑调仓回测

1. 左侧点 **历史回测**（地址是 `/replay`）。
2. 找到上面那一块标题 **调仓回测**。页面下面还有一块「做 T 回测」，也有一颗「跑回测」。第一次不要点下面那颗：做 T 默认对着模拟持仓，空仓不是这条路径。
3. 工具栏保持：
   - **时间** `09:30`
   - **窗口** `10`（最小就是 10 个交易日，填更小也会被抬回 10）
   - **模型** `研究`
   - **ŷ头** `Ridge`（用第 7 步启用的研究套。`Tree` 是另一套模型，第一次不要选）
4. **分档**里的 A、B、C **全部取消勾选**。页面默认勾着 A 和 B。勾着就会去读研究枢纽的分档报告；刚 clone 没有这份报告，回测会失败，提示先去研究枢纽跑「观察池分档」。
5. 点这一栏的 **跑回测**。

标题行会出现「回测中…」，并开始计时。日线和 5 分钟 K 应已在第 6 步进本地缓存；这里是按窗口重算。终端保持 `run_web.py` 不要停。

跑完后，这一栏的累计收益、超额、回撤、胜率、命中率不再是「—」，副标题也不再是「先跑回测」。成功结果写在 `data/last_portfolio_backtest.json`。之后刷新 `/replay` 会直接恢复这次结果，不会重跑；要重算再点一次「跑回测」。

数字只说明这 10 个交易日、这 3 只股票、当前规则下的模拟结果，不代表收益。

---

## 9. 想换成模板里的整池

确认小名单已经跑通之后，再换大池。下面会删掉当前 `data/watching.json`：

```bash
rm data/watching.json
python3 research/watching_run.py --init
python3 research/watching_run.py --show
```

`--init` 把 `watching.example.json` 复制成 `watching.json`。文件已经存在时它会报错并拒绝覆盖。换池之后要重新做第 6、7 步（整池拉数和拟合都会久很多），回测时分档仍然全部取消勾选。

---

## 已经初始化过时

`bash scripts/setup_quant.sh` 看到 `watching.json` / `paper.json` 已存在就会跳过，不会重置。

只想把名单收成 3 只：编辑 `data/watching.json`，把 `watchlist` 改成上面那三个代码，`sources` 保持 `[]`。并行的 `watchlist_names` 等数组长度如果对不上，刷新时会按新名单重写。然后：

```bash
python3 research/watching_run.py --refresh
python3 research/watching_run.py --show
```

确认输出里只有这 3 只，再从第 6 步拉数据。

---

## 常见问题

| 现象 | 处理 |
|------|------|
| `python3 --version` 低于 3.10 | 安装 Python 3.10+ 后重建 `.venv` |
| `No module named ...` | 当前终端没有 `source .venv/bin/activate`，或依赖没装完 |
| 页面打不开 | `run_web.py` 那个终端还在跑吗；端口是不是 8000 |
| 回测提示 `watching.json 不存在` | 回到第 3 步写文件，再跑 `setup_quant.sh` |
| 日线 / 分钟线 Missing 不掉 | 名单是不是仍约 500 只；外网是否通。第一次用「增量补齐」，不要用强更 |
| 启用研究是灰的 | 这张卡的「拟合」还没跑完，或失败了。看卡片状态条上的报错 |
| 回测提示无枢纽分档报告 | 分档 A/B/C 全部取消勾选后再点「跑回测」 |
| 回测像没读到新模型 | 拟合之后要点「启用研究」。只拟合不启用，历史回测仍用旧研究套 |
| 点了「跑回测」但像对着持仓 | 点的是下面「做 T 回测」。改点上面「调仓回测」 |
| 一直停在「回测中…」 | 先确认第 6 步 Missing 已是 0。名单如果仍是约 500 只，先收成 3 只 |
| 只想离线看测试能不能过 | `python3 evals/run_checklist.py --mock`。这不是一次真实行情回测 |
