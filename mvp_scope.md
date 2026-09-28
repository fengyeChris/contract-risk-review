# MVP 范围定义：合同风险条款审查 Agent

> 版本：v0.1（草稿）
> **本文件是全项目的判定基准**：Step 2 的数据筛选、Step 3 的 prompt 与输出 schema、Step 4 的评测指标，全部以本文件为准。
> 用法：每类条款都要回答 5 个问题 —— 它管什么 / 防范什么风险 / 命中标准 / 边界反例 / 风险三档。

---

## 0. MVP 边界（第一版严格遵守）

| 项 | 范围 |
|---|---|
| 语言 | 仅英文（CUAD 纯文本） |
| 条款类别 | 10 类（见 §1） |
| 技术路线 | 朴素向量 RAG：切块 → embedding → top-k 检索 → LLM 按 schema 输出 |
| 交互 | 先 CLI，再包一层最简 FastAPI |
| 明确不做 | 中文合同、扫描件 OCR、PDF 版面解析、BM25 混合检索、rerank、查询纠错、多 Agent、HITL 人工审核、Word 报告、Langfuse、成本看板、灰度、压测、微调、安全网关 |

## 0.1 阅读约定（合同是英文，但判定标准用中文思考）

> **职责分工**：英文原文由 LLM 处理；判定标准由人用中文定义，只保留少量英文信号词作为"识别线索"。
> 开发者不需要具备英文法务阅读能力，只需要做到**关键词级识别**（看到 `governed by` 知道是适用法律即可）。

### 高频信号词速查表（v0.1，Step 2 会用 CUAD 实际标注校准）

| # | 类别 | 英文信号词（看到就往这类想） | 中文含义 |
|---|---|---|---|
| 1 | 争议解决与适用法律 | `governed by` / `construed in accordance with` / `jurisdiction` / `venue` / `arbitration` / `AAA` `JAMS` `ICC` / `waive trial by jury` / `attorneys' fees` | 适用法律 / 管辖地 / 仲裁 |
| 2 | 控制权变更 | `change of control` / `change in control` / `merger` / `consolidation` / `sale of all or substantially all assets` / `majority of the voting securities` | 控制权变更 / 合并 / 出售实质全部资产 |
| 3 | 转让限制 | `shall not assign` / `may not assign` / `prior written consent` / `transfer` / `successors and assigns` | 不得转让 / 事先书面同意 / 承继与受让人 |
| 4 | 竞业限制 | `non-compete` / `shall not engage in` / `competing business` / `during the term and for ... years` | 竞业限制 / 期限 |
| 5 | 独家排他 | `exclusive` / `exclusivity` / `solely` / `shall not ... with any third party` | 独家 / 排他 |
| 6 | 终止与通知期 | `terminate` / `termination for convenience` / `upon ... days' notice` / `written notice` | 终止 / 提前通知期 |
| 7 | 自动续期 | `automatically renew` / `auto-renewal` / `renewal term` / `successive ... periods` | 自动续期 / 续展期 |
| 8 | 责任上限 | `liability ... shall not exceed` / `cap on liability` / `aggregate liability` / `in no event` | 责任上限 / 累计责任 |
| 9 | 违约金 | `liquidated damages` / `as liquidated damages and not as a penalty` / `for each day of delay` / `sole and exclusive remedy` | 违约金 / 每日计算 / 唯一救济 |
| 10 | 知识产权归属 | `shall own` / `hereby assigns` / `work product` / `intellectual property` / `deliverables` | 归属 / 转让 / 工作成果 |

### 易混淆陷阱表（Step 4 badcase 归因时高频出现）

| 陷阱词 | 真实含义 | 别误判成 |
|---|---|---|
| `merger clause` / `merges all prior discussions` | 完整协议条款（作废此前约定） | ❌ 控制权变更 |
| `successors and assigns` | 约束力延伸条款 | ❌ 转让限制 |
| `in any jurisdiction` | 合规义务的适用地域 | ❌ 争议解决 / 适用法律 |
| `assignment of IP` / `assigns all right, title and interest in the Work Product` | 知识产权转让 | ❌ 合同转让限制 |

### 分类铁律（来自条款 2 / 3 的边界复盘）

| 铁律 | 说明 | 对系统设计的影响 |
|---|---|---|
| ① **看动作，不看词** | `merger`、`sale of all or substantially all assets` 这类词**跨类别共用**：出现在"能不能转让本合同"的句子里 → 转让限制；出现在"换老板后合同怎么办（解约/同意/继续有效）"的句子里 → 控制权变更 | Step 3 的 prompt 必须写"先判断这句话在做什么动作"，**禁止关键词硬匹配** |
| ② **一条证据可以命中多个类别** | 同一句话可能同时被两个类别引用（如转让限制的例外条款借用了控制权变更的触发词） | 输出 schema 里每个证据片段必须带 `matched_categories: list[str]`，不能是单值 |
| ③ **承继条款 ≠ 转让限制** | `successors and assigns` 只说明"约束力延伸到受让人"，没有限制转让行为 | 写入陷阱表，并作为评测集里针对性测试用例 |

### 0.2.1 限制型条款通用判读框架（适用于条款 4 竞业 / 5 独家 / 6 终止 / 7 续期）

**核心矛盾**：限制型条款的天平永远是倾斜的 —— **限制谁，谁就痛**。所以风险分档的第一个问题是：**这个限制是单向还是双向？**

**五问法**（拿到任何一句限制型条款，先问这 5 个）：

| # | 问什么 | 关键词线索 |
|---|---|---|
| 1 | 限制**谁**？（单向 = 只约束 `Party A`；双向 = `Neither party`） | 主语 |
| 2 | 限制**什么行为**？（范围） | `engage in` / `compete` / `deal with` / `solicit` |
| 3 | 限制**多久**？（期限） | `during the Term and for ... years` |
| 4 | 限制**在哪**？（地域） | `within [Territory]` / `worldwide` |
| 5 | 有无**对价、例外、退出机制**？ | 补偿金 / `except` / `may terminate` |

**风险原则（条款 4/5/6/7 通用）**：单向限制、无期限、全球地域、无对价补偿、无例外或退出机制 —— 命中任意一条都往**高风险**调。

### 0.3 存在性与风险性是两个独立维度（输出 schema 的前置约定）

- **某条款缺失，不等于风险高。** 有些条款缺失反而更清晰。
- 典型例子：条款 7 自动续期（没有自动续期 = 到期自然结束，规则更清楚）、条款 4 竞业限制（对被动方来说，没有反而是好事）。
- 因此输出 schema 必须拆成两个字段：`exists: bool` 与 `risk_level: high | medium | low | na`。**条款不存在时，风险填 `na`，不要填 `high`。**
- 「缺失必备条款清单」只纳入真正必备的类：**01 争议解决、08 责任上限、10 知识产权归属**（Step 2 已用真实数据复核后定稿，见 §0.6），其余类别缺失只报告、不报警。

### 0.4 风险判读的立场（stance）：MVP 固定为「客户 / 买方视角」

- **为什么必须固定**：同一份合同，站在甲方和乙方看，"风险"结论正好相反（例：责任上限对**乙方**是保护，对**甲方**可能是陷阱）。立场不固定 → 评测集没有唯一正确答案 → Step 4 的指标算不出来。
- **决定**：MVP 统一采用「**我方 = 客户 / 买方 / 服务接收方**」视角（产品典型用户是采购方与法务）。
- **实现**：Step 3 写入 system prompt；schema 预留 `reader_stance` 字段，默认 `customer`。
- **backlog**：供方视角（`vendor`）切换，留到 Step 5 之后。

**立场回查记录（v0.8）**：固定为买方视角后，已回查全部 10 类，修正了 4 处隐含供方视角或方向不清的表述：

| 条款 | 原表述的问题 | 修正结果 |
|---|---|---|
| 3 转让限制 | 只说"一方可自由转让"，未指明谁吃亏 | 高 = **对方可自由转让，而我方被限制** |
| 4 竞业限制 | 未区分"限制谁"，H 句结论偏差 | 增加"限制谁"前置判断；H 句由"中"修正为 **低**（限制对方 = 对我方有利） |
| 5 独家排他 | 风险三档误用**供方视角**（原文写"我独家供你"） | 改为客户视角：高 = **我方被独家绑定且对方无供货保障** |
| 7 自动续期 | "可能被绑住的一方"表述含糊 | 明确为"不续期通知是否为我方义务、我方是否易遗漏" |

> 这次回查由学习者主动提出，是本项目最有价值的一次 QA —— 说明"立场"这类隐含假设必须在写标准时显式声明，否则错误会一路传到 Step 4 的评测结果里。

### 0.5 跨条款关联（Step 4 归因时必看）

| 关联 | 说明 |
|---|---|
| **8 ↔ 9** | 违约金（第 9 类）的金额**是否计入责任上限**，是常见争议点：合同若写"违约金不受责任上限约束"，则上限形同虚设；反之违约金可能吃掉整个上限额度。**只看 8 不看 9 是最常见的误判。** |
| **9 ↔ 10** | 知识产权侵权索赔（第 10 类）是最常见的赔偿触发事由；两者常写在同一段里。 |
| **5 ↔ 6** | 独家（第 5 类）常与"未达最低采购量可终止"（第 6 类）配套，单独看会漏判风险。 |

### 0.6 数据集口径与金标准覆盖（Step 2 实测后定稿）

**金标准覆盖**：CUAD 41 类 → 本项目 10 类，**10 类全部有对应标签**。

- 原第 9 类「赔偿条款（Indemnification）」在 CUAD 中**没有**对应标签（实测：20,910 条标注、41 个类别名中不含 `Indemn`）。
- **口径变更（已确认）**：第 9 类改为「**违约金（Liquidated Damages）**」，CUAD 有该类标注。
- **Indemnification 移入 Step 5.8 backlog**：届时用 LLM 预标注 + 人工抽检自建弱标注集，补齐这一类的评测能力。

**三份数据集（实测拆分）**

| 数据集 | 合同数 | 用途 | 能算的指标 |
|---|---|---|---|
| **主评测集**（官方原始文本） | **198** | 主指标报数 | 存在性 F1 / 召回率 **+ 证据定位准确率** |
| **鲁棒性集**（PDF 转换文本） | **303** | 脏文本鲁棒性验证 | 只算存在性 F1 / 召回率（**单独报，不混入主指标**） |
| 未纳入 | 9 | 4 个文件名特殊字符未匹配 + 5 个正样本过少 | — |

**主评测集各类别正样本数（实测）**

| 条款 | 正样本 | 条款 | 正样本 |
|---|---|---|---|
| 01 争议解决与适用法律 | 173 | 06 终止与通知期 | 79 |
| 02 控制权变更 | 49 | 07 自动续期 | 70 |
| 03 转让限制 | 147 | 08 责任上限 | 106 |
| 04 竞业限制 | 46 | **09 违约金** | **21** ⚠️ |
| 05 独家排他 | 68 | 10 知识产权归属 | 50 |

**已知局限（必须写进报告，面试时主动讲）**

1. **第 9 类违约金正样本仅 21 个** → 该类指标波动大。处理方式：报告里**注明样本量**，对该类只报"准确率 + 样本量"，不下强结论。
2. 标注位置精确的占 39.8%，另有 46.2% 可"忽略空白"重新定位，**14.0% 彻底不可定位**（集中在 PDF 转换文本）→ 这是主评测集只取官方文本的根本原因。
3. 主评测集 **198 份超出 MVP 规格的 50~100 份**，属**边界调整**，理由：位置可信 + 样本量最大化 + Step 5 需要在同一份集合上做前后对比。
4. **无训练集、也不切独立 dev 集**：本项目的朴素 RAG 不训练模型，"训练集"不适用；不切 dev 是为了让 Step 5 的每项增强都能在**同一份固定评测集**上做前后对比（切开会失去可比性）。调 prompt 时只做人工 badcase 归因（Step 4 要求），不拿评测集当调参集。

### 0.7 切块参数与实测依据（Step 2.5 定稿）

**策略**：方案 B —— 段落为主 + 超长段落二级切
**脚本**：`scripts/analyze_chunking.py`（定参数）、`scripts/build_chunks.py`（执行切块）
**参数**：`CHUNK_MAX_CHARS = 1200`、`OVERLAP = 200`

**实测依据（198 份主评测集合同）**

| 分布 | p50 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|
| 段落长度 | 137 | 826 | 1353 | 3193 | 6967 |
| 句子长度 | 118 | 365 | 476 | 795 | 4986 |
| 金标准证据长度 | 268 | 643 | 849 | 1388 | 2780 |

- **chunk 上限 1200**：可装下 98.1% 的证据（>1200 的仅 1.9%）；约 240~300 token，远低于 `bge-small-en` 的 512 上限
- **overlap 200**：≥ 句子 p50（118），≈ chunk 的 16.7%，与业界默认（1000/200）同量级
- 二级切分**优先在句子边界断开**，因此 overlap 对覆盖率影响很小：实测 60 / 200 / 365 的"单块完整包含率"为 94.2% / 94.2% / 94.5%，但 chunk 数量分别 +0% / +2.5% / **+6.4%** → 说明 365 的边际收益（+0.3% 覆盖）不划算
- 注意：overlap 真正的收益是"相邻块保留跨句上下文"，**本指标量不出来**，所以最后一档是"数据 + 工程惯例"的折中，不假装是纯数据结论

**切块结果（实测）**

| 数据集 | 合同数 | chunk 数 | 平均每份 | chunk 长度 p50 |
|---|---|---|---|---|
| 主评测集 | 198 | 13,257 | 67.0 | 996 |
| 鲁棒性集 | 303 | 19,616 | 64.7 | 1,009 |

**金标准证据的覆盖情况（主评测集 1,405 条证据）**

- 94.2% 被单个 chunk **完整包含**；≥80% 覆盖率合计 **97.1%**；<50% 仅 **0.1%**
- **已知缺口**：**5.8%** 的证据无法被任何单个 chunk 完整包含（多为跨段落的长证据）
  → 因此 Step 4 的 context recall **必须按"检索结果集合"计算，不能按单块计算**；同时也说明检索阶段返回多个相邻 chunk 是必需的，靠调大 overlap 解决不了

### 0.8 技术选型记录（Step 3 前置）

| 组件 | MVP 原定 | 实际采用 | 理由 | 备注 |
|---|---|---|---|---|
| **Embedding** | `bge-small-en` | **`bge-m3`**（本地已有：`rag_kb_service/models/bge-m3`，2.19 GB / 1024 维 / 8192 token 上限） | 避免重复下载 2.2 GB；模型质量更强 | 见下方「实测记录」 |
| **Reranker**（精排） | 不做（MVP） | **不做** | `bge-reranker-v2-m3` 属 Step 5.3；现在接入会让 baseline 变强，Step 5.3 再对比时就没有干净的"before" | 本地已具备该模型（2.19 GB），Step 5.3 可直接复用 |
| 向量库 | Chroma | Chroma | 按 MVP | |
| LLM | 云 API | DashScope（`qwen-turbo` 起步） | 已有 key | 结构化输出稳定性待 Step 3 实测，不稳则换 `qwen-plus`（需核实价格与模型名） |

**Embedding 实测记录（Step 3.2）**

| 项目 | CPU（`torch 2.11.0+cpu`） | **GPU（`torch 2.11.0+cu128`，RTX 3060）** |
|---|---|---|
| 模型加载耗时 | 2.1 秒 | 4.1 秒 |
| 嵌入吞吐 | 3.3 条/秒 | **44.4 条/秒（13.5×）** |
| 主评测集全量 13,257 chunk | 约 67 分钟 | **约 5.0 分钟** |
| 鲁棒性集全量 19,616 chunk | 约 99 分钟 | **约 7.4 分钟** |
| 单条 query 编码 | 0.107 秒 | **0.056 秒** |
| 最优 batch_size | 8 | 8 |

其他实测：向量维度 1024；最大序列长度 8192 token（我们的 chunk 约 250 token，远未触顶）；模型目录 2,187 MB。

**环境变更记录（已确认采用方案 1）**

- 把 `langchain1.2` 环境中的 `torch 2.11.0+cpu` **替换**为 `torch 2.11.0+cu128`（同版本号，仅换 build）
- wheel 来源：官方 `download.pytorch.org/whl/cu128`，**2.56 GB**，**SHA256 校验通过**
- 验证结果：`cuda_ok True | cuda 12.8 | device NVIDIA GeForce RTX 3060`
- **回滚命令**：`pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu`
- 本地 `D:\edgeLoad` 中原有的 `torchaudio/torchvision +cu128` 轮子为 **cp312**，与本环境（Python 3.13）不兼容，且缺少 torch 本体 → 未使用

**踩到的三个坑（已修）**

1. **本地目录缺 `1_Pooling/config.json`**：`sentence-transformers` 会**静默**退化为 mean pooling，而 BGE 系列必须用 CLS pooling → 检索质量会悄悄变差、且不报任何错。修法：显式构造 `Pooling(dim, pooling_mode="cls")`。
2. **`sentence-transformers 6.1` 的 API 变更**：`pooling_mode_cls_token` 已弃用（改用 `pooling_mode`）；`get_word_embedding_dimension()` 已改名 `get_embedding_dimension()`；`sentence_transformers.models` 已迁移到 `sentence_transformers.sentence_transformer.modules`。
3. **`str.splitlines()` 读 JSONL 会被"看不见的换行"切断**（Step 3.3 暴露）：PDF 转换文本里含 **U+2028（LINE SEPARATOR）17 个**，`json.dumps(ensure_ascii=False)` 不会转义它（JSON 规范允许字符串里未转义的 U+2028），但 `splitlines()` 把它当换行 → 一条记录被拦腰切断 → `JSONDecodeError: Unterminated string`。
   - **修法**：在 `text_utils.py` 提供唯一的 `read_jsonl()`（按文件对象迭代，只认 `\n`）与 `write_jsonl()`（写完**自检**文件行数 == 记录数），全项目 4 处调用点统一替换。
   - **教训**：① `splitlines()` 会按 10 种字符断行，JSONL 的"行"只应由 `\n` 定义；② 这个坑只在**鲁棒性集（PDF 转换文本）**上暴露，主评测集（官方 txt）永远碰不到 —— **脏数据集的第一个价值就是逼出这类潜伏问题**。

**工程约束（GPU 之后已放宽）**

- 索引仍然**持久化 + 增量构建**：已存在的 chunk 跳过、中断可续跑（虽然全量只要 5 分钟，但增量构建能避免"改了几个 chunk 就重建全量"）
- 全量嵌入时间已可接受 → **主评测集与鲁棒性集都可以建索引**
- 可选优化（记入 backlog）：fp16/bf16 推理可能再快约一倍，但会轻微改变向量值 → 为保持 Step 4/5 指标可复现，MVP 用 fp32
- 任何改动若会改变 chunk 集合，都要先评估是否触发重建
- 向量库采用**单 collection + metadata 区分**：一个 `contract_chunks` 同时存主评测集与鲁棒性集，靠 `contract_id`（全局唯一）与 `split` 过滤。理由：检索本来就按 `contract_id` 过滤，建多 collection 只会增加配置面。代价：**任何"全量统计"必须显式带 `where={"split": ...}`**，不能直接用 `collection.count()`

**backlog（累计）**

| 编号 | 内容 | 触发条件 |
|---|---|---|
| 5.3 | `bge-reranker-v2-m3` 精排（本地模型已就绪，可直接复用） | Step 5 |
| 5.8 | Indemnification 弱标注集自建（LLM 预标注 + 人工抽检） | Step 5 |
| 5.9（候选） | Embedding 模型对比：`bge-small-en` vs `bge-m3`（同一评测集前后对比；预估 bge-small-en 建索引仅约 4 分钟） | Step 5 |
| — | fp16/bf16 推理加速（预计再快约一倍，但会轻微改变向量值） | 需要优化吞吐时 |
| — | 中文合同版本（需自建数据集；中文无 CUAD 对标的公开标注） | Step 5 之后 |

### 0.9 检索层实测与查询构造决策（Step 3.4）

**小型实测（30 个样本：每条款取 3 条"有金标准 chunk"的合同）**

| 查询构造方式 | recall@3 | recall@5 | recall@10 | 平均命中位置 | 中位 |
|---|---|---|---|---|---|
| full：中文描述 + 英文信号词 | 70.0% | 73.3% | 83.3% | 2.5 | 2 |
| **signals_en：英文条款名 + 英文信号词（采用）** | **76.7%** | **80.0%** | 83.3% | **1.9** | **1** |

**决策**：baseline 的检索查询 = **英文条款名 + 英文信号词**（`ClauseSpec.retrieval_query`）。
理由：中文描述会稀释查询向量（+6.7pp 差异）；**中文判定标准留给 prompt，英文信号词留给检索**，分工清晰。

**命中位置的分布**：平均 1.9、中位 1 → top-k 取 **k=5** 起步是安全的（`recall@10` 与 `recall@5` 只差 3.3pp）。

**两个必须记住的观测（影响 Step 4 归因与 Step 5 增强）**

1. **"未命中金标准 chunk" ≠ "检索失败"**：8 个未命中样本里，至少 3 个其实召回了**主题相关**的 chunk（例如 `03` 的 Top1 就是 `6.1 Assignment. No assignment of this Agreement...`），只是金标准标注的是同一主题的另一处位置。
   → **存在性判定仍可能正确**。因此 Step 4 的"未召回"归因必须区分：① 完全没找到相关内容；② 找到了相关内容但不是金标准那一处。
2. **单例根因已定位（05 exclusivity）**：金标准原文含 `exclusive`（全文出现 11 次），但长信号词清单把它排到 10 名之外；改用短短语 `exclusive distributor appointment` 后，金标准 chunk 回到 **第 3 位**。
   → 说明失败模式是**查询过度泛化**，而这类"信号词字面出现在原文里却排不上"的情况，正是 **Step 5.2 混合检索（BM25）** 最擅长修的；短核心短语的做法记入 **Step 5.4 查询改写** 的待验证假设。

**局限说明**：以上结论基于 30 个样本，属**初步证据**；Step 4 会用 198 份全量评测集复核查询口径与 k 的取值。
| 5.8 | Indemnification 弱标注集自建（LLM 预标注 + 人工抽检） | Step 5 |
| 5.9（候选） | Embedding 模型对比：`bge-small-en` vs `bge-m3`（同一评测集前后对比） | Step 5 |
| — | 中文合同版本（需自建数据集，中文无 CUAD 对标的公开标注） | Step 5 之后 |

---

## 1. 十类条款总览

> CUAD 标签名为**暂定**，Step 2 会以数据集实际字段逐一核对（不编造）。

| # | 中文名 | 英文名 | CUAD 原始标签（暂定） | 状态 |
|---|---|---|---|---|
| 1 | 争议解决与适用法律 | Governing Law / Dispute Resolution | `Governing Law` | ✅ 定稿 |
| 2 | 控制权变更 | Change of Control | `Change Of Control` | ✅ 定稿 |
| 3 | 转让限制 | Anti-Assignment | `Anti-Assignment` | ✅ 定稿 |
| 4 | 竞业限制 | Non-Compete | `Non-Compete` | ✅ 定稿 |
| 5 | 独家排他 | Exclusivity | `Exclusivity` | ✅ 定稿 |
| 6 | 终止与通知期 | Termination & Notice Period | `Termination For Convenience`（**已收窄，见 §6.0**） | ✅ 定稿（v0.9.2） |
| 7 | 自动续期 | Renewal Term | `Renewal Term` | ✅ 定稿 |
| 8 | 责任上限 | Cap on Liability | `Cap On Liability` | ✅ 定稿 |
| 9 | 违约金 | Liquidated Damages | `Liquidated Damages` | ✅ 定稿（口径变更，见 §0.6）|
| 10 | 知识产权归属 | IP Ownership Assignment | `Ip Ownership Assignment` | ✅ 定稿 |

---

# 条款 1：争议解决与适用法律（Governing Law / Dispute Resolution）

## 1.1 它管什么

两件事，经常写在同一段里：

1. **适用法律（Governing Law）**：本合同按哪个法域的法律来解释 —— 如 `the laws of the State of Delaware`。
2. **争议解决（Dispute Resolution）**：出了争议去哪儿解决 ——
   - 法院诉讼：`the courts located in ...`、`venue`、`exclusive / non-exclusive jurisdiction`
   - 仲裁：`arbitration`（机构如 AAA / JAMS / ICC / HKIAC / SIAC）
   - 配套安排：放弃陪审团审判（`waive trial by jury`）、律师费由败诉方承担（`prevailing party ... attorneys' fees`）、可申请禁令救济（`injunctive relief`）

## 1.2 防范什么风险

- **法律适用不确定** → 双方各挑对自己有利的法域（forum shopping），结果不可预期、谈判被拖长
- **争议解决地不确定** → 被拖到对方主场打官司/仲裁，时间与律师费失控（**这是你答对的那一半**）
- **判了执行不了** → 跨境判决/裁决能否被承认与执行存在不确定性
- **该条款完全缺失** → 由法院依冲突法规则（conflict of laws）推定，是最不可控的情形

## 1.3 命中标准（存在性判定）

合同正文（含正文明确引用的附件）出现下列任一**实质性约定**，即判定为「存在」：

| 信号类型 | 典型表述 |
|---|---|
| 适用法律 | `governed by` / `construed in accordance with` / `shall be interpreted under` + 具体法域名 |
| 法院管辖 | `exclusive (non-exclusive) jurisdiction`、`venue`、`submit to the jurisdiction of the courts of` |
| 仲裁 | `arbitration`、`arbitral tribunal`、`AAA` / `JAMS` / `ICC` / `HKIAC` / `SIAC` |
| 配套 | `waive ... trial by jury`、`prevailing party ... attorneys' fees`、`injunctive relief` |

## 1.4 边界情况

| 情形 | 判定 | 说明 |
|---|---|---|
| `... in any jurisdiction in which it operates` | ❌ 不算 | 泛指"任何司法管辖区"，属合规义务，不是管辖约定 |
| `jurisdictional requirements` | ❌ 不算 | 此处 jurisdiction 指监管要求，不是法院管辖 |
| `without regard to its conflict of laws principles` | ⚠️ 属片段但非风险信号 | 常规技术性措辞，出现时应与主干句合并成一条证据 |
| 条款写在附件里（`Exhibit A: Governing Law: Delaware`） | ✅ 算，但标注位置 | 存在性为真；证据位置须指向附件 |
| 正文仅作引用（`as set forth in Section 12.4`） | ✅ 算，但标注"引用位置" | 置信度可能略低，Step 4 会单独看这类 case |

## 1.5 风险三档（草案，待你挑战）

| 档位 | 判读依据 | 例特征 |
|---|---|---|
| **高** | 条款缺失；或明显对方主场 + 专属管辖 + 无中立选项 | `exclusive jurisdiction of the courts located in [对方所在州]`，且全文无仲裁约定 |
| **中** | 有约定但成本/不确定性偏高，或条款内部自相矛盾 | 异地诉讼、境外仲裁；同一合同既约定仲裁、又约定法院专属管辖 |
| **低** | 规则清晰、地缘中立或在我方所在地、内部一致 | 仲裁地在中立地/我方城市；双方所在地法院非专属管辖 |

## 1.6 正例（存在，用于 Step 2 数据筛选参照）

```
This Agreement shall be governed by and construed in accordance with the laws
of the State of New York, without regard to its conflict of laws principles.
Each party irrevocably submits to the exclusive jurisdiction of the state and
federal courts located in New York County, New York.
```

→ 存在性：✅ ｜ 风险：取决于"我方"在哪（非纽约方 = 中/高）

## 1.7 反例（容易被误判为存在）

```
Vendor shall comply with all applicable laws and regulations in any
jurisdiction in which it operates.
```

→ 存在性：❌ ｜ 原因：这是合规义务条款，未约定适用法律，也未约定争议解决地

---

# 条款 2：控制权变更（Change of Control）

## 2.1 它管什么

当一方**"换老板"了** —— 被收购、控股股东易主、实际控制人改变、发生合并（merger）、出售几乎全部资产 —— 这份合同接下来怎么处理：

- 另一方能不能因此**解除合同**（terminate）？
- 还是要**事先书面同意**（prior written consent）？
- 还是约定"对受让方继续有效"（自动延续）？

## 2.2 防范什么风险

- **"你是谁"不重要，"你背后是谁"才重要**：我签合同看中的是你的履约能力、技术、资信。你被收购后公司主体没变，但老板换了，履约意愿和能力可能完全不同。
- **落到竞争对手手里**：对方被我的竞争对手收购 → 我的独家条款、定价、商业信息间接流到对手那里。
- **对被收购方自己**：这类条款是融资和退出的障碍（买家不愿接手"控制权一变就崩"的合同），谈判时会被压价。
- **该条款缺失** → 对方被收购后你**没有任何反应权**，只能被动接受新老板。

## 2.3 命中标准（存在性判定）

| 判断要点 | 说明 |
|---|---|
| 有"触发事件"定义 | `Change of Control` 定义句，如 `means ... acquires ... majority of the voting securities` |
| 有"触发后果" | 解约权（`may terminate`）、同意权（`prior written consent`）、通知义务（`shall notify`）、或"对受让方继续有效" |
| 只有定义、没有后果 | **仍算存在**，但风险判读要注明"无实际约束力" |

## 2.4 边界情况（像但不算）✅ 已定稿

| 情形 | 判定 | 理由 |
|---|---|---|
| `This Agreement merges all prior discussions and constitutes the entire agreement.` | ❌ 不算 | `merger` 此处是"并入/取代"，属**完整协议条款（merger clause）**，与控制权变更无关 |
| `This Agreement shall be binding upon and inure to the benefit of the parties and their respective successors and assigns.` | ❌ 不算 | 属**承继条款**：只说明"约束力延伸到受让人"，**既没限制转让行为、也没有触发后果**。本项目最高频的假命中（Step 4 重点观测项） |
| `Neither party may assign ... except ... in connection with a merger or sale of all or substantially all of its assets.` | ⚠️ 归**转让限制**，不算控制权变更 | 该句在做"能不能转让本合同"的动作，`merger` 只是转让的**例外条件**。但它证明触发词跨类别共用 → 见 §0.2 铁律① |

## 2.5 风险三档

> 判断依据（三问法）：① 有触发事件定义吗？② 触发后我方能做什么（解约权 > 同意权 > 仅通知）？③ 双方是否对称？

| 档位 | 判定 |
|---|---|
| **高** | 无该条款（对方被收购后我方**无任何反应权**）；或触发后我方**无任何权利**，仅被要求配合 |
| **中** | 有触发事件，但我方只能"要求对方取得同意"或"仅收到通知"（**被动响应**） |
| **低** | 触发后我方有**解约权**，且权利义务对双方对称（**可主动退出**） |

> 阶梯记忆法：**高 = 没反应权｜中 = 只能被动响应｜低 = 可主动退出**。此阶梯后续可复用到条款 5（独家）、6（终止）的风险分档。

## 2.6 正例

```
If a Change of Control occurs with respect to Licensee, Licensor may terminate
this Agreement upon thirty (30) days' prior written notice to Licensee.
```

中文：若被许可方发生控制权变更，许可方可提前 30 天书面通知解除本合同。
→ 存在性：✅（触发事件 + 后果 = 解约权）

## 2.7 反例

```
The parties acknowledge that this Agreement merges all prior discussions and
constitutes the entire agreement between them.
```

中文：双方确认本协议取代此前所有讨论，构成双方之间的完整协议。
→ 存在性：❌（`merges` 此处是"并入/取代"，属完整协议条款，与控制权变更无关）

---

# 条款 3：转让限制（Anti-Assignment）

## 3.1 它管什么

这份合同能不能**"转手"给别人** —— 把权利或义务转让（assign / transfer）给第三方，需不需要对方**事先书面同意**（prior written consent）。

生活类比：**租房转租**。你租的房子能不能转租给第三人？房东要不要点头？如果谁都能接手，房东根本不知道最后住进去的是谁。

## 3.2 防范什么风险

- **履约对象失控**：对方把合同转给一家没有履约能力的空壳公司 → 你想追责都追不到人。
- **落到竞争对手手里**：转给竞争对手 → 你的价格、商业信息间接泄露。
- **反向风险（容易被忽略）**：限制太死也会伤到**我方自己** —— 我方要融资、重组、把业务卖给买家时，会被这条卡住。所以实务中常见"双向限制 + 例外清单"。
- **该条款缺失** → 能否转让回到默认规则，不确定性高（一般而言权利比义务更容易被转让）。**结论：能转让的边界必须写清楚。**

## 3.3 命中标准（存在性判定）

| 类型 | 典型表述 | 判定 |
|---|---|---|
| 禁止/限制型 | `shall not assign`、`may not assign`、`shall not transfer` | ✅ 命中（最典型） |
| 同意要求型 | `without the prior written consent of the other party` | ✅ 命中 |
| 例外/豁免型 | `except ... to its Affiliate`、`in connection with a merger or sale of all or substantially all of its assets` | ✅ 命中（但会**削弱**限制力度，影响风险档） |
| 单向许可型 | `Licensor may assign ... to any third party without consent` | ✅ 命中（属"转让规则"，且**风险高**：不对称） |
| 承继条款 | `binding upon ... successors and assigns` | ❌ **不算**（见 §0.1 陷阱表） |
| 知识产权转让 | `all right, title and interest ... shall be assigned` | ❌ 不算（属条款 10） |

## 3.4 边界情况（像但不算）✅ 已定稿

| 情形 | 判定 | 理由 |
|---|---|---|
| `This Agreement shall be binding upon ... successors and assigns.` | ❌ 不算 | 承继条款：只说效力范围，没有设门槛（同 §0.1 陷阱表） |
| `All right, title and interest in and to the Work Product shall be assigned ...` | ❌ 不算 | 被转让的**标的物**是"工作成果/知识产权"，不是"本合同本身" → 归条款 10 |
| `except ... in connection with a merger or sale of substantially all assets` | ⚠️ 算命中，但**削弱**限制力度 | 属例外条款（carve-out），对方在并购时可自由转走 → 风险上调 |

## 3.5 风险三档 ✅ 已定稿

> 本表直接来自对 E/F 两句的实测判断（人工判断 → 沉淀为标准，这正是本项目的方法）。

| 档位 | 判定 |
|---|---|
> 视角：我方 = 客户 / 买方（§0.4）。判断第一步：**谁被锁住**。

| 档位 | 判定 |
|---|---|
| **高** | **对方可自由转让，而我方被限制**（吃亏的是我方）：如 `Licensor may assign ... without the consent of Licensee`，或对方自由转而我方须经同意 |
| **中** | 双向限制但存在宽泛例外（关联方、并购自动放行），或同意权没有"不得无理拒绝"的约束 |
| **低** | 双向限制 + 需事先书面同意 + `which consent shall not be unreasonably withheld` + 例外范围窄 |

## 3.6 正例

```
Neither party shall assign this Agreement, in whole or in part, without the
prior written consent of the other party.
```

中文：任何一方未经对方事先书面同意，不得全部或部分转让本协议。
→ 存在性：✅（双向 + 同意要求，结构对称）

## 3.7 反例

```
This Agreement shall be binding upon and inure to the benefit of the parties
and their respective successors and assigns.
```

中文：本协议对双方及其各自的承继人与受让人具有约束力并为其利益服务。
→ 存在性：❌（承继条款，只说效力范围，没有设门槛）

---

# 条款 4：竞业限制（Non-Compete）

## 4.1 它管什么

约定一方（通常是要退出的一方：员工、被许可方、卖方、供应商）在**特定期限、特定地域**内**不得从事与对方相竞争的业务**。

生活类比：**"分手后两年内不许在同一条街开同类店铺"**。合理范围叫保护，过度范围就叫"锁死"。

## 4.2 防范什么风险

- **权利方（提出限制的一方）怕**：对方拿着我的技术、客户名单、定价信息，转身去帮竞争对手，或干脆自己干。
- **被限制方怕**：生计/业务自由被锁死 —— 没期限、没地域、全球范围、又没补偿，等于签了"卖身契"。
- **可执行性风险（重要）**：某些法域对竞业限制的可执行性极其严格（例如美国加州对企业与员工之间的竞业条款基本不予执行）。**具体法域的规则需要在 Step 3 按适用法律核实后再写进 prompt**，不要凭印象下结论。
- **该条款缺失** → 对方在合作结束/股权转让后可以直接做竞争业务，你只能看着。

## 4.3 命中标准（存在性判定）

| 要素 | 典型表述 |
|---|---|
| 限制主体 | `Vendor shall not` / `Neither party shall` |
| 限制行为 | `engage in any business that competes`、`directly or indirectly`、`own, manage, operate, control` |
| 期限 | `during the Term and for two (2) years thereafter` |
| 地域 | `within the Territory`、`worldwide` |
| 例外 | `except ...`、`passive investment of less than 1%` |

> ⚠️ Step 2 待复核：CUAD 里把"招揽"单独标注为 `No-Solicit Of Customers` / `No-Solicit Of Employees`，本项目只有 10 类，**这两类如何归并需要在实际标注数据上复核后再定**（当前暂定：单独出现不算竞业限制，与竞业条款同段出现则并入同一证据）。

## 4.4 边界情况（像但不算）✅ 已定稿

| 情形 | 判定 | 理由 |
|---|---|---|
| `Vendor shall not solicit for employment any employee of the Company.` | ❌ 不算 | 它限制的行为是"**挖人**"，不是"从事竞争业务"。CUAD 另有 `No-Solicit Of Employees` / `No-Solicit Of Customers` 标签 → **Step 2 复核归并规则**（暂定：单独出现不算；与竞业条款同段出现则并入同一证据） |
| `The Company may engage in any business, including businesses that compete with Vendor.` | ❌ 不算 | 这是**放弃竞争限制**的声明，方向正好相反 |
| `Distributor shall sell the Products exclusively to End Users ...` | ❌ 不算竞业，归条款 5 | 独家约束的是"**与谁交易**"，不是"能不能从事竞争业务" |
| `shall not, directly or indirectly, engage in any business that competes ...` | ⚠️ 算命中，但**上调风险** | `directly or indirectly` + `any business` 范围过宽 |

## 4.5 风险三档 ✅ 已定稿

> 判读用 §0.2.1 五问法。视角：我方 = 客户 / 买方（§0.4）。**第一步永远是问"这条在限制谁"**：
> - 限制**对方**（如客户限制供应商）→ 方向对我方有利，基准风险**下调**（但仍需标注可执行性风险）；
> - 限制**我方**（如供应商限制客户不得与竞品合作）→ 我方被锁，基准风险**上调**。

| 档位 | 判定 |
|---|---|
| **高** | 限制**我方**，且无期限或无地域限制（`at any time` / `worldwide`），或叠加"直接或间接从事任何业务"的超宽范围，且**无对价补偿** |
| **中** | 限制**我方**，但有明确期限 + 明确地域 + 商业对价；或限制对方但范围过宽（`worldwide` + 无期限 + 无例外） |
| **低** | 限制对象是**对方**且有明确期限 / 地域 / 例外（如小额被动投资除外）；或双向限制且范围窄 |

> **实测修正**：H 句（限制 Vendor = 对方，2 年 + 明确地域）在原判读中记为"中"，按固定视角复核后应为 **低**（对我方有利），但须在说明中标注"`any business` + `directly or indirectly` 范围过宽 → 可执行性存疑"。

## 4.6 正例

```
During the Term and for two (2) years thereafter, Vendor shall not, directly or
indirectly, engage in any business that competes with the Company within the
Territory.
```

中文：在本协议期限内及此后两年内，供应方不得在约定地域内直接或间接从事与公司相竞争的业务。
→ 存在性：✅（限制主体=供应方；行为=竞争业务；期限=2 年；地域=明确）

## 4.7 反例

```
The Company may, at any time, engage in any business, including businesses that
compete with Vendor, without restriction.
```

中文：公司可在任何时候从事任何业务，包括与供应方相竞争的业务，不受限制。
→ 存在性：❌（这是**保留竞争自由**的声明，不是限制条款；方向相反）

---

# 条款 5：独家排他（Exclusivity）

## 5.1 它管什么

约定一方（或双方）在特定期限内**只能与对方做生意**，不得与第三方做同类交易。常见形态：

- 独家供应（sole supplier）、独家采购（exclusive purchasing）
- 独家经销 / 独家代理（exclusive distribution）
- 最惠待遇（Most Favored Nation, MFN）—— 常与独家捆绑出现

生活类比：**"这个店面只能卖我的品牌，隔壁牌子不许上架"**；反过来也成立——**"你只能从我这一个供应商进货"**。

## 5.2 防范什么风险

- **提出方怕**：对方把产能、货、渠道给了我的竞争对手，我投入的推广/铺货/授权被"搭便车"。
- **被限制方怕**：被绑定后失去议价能力与市场灵活性 ——一旦对方供货出问题、价格没优势，我也没有替代方案。
- **反垄断/竞争法风险（重要）**：排他性交易可能触碰竞争法红线（如滥用市场支配地位、限制交易）。**具体法域规则要在 Step 3 按适用法律核实后写进 prompt**，不要凭印象下结论。
- **该条款缺失** → 对方可以同时与你的竞争对手合作。

## 5.3 命中标准（存在性判定）

| 要素 | 典型表述 |
|---|---|
| 独家性 | `exclusive` / `exclusively` / `sole` / `solely` |
| 排除第三方 | `shall not sell ... to any person other than`、`shall not purchase ... from any third party` |
| 地域/领域 | `within the Territory`、`in the Field` |
| 配套承诺 | `minimum purchase commitment`（最低采购量，常与独家绑定） |
| 相邻概念 | `most favored nation`、MFN（CUAD 有独立标签 → 归并待复核） |

## 5.4 边界情况（像但不算）✅ 已定稿

| 情形 | 判定 | 理由 |
|---|---|---|
| `Distributor shall have the sole discretion to determine the retail price.` | ❌ 不算 | `sole` 修饰的是"**自行决定权**"，不是独家性 |
| `the exclusive jurisdiction of the courts located in ...` | ❌ 不算 | 属条款 1 争议解决 |
| `the exclusive remedy for ... shall be ...` | ❌ 不算 | 讲的是"**唯一救济方式**"，属责任/救济条款（8、9） |
| `an exclusive license to use the Trademarks` | ⚠️ 相邻 | 属知识产权授权（CUAD 有 `License Grant`），需判断它是否实际排除了与第三方交易 |
| `most favored nation` / MFN | ⚠️ 相邻 | 最惠待遇不是排他，但常与独家绑定；归并规则待 Step 2 复核 |

## 5.5 风险三档 ✅ 已定稿

| 档位 | 判定 |
|---|---|
> 视角：我方 = 客户 / 买方（§0.4）。先分清**谁被独家绑住**：
> - 绑定**对方**（供应商只能供我方）→ 对我方有利；
> - 绑定**我方**（我只能从它采购 / 只能卖它的产品）→ 对我方不利 → 基准风险**上调**。

| 档位 | 判定 |
|---|---|
| **高** | **我方被独家绑定**（`Customer shall purchase exclusively from Supplier`）+ 对方**无供货或价格保障** + 无退出机制 |
| **中** | 我方被绑定，但有最低供货量 / 价格保护或期限较短；或对方被绑定，但我方需承担最低采购量且无质量保障 |
| **低** | 绑定对象是**对方**（供应商只能供我方），且有明确期限与终止条件；或双向独家 + 双方互有保底承诺 |

## 5.6 正例

```
During the Term, Supplier shall sell the Products exclusively to Distributor
within the Territory and shall not sell the Products to any other person
within the Territory.
```

中文：在本协议期限内，供应方应在约定地域内向经销商**独家**销售产品，并不得向该地域内任何其他人销售产品。
→ 存在性：✅（单向独家 + 地域限定明确）

## 5.7 反例

```
Distributor shall have the sole discretion to determine the retail price of the
Products in the Territory.
```

中文：经销商有**自行决定权**确定产品在约定地域内的零售价格。
→ 存在性：❌（`sole` 修饰"决定权"，与独家交易无关）

---

# 条款 6：终止与通知期（Termination & Notice Period）

## 6.0 口径变更（v0.9.2，依据 badcase_log.md BC-003）

**本类定义已收窄为「任意终止权（for convenience）」。**

| 项 | 变更 |
|---|---|
| 原定义 | 终止与通知期（含**违约终止**、通知期、终止后果） |
| **现定义** | **只有『任意终止权』算命中**：一方**无需理由**即可终止本合同（含其提前通知期） |
| 明确排除 | **违约终止（for cause）**。理由：① 对方严重违约时我方可解约，通常是**法律默认权利**，不是谈判焦点；② CUAD 金标准只标注 `Termination For Convenience` —— 不收窄会产生**系统性误报**（实测：单份合同即误报 1 类） |
| 归属调整 | "不续期通知期"归**条款 7**（不计入本类） |
| 产品影响 | 本类不再报告"有没有违约终止权/补救期" → 记入 backlog（Step 5 之后可考虑增设子字段） |

> **这是本项目第一次"因为真实数据而修改标准"**。记录在此，因为它是可复现的工程动作：先跑 → 与金标准对照 → 定位到"标准层"而非"模型层" → 改标准 → 重跑验证。

## 6.1 它管什么

合同**能不能被一方"无理由"结束，以及要提前多少天说**：

| 类型 | 说明 | 是否算本类命中 |
|---|---|---|
| 到期终止 | 期限届满自然结束（Expiration） | ❌ |
| 违约终止（for cause） | 对方违约时可终止，通常先给**补救期**（cure period） | ❌ 见 §6.0 |
| **任意终止（for convenience）** | **不需要理由**就能终止 | ✅ **本类唯一命中类型** |
| 程序要求 | 提前多少天通知（notice period）、通知形式（书面）、发给谁 | ✅（作为风险判读依据） |
| 终止后果 | 存续条款（survival）、已交付结算、保密信息返还 | ❌（不构成本类存在性） |

生活类比：**退租**。押金怎么退、提前多少天说都不是关键；关键是「**能不能无理由退租**」——那才是谈判筹码。

## 6.2 防范什么风险

- **被通知方怕**：对方有"无理由终止权"（for convenience）＋ 通知期很短 → 我备的产能、库存、人员投入瞬间打水漂，还没有过渡时间。
- **另一方怕**：自己**没有**终止权 → 被一个不合格的供应商长期绑定。
- **终止后果没写清** → 已交货/已付款/半成品怎么结算会扯皮。
- **该条款缺失** → 何时能结束合作不确定，只能靠违约或法定规则解决。

## 6.3 命中标准（存在性判定）

| 类型 | 典型表述 | 判定 |
|---|---|---|
| 任意终止 | `may terminate this Agreement for convenience`、`at any time`、`without cause` | ✅ **命中** |
| 通知期 | `upon thirty (30) days' prior written notice`、`written notice` | ✅ 命中（随任意终止权一并记录，作为风险档依据） |
| 违约终止 | `terminate upon ... breach`、`if the other party fails to perform`、`cure period` | ❌ **不算命中**（见 §6.0） |
| 终止后果 | `shall survive termination`、`return of Confidential Information` | ❌ 不算存在性 |

## 6.4 边界情况（像但不算）

| 情形 | 判定 | 理由 |
|---|---|---|
| `"Terminated Employee" means any employee whose employment has been terminated by the Company.` | ❌ 不算 | 说的是**雇佣关系的终止**，不是本合同的终止 |
| `The Term shall commence on the Effective Date and continue until December 31, 2026.` | ⚠️ 相邻 | 只是**期限约定**（CUAD 的 `Expiration Date` 是独立标签），不含终止权 |
| `This Agreement shall automatically renew ... unless notice of non-renewal ...` | ⚠️ 与条款 7 交叉 | **已决（§6.0）**：主类别归条款 7，"不续期通知期"也归 7，不计入本类 |
| `may terminate immediately upon notice` | ⚠️ 算命中，但**上调风险** | 零通知期 = 无缓冲 |
| `Either party may terminate this Agreement upon ... breach` | ❌ **不算命中** | 违约终止，见 §6.0（属默认权利） |

## 6.5 风险三档

> 视角：我方 = 客户 / 买方（§0.4）。**前提是本类已命中**（即存在任意终止权）；条款完全缺失时按 §0.3 填 `na`，不适用本表。

| 档位 | 判定 |
|---|---|
| **高** | **只有对方**有任意终止权（我方无），且通知期 ≤ 30 天、无补偿或无过渡安排 |
| **中** | 双向任意终止，通知期 60~90 天；或终止后的结算与存续条款不清晰 |
| **低** | 双向任意终止 + 通知期合理（≥ 90 天）+ 终止后结算与存续条款清晰 |

## 6.6 正例

```
Either party may terminate this Agreement for convenience upon ninety (90) days'
prior written notice to the other party.
```

中文：任何一方均可提前 **90 天**书面通知对方后**任意终止**本协议。
→ 存在性：✅（双向 + 90 天通知，对称）

## 6.7 反例

```
"Terminated Employee" means any employee whose employment has been terminated
by the Company for Cause.
```

中文："被终止雇佣的员工"指因正当理由被公司解除雇佣关系的员工。
→ 存在性：❌（`terminate` 在此指雇佣关系终止，与合同终止无关）

---

# 条款 7：自动续期（Renewal Term）

## 7.1 它管什么

合同到期后**自动延长**，除非一方在约定时间前通知"不续期"。三个关键要素：

1. **续期时长**：`successive one-year terms`
2. **不续期通知期**：`at least sixty (60) days prior to the end of the then-current Term`
3. **续期次数上限**：`shall not renew more than two (2) times`

生活类比：**会员自动续费**。到期不主动取消就自动扣款续一年 —— 商家最喜欢，用户最容易踩坑。

## 7.2 防范什么风险

- **被绑方怕**："**忘了发不续期通知**"就被动再续一期；或通知期过长（如 180 天），实务上很难操作。
- **对方怕**：我投入了长期资源，你突然不续期，我来不及找替代方案。
- **该条款缺失** → 到期自然结束、需重新谈判 —— **这反而更清晰，不构成风险**（见 §0.3：存在性与风险性是两个独立维度）。

## 7.3 命中标准（存在性判定）

| 要素 | 典型表述 |
|---|---|
| 自动续期 | `shall automatically renew`、`auto-renewal`、`shall be extended` |
| 续期方式 | `for successive one-year terms`、`for an additional term of ...` |
| 不续期通知 | `unless either party provides written notice of non-renewal`、`at least ... days prior to the end of the then-current Term` |
| 次数上限 | `up to ... additional terms`、`shall not renew more than ... times` |

## 7.4 边界情况（像但不算）

| 情形 | 判定 | 理由 |
|---|---|---|
| `This Agreement may be renewed only by a written amendment signed by both parties.` | ❌ 不算 | 需双方另行签署，**不是自动** |
| `The Term shall be one (1) year.` | ❌ 不算 | 只有期限，没有任何续期机制 |
| `upon expiration, the parties shall negotiate in good faith` | ❌ 不算 | 只是"善意协商"义务，不产生续期效果 |
| `automatically renew ... unless notice of non-renewal ...` | ⚠️ 与条款 6 交叉 | 主归条款 7，不续期通知部分与条款 6 重叠 |

## 7.5 风险三档

> 视角：我方 = 客户 / 买方（§0.4）。自动续期绑住的是**忘了发通知**的一方，所以先问：**不续期通知是不是我方的义务？我方是否容易遗漏？**

| 档位 | 判定 |
|---|---|
| **高** | 自动续期 + 不续期通知期**过长**（≥ 120 天）或通知方式苛刻（挂号信等） + **无续期次数上限**（可能无限续） |
| **中** | 自动续期 + 通知期 60~90 天，或续期需一方主动发出续期通知（方向易被忽略） |
| **低** | 有明确**续期次数上限** + 通知期合理（≤ 60 天）+ 有提前提醒机制；或**无自动续期**（到期自然结束、重新协商） |

## 7.6 正例

```
The Term shall automatically renew for successive one-year periods unless
either party provides written notice of non-renewal at least sixty (60) days
prior to the end of the then-current Term, provided that the Term shall not
renew more than two (2) times.
```

中文：本协议将自动续期一年，除非一方在当前期限结束前至少 **60 天**书面通知不续期；但续期**不得超过两次**。
→ 存在性：✅（自动续期 + 通知期 + 次数上限，三要素齐全）

## 7.7 反例

```
This Agreement may be renewed only by a written amendment signed by both parties.
```

中文：本协议只能通过双方签署书面修订文件予以续期。
→ 存在性：❌（不是自动续期，需双方另行签署）

---

# 条款 8：责任上限（Cap on Liability）

## 8.0 口径变更（v0.9.3，依据 badcase_log.md 的重排轮误报证据）

**本类只认「一般性的责任上限」，不认「绑定特定情形 / 特定救济的额度限制」。**

| 项 | 说明 |
|---|---|
| 原口径 | 出现"赔偿金额天花板" **或** "排除间接损失"即算命中 |
| **现口径** | 必须是**针对一方在合同项下责任的一般性限制**（如"累计责任不超过过去 12 个月费用"、"任何一方均不对间接损失负责"） |
| **明确排除** | ① 绑定**特定违约情形**的额度限制（例："延迟交货的罚金不超过货值 5%" —— 那属**第 9 类违约金的细节**）；② 绑定**特定事件**的免责（例："双方均不就延迟交货承担间接损失"） |
| 实测依据 | 重排轮 2 个 08 误报**全部**属此类：LIMEENERGYCO 的"延迟交货不承担间接损失"、LohaCompany 的"延迟交货罚金上限 5%" |
| 为什么这样改（**不是**为了让样本判对） | §8.2 给本类的存在理由只有一个：**把不可预测的赔付风险变成可计算、可定价的确定成本**。"特定情形的额度限制"给不出整体敞口的天花板 —— 它只限制了那一个情形。所以按本类的目的，必须要求"一般性" |

## 8.1 它管什么

约定一方（或双方）在合同项下**承担赔偿的最高金额**，通常由 4 个零件组成：

| 零件 | 说明 | 典型表述 |
|---|---|---|
| 上限金额 | 天花板是多少 | `shall not exceed`、`limited to the fees paid in the preceding 12 months` |
| 排除间接损失 | 利润损失、商誉损失等不赔 | `shall not be liable for any consequential, indirect, incidental or punitive damages` |
| 例外清单（carve-outs） | 哪些责任**不受**上限约束 | `except for ... breach of confidentiality, IP infringement, willful misconduct` |
| 索赔时效 | 多久内必须提出索赔 | `within twelve (12) months after termination` |

**本质**（你的回答抓对了）：把**不可预测的赔付风险**，变成**可计算、可定价、可投保**的确定成本。没有它，合同的赔付责任没有天花板。

## 8.2 防范什么风险

- **赔付方（乙方/供应方）**：没有上限 = 无法定价，不知道该预留多少风险成本 → **所以乙方希望有**（你说对了）。
- **接收方（客户/我方，MVP 视角）**：
  - 上限**过低**（例如只有 1 个月服务费）→ 对方出大事时我拿不到足够赔偿；
  - 上限是**单向**的（只保护对方，我方责任无上限）；
  - **例外清单过宽**（如"任何保密违约或 IP 侵权都不受上限"）→ 上限实际上形同虚设。
- **该条款缺失** → 在法定范围内，对方可索赔任何金额，尤其间接损失可能远超合同总额。

## 8.3 命中标准（存在性判定）

| 类型 | 典型表述 | 是否算命中 |
|---|---|---|
| 上限本体（一般性） | `aggregate liability ... shall not exceed`、`liability ... shall be limited to`、`Cap on Liability` | ✅ |
| 排除间接损失（一般性） | `NEITHER PARTY SHALL BE LIABLE FOR ANY ... consequential, indirect, special or punitive damages` | ✅（须为一般性表述，不带"仅就某情形"的限定） |
| 例外清单 | `except for ...`、`The limitations in this Section shall not apply to ...` | ✅（须附在上限句上，单独出现不算） |
| 索赔时效 | `no claim may be brought more than ... months after ...` | ✅（须附在上限句上） |
| **绑定特定情形的额度限制** | `the penalty ... shall not exceed 5% of the total value of the goods involved in the late delivery` | ❌ **不算**（见 §8.0：属第 9 类违约金的细节） |
| **绑定特定事件的免责** | `neither party shall have liability for consequential damages pertaining to late delivery` | ❌ **不算**（只限制了一个情形，不构成本类要的"整体天花板"） |

> ⚠️ Step 2 待复核：CUAD 中 `Cap On Liability` 与 `Uncapped Liability` 是两个独立标签。本项目的第 8 类**只覆盖"存在上限"的情形**；若合同明确写"某类责任不受限制"（uncapped），按**边界情况**处理（见 8.4）。

## 8.4 边界情况（像但不算）

| 情形 | 判定 | 理由 |
|---|---|---|
| `Nothing in this Agreement shall limit either party's liability for fraud or willful misconduct.` | ❌ 单独出现不算 | 这是**例外声明**（说明哪类责任不受限），本身**没有设定上限**；它只有和上限句一起出现时才组成完整条款 |
| `Supplier's liability for breach of confidentiality shall be unlimited.` | ⚠️ 算命中（责任分配条款），但**风险=高** | 它把最难控的风险排除在上限之外 |
| `Customer shall maintain insurance of at least US$1,000,000.` | ❌ 不算 | 属保险条款（CUAD 有 `Insurance` 独立标签） |
| `Supplier shall not be liable for any indirect or consequential damages.` | ✅ 算命中 | **一般性**地排除间接损失，是责任限制的组成部分 |
| `neither the Distributor nor the Company shall have liability for consequential or liquidated damages pertaining to late delivery` | ❌ 不算 | 见 §8.0：绑定**特定情形**（延迟交货）的免责，给不出整体敞口的天花板（**实测误报 case**） |
| `the penalty ... shall not exceed 5% of the total value of the goods involved in the late delivery` | ❌ 不算（归第 9 类） | 它是**违约金的额度上限**，属第 9 类的细节，不是合同项下的一般责任上限（**实测误报 case**） |

## 8.5 风险三档（客户视角，示范）

| 档位 | 判定 |
|---|---|
| **高** | 上限过低（远低于潜在损失）；或**单向**只保护对方；或例外清单极宽（保密、IP 侵权、数据泄露全部不受限）；或**我方义务无上限而对方有上限** |
| **中** | 双向对等上限、金额与合同规模大致匹配，但例外清单偏宽；或索赔时效过短（< 12 个月） |
| **低** | 双向对等 + 上限金额与合同金额匹配（如 12~24 个月费用或明确金额）+ 例外窄且明确 + 索赔时效合理（≥ 12 个月） |

## 8.6 正例

```
Except for the Excluded Claims, each party's aggregate liability under this
Agreement shall not exceed the total fees paid or payable by Customer in the
twelve (12) months preceding the event giving rise to the claim.
```

中文：除"例外索赔"外，任何一方在本协议项下的**累计责任**不超过索赔事件发生前 **12 个月**内客户已付或应付的费用总额。
→ 存在性：✅（双向对等 + 上限计算方式明确）

## 8.7 反例

```
Nothing in this Agreement shall limit either party's liability for fraud or
willful misconduct.
```

中文：本协议不限制任何一方因欺诈或故意不当行为的责任。
→ 存在性：❌ 单独出现不算（这是例外声明，没有设定上限）

---

# 条款 9：违约金（Liquidated Damages）

> **口径变更说明**：本类的原定对象是「赔偿条款（Indemnification）」，但 CUAD 41 类中**没有**该标签（Step 2 实测确认），因此改为「违约金」。Indemnification 移入 **Step 5.8 backlog**（用 LLM 预标注 + 人工抽检自建弱标注集）。详见 §0.6。

## 9.1 它管什么

约定"**一旦发生某种违约，就按事先定好的金额或公式赔**"，不需要去打官司证明实际损失是多少。三个要素：

| 要素 | 说明 | 典型表述 |
|---|---|---|
| 触发事件 | 什么情况要赔 | 延迟交付、逾期付款、提前终止 |
| 计算方式 | 怎么算 | `for each day of delay`、`an amount equal to 0.5% of the total fees` |
| 上限 | 最多赔多少 | `up to a maximum of 10% of the total fees` |

配套条款：`as the sole and exclusive remedy`（**唯一救济**）—— 一旦选它，就不能再按实际损失索赔。

生活类比：**快递延误赔付** ——"每延误一天赔运费的 5%，最高不超过运费本身"。事先约好金额，出事不用吵架；但金额定得离谱，可能被认定为"惩罚性违约金"而不予执行。

> ⚠️ **可执行性风险**：某些法域会把**过高**的违约金认定为惩罚性条款而调整或不予执行。**具体规则需在 Step 3 按适用法律核实后再写进 prompt**，不要凭印象下结论。

## 9.2 防范什么风险

- **我方是收钱方（对方违约）**：违约金**过低** → 对方拖延成本极低，"违约比履约划算"；**没有违约金** → 我方必须举证实际损失（举证难、周期长、律师费高）。
- **我方是付钱方（我方违约）**：违约金**过高** → 一次触发赔掉大笔利润。
- **"唯一救济"是隐藏陷阱**：若约定违约金是唯一救济且金额偏低，我方就**失去了索赔实际损失的权利**。
- **该条款缺失** → 只能按实际损失索赔，成本高、结果不确定。
- **注意**：违约金**不属于"必备条款"**，缺失不报警（见 §0.3）。

## 9.3 命中标准（存在性判定）

| 类型 | 典型表述 |
|---|---|
| 名称明确型 | `liquidated damages`、`as liquidated damages and not as a penalty` |
| 公式型 | `for each day of delay`、`per day`、`an amount equal to ...% of ...` |
| 上限型 | `up to a maximum of ...`、`shall not exceed ... in the aggregate`（特指违约金总额） |
| 唯一救济型 | `as the sole and exclusive remedy for such delay` |
| 相邻但不同 | 逾期付款利息（`interest at 1.5% per month`）、一般违约赔偿（`shall be liable for all damages`） |

## 9.4 边界情况（像但不算）

| 情形 | 判定 | 理由 |
|---|---|---|
| `Supplier shall be liable for all damages arising out of any breach.` | ❌ 不算 | **普通违约赔偿义务**：金额未事先约定，仍需举证实际损失 |
| `Supplier shall maintain commercial general liability insurance of at least US$1,000,000.` | ❌ 不算 | 属保险条款（`Insurance`），投保 ≠ 约定赔偿额 |
| `Each party covenants not to sue the other with respect to ...` | ❌ 不算 | 属"承诺不起诉"（`Covenant Not To Sue`） |
| `Customer shall pay interest on overdue amounts at 1.5% per month.` | ⚠️ 相邻 | 逾期利息功能接近违约金，但通常被视为"迟延利息" → 归并规则待 Step 2 复核 |
| `liability ... shall not exceed the fees paid in the preceding 12 months` | ❌ 不算 | 属**责任上限**（第 8 类），不是约定赔偿额（见 §0.5 关联） |

## 9.5 风险三档（客户视角）✅ 已定稿

> 判读两问：① 这条**约束谁**（对方违约时我方收钱，还是我方违约时我方付钱）？② 计算方式、上限、救济性质（是否唯一救济）是否写清？

| 档位 | 判定 |
|---|---|
| **高** | 约束**我方**且金额高、触发条件宽；或约束对方但金额过低（远低于可证明损失）；或约定为**唯一救济**且金额偏低（我方失去索赔实际损失的权利） |
| **中** | 约束对方、金额与损失量级大致匹配，但缺总额上限，或触发事件偏窄（保护力一般） |
| **低** | 约束对方 + 计算方式明确（每日 X%）+ 累计上限明确 + 明确"不影响我方索赔其他救济"（non-exclusive remedy） |

## 9.6 正例

```
If Supplier fails to deliver the Deliverables by the Delivery Date, Supplier
shall pay to Customer, as liquidated damages and not as a penalty, an amount
equal to 0.5% of the total fees for each day of delay, up to a maximum of 10%
of the total fees.
```

中文：若供应方未在交付日交付，应按每日总费用的 **0.5%** 向客户支付违约金（**明示为违约金而非罚款**），累计不超过总费用的 **10%**。
→ 存在性：✅（触发事件 + 计算方式 + 上限 + 明示性质）

## 9.7 反例

```
Supplier shall be liable for all damages arising out of any breach of this
Agreement.
```

中文：供应方应就任何违反本协议的行为造成的全部损害承担责任。
→ 存在性：❌（普通违约赔偿义务，金额未事先约定）

---

# 条款 10：知识产权归属（IP Ownership Assignment）

## 10.0 口径变更（v0.9.3 → v0.9.4，依据 badcase_log.md 的两轮实测）

**本类只认「归属 / 转让的约定动作」，不认「谁拥有自己 IP 的现状声明」。**

| 版本 | 判据 | 实测结果（重排轮 30 份） |
|---|---|---|
| v0.9.2（原口径） | "各自保留 + 授权"也算命中 | 6 个误报（全是背景 IP 保留声明） |
| v0.9.3 | 必须是**合同项下新产生成果**的归属安排 | ✅ 6 个误报全清；❌ 但误伤 3 个真命中（合同里明明有 `will assign` / `transfer` 条款） |
| **v0.9.4（现行）** | **看动作**：出现归属 / 转让的**约定动作**即算命中 —— **标的可以是新产生成果，也可以是明确列明的既有 IP** | 见下方核对依据 |

**判据细则**：

- ✅ **命中**：`assign` / `transfer` / `shall be owned by` / `shall be the property of`、明确列明标的（域名、NDA、商标等）的归属安排，以及**转让的配套义务**（`Recordation`、`execute documents to perfect such assignment` —— 登记以转让为前提）
- ❌ **不算**：单纯 `retain ownership` / `is the owner of all rights` / `licensors own all right, title and interest` 这类**现状声明**；单纯"不转让"声明（`Nothing shall be construed as transferring ...`）；单纯授权（`grants a license`）

**为什么这样改（一致性论证，不是为了让样本判对）**：这正是 §0.2 铁律①「**看动作，不看词**」在本类的落地 ——
"保留 IP"是**现状**（没有发生归属安排），"转让 / 归属"是**动作**（发生了安排）。
v0.9.3 我错在把判据放在"标的物是否为新产生成果"上；真正有区分力的是"**有没有动作**"。

| 实测核对依据 | case | 判据下的结果 |
|---|---|---|
| 6 个误报 | WHITESMOKE / Reynolds / PREMIERBIOMEDICAL / OPERA / BORROWMONEY / ChinaRealEstate | 全是现状声明 → 应判 ❌（v0.9.3 已达成） |
| 3 个误伤 | HERTZGLOBAL（`THC will assign ... the THC ERB Domains`）、Cerence（`recordation of the transfers`）、PACIRA（`EKR shall promptly transfer the Transferred NDA`） | 有动作 → 应判 ✅（v0.9.4 要收回） |

## 10.1 它管什么

合同产生的**知识产权归谁**。必须分清三层：

| 层 | 名称 | 说明 |
|---|---|---|
| 1 | **背景知识产权**（Background IP） | 合作前各自已有的技术/作品 → 通常各自保留所有权，只授予对方**许可**（license） |
| 2 | **新产生成果**（Work Product / Foreground IP） | 合作过程中新产生的代码、设计、报告、发明 → **归属是谈判焦点** |
| 3 | **授权范围**（License） | 若不转让，是否授予许可、是否排他（exclusive）、是否可再授权 |

生活类比：**一起装修房子**。你自带的老家具还是你的（背景 IP）；装修期间**新打的柜子归谁**，必须事先说清（新产生成果）。

## 10.2 防范什么风险

- **客户怕**：付了钱却**不拥有成果**（只拿到使用权）→ 不能自由修改、再分发、申请专利；或成果里混入了承包方的背景 IP，日后被"卡脖子"甚至被索赔。
- **承包方怕**：把"通用方法论/工具"也一并转让 → 以后复用自己沉淀的能力都受限。
- **该条款缺失** → 归属回到法定默认规则（不同法域差别很大，例如受雇创作 vs 独立承包方创作结论可能相反）→ **Step 3 必须按适用法律核实，不要凭印象写进 prompt**。
- **与条款 9 联动**：IP 侵权索赔是最常见的赔偿触发事由（见 §0.5）。

## 10.3 命中标准（存在性判定）

| 类型 | 典型表述 | 是否算命中 |
|---|---|---|
| 转让（标的为新成果） | `hereby assigns all right, title and interest in and to the Work Product` | ✅ |
| 转让（标的为明确列明的资产） | `THC will assign all right, title and interest in and to the THC ERB Domains`、`EKR shall promptly transfer the Transferred NDA to PPI` | ✅（**看动作**，不要求标的是新成果） |
| 归属约定 | `all Work Product shall be the sole and exclusive property of Client`、`IP developed under this Agreement shall be owned by ...` | ✅ |
| 转让的配套义务 | `Recordation`（转让登记）、`shall execute such documents as reasonably requested to perfect such assignment` | ✅（登记 / 完善手续**以转让为前提**） |
| 保留背景 IP（**现状声明**） | `each party retains all right, title and interest in its Background IP`、`HSI will retain ownership of ... Trademarks` | ❌ **不算**（没有发生归属安排） |
| 单纯权属声明 | `Licensor is the owner of all rights ...`、`Supplier shall retain all rights to its IP in the Products` | ❌ **不算**（同上） |
| 单纯不转让声明 | `Nothing ... shall be construed as transferring the IP of either Party` | ❌ **不算** |
| 授权型 | `grants ... a non-exclusive, worldwide, royalty-free license` | ⚠️ 相邻（授权 ≠ 归属，归 `License Grant` 一族） |

## 10.4 边界情况（像但不算）

| 情形 | 判定 | 理由 |
|---|---|---|
| `"Intellectual Property" means all patents and copyrights of either party existing as of the Effective Date.` | ❌ 不算 | 只是**定义句**，没有约定归属规则（但"有定义无归属"本身是高风险信号） |
| `Customer shall not assign this Agreement without prior written consent.` | ❌ 不算 | `assign` 的标的物是**合同**，不是知识产权 → 归条款 3 |
| `Supplier grants Customer a non-exclusive license to use the Software.` | ⚠️ 相邻 | 授权≠归属（CUAD 有 `License Grant` 独立标签）→ 归并规则待 Step 2 复核 |
| `Distributor acknowledges that Google and/or its licensors own all right, title and interest, including all Intellectual Property ...` | ❌ 不算 | 见 §10.0：**现状声明**（谁拥有自己的 IP），没有归属 / 转让的动作（**实测误报 case**） |
| `Nothing in this Agreement shall be construed as transferring the Intellectual Property Rights of either Party or its suppliers ...` | ❌ 不算 | 纯"不转让"声明，同样没有归属动作（**实测误报 case**） |
| `THC will assign all right, title and interest in and to the THC ERB Domains` | ✅ 命中 | **有 assign 动作**、标的为明确列明的资产（v0.9.3 曾误判为不算 —— **实测误伤 case**） |
| `Section 2.02. Recordation. The relevant assignee Party ...` / `... the recordation of the transfers ...` | ✅ 命中 | 转让**登记**义务以转让为前提 ⇒ 视为存在转让安排（v0.9.3 曾误判为不算 —— **实测误伤 case**） |
| `Contractor hereby assigns ... the Work Product ...` | ✅ 命中 | 明确的成果转让 |

## 10.5 风险三档（客户视角）✅ 已定稿

> 判读两问：① **成果归谁**？② 我拿到的是**所有权**还是**有限的使用许可**？

| 档位 | 判定 |
|---|---|
| **高** | 成果归**承包方**；或我方只拿到"非排他、不可转让"的使用许可；或**归属未约定**（含"只有定义、无归属规则"）；或承包方可复用成果卖给竞争对手；或须另付费用才能取得所有权 |
| **中** | 成果归我方，但**附条件、且条件不完全由我方控制**（如需承包方另行书面确认、以"验收合格且无争议"为前提）；或未约定"不可撤销"、未约定配合完善手续义务；或承包方保留"通用工具与方法论"所有权且范围过宽 |
| **低** | 成果全部归我方 + 明确"不可撤销" + 配合完善手续义务 + 背景 IP 已列明并授予充分许可 + 与条款 9 的 IP 侵权赔偿联动 |

> **标准自纠（v0.8）**：本表初稿把"付清全款后转让"一律记为"中"。复核 L 句后修正为：**条件由我方控制（如"我方付清全款"）时判低**，但须标注两个缺口 —— 建议补充 `irrevocably`（不可撤销）与"配合完善转让手续"义务；只有条件不由我方控制时才上调为"中"。

## 10.6 正例

```
Contractor hereby irrevocably assigns to Client all right, title and interest in
and to the Work Product, and agrees to execute such documents as Client may
reasonably request to perfect such assignment.
```

中文：承做方**不可撤销地**将工作成果的全部权利、所有权和权益转让给客户，并同意签署客户为完善该转让而合理要求的文件。
→ 存在性：✅（明确转让 + 不可撤销 + 配合完善手续）

## 10.7 反例

```
"Intellectual Property" means all patents, copyrights, trademarks and trade
secrets of either party existing as of the Effective Date.
```

中文："知识产权"指任一方在生效日已拥有的所有专利、著作权、商标与商业秘密。
→ 存在性：❌（只有定义，没有归属约定）
