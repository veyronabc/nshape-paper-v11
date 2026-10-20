# N字二板模拟盘 v1.1（GitHub Pages）

这是一个可以部署到 GitHub 的 10 万模拟盘：

- 09:25 盘前筛选股票池
- 09:50 检查 09:30–09:45 是否涨超 3%
- 16:30 收盘复盘、断板卖出、更新净值
- 手机和其他电脑随时打开固定网址
- 不依赖本地电脑开机

当前线上执行：**v1.1 + 单笔 20% 仓位**。

## T+1 修正

当前线上执行按 A 股 T+1：

- 买入日当天不能卖出
- 买入日未封二板：T+2 收盘卖出
- 买入日封二板：继续持有连续板，断板日收盘卖出

按 T+1 修正后的回测：

| 模型 | 仓位 | 总收益 | 年化 | 最大回撤 | 夏普 |
|---|---:|---:|---:|---:|---:|
| **v1.1** | **20%** | **+31.02%** | **+12.32%** | **12.93%** | **1.01** |
| **v1.2-B** | **20%** | **+34.09%** | **+13.44%** | **4.74%** | **1.70** |

T+1 开盘卖是低回撤方案；T+2 收盘卖是收益更高的方案。

> 之前的 +33.49% 未完全按 T+1，已废弃，不再作为实盘参考。

## 双层候选池

网页会同时展示：

1. **v1.1 股票池**：按 v1.1 条件筛选，参与执行
2. **v1.2 精选池**：从 v1.1 待触发/等待回调中，再排除：
   - 首板换手率 10%–20%
   - 流通市值 100–200 亿

v1.2 精选池当前只做展示，不改变 v1.1 执行。

## 一、部署步骤

### 1. 新建 GitHub 仓库

建议新建一个 **Public** 仓库，例如：

```text
nshape-paper-v11
```

> Public 仓库可以免费使用 GitHub Pages。  
> 如果使用 Private 仓库，GitHub Pages 可能需要付费方案。

### 2. 上传本项目

把当前目录 `github_deploy` 里的所有文件上传到仓库根目录，保持目录结构：

```text
.github/workflows/paper.yml
engine/
public/
deploy_runner.py
paper_account_v11.json
requirements.txt
```

### 3. 开启 Actions 写权限

进入仓库：

`Settings > Actions > General > Workflow permissions`

选择：

```text
Read and write permissions
```

保存。

### 4. 开启 GitHub Pages

进入：

`Settings > Pages > Build and deployment`

Source 选择：

```text
GitHub Actions
```

### 5. 手动运行一次

进入：

`Actions > N字二板模拟盘 > Run workflow`

Stage 先选择：

```text
preopen
```

运行完成后，Pages 会生成固定网址：

```text
https://<你的GitHub用户名>.github.io/<仓库名>/
```

例如：

```text
https://michael.github.io/nshape-paper-v11/
```

## 二、自动运行时间

GitHub Actions 使用 UTC 时间，已配置为：

| 北京时间 | UTC | 阶段 |
|---|---|---|
| 09:25 | 01:25 | 盘前筛选 |
| 09:50 | 01:50 | 触发买入 |
| 16:30 | 08:30 | 收盘复盘 |

周一至周五运行。

注意：

- GitHub 免费版 cron 可能延迟 5–15 分钟
- 第一次运行会拉取最近 90 天数据，可能需要几分钟
- 后续会使用 Actions 缓存，速度更快

## 三、网页内容

固定网址会显示：

- 总资产、累计收益、现金、持仓数、最大回撤
- 第一步：股票池（待触发/等待回调/淘汰原因）
- 第二步：持仓池
- 第三步：今日复盘
- 历史交易
- 净值曲线

## 四、项目文件

| 文件 | 用途 |
|---|---|
| `engine/v11_engine.py` | v1.1 三阶段引擎 |
| `engine/paper_engine.py` | 数据与信号底层 |
| `engine/preopen_pool.py` | 盘前股票池 |
| `engine/miana_api.py` | miana 数据接口 |
| `engine/bootstrap_cache.py` | GitHub Actions 行情缓存 |
| `deploy_runner.py` | Actions 执行入口 |
| `paper_account_v11.json` | 模拟账户状态 |
| `public/index.html` | 静态网页 |
| `public/state.json` | 网页读取的账户快照 |
| `.github/workflows/paper.yml` | 自动运行配置 |

## 五、常见问题

### 网页 404

- 检查 `Settings > Pages` 是否选择了 GitHub Actions
- 第一次部署完成前会 404
- 检查 Actions 是否成功

### Actions 失败

- 检查 `Settings > Actions > General` 是否允许读写
- 检查数据源是否临时不可用
- 失败后会自动重试 3 次，每次间隔 5 分钟

### 股票池为空

- 只有交易日 09:25 之后才会生成
- 可以在 Actions 里手动运行 `preopen`
- 查看 `public/state.json` 中的 `stock_pool`

### 这是真实交易吗

不是。  
这是本地/云端模拟盘，不会向券商发送真实委托。

## 六、隐私提示

Public 仓库会公开：

- 策略规则
- 回测结果
- 模拟持仓和交易记录

如果不想公开策略，可以使用：

- Private 仓库 + 付费 GitHub Pages
- 或部署到自己的云服务器

## 七、风险提示

- 参数为样本内筛选，存在过拟合风险
- 分支 B 样本较少
- 未模拟涨停排队、封单不足、炸板
- 模拟盘不等于实盘收益
- 本工具仅用于策略研究，不构成投资建议
