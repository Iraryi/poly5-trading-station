# 模型训练台 1.0.1 改动说明（Claude Code 修改，2026-10-22）

本文件写给后续接手的 Codex / 开发者。改动只涉及 `trainstation/`，没有触碰交易台、下单路径、凭据格式或数据库结构。

## 用户反馈的问题

使用 `POLY5Trainer.exe --autostart` 后界面长时间“完全不动”，AI 监督看起来从未起作用。

## 根因

1. **AI 监督每次都失败，且失败被吞掉。**
   - 配置的模型 `deepseek-flash`（DeepSeek-V4.1-Flash）是推理模型，先把输出 token 用在 `reasoning_content` 上。
   - 旧代码 `max_tokens=600` → 600 个 token 全部用于推理，`content` 为空字符串，`finish_reason=length` → `json.loads('')` 报错。
   - 错误状态只写进 `state.json` 的 `ai_status`，约 10 秒后等待循环再次调用 `review()`，由于配额间隔未到，它返回“等待下次周期”，覆盖了错误。失败也没有写进 `ai/`，因此 `ai/` 里只有 `quota.json`，没有任何 `latest.json` / `review-*.json`。
   - 已用真实 Key 复现：`max_tokens=600` → content 为空；`max_tokens=4000` 加 `response_format=json_object` → 返回合法 JSON，用了 451 个 completion token。
2. **“不动”本身是设计行为，但界面没有显示进度。** 每批数据评估满 48 个候选后，必须新增 12 个有标签市场（约 1 小时，每个 5 分钟市场 1 个）才开始下一代；界面只显示固定文字和 100% 进度条。
3. 等待期间发给 AI 的摘要只有代数和市场数，没有任何模型指标，AI 无从给出有效建议。

4. **后台 worker 会因一次 Windows 文件占用直接退出（1.0.0 已存在）。** worker 每秒用 `atomic_json`（先写 `.tmp` 再 `os.replace`）写 `state.json`，GUI 每 0.5 秒读取这个文件。读的那一刻正好赶上 replace，Windows 就会抛 `PermissionError: [WinError 5] 拒绝访问 state.tmp -> state.json`。异常落到 `runner.run` 最外层 except，整个训练停止。杀毒软件或索引服务短暂打开文件也会触发。

## 改动

### `poly5/common.py`（与交易台共用）
- `atomic_json` 遇到 `PermissionError` 时，每 50ms 重试 `os.replace`，最多 40 次（约 2 秒），仍失败才抛出。其余行为不变。交易台 `tests/` 121 项测试通过，但已安装的交易台 exe 没有重新构建。

### `trainstation/supervisor.py`
- `max_tokens` 从 600 提到 `MAX_TOKENS=4000`；请求带 `response_format: {"type":"json_object"}`。若兼容服务返回 HTTP 400，会去掉该字段重试一次（仍只计一次配额）。
- 超时从 25 秒延长到 120 秒（推理模型较慢）。
- `content` 为空时明确报错：`模型未返回正文（finish_reason=...）`。HTTP 错误附带响应体前 200 字。
- 每次实际调用（无论成功或失败）都追加到 `ai/calls.jsonl`，最近一次写入 `ai/last.json`。
- 配额等待期间，状态文字不再是固定的“等待下次周期”，改为：`AI 监督 · N 分钟间隔 · 上次 HH:MM 成功：建议 X / 失败：原因 · 下次约 HH:MM`。
- 配额、间隔、白名单、“不执行代码”等原有约束未变。

### `trainstation/runner.py`（只改等待分支）
- 阶段文字改为 `持续采集 · 新标签市场 x/12，满后开启下一批进化`。
- 等待期发给 AI 的 summary 增加 `new_labelled_markets` 和 `champion`（family、params、metrics、前向评分）。

### `trainstation/gui.py`
- 采集线程出错时，在连接状态行显示 `采集错误：...`（此前 `collector.error` 写进了 state，但界面不显示）。
- 连接状态标签的 tooltip 显示完整的 AI 状态。

### `trainstation/test_trainer.py`
- 原 AI 配额测试补充断言：请求带 `max_tokens>=4000` 和 `response_format`。
- 新增 `test_ai_empty_reasoning_reply_is_recorded_and_shown_while_waiting`、`test_state_write_survives_transient_windows_lock`。
- 8 项测试全部通过：`python -m unittest trainstation.test_trainer`。

## 构建与部署（已完成）

- 构建（在 PowerShell 中，PATH 去掉 Git/mingw，在 `D:\POLYMARKET\work` 下执行）：`python -m PyInstaller POLY5Trainer.spec --noconfirm --clean --distpath D:/POLYMARKET/work/trainer-1.0.1b-dist --workpath D:/POLYMARKET/work/build-trainer-1.0.1b`。
- **必须在 PowerShell 中构建，不能在 Git Bash 中构建。** Git Bash 的 PATH 里有 `C:\Program Files\Git\mingw64\bin`，PyInstaller 会把 Git 自带的 MinGW 版 `libssl-3-x64.dll` / `libcrypto-3-x64.dll` / `libffi-8.dll` 打进包里，而不是用 Python（`C:\Espressif\tools\python\DLLs`）自己的。这样打出的包 HTTPS（httpx）还能用，但 websockets 连接 `wss://ws-live-v2.polymarket.com/ws` 会持续报 `参考行情连接中断：SSLError`。1.0.1 的第一版就是因此出错。构建后要核对 `_internal` 里这几个 DLL 的哈希与 Python `DLLs` 目录一致。最终安装的版本 `work/trainer-1.0.1b-dist` 与原 1.0.0 相比，只有 `POLY5Trainer.exe` 和 `base_library.zip` 不同。
- **注意：** PyInstaller 遇到有语法错误的模块不会报错，只会在 `warn-*.txt` 里写 `invalid module named ...`，exe 照样生成，运行时才报 `No module named`。构建后请检查 `warn-POLY5Trainer.txt` 中是否有 `trainstation` / `poly5` 的 invalid module，或者先跑 `python -m compileall trainstation`。
- **不要只替换 `POLY5Trainer.exe`：** 新 exe 与旧 `_internal/base_library.zip` 不匹配，启动会直接退出（exit 1）。必须用整个 dist 目录覆盖安装目录。
- 覆盖安装前，必须确认所有 `POLY5Trainer.exe` 进程都已退出（先写 `stop.json` 让 worker 保存并停止，再关闭 GUI）。否则 exe 被占用、复制失败，安装目录会变成新旧文件混合。复制后要逐个文件对比哈希。
- 新 exe 的 `--smoke-test` 已通过（临时目录 `work/trainer-1.0.1b-smoke`，12 个候选，正常停止）。
- 已覆盖安装到 `%LOCALAPPDATA%\Programs\POLY5Trainer`，并带 `--autostart` 重启；训练数据、模型、进度均保留在 `D:\POLYMARKET\trainer`。旧 exe 备份在 `work/POLY5Trainer-1.0.0.exe.bak`。
- **未做：** 没有重新生成 NSIS 安装包、`manifest.json`、源码 zip 或 `outputs/validation`，安装目录中的 `manifest.json` 仍是 1.0.0 的哈希，`finalize_trainer.py verify` 会报不一致。正式发版时请按原流程重新 stage/打包，并把版本号改为 1.0.1。

## 可选的后续改进（未实施）

- AI 建议目前只影响下一代 6 个候选中第 5、6 个的算法族（`advice['family']`），`concern` / `suggestion` 文本没有被程序使用。若要让 AI “互动”更深，可以把建议映射到受白名单约束的参数范围，但必须仍由程序判官评分决定。
- 48 候选 / 12 新市场的节奏是防止反复刷旧验证集的设计，不建议简单调低。

---

# 1.1.0 训练逻辑优化（Claude Code，2026-10-22）

先在真实快照上离线实验，量化后才改代码。实验脚本在 `D:\POLYMARKET\work\trainer-1.1-experiments\`（`exp1.py` 模型对比，`exp2.py` 判官排名能否预测审计集，`exp3.py` 两个快照上的稳健性检验和“用近期数据训练”的价值）。所有显著性检验都是按市场分组的 bootstrap（2000 次，95% CI），因为同一市场的 240s/270s 样本不独立。

## 实验结论（late 任务，审计集 Brier，越低越好）

| 方案 | 快照 c7c00 | 快照 c9f51 | 结论 |
|---|---|---|---|
| 1.0 现状（forest 100 棵、深度 10，8 个原始特征） | 0.0530 | 0.0542 | 基准 |
| + 5 个派生特征 | 0.0519 | 0.0529 | 两个快照都提升约 0.0011（CI 刚好跨 0），log-loss 稳定下降约 5–8%，**采纳** |
| + 用 train+val 训练（更近的数据） | **0.0496** | 0.0507 | 提升 0.0024，c7c00 上显著，**采纳**（以全量重训的形式） |
| 300 棵 / max_features 0.5 | 0.0534 | 0.0548 | 无益，不采纳 |
| 只用 z 的逻辑回归（BS 数字期权“物理模型”） | 0.0562 | — | 更差，不采纳 |
| HGB + 单调约束 / 模型平均 | 0.0535 / 0.0534 | — | 持平，不采纳 |

`exp2.py`：判官的验证集排名与审计集排名的 Spearman 相关为 0.69–0.95，判官本身可靠。但每个数据批次的冠军审计分只在第 1–4 代有改善，之后停滞，后几代的“进步”是噪声。

## 发现的结构性问题

1. **新采集的实时数据从未进入训练。** `split_rows` 按时间切成 60/20/20，实时样本最新，永远落在审计尾段（快照 c7c00：审计集 923 条中 95 条是 live，train/val 中为 0）。所以“继续采集→进化”实际上只是在更新审计集。
2. **每个新数据批次都从随机参数重新开始**（`prior` 按 scope 查询，新 scope 为空），每小时重复发现同一个深度 10 的森林。
3. 特征只有原始 bps 值，没有把“距基准的距离 / 剩余时间内的预期波动”这一结算核心量显式给模型。

## 改动

### `trainstation/data.py`
- 新增 `DERIVED`、`derived(x)`、`design(task)`、`vector(x, features)`。派生特征在训练和预测时由已存特征现算，**无需迁移数据库或快照**：
  - `z_spot = spot_open_bps / (max(vol_60s_bps, 0.5) * sqrt(剩余秒数))`，即数字期权（cash-or-nothing）定价中的 d2；`z_twap` 同理。
  - `p_digital = Φ(z_spot)`（用 `math.erf`，不引入 scipy）。
  - `sqrt_remaining_s`，`z_momentum_15s = return_15s_bps / 同一尺度`。
- `vector()` 只在特征列表含派生名时才计算，旧模型（8 个特征）照常预测。

### `trainstation/modeling.py`
- 抽出 `build(family, params, seed, classification)`。
- 特征列表来自 spec 的 `features`，缺省时用旧的 `TASKS[task][1]`，兼容旧任务。
- 分类任务的指标新增 `log_loss`（仅记录，评分仍是 Brier 加分段波动惩罚，不改判官口径）。
- **部署版重训**：spec 带 `refit_of` 时，用快照中全部有标签样本训练同一配置，不再做切分；`metrics` 沿用被选中模型的审计/验证指标，另加 `refit_rows` 和新的 `trained_through_ms`。前向评分只统计 `trained_through_ms` 之后的样本，所以仍然诚实。

### `trainstation/runner.py`
- 候选 spec 写入 `features: design(task)`（13 个特征）。
- **热启动**：建立新数据批次时，把上一批的 `research_best` 和 registry 中同任务的配置（最多 2 个，排除 `_FULL`）存入 `evolution.json` 的 `seed_parents`；第 1 代 `parents = prior[:2] or seed_parents`。做法借鉴 Population Based Training（Jaderberg et al., 2017）的 exploit/explore。
- **`ensure_deploy()`**：每轮循环检查一次。若当前冠军没有对应的部署版，就以 `<冠军id>_FULL` 启动重训子进程（300 秒超时、可被停止），结果写入 `research_best.json` 的 `deploy`，并在 `registry.json` 中用部署版替换冠军条目，这样采集器用部署版做实时预测。失败会留档为 `rejected`，不重试，直到冠军更换，此时继续使用选优模型。事件为 `deploy_refit`。
- 部署版写在 `root/models/<task>/<family>/G<代数>/`，不写到冠军原路径（避免在副本目录测试时写进真实数据目录）。
- `forward_check()` 用 `live_id()`（有部署版就用部署版 id）统计冠军的前向分和配对比较。

### `trainstation/collector.py`
- 预测用 `vector(f, bundle['features'])`。

### `trainstation/gui.py`
- 摘要行显示“部署版：全部 N 条样本重训”。

### 测试
- 新增 `test_derived_features_match_digital_probability_and_keep_old_models_working` 和 `test_deployment_refit_learns_from_every_labelled_row`。训练台 10 项、交易台 121 项测试通过。
- 端到端：在真实数据库的副本 `work/trainer-1.1-integration` 上离线跑 worker 3 代。第 1 代继承了旧冠军，验证分 0.03953（旧流程同类数据第 8 代为 0.04023）；部署版用 4,630 条样本重训成功，registry 正确切换。

## 部署
- 在 PowerShell 中构建（PATH 去掉 Git/mingw）：`--distpath D:/POLYMARKET/work/trainer-1.1.0-dist --workpath D:/POLYMARKET/work/build-trainer-1.1.0`。DLL 哈希与 Python 一致，smoke 通过，已覆盖安装并核对全部文件哈希，通过快捷方式启动。
- 启动 18 秒后，现冠军的部署版 `BTC5M_LATE_SKFOREST_G0006_C00032_604afd_FULL` 已用 4,612 条样本重训完成。下一个数据批次开始，所有候选会使用 13 个特征并热启动。
- 同样没有重做 NSIS 安装包和 manifest。

## 文献依据
- 按市场分组的时间切分、隔离带：López de Prado, *Advances in Financial Machine Learning*（Wiley 2018）第 7 章（purged k-fold / embargo）。
- 选定超参后在全部数据上重训再部署：scikit-learn `GridSearchCV(refit=True)` 的标准做法，https://scikit-learn.org/stable/modules/grid_search.html 。
- Brier / log-loss 是严格适当评分规则：Gneiting & Raftery (2007), JASA 102(477)。
- `z = d / (σ√τ)`、`Φ(z)`：数字期权在无漂移布朗运动下的到期概率（Hull, *Options, Futures, and Other Derivatives*，二元期权一章）。
- 分组 bootstrap 比较两个预测的损失差：思路同 Diebold & Mariano (1995), JBES 13(3)。
- 热启动：Jaderberg et al. (2017), *Population Based Training of Neural Networks*, arXiv:1711.09846。

## 尚未解决、值得做的
- **最大的瓶颈是数据量**：标签样本约 2,300 个市场，其中 8 天是 8 月的 PMData 档案，实时采集每 5 分钟只增加 1 个市场。
- late 任务的模型不含市场价格。94% 的准确率不等于有交易优势，市场在最后一分钟同样“知道”方向。要评估优势，需要把同一时刻的盘口 ask 纳入特征或评价，比较 P(up) 与 ask。

---

# 1.2.0 历史数据回填（代理价格 + 官方结果）（Claude Code，2026-10-22）

## 结论先行
- **Polymarket BTC 5m 的结算规则在 2026-08-07 前后改变**：此前是“收盘现货 ≥ 开盘现货”，此后是“Chainlink 60s TWAP 收盘 ≥ 开盘”。用币安数据复现官方结果（波动 ≥3bps）：2025-12 至 2026-07 现货规则 98–99%，TWAP 规则 91–97%；08-08 起 TWAP 规则 100%。PMData 中 08-08 至 08-13 的“TWAP 与官方结果不一致”也与此有关。**规则切换前的数据不能直接用于末分钟任务。**
- **币安 BTCUSDT 1s 可以作为 Chainlink 的代理价格**（`work/trainer-1.1-experiments/exp5_binance_proxy_check.py`，2026-08-14 至 08-21 重叠期）：币安 60s TWAP 复现官方结果 95.1%，波动 ≥3bps 时 100%（不一致的全是 <1bps 的近平盘）。特征相关系数：spot_open 0.997，twap_open 0.998。用币安特征训练、在真实 Chainlink 数据上测试，与用 Chainlink 训练基本持平（08-19 至 08-21 Brier 0.0485 vs 0.0475；10 月 live 0.0266 vs 0.0282）。
- **Sigrex 标签可信**：`Sigrex/polymarket_btc_up_down_5m_251218_260909`（rev `ca60755f`，MIT）共 71,602 个已结算市场，与 PMData 官方结果在 3,990 个重叠市场上 100% 一致。
- **价值检验**（`exp6_proxy_value.py`，只用测试窗口之前的代理数据）：真实 Chainlink 的 08-19 至 08-21 上，HGB 显著改善（+0.0042，CI [+0.0006, +0.0078]），森林改善 +0.0020（CI 刚跨 0）；10 月 live（59 个市场）略差，但 CI 约 ±0.008，无法下结论。因此**不强制使用**，权重交给程序判官。
- PMData 08-08 至 08-13 的 Chainlink 数据（`research/btc5m-v2-extended`）已经构建，但 `exp4` 显示对 live 效果为负，且这段处于规则过渡期，**未进入生产**。

## 数据（均在 `D:\POLYMARKET\research\`）
- `proxy-backfill/raw/binance-1s/`：267 天币安 1s K 线（608MB），官方 `.CHECKSUM` 全部校验，清单见 `binance-manifest.json`。由 `scripts/research_proxy_download.py` 下载。
- `proxy-backfill/proxy-features-all.parquet`：71,602 个市场、358,010 行（offset 60/120/210/240/270），由 `scripts/research_proxy_features.py` 构建。公式与 `research_features.py` 完全相同，严格因果：1s K 线的收盘价在 open_time+1s 才算已知，数据过期或缺口超过 5s 就拒绝。另输出 `proxy-label-audit.csv`（每个市场两种规则是否复现官方结果）和 `proxy-monthly-agreement.csv`。
- `proxy-backfill/export/binance1s_twap_regime_20260808_20260908.parquet`（sha256 `b8ad07a9…f35fa83f`，9,215 个市场、46,075 行），只含 TWAP 规则期。已复制到 `D:\POLYMARKET\trainer\proxy\`。
- `scripts/research_features.py` 增加了 `--root/--since` 参数，默认值不变，仍可复现原 btc5m-v1。

## 训练台改动
- `data.py`：`proxy_manifest(root)`（`root/proxy/*.parquet` 加 sha256），`proxy_rows(manifest, task, exclude_slugs)`，`proxy_count()`。代理数据**不进 SQLite、不进每小时快照**，避免磁盘膨胀。读取时校验哈希，与真实数据重复的市场（按 slug）自动排除。只支持 late/direction 任务（代理数据没有盘口，不能用于 ROI 任务）。
- `modeling.py`：`spec['proxy']`（文件清单）+ `spec['data_params']['proxy_weight']`。**选优训练时只加入 `start_s+300 < 验证集起点-600` 的代理行**；验证集和审计集只有真实数据，判官口径不变。部署版重训加入全部代理行。用 `fit_weighted()` 传 sample_weight（Pipeline 用 `<step>__sample_weight`）。指标新增 `proxy_train_n`、`refit_proxy_rows`。
- `runner.py`：`PROXY_WEIGHTS=[0, .25, .5, 1]` 作为进化的数据参数，子代继承父代权重并以 30% 概率变异；代理文件清单变化即开始新数据批次；`seed_parents` 带上 `data_params`；部署版重训沿用冠军的权重。
- `gui.py`：显示“另有历史代理 N”。
- 测试：新增 `test_proxy_backfill_is_training_only_and_never_after_validation_start` 和 `test_every_packaged_module_compiles`（后者是因为 1.2.0 第一次构建时 gui.py 用了 Python 3.11 不支持的同引号嵌套 f-string，PyInstaller 又一次静默丢掉了模块，smoke 测试时弹出 `No module named trainstation.gui`）。训练台 12 项、交易台 121 项通过。

## 部署结果
- 已安装 1.2.0（`work/trainer-1.2.0-dist`），哈希全部一致。启动后因代理文件变化开始新批次，48 个候选 25 秒内跑完。该批次判官选中权重 0 的森林（验证 0.03991，log-loss 0.143），与带权重候选的差距在噪声范围内。之前在副本上的集成测试中选中的是 0.5。每个新批次都会重新评估。

## 后续可做
- 2026-09-08 至今的市场：官方 Gamma API 可直接取结果（`gamma-api.polymarket.com`，series `btc-up-or-down-5m`），配合币安 1s 数据即可补齐约 6 周、1.3 万个市场，且全部属于 TWAP 规则期。这批数据离 live 最近，最可能有帮助。
- 规则切换前的 6 万多个市场：如果要用，需要针对“现货结算”单独设计特征/任务（例如把结算基准换成开盘现货，并去掉 TWAP 锁定效应），不能直接混入。
- 要实现真正的交易优势评估，仍需在 late 任务中引入同时刻的盘口价格（见 1.1.0 末尾）。

---

# 1.2.1 回填 2026-09-09 至 10-21（Gamma 官方结果 + 币安 1s）（Claude Code，2026-10-22）

本节只涉及研究数据和实验，**未修改 `trainstation/`，未重新构建或安装 exe，未改动 `D:\POLYMARKET\trainer`**。新的 parquet 还没有放进 `trainer\proxy\`，需要用户确认后再放。

## 结论先行
- **标签**：直接从官方 Gamma API 取结果（`https://gamma-api.polymarket.com/markets?slug=...&closed=true`，series `btc-up-or-down-5m`，id 10684，观察时间 2026-10-22T05:10:36Z）。市场描述与 `resolutionSource` 均为 `data.chain.link/streams/btc-usd-twap-60s-streams`（TWAP 规则）。按 300s 网格枚举 2026-09-01 至 10-22 05:05 的 14,749 个 slug，找到 14,748 个，全部为 `closed`、`umaResolutionStatus=resolved`、`outcomePrices` 恰好为 1/0，没有被剔除的市场。唯一缺失的 `btc-updown-5m-1788448800`（09-04 15:20 UTC）在 Sigrex 中同样不存在。
- **与 Sigrex 交叉核对**（09-01 至 09-08）：2,303 个市场两边一一对应，label、condition_id、winning_outcome **100% 一致**。
- **币安代理的规则一致率**（波动 ≥3bps）：2026-09 为 **100%**（8,639 个市场），2026-10 为 **100%**（6,048 个有价格的市场）。全部市场的 TWAP 规则一致率为 97.7% / 98.1%，现货规则为 86.5% / 84.8%，与 1.2.0 中“08-08 起为 TWAP 规则”的结论一致。
- **对 10 月 live 的影响**：方向为改善，但 live 只有 61 个市场，**CI 全部跨 0，无法下结论**。在更大的代理留出集（1,972 个市场）上，新数据显著改善，见下表。

## 数据（均在 `D:\POLYMARKET\research\proxy-backfill\`）
- `gamma-2026-09/gamma-outcomes.csv`：14,748 个市场（09-01 至 10-22 05:00），列为 `slug, condition_id, start_ts, label_up, winning_outcome`；`gamma-manifest.json` 记录来源、观察时间、筛选规则。由新脚本 `scripts/research_gamma_outcomes.py` 生成（每次查询 50 个 slug，只读）。
- `raw/binance-1s/`：新增 2026-09-10 至 10-21 的 K 线（09-08、09-09 已有，校验后跳过），44 天全部 `.CHECKSUM` 校验通过。`research_proxy_download.py` 会覆盖 `binance-manifest.json`，因此本次清单另存为 `binance-manifest-20260908_20261021.json`，原清单已恢复。
- `gamma-2026-09/proxy-features-all.parquet`、`proxy-label-audit.csv`、`proxy-monthly-agreement.csv`：`research_proxy_features.py --labels gamma-outcomes.csv` 的输出（14,748 个市场，73,435 行；10-22 的 61 个市场还没有币安日文件，因此没有特征）。
- `export/binance1s_twap_regime_20260909_20261021.parquet`（sha256 `94533e34…6d818b69`，**12,384 个市场、61,920 行**，5 个 offset 各 12,384 行）和同名 `.json`。列与 dtype 与 08-08 至 09-08 文件完全相同，`label_source` 改为 `polymarket official outcome (Gamma API)`。
  - **从 09-09 00:00 UTC 开始，而不是 09-08**：旧文件已包含整个 09-08（最后一个市场 start_s 1788911700）；`data.proxy_rows()` 只按真实数据去重，不在代理文件之间去重，重叠会导致同一市场被重复计权。已断言两个文件没有共同的 slug。
  - 用法：确认后复制到 `D:\POLYMARKET\trainer\proxy\`。训练台会因清单变化开启新的数据批次，权重仍由判官决定。新文件的最后几小时（10-21 20:35 起）与当前 live 样本时间重叠，但 `proxy_rows()` 会按 slug 排除已有真实样本的市场，选优训练还有“验证集起点 −600s”截断，所以不会泄漏到判官。

## 实验：`work/trainer-1.1-experiments/exp7_gamma_proxy_value.py`（exp6 的扩展，输出在 `exp7_output.txt`）
设置同 exp6：late 任务（offset 240/270），13 个特征，森林（100 棵、深度 10）和 HGB 各用 3 个种子取平均；按市场分组 bootstrap 2000 次，95% CI；“增益”= 基准 Brier − 候选 Brier，正值表示候选更好。真实数据为 Chainlink 08-14 至 08-21 共 4,517 行；代理只取测试起点 −600s 之前结束的市场。“old”为 08-08 至 09-08 文件，“new”为本次文件。

**A. 10 月 live（真实 Chainlink，121 行 / 61 个市场，10-21 20:35 至 10-22 04:55）**

| 模型 | 只用真实数据 | +old w=1 | +new w=0.5 | +old+new w=0.5 | old+new 相对 old（同权重） |
|---|---|---|---|---|---|
| 森林 | 0.0311 | 0.0262（+0.0049 [−0.0009, +0.0116]） | 0.0271 | 0.0261（+0.0050 [−0.0012, +0.0113]） | w=.25/.5/1：+0.0012 / +0.0010 / +0.0003，CI 均约 ±0.005 |
| HGB | 0.0280 | 0.0277（+0.0003） | 0.0251（+0.0029 [−0.0042, +0.0113]） | 0.0263（+0.0016） | w=.25/.5/1：+0.0023 [−0.0004, +0.0062] / +0.0024 [−0.0004, +0.0056] / −0.0002 |

所有 live 对比的 CI 都跨 0。与 1.2.0 中“live 略差”不同，这次加入代理在两种模型上都是正方向，但 61 个市场的样本量不足以下结论。（本实验的基准用全部 08-14 至 08-21 真实数据训练，exp6 只用到 08-19 之前，因此两者的基准数值不可直接比较。）

**B. 代理留出集（新文件中 10-14 00:00 至 live 开始，3,944 行 / 1,972 个市场；币安特征 + 官方标签）**——样本量大，但特征来自币安而非 Chainlink；根据 1.2.0 exp5，两者在训练/测试上表现基本相同。

| 模型 | 只用真实数据 | +old w=0.5 | +new w=0.5 | +old+new w=0.5 | old+new 相对 old |
|---|---|---|---|---|---|
| 森林 | 0.0452 | 0.0424（+0.0028 [+0.0012, +0.0044]） | 0.0410（+0.0042 [+0.0025, +0.0059]） | 0.0407（+0.0045 [+0.0027, +0.0064]） | **+0.0017 [+0.0008, +0.0027]**（三个权重都显著） |
| HGB | 0.0440 | 0.0408（+0.0032） | 0.0401（+0.0039 [+0.0022, +0.0058]） | 0.0402（+0.0038） | +0.0006 [−0.0002, +0.0014]；w=1 时 +0.0009 [+0.0001, +0.0018] |

解读：
1. 代理数据相对“只用真实数据”在 B 上全部显著改善，且越近的数据（new）收益越大；对森林来说，在 old 之上再加入 new 也显著有益。
2. 对 HGB 来说，new 单独使用与 old+new 相差不大，说明它主要受益于“近期”，而不是样本总量。
3. 权重 0.25–1 之间差异都在噪声内，维持由判官在 `PROXY_WEIGHTS` 中选择的做法。
4. 建议：确认后把新 parquet 放进 `trainer\proxy\`。live 样本累积到几百个市场后，再用 exp7 的 A 部分复查。

## 后续可做
- 定期增量回填（每周跑一次 `research_gamma_outcomes.py` + 下载 + 特征），每次导出一个不重叠的新文件。若文件数量增多，可以考虑在 `proxy_rows()` 中增加跨文件按 slug 去重（需要改 `trainstation/`，本次未做）。

---

# 1.3.0 历史快进（walk-forward 回放）（Claude Code，2026-10-22）

用户反馈：实时采集太慢（每批要等 12 个新市场，约 1 小时），希望用历史数据推进进化，但不能反复用同一批历史数据评分。

## 机制
- 把历史当作按时间到来的数据流。每个批次揭示一个**从未评分过的 UTC 日**作为验证集。训练只用在该日开始前至少 10 分钟已结算的市场（真实 + 代理），其后的真实数据只记作 audit，不参与选优。
- **每个历史日恰好当一次验证集**，之后只作训练数据，因此不会出现反复刷同一验证集的选择性过拟合。游标存在 `D:\POLYMARKET\trainer\replay.json`（`{"late": <最近一次验证日的起始秒>}`），重启后继续，不会重复。
- 前 3 天只做训练（预热），市场数不足 200 的日子跳过（`data.replay_days`）。代理数据中已有真实 Chainlink 数据的市场（PMData 08-14 至 08-21）自动用真实数据验证。
- 一个批次（48 个候选）跑完就立即进入下一天，不再等待实时市场；所有历史日用完后自动回到“等 12 个新实时市场”的原逻辑。若 `trainer/proxy/` 中加入新的代理文件（例如 9/8 至今），日程会自动延长，从游标处继续。
- 回放批次中 `proxy_weight` 只在 {0.25, 0.5, 1} 中取值（验证日主要是代理数据，且早期没有真实训练数据，权重为 0 会导致训练集为空）。
- 热启动、部署版全量重训、前向监控、自动回滚都不变：每个回放批次的冠军在晋升后用全部真实 + 代理数据重训部署版。

## 代码
- `data.py`：`replay_days(manifest, rows, warmup_days=3, min_markets=200)`。
- `modeling.py`：`spec['split'] = {'mode':'replay','val_start_s','val_end_s'}` 时使用回放切分；指标新增 `split` 字段（`replay` 或 `time_60_20_20`）。
- `runner.py`：批次切换条件新增“上一批跑满 48 个且还有未评分的历史日”。快照文件名改用行内容哈希，批次 scope 再加上验证日，因此回放批次复用同一快照文件，不重复占磁盘。阶段显示“历史快进 · 验证日 YYYY-MM-DD（第 k/N 天）”。
- 测试：新增 `test_replay_judges_each_history_day_once_without_lookahead`，训练台共 13 项通过，交易台 121 项通过。

## 实测
- 副本集成测试（`work/trainer-1.3-integration`）：约 70 秒推进一天。各验证日最优分：08-11 0.0729 → 08-12 0.0735 → 08-13 0.0552 → 08-14 0.0441（训练集 1,722 → 3,450 行；基准 0.25）。
- 已安装 1.3.0 并通过快捷方式启动，当前在回放第 1/29 天。29 天预计约 35 分钟跑完；模型约 160KB/个，整轮约 0.2GB。

## 注意
- 回放期间每个批次的冠军都会替换上一批（scope 不同），所以“研究最优”会频繁更换。回放结束后，冠军就是在最近历史日上选出的配置，部署版用全部数据训练。
- 回放的验证日大多是币安代理数据（与 Chainlink 的等价性见 1.2.0）。8 月的真实 Chainlink 日和 10 月的实时数据仍是最终检验。

## 1.3.0 部署补记
- 用户确认后，`binance1s_twap_regime_20260909_20261021.parquet` 已复制到 `D:\POLYMARKET\trainer\proxy\`（sha256 校验一致）。训练台自动识别到清单变化，历史快进日程从 29 天延长到 72 天（08-11 至 10-21），从游标处继续，没有重复已评分的日子。late 任务可用的代理行数为 43,198。

---

# 1.3.1 状态显示修正（Claude Code，2026-10-22）
- **采集错误不会自动清除**：`collector.status['error']` 写入后从不删除，一次短暂的 `APIError: ConnectError` 会一直挂在界面上（实际采集正常，约 82 条实时样本/小时）。现在写入错误时同时记录 `error_ms`，界面只显示 10 分钟内的错误，并标注发生时间。
- **前向评分样本太少**：回放期间冠军频繁更换，每个部署版只积累约 20 个实时样本。新增 `Data.deployed_forward(task)`：每个实时样本取任一部署版（`_FULL`）在结果公布前的第一次预测，合并统计。runner 以 `live_pooled` 发布，界面显示“实时前向（所有部署版合计）”。
- 历史快进 72 天已在 15:39 全部跑完，此后回到实时批次。截至 17:40：部署版合并实时 Brier 0.0293、准确率 96.7%（92 个样本、约 51 个市场）；同一批样本上，纯公式 `p_digital=Φ(z)` 的 Brier 为 0.0457，模型优于公式 +0.018（按市场分组 bootstrap 95% CI [+0.001, +0.034]）。72 个回放日的冠军全部是森林；代理权重的选择次数为 0.25（35 次）、1.0（28 次）、0.5（9 次）。
- 训练台 13 项、交易台 121 项测试通过；`test_every_packaged_module_compiles` 在打包前拦下了一次 gui.py 的字符串换行错误。

---

# 1.4.0 多任务轮流训练、纸面盈亏、盘口采集修复（Claude Code，2026-10-22）

用户要求：四种任务都要训练（以后由多个模型协调组成混合模型，现在各自独立训练即可）；系统整体再优化；每个模型的盈亏要直观展示给用户（不作为训练依据）。

## 1. 多任务轮流（`runner.py` 重写）
- `config.task` 新增 `all`（界面“全部任务（轮流训练）”，现为默认值）。每个任务各有一套状态：`trainer/tasks/<task>/{evolution,best,registry,forward_rejected}.json`；`replay.json` 本来就按任务分键。
- 调度：轮流（round-robin）检查各任务，有活（批次未满 48 个、有待评分的历史日、新增 12 个标签市场、或用户指定回滚到该任务的模型）的任务训练**一代**（6 个候选），然后轮到下一个任务。全部没活时进入等待和 AI 监督，AI 摘要包含所有任务的冠军。
- 迁移：`migrate_legacy()` 首次启动时把根目录的旧状态（evolution/research_best/registry/forward_rejected）**复制**到 `tasks/<旧任务>/`，原文件不删；目标已存在则不覆盖。根目录这几个文件此后不再更新，仅留作历史。
- 采集器从 `data.live_registry(root)` 读取各任务 registry 的前 3 个模型（部署版排第一）。
- 并行度：`PARALLEL = clamp(cpu_count // 8, 2, 4)`，本机 32 线程，因此为 4 个候选并行、每个 2 线程。候选超时 600 秒，部署版重训超时 900 秒（direction 加代理约 10 万行）。
- direction 任务同样使用代理数据和历史快进（5 个时点，约 10.8 万行，72 天）。ROI 任务没有代理数据（缺少盘口历史），走原来的 60/20/20 时间切分。
- `state.json` 新增 `tasks` 字典（每个任务的代数、候选数、统计、冠军、实时合并前向、纸面盈亏、快进进度）。顶层 `best` 已不再发布。

## 2. 盘口采集修复（`collector.py`）——ROI 实时样本一天只有 8 条的原因
- 实测（30 秒）：先用 REST 快照初始化盘口再接收 WS 增量时，出现 69 次 GAP（消息里的 best_bid/ask 与本地盘口不一致）。原代码遇到 GAP 就 `raise` → 断开 → 等 3 秒 → 再用 REST 初始化 → 再次 GAP，几乎一直在重连，盘口很少处于 fresh 状态。只用 WS 自带的 `book` 全量消息（订阅时和每次成交后都会推送，约每秒 2 次）初始化时，25 秒内 6,246 条增量 0 次 GAP。
- 修复：去掉 REST 初始化；发生 GAP 不再断线，`Book.delta` 已把该侧标为未初始化，等下一条 `book` 全量消息恢复。**`poly5/domain.py` 未修改**。交易台的采集器可能存在同样的问题，是否修复请另行评估。

## 3. 纸面盈亏（只展示给用户，不参与选优）
- 采集 direction/late 样本时，在 `aux.quotes` 中记录当时两侧的可成交 $5 买入估算（`preview_buy_notional` 按深度逐档吃单、含手续费，限价为 ask+1 tick，最高 0.99）：`{at_ms, Up:{shares,total,best_ask,best_bid}, Down:{...}}`。盘口不 fresh 或深度不足时记为 null。
- 规则（`data.paper_trades`，`EDGE=0.03`）：模型对某一侧的概率比该侧可成交均价（total/shares）高 3 个百分点以上，才模拟买入 $5；每个市场只取第一次符合条件的决策；赢了每份得 $1。ROI 任务（`roi_trades`）：预测 ROI ≥ 0.005 就入场，盈亏 = entry_cost × 实际 ROI。
- 两个口径：
  - **每个候选**：`metrics.paper_pnl`，用训练截止之后（审计段）的实时样本做样本外计算（界面表格新增“纸面盈亏（训练后实时样本）”一列）。
  - **每个任务的部署版合计**：`Data.forward_paper(task)` 只统计结果公布前做出的实时预测（界面摘要行）。
- 1.4.0 之前的实时样本没有 quotes，所以纸面盈亏要从 1.4.0 部署后才开始积累。

## 4. 其他优化
- 分类指标新增 `ece`（10 个等宽分箱的期望校准误差），供以后多模型协调使用。late 冠军 ECE 约 0.018。
- 森林新增 `max_samples ∈ {0.3, 0.6, None}`（自助采样比例）：在大数据（direction）上提速 2–3 倍，同时起正则作用。`propose()` 会给旧父代补齐新参数。
- 界面摘要改为每个任务一行：代数、候选数、样本（含代理）、验证分、实时准确率、纸面盈亏、快进进度。

## 实测
- 副本集成测试（`work/trainer-1.4-integration`，7 分钟）：四个任务都在训练；late 状态迁移成功；direction 快进 5/72 天；候选平均 2–5 秒；没有被拒绝的候选。
- **ROI 任务目前学不出东西**：最优候选验证 0.0847，不如“预测均值”基准 0.0839，因此不产生冠军（判官按设计拒绝）。原因是 ROI 只有 8 月 8 天的档案数据，实时 ROI 样本几乎为零。盘口修复后实时 ROI 样本会增加；若要真正改善，需要更多盘口历史（例如 PMData l2 的 08-08 至 08-13，或其他盘口数据集）。
- 已安装 1.4.0，配置改为 `task: all`（用户确认要求全部任务）。启动后盘口状态为“已连接”，新样本开始记录 quotes。
- 测试：训练台 15 项（新增纸面盈亏规则、旧状态迁移、ECE），交易台 121 项通过。

## 留给“以后协调多个模型”的基础
- 每个实时样本都保存了各任务部署版在结果公布前的预测（`predictions` 表）以及当时的可成交价格（`aux.quotes`）。这正是以后训练“协调器 / 元模型”（stacking 或按时点切换）需要的数据，且没有前视偏差。
- 建议的协调方式：direction（60/120/210s）给出早期方向和置信度，late（240/270s）在末段接管，ROI 模型只在盘口条件满足时给出入场时机。用纸面盈亏和 ECE 做评估。

---

# 1.4.1（进行中，未部署）盘口历史与协同策略回测（Claude Code，2026-10-22）
- **whodisidk 数据集（3/27 至 8/29，约 60GB，未声明许可）未通过验证，不使用**：与 PMData 同日（8/15）对比，22% 的决策时点中间价相差 5 个跳动以上；在这些分歧点上，连它自己记录的成交价也有 82% 更接近 PMData（中位距离 0.005 对 0.100）。验证脚本的结论写在本节，样本在 `research/whodisidk-check`。
- PMData 8/8 至 8/13 的 l2 已下载（sha256 校验），存在 `research/btc5m-v2-extended/raw/l2`。`research_features.py` 支持 `POLY5_RESEARCH_ROOT`，`research_books.py` 新增 `--checkpoints`（输出 60/120/210/240/270 秒及 +0.3 秒的盘口状态到 `checkpoint-books/`）。`scripts/research_hf_resumable.py` 用于断点续传下载。
- 训练台代码（已构建 `work/trainer-1.4.1-dist`，smoke 通过，**未安装**）：`root/archive/` 外部档案导入（`import_seed` 跳过缺失文件）；采集器纸面报价改为按深度吃单至 0.99（原来 ask+1 跳动的上限导致末段大多没有报价）。部署前需重新构建，以包含报价上限的修改。
- **待办**：① 把 `research/btc5m-v2-extended/roi-dataset.parquet`（8/8 至 8/13）复制到 `trainer/archive/`，并部署 1.4.1；② 查看后台回测 `scripts/research_coordinated_backtest.py` 的输出 `research/coordinated-backtest/report.json`。
- **初步发现（8/14 至 8/15 两天）**：只看模型概率、高于可成交价 3 个点就入场时会系统性亏损。最后一分钟策略 358 笔中只赢 54 笔，ROI −55%。原因是随机森林在概率两端偏保守，而市场在尾部更准，“优势”主要来自模型自己的误差（逆向选择）。回测已加入“市场感知协调器”：用逻辑回归处理 [logit(模型概率), logit(盘口中间价)]，只用之前日期的数据拟合。用户提出的协同策略（方向模型入场，之后按“立即卖出所得 vs 持有期望”或“盈利 ≥ $1.5 且仍有反转风险”提前止盈）也在回测之中。

---

# 1.5.0 盘口感知任务、对市场技能判官、预先登记冻结验证、扩大种群（Claude Code，2026-10-22，已部署）

## 起因
用户发现判官评分与实时纸面盈亏严重脱节。`work/trainer-1.1-experiments/exp8_live_paper_audit.py` 的实时审计结论：
- 判官用 Brier 衡量全部样本上的概率准确度；盈亏只取决于模型与市场分歧（> 3pp）的少数样本。
- 每个模型的实时交易只有 1 到 11 笔，排名完全是噪声。“落选却赚钱”的模型，盈利几乎都来自一两笔冷门单（约 0.1 的价格买入，赢一次得约 $50）。
- 合计（18:13 至 22:34）：late 9 笔 +$16，CI [−18, +49]；direction 38 笔 +$51，CI [−24, +142]，其中 7 笔冷门单贡献了 +$41。**统计上不成立**，与历史回测“同规则长期亏损”（direction −8.5%、late −23%，约 4,500 笔）一致。
- 机制：随机森林在两端概率偏保守，而市场在尾部更准，“优势”主要来自模型自身的误差，即买冷门的逆向选择。这对应博彩文献中的 favourite-longshot bias（Thaler & Ziemba 1988；Snowberg & Wolfers 2010）。

`exp9_market_aware.py`（08-08 至 08-17 训练，08-18 至 08-21 测试）：在两边都可成交的样本上，**没有任何模型的 Brier 胜过市场中间价**（direction 市场 0.176，late 0.108，所有模型的 skill_vs_market 都小于 0）。参考价格特征的信息已被市场定价。

## 改动
- **数据**
  - `scripts/research_market_rows.py`：用 PMData 检查点盘口与 Chainlink 特征生成 `market-features.parquet`，含 15,480 行、3,988 个市场，两侧可成交价按 $5 吃单到 0.99、含费。sha256 `77ae5507…`。
  - `research/btc5m-v2-extended/roi-dataset.parquet`（08-08 至 08-13）已复制到 `trainer/archive/`，ROI 从约 4,200 条增加到 7,590 条。
- **新任务** `late_mkt` / `direction_mkt`（`data.TASKS`、`MARKET_TASKS`）
  - 特征：REF + stage + `MKT=[mkt_bid, mkt_ask, up_avg_price, down_avg_price]`，派生 `MKT_DERIVED=[mkt_mid, mkt_spread, logit_mid, implied_z, z_gap]`。`implied_z` 用 `statistics.NormalDist` 反解中间价，`z_gap = z_spot − implied_z`，即模型隐含距离与市场隐含距离之差。
  - 采集器在有两侧报价时同时写入这两类样本；官方标签查询也包含这两类任务。
  - `import_seed` 支持 `market-features.parquet`（aux 中带 quotes）。
- **判官**
  - 市场任务输出 `market_brier` 与 `skill_vs_market = market_brier − mse`。
  - **晋升条件**：普通任务仍是“得分 < 常数基准”；市场任务还必须 `skill_vs_market > 0`，取满足条件的最优者。没有合格候选时不产生冠军，界面显示原因。
  - 纸面盈亏改为在所有带可成交报价的 audit 样本上计算（历史重建 + 实时），并附 95% bootstrap 区间（`data.summarise`，`pnl_ci`）。
- **预先登记冻结验证**：市场任务第一个部署版冠军写入 `tasks/<task>/frozen.json`，从此不再重训或替换，并持续实时预测（`live_registry` 包含它）。只统计冻结之后的预测（`forward_paper(..., since_ms)`）。判定规则 `data.verdict`：≥ 200 笔且区间下限 > 0 为“已确认盈利”，区间上限 < 0 为“已否定”，其余为“验证中”。
- **种群**
  - 每代 8 个候选（5 个利用父代，3 个探索，其中 1 个参考 AI 建议），每批 64 个，并行 `clamp(cpu//5, 2, 6)`，本机为 6。
  - 新算法 `extra`（ExtraTrees，Geurts et al. 2006）和 `logit`（L2 逻辑回归；回归任务对应 Ridge）。
  - 新数据参数 `calibration ∈ {none, sigmoid, isotonic}`：按时间顺序，前 80% 拟合、最近 20% 用 `CalibratedClassifierCV(FrozenEstimator)` 校准。依据 Platt 1999、Zadrozny & Elkan 2002、Niculescu-Mizil & Caruana 2005。
- **界面**：每个任务一行，市场任务另显示对市场技能与合格状态；冻结验证单独一行；盈亏带 95% 区间与“显著/未显著”。表格识别新算法名。

## 集成测试（`work/trainer-1.5-integration`，10 分钟，真实数据副本）
- 6 个任务各 64 个候选，0 个失败。
- ROI 首次产生冠军（ExtraTrees，数据增加后能胜过常数基准）。
- `late_mkt`：64 个候选中 41 个 skill > 0，冠军为 logit + isotonic（skill +0.0038）；样本外纸面盈亏 +$60 / 301 笔，**CI [−116, +273]，不显著**；已进入冻结验证。isotonic 的平均 skill 为 +0.0014，sigmoid 为 −0.0024。
- `direction_mkt`：0 个候选胜过市场，不产生冠军。
- 测试：训练台 19 项、交易台 121 项通过。smoke 通过。已安装 `work/trainer-1.5.0-dist` 并核对哈希；archive 已放入，生产环境已有实时 `direction_mkt` 样本。

## 解读与后续
- 目前**没有任何策略被证明能盈利**。late_mkt 只是“略胜市场”，需要冻结验证积累到 ≥ 200 笔，按目前约每小时 1 到 3 笔，需要数天。
- 下一步更可能找到优势的方向是市场尚未定价的信息，例如：币安领先 Chainlink 的延迟（需要实时接入币安成交流）、订单流与盘口失衡的微结构特征、TWAP 结算窗口锁定效应的精确建模。单纯扩大模型规模，在现有特征下已经证明无法超过市场。

## 1.5.1 界面措辞修正（已部署）
- 用户把表格列中的数百美元亏损误认为实时亏损。这一列实际是每个候选的样本外回测，从 1.5.0 起盘口感知任务还包含历史盘口重建，每行约 280 到 490 笔，其中 direction_mkt 候选在 −517 到 −34 美元之间，正好印证判官没有选它们当冠军。列名已改为“候选回测盈亏（样本外，含历史盘口）”，并加了 tooltip；每个任务一行的数字改称“实时纸面盈亏”。部署时，实时合计为 late 10 笔 +$17、direction 42 笔 +$48，区间都包含 0。

---

# 1.6.0 AI 监督 v2：结构化提案、护栏、配对对照组、按任务熔断（Claude Code，2026-10-22，已部署）

## 起因（`exp` 结果见下）
- 截至 1.5.1：AI 调用 31 次全部成功，建议 31 次都是 forest。程序只取它的 `family` 字段去填 1 到 2 个名额，而父代本来就是 forest，所以 AI 名额候选的代内相对名次为 0.508（区间 0.495 到 0.522），和随机没有区别，**完全无作用**。AI 的文字分析其实是对的（20:51 已指出“MSE/ECE 很低但纸面 PnL 为负”，建议约束参数、加校准），但程序从不读取这些文字。

## 机制（`trainstation/ai_policy.py`，新文件）
- **提案**：每次审阅，AI 对每个任务最多给出 `AI_SLOTS=2` 个完整配置（family、params、calibration、proxy_weight、rationale）。AI 看到的摘要（`runner.ai_context`）包括：全部任务的冠军指标（score、baseline、skill_vs_market、ECE、log_loss、paper_pnl 及区间）、合格状态、实时纸面盈亏、冻结判定、AI 与对照的配对统计、上一轮提案在本轮的得分，以及熔断状态。
- **护栏**：
  - `sanitize` 用白名单校验 `BOUNDS`，故意比程序网格宽，例如 forest 的 n_estimators 可取 20 到 300、深度 2 到 20，logit 的 C 可取 1e-4 到 100。越界数值夹到边界，非数值、NaN、布尔值丢弃，未知参数丢弃，非白名单算法整条拒绝，rationale 截到 400 字。
  - `parse` 拒绝非对象 JSON；每个任务最多保留 2 条提案；未知任务忽略；全部提案都被拒时，本次审阅记为失败。
  - 输出上限 16000 token，正文上限 30000 字符；`finish_reason=length` 时明确报“被截断”并不采用（集成测试中 4000 token 时 JSON 在约 3986 字符处被截断）。
  - 所有提案和修改说明写入 `ai/review-*.json`、`ai/latest.json`（含原文 raw）、`ai/calls.jsonl`。
- **名额（每代 8 个）**：0 到 3 号利用父代；4 号探索其他算法；5 到 6 号放 AI 提案（只有一条提案时，6 号用程序对它做变异）；7 号是**对照**：与 AI 第一条提案同算法，参数由程序选。在 AI 关闭、提案超过 12 小时、回复无效或被熔断时，5 到 6 号由程序填充。每个候选记录 `origin`（program/ai/control）、`ai_review_ms` 和 `ai_rationale`。
- **配对统计**（`report`）：每一代比较 AI 最佳与对照的相对名次（0 最好，1 最差）。`mean_advantage = 对照名次 − AI 名次`，大于 0 表示 AI 更好，用 bootstrap 求 95% 区间，并统计代内第一来自哪一方。按整体和按任务分别统计，窗口为 24 小时。
- **熔断**（`breaker`，状态存 `ai/breaker.json`）：整体 ≥ 30 组配对且区间上限 < 0 时，暂停全部 AI 名额 6 小时；单个任务 ≥ 16 组且区间上限 < 0 时，只暂停该任务 6 小时（`ai_allowed`）。每次熔断记录事件 `ai_circuit_breaker`。
- AI 仍然**不能**改判官、数据、部署和代码；晋升只看程序判官。
- 界面：显示一行 AI 与对照的对比（配对数、名次、优势和区间、代内第一、熔断状态）；表格中 AI 候选标“· AI”，对照标“· 对照”。

## 集成测试（`work/trainer-1.6-integration`，用真实 DeepSeek Key，数据为副本）
- 第一次 AI 调用因 4000 token 截断而失败，**后备方案生效**：384 个候选全部由程序生成，0 失败。
- 修复后，一次真实调用返回 **12 个提案、覆盖 6 个任务，全部通过白名单，无需夹值**，内容合理（例如盘口感知任务用更强的 L2 加 isotonic，5 秒 ROI 用更强的正则）。
- 第二次运行：384 个候选，其中 program 240、**ai 96**、**control 48**，0 失败。AI 候选完全按提案参数训练。首批 48 组配对：整体优势 +0.042，区间 [−0.057, +0.14]，不显著。分任务看，direction（+0.20）和 roi5（+0.37）上 AI **显著更好**，roi15（−0.20）和 direction_mkt（−0.29）上**显著更差**，因此增加了按任务熔断。
- 测试：训练台 21 项（新增护栏、配对报告与熔断，包括按任务熔断和到期恢复），交易台 121 项通过；smoke 通过；已安装并核对哈希。

---

# 策略头脑风暴研究（2026-10-22，未改代码）
完整报告见 `D:\POLYMARKET\research\strategy-brainstorm-20261022.md`，脚本见 `work/trainer-1.1-experiments/exp10` 至 `exp14_*`。要点：
- 吃单（模型或延迟套利）、在热门一侧挂单（maker）、maker 加币安快速撤单，在 08-08 至 08-21 的数据上**都没有稳健的扣费后优势**。
- 显著的结构性规律：冷门（< 0.35）在 210 秒后被高估，亏 15% 到 46%；被动成交有强逆向选择（例如热门胜率在成交单为 91.7%、未成交单为 99.7%）。币安领先中间价约 1 秒，但不足以覆盖手续费。
- 下一步建议：订单流特征进盘口感知任务；以“210 秒后不买 < 0.35”作为全局风控先做纸面验证。PMData trades 已下载到 `research/btc5m-v2-extended/raw/trades/`。

---

# 1.6.1 / 1.6.2 冷门过滤（已验证、已部署）与判定修正（Claude Code，2026-10-23）
用户要求：“所有能带来正向收益的更改，一律执行”。原则：**只采用样本外验证为正的改动**。

## 验证（`work/trainer-1.1-experiments/exp15_flow_and_filter.py`，滚动 walk-forward：测试日 08-14 至 08-21，共 8 天，每天只用之前的数据训练）
- **冷门过滤（入场价格 ≥ 0.35）**：8 个配置中 7 个 P&L 改善。**late 盘口感知加过滤后，4 个配置全部显著盈利**：logit +$158 [+69, +246]、hist +$150 [+55, +247]、logit+flow +$119 [+30, +206]、hist+flow +$147 [+54, +241]。direction 有改善但仍不显著。→ **采用**。
- **订单流特征**（`scripts/research_flow_rows.py`，生成 `research/market-rows/market-features-flow.parquet`：10 秒/30 秒带方向的主动成交、30 秒成交数、前 5 档失衡、最近成交价相对中间价）：对市场技能 4 个配置中 3 个变好，但 P&L 只有 2 个变好，且都不显著。→ **暂不采用**，数据保留，待更多数据后复测。
- 双边做市、跨周期一致性、末段 TWAP：没有验证为正（maker 回测见 exp12 至 exp14），不实施。

## 改动
- `data.MIN_ENTRY_PRICE=0.35`；`paper_trades(items, min_price=MIN_ENTRY_PRICE)`；`Data.forward_paper(..., min_price)`。所有纸面盈亏（实时、候选样本外、部署版合计）默认使用过滤。
- **不污染已有的预先登记**：原冻结验证继续按原规则（`frozen.json` 中 `min_price=0`）计算；同一冻结模型另外**预先登记“冷门过滤规则”**，只计 `filtered_since_ms`（首次升级时写入）之后的预测，事件为 `frozen_rule_registered`。界面分两行显示。
- **1.6.2 判定修正**：部署 1.6.1 后，原规则冻结验证仅凭 2 笔亏损就被判为“已否定”（2 笔亏损的 bootstrap 区间必然全在 0 以下）。`verdict` 增加 `refute_minimum=50`：判定否定同样需要 ≥ 50 笔。现在两条都显示“验证中”。
- 测试：训练台 21 项（新增过滤与“2 笔不足以否定”的用例），交易台 121 项通过；smoke 通过；已安装并核对哈希。

---

# 1.7.0 全量盘口记录器（Claude Code，2026-10-23，已部署）
动机：历史数据中只有 PMData 的 14 天带盘口，能做成交模拟；第三方大盘口数据集（whodisidk）没有通过核验。因此改为自己持续记录。

## `trainstation/recorder.py`（新）`OrderBookRecorder`
- 写入 `D:\POLYMARKET\trainer\orderbook\date=YYYY-MM-DD\<表>\part-<ms>.parquet`（按 UTC 日期分目录，zstd 压缩，先写临时文件再改名），共 5 张表：
  - `books`：每秒每个 outcome 一行，前 20 档买卖价与数量，含 `initialized`、`quality`、`source_ms`、`received_ms`。
  - `trades`：每个 `last_trade_price` 事件一行（outcome、price、size、taker side、exchange_ms、received_ms）。这一事件此前被采集器忽略。
  - `bbo`：每个 outcome 的最优价或最优档数量**发生变化**时一行，带 `valid`。
  - `reference`：每个 Chainlink 现货/TWAP 点一行（observed_ms、received_ms、精确十进制字符串、`snapshot` 标记）。重连时推送的历史快照点 `snapshot=true`，它们的观测时间较旧，不能当作实时延迟。
  - `markets`：每个市场一行（slug、condition_id、两个 token、开始/结束时间、tick）。
- 每 300 秒落盘一次，崩溃最多丢 5 分钟；停止时强制落盘。可用空间低于 5GB 时丢弃缓冲并暂停，界面显示原因，不影响训练。
- 采集器接入点：WS 的 `book` 和 `price_change` 处理后调用 `on_book`；处理 `last_trade_price` 时调用 `on_trade`；`Collector.event('reference.price')` 调用 `on_reference`（任何解析异常都被吞掉，不影响行情）；切换市场时调用 `on_market`；主循环每轮调用 `snapshot` 和 `maybe_flush`。只记录当前 5 分钟市场，下一轮开盘前的盘口不在记录范围。
- 实测：独立采集器连真实市场 80 秒，得到快照 158 条（20 档齐全）、成交 329 笔、最优价变化 24,236 条（valid 99.7%，交易所到本机延迟中位数约 0.6 秒）、参考价 394 条。体积约 244KB，**约 260MB/天，8GB/月**，主要来自 bbo（约每秒 300 条）。生产环境部署后第一次落盘：快照 560、成交 1,487、最优价变化 74,008。
- 用途：几天后即可对挂单（排队、逆向选择）、吃单、订单流特征、末段 TWAP 效应等做比 PMData 更细的模拟；这些数据的格式与 PMData 不同，研究脚本需要单独写读取逻辑。
- 测试：新增 `test_order_book_recorder_writes_readable_tables`；训练台 22 项、交易台 121 项通过；smoke 通过；已安装并核对哈希。

---

# 1.8.0 AI 名额按战绩分配、熔断试用期、全局熔断收紧、提案历史（Claude Code，2026-10-23，已部署）

## 起因（审计 1.6.0 起的完整记录）
- 3,584 个候选中只有 60 个来自 AI（1.7%），仅 30 组配对，集中在 2 个任务和约 1.3 小时内。late 上与对照持平（−0.009），direction_mkt 明显更差（−0.316）。6 个现任冠军和 56 个批次冠军中，没有一个来自 AI，说明判官已经把它的坏提案淘汰掉了。
- **设计缺陷**：01:19 熔断，07:19 到期后又用同样的 30 组旧配对重新判定，立即再熔断 6 小时，等于**永久封锁**。另外，单个任务表现差就触发了全局熔断，把所有任务的 AI 名额都收走。
- AI 的提案重复度高，例如 roi5+forest 出现 14 次，late_mkt+logit+isotonic 出现 13 次。

## 改动（`ai_policy.py`、`runner.py`、`supervisor.py`、`gui.py`）
1. **名额按战绩分配**（`slots_for`）：冷却中 0 个，默认 1 个；在该任务上 ≥ 16 组配对且区间下限 > 0（AI 显著更好）时给 2 个。生成每代候选时按此分配（5 号起），7 号对照不变。
2. **熔断试用期**（`breaker(trials, state)`）：熔断时记录 `since_ms = until_ms`，到期后只统计此后产生的新配对；条目在到期后保留 `since_ms`，防止旧证据反复触发。
3. **全局熔断收紧**：只有在 ≥ 2 个任务同时显著更差时才全局熔断，否则只熔断该任务。状态格式改为 `{'global': {...}, 'tasks': {...}}`，旧格式会自动迁移（当时生效中的全局熔断照常持续到约 13:59）。
4. **提案历史与去重**（`history`）：每 10 分钟汇总一次 AI 自 v2 以来全部候选的代内平均名次，按任务、算法、校准分组，作为 `ai_track_record` 发给 AI。同一组参数（数值按 log10 取一位小数后比较）出现 ≥ 2 次且平均名次 > 0.6 的，再次提交时 `parse(..., avoid)` 直接拒绝并说明原因。提示词要求 AI 偏向排名好的区域、离开排名差的区域，并告知各任务当前的名额数。
- 界面：AI 对照行增加各任务 AI 名额，以及全局/按任务熔断的原因。
- 修复：集成测试发现 SQL 字符串因转义丢失被 Python 当成 % 格式化（`TypeError`），改为参数化查询。
- 测试：重写熔断测试，覆盖单任务差不触发全局、两个任务差触发全局、试用期内旧证据不能重新熔断而新证据可以、名额 0/1/2、历史名次与重复提案拒绝。训练台 22 项、交易台 121 项通过；用生产数据副本加真实 AI 做了集成测试；smoke 通过；已安装并核对哈希。

---

# 交易台 3.3 实盘规则复核（2026-10-23，只读分析，未改代码）
详见 `D:\POLYMARKET\research\live33-rule-review-20261023.md`。要点：触发约占市场 2% 到 4%；持有到结算是“赢约 $0.41、输 $5”的结构，历史胜率 95.5%，正收益不显著；3.3 的 +0.5% 止盈在逐秒重放中让每笔收益从 +$0.27 降到 +$0.09，盈亏平衡胜率从约 92% 升到约 98%；$34 本金下单笔约占 15%，按历史分布 100 笔内触发 50% 回撤停止的概率约 17%。建议先建立该规则的影子账本再考虑启用。

---

# 交易台 3.3 规则影子账本（Claude Code，2026-10-23 17:15，已并入源码，**未由 Claude 安装**）
- 新模块 `trainstation/shadow33.py`：用全量盘口记录器的逐秒数据，对每个已出官方结果的市场重放 3.3 末分钟规则（240 秒至结束前 15 秒，TWAP 距开局 TWAP ≥ 阈值且现价同侧，连续 3 秒确认；按下一秒盘口买入 $5，吃单到 0.99，手续费 7%·p·(1−p)；不重放模型过滤，模型过滤只会减少交易）。三个变体：`tp05`（3.3 现行：净 ≥ +0.5% 即卖，否则结算）、`hold`（持有到结算）、`hold80`（$80 阈值、持有到结算）。结果按 (slug, variant) 幂等写入 SQLite 表 `shadow33`；`update(data)` 返回各变体的 summarise（含 95% 区间）。跨 UTC 午夜的市场会读取次日文件夹。
- `runner.py`：在等待分支每 10 分钟调用一次 `shadow_update()`，结果以 `state['shadow33']` 发布，出错时只发布错误信息、不中断训练。`gui.py`：`shadow_line()` 显示三行对照。
- 验证：新增 `test_shadow33_replays_trigger_and_both_exits`。用真实记录器数据核对与 `exp17` 一致（201 个市场，触发 4 笔：tp05 +$0.37，hold +$1.08，hold80 0 笔）；首次运行 1.9 秒，增量运行 0.9 秒。训练台 23 项、dynamic/revision 测试、交易台 121 项均通过。
- **并行开发说明**：Codex 在 17:13 同时修改训练台并构建 1.9.1。核对 `build-trainer-1.9.1` 的 xref，已包含 `trainstation.shadow33`。为避免互相覆盖安装，Claude 没有安装；由 Codex 安装 1.9.1 时一并生效。若重新构建，请确认 `runner.py` 中 `shadow_update` 和 `gui.py` 中 `shadow_line` 仍在。
- 目的：在不改动实盘代码的前提下，判断 3.3 的“+0.5% 止盈”和“持有到结算”、$50 和 $80 阈值哪种更好。按当前约每天 6 笔的触发量，约两周后能积累约 100 笔。届时只把被证明显著更好的那一个参数改进 3.3。

---

# 1.9.2 第二轮历史快进 + 终考；冷门专家实验（Claude Code，2026-10-23，已部署）

## 第二轮快进与终考（`runner.py`、`modeling.py`）
- 动机：首轮快进（1.3.0）时只有 3 种算法，没有校准、AI 提案和盘口感知。现在用更强的搜索重走一遍历史，但不能重复使用旧验证日来选模型，否则会过拟合。
- 触发文件 `D:\POLYMARKET\trainer\pass2.json`：`{"exam_start_s": 1792614900, "tasks": {"late": {"cursor": 0}, "direction": {"cursor": 0}}}`（exam_start = 第一个实时市场 10-21 20:35 UTC）。
- **第二轮**：按 `replay_days` 中 `d+86400 <= exam_start` 的日子逐日回放（split `mode=replay, pass=2`）。每天的合格冠军只写入 `tasks/<task>/pass2_best.json`，作为下一天的父代；**不写 best.json、不更新 registry、不部署**。游标独立存放在 `pass2.json`，不影响首轮的 `replay.json`。
- **终考**：第二轮结束后跑一个 64 候选批次（split `mode=exam`）。验证集**只用 exam_start 之后的真实（实时）样本**：这些样本此前只在审计段出现过，从未参与选优。训练集为在 exam_start−600 秒之前结算的全部真实数据和代理数据；代理数据永远不作验证。第 1 代 0 号名额放**现任冠军的原样配置**（origin=`incumbent`），种子为 pass2_best 和现任冠军。批次结束时按原有晋升规则（新 scope 的合格最优者替换旧冠军，同一 scope 内需要好 0.0001 以上）晋升并部署。`pass2.json` 记录 exam_best、incumbent_rank/score、champion_after_exam；事件为 `pass2_exam_done`。
- 回放与终考批次里，proxy_weight 只在 replay 模式下禁止取 0；指标 `split` 现在记录实际模式（replay/exam/time_60_20_20）。
- 集成测试（`work/trainer-pass2-integration`）：第二轮 3 天共 192 个候选，冠军未被改动；终考 64 个候选，验证 805 条实时样本，**现任冠军排第 55 名**（0.0326），第二轮后代胜出（0.0291），自动晋升并部署（重训 5,322 条）。修复了终考记录字段（原来取自已截断的前 3 名）。
- 测试：新增 `test_final_exam_validates_only_on_unseen_real_rows`；训练台 51 项（含 dynamic/revision）、交易台 121 项通过；smoke 通过。已部署 1.9.2，第二轮已在生产启动（late/direction 各 71 天，预计 2 到 3 小时，然后各自终考）。
- 回填检查：代理数据已覆盖到 10-21 23:55，实时采集从 10-21 20:35 开始，两者重叠；实时只缺 39 个市场（重启期间），币安 10-23 日文件次日才发布，因此本次没有回填。

## 冷门专家实验（`work/trainer-1.1-experiments/exp18_longshot_specialist.py`，未改代码）
- 用户提出：是否应该把“识别会翻盘的冷门、低价买入”作为可训练技能。专门用冷门样本（某侧可成交价 < 0.35，10,603 条，3,870 个市场）训练“冷门是否翻盘”的模型，特征从冷门一方视角表达（参考价、市场价、模型与市场的距离差、订单流），滚动 walk-forward 8 天。
- 结果：冷门实际胜率 13.4%，平均价格 15.4%（冷门被高估）。**没有一个模型的 Brier 胜过市场价格**（市场 0.1212，模型 0.1232 至 0.1251）。买入全部冷门 −12%；按“模型概率超出价格 3/5/10 个点”入场，各配置多为负，hist-GB 原始 +4% 到 +5% 但区间跨 0，isotonic 版本显著为负。→ **目前没有证据表明这种技能存在，冷门过滤保留**。数据和脚本已保留，盘口记录器积累更多数据后可以复测。

## 1.9.3（Claude，2026-10-24）

- `runner.py`：主训练循环每代结束后也调用限频（10 分钟）的 `shadow_update()`；此前只在等待分支调用，长时间回放/pass2 期间 3.3 影子账本一直为空。
- 首次按 Codex 流程构建与部署：`scripts/build_trainer.ps1` → `trainer_installer.nsi /DTRAINER_VERSION=1.9.3` → `trainer_release.py verify`（1103 文件通过）。注意：强制结束进程后 EXE 可能仍被锁 1 秒左右，安装器会返回 2 且不改动；需确认可独占打开后再静默安装。
- pass2 期末考试结果（仅用从未参与选优的 10/22 之后实时样本验证）：late 原冠军 64/64 名（0.0333），新冠军 `G0004_C00031_2ca532` 0.0277（n=880，准确率 96.5%，ECE 0.015）；direction 原冠军 49/64（0.0987），新冠军 `G0007_C00051_521338` 0.0973（n=2181，准确率 86.0%）。纸面 P&L：late 35 笔 +$47.08（区间 [+6, +87]）；direction 208 笔 +$49.29（区间 [-53, +152]，不显著）。
