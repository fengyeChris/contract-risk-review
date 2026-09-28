"""
条款登记表（Clause Registry）：把 `mvp_scope.md` 的判定标准变成系统能用的数据

这是 **Step 1 → Step 3 的桥梁**：文档里写的「命中标准 / 边界反例 / 风险三档」，
在这里变成结构化的字段，被两个地方使用：

- `query_text`：用于**检索**（定义 + 信号词；**不要用条款名去搜**，那是标注者的语言）
- `criteria / boundaries / risk_tiers`：用于**组装 prompt**（模型需要判据，才能判断）

维护约定：本文件是 `mvp_scope.md` 的"可执行副本"。**改了标准文件就要同步改这里**，
两边不一致会导致"系统按旧标准判、你以为按新标准判"——这是最难查的一类问题。
"""

from dataclasses import dataclass

from schemas import ClauseId


@dataclass(frozen=True)
class ClauseSpec:

    clause_id: ClauseId
    name_zh: str
    name_en: str
    query_text: str  # 原始说明：中文定义 + 英文信号词（保留作文档）
    criteria: str  # prompt 用：命中标准
    boundaries: str  # prompt 用：边界（像但不算）
    risk_tiers: str  # prompt 用：风险三档（高/中/低）

    @property
    def retrieval_query(self) -> str:
        """
        **检索用的查询**：英文条款名 + 英文信号词（不含中文描述）。

        依据（30 样本实测，见 mvp_scope.md §0.9）：
            full（中文描述+英文信号词）: recall@5 = 73.3%，平均命中位置 2.5
            signals_en（本属性）      : recall@5 = **80.0%**，平均命中位置 **1.9**
        → 中文描述会稀释查询向量；**中文留给 prompt，英文留给检索**。
        """
        parts = self.query_text.split("常见表述：")
        if len(parts) == 2:
            return f"{self.name_en} clause: {parts[1]}"
        return self.query_text


CLAUSES: tuple[ClauseSpec, ...] = (
    ClauseSpec(
        clause_id=ClauseId.GOVERNING_LAW,
        name_zh="争议解决与适用法律",
        name_en="Governing Law / Dispute Resolution",
        query_text=(
            "适用法律与争议解决条款：本合同按哪个法域的法律解释、争议提交哪个法院或仲裁机构。"
            "常见表述：governed by / construed in accordance with the laws of / "
            "exclusive jurisdiction / venue / submit to the jurisdiction of the courts / "
            "arbitration / AAA / JAMS / ICC / waive trial by jury / attorneys' fees"
        ),
        criteria="出现『以某法域法律解释本合同』或『争议提交某法院/仲裁机构』的实质性约定，即算存在。",
        boundaries=(
            "不算命中：`in any jurisdiction`（合规义务的地域范围）；`jurisdictional requirements`（监管要求）；"
            "仅顺带提到某州法律而非管辖约定。写在附件里也算存在，但证据位置要指向附件。"
        ),
        risk_tiers=(
            "高＝条款缺失，或对方主场+专属管辖且全文无中立选项；"
            "中＝有约定但成本高或内部矛盾（既约定仲裁又约定法院专属管辖）；"
            "低＝规则清晰、地缘中立或在我方所在地、内部一致。"
        ),
    ),
    ClauseSpec(
        clause_id=ClauseId.CHANGE_OF_CONTROL,
        name_zh="控制权变更",
        name_en="Change of Control",
        query_text=(
            "控制权变更条款：一方被收购、控股股东易主、实际控制人改变、发生合并或出售实质全部资产时，合同如何处理。"
            "常见表述：change of control / change in control / merger / consolidation / "
            "sale of all or substantially all assets / majority of the voting securities / "
            "may terminate upon a change of control / prior written consent"
        ),
        criteria="有『控制权变更』的触发事件定义，或触发后果（解约权 / 同意权 / 通知义务 / 对受让方继续有效），即算存在。",
        boundaries=(
            "不算命中：`This Agreement merges all prior discussions`（完整协议条款，merges 是并入之意）；"
            "`binding upon successors and assigns`（承继条款，无触发后果）。"
        ),
        risk_tiers=(
            "高＝无该条款（对方被收购后我方无任何反应权），或触发后我方无任何权利；"
            "中＝有触发事件，但我方只能要求对方取得同意或仅收到通知；"
            "低＝触发后我方有解约权，且权利义务对双方对称。"
        ),
    ),
    ClauseSpec(
        clause_id=ClauseId.ANTI_ASSIGNMENT,
        name_zh="转让限制",
        name_en="Anti-Assignment",
        query_text=(
            "合同转让限制条款：本合同能否转手给第三方、需不需要对方事先书面同意。"
            "常见表述：shall not assign / may not assign / without the prior written consent of / "
            "shall not transfer / except to its Affiliate / in connection with a merger"
        ),
        criteria="出现『禁止/限制转让本合同』或『转让需对方事先书面同意』即算存在；单向允许某方自由转让也算（且风险高）。",
        boundaries=(
            "不算命中：`binding upon ... successors and assigns`（承继条款，只说效力范围）；"
            "`all right, title and interest in the Work Product shall be assigned`（转让标的是知识产权，属第 10 类）。"
        ),
        risk_tiers=(
            "高＝对方可自由转让而我方被限制；"
            "中＝双向限制但存在宽泛例外（关联方、并购自动放行），或同意权无『不得无理拒绝』约束；"
            "低＝双向限制 + 需事先书面同意 + 同意不得被无理拒绝 + 例外范围窄。"
        ),
    ),
    ClauseSpec(
        clause_id=ClauseId.NON_COMPETE,
        name_zh="竞业限制",
        name_en="Non-Compete",
        query_text=(
            "竞业限制条款：一方在特定期限、特定地域内不得从事与对方相竞争的业务。"
            "常见表述：non-compete / shall not engage in any business that competes / "
            "directly or indirectly / own manage operate control / during the Term and for ... years / "
            "within the Territory"
        ),
        criteria="限制特定主体在期限/地域内从事竞争业务即算存在（限制对方也算，属对我方有利）。",
        boundaries=(
            "不算命中：`shall not solicit for employment any employee`（限制的是挖人，不是竞争业务）；"
            "`The Company may engage in any business that competes with Vendor`（放弃竞争限制，方向相反）。"
        ),
        risk_tiers=(
            "高＝限制我方且无期限/无地域或范围过宽且无对价；"
            "中＝限制我方但有明确期限+地域+商业对价，或限制对方但范围过宽；"
            "低＝限制对象是对方且有明确期限/地域/例外。"
        ),
    ),
    ClauseSpec(
        clause_id=ClauseId.EXCLUSIVITY,
        name_zh="独家排他",
        name_en="Exclusivity",
        query_text=(
            "独家/排他条款：一方在特定期限内只能与对方交易，不得与第三方做同类交易。"
            "常见表述：exclusive / exclusively / sole supplier / shall not sell to any person other than / "
            "shall not purchase from any third party / minimum purchase commitment / most favored nation"
        ),
        criteria="出现『独家交易』约定（只能向对方买/卖，排除第三方）即算存在。",
        boundaries=(
            "不算命中：`sole discretion`（自行决定权，sole 修饰决定权）；"
            "`exclusive jurisdiction`（专属管辖，属第 1 类）；`exclusive remedy`（唯一救济）。"
        ),
        risk_tiers=(
            "高＝我方被独家绑定且对方无供货/价格保障、无退出机制；"
            "中＝我方被绑定但有最低供货量或期限较短；或对方被绑定但我方需承担最低采购量且无质量保障；"
            "低＝绑定对象是对方且有明确期限与终止条件，或双向独家+互有保底。"
        ),
    ),
    ClauseSpec(
        clause_id=ClauseId.TERMINATION_NOTICE,
        name_zh="终止与通知期",
        name_en="Termination & Notice Period",
        query_text=(
            "任意终止权条款：一方无需理由即可终止合同，以及终止的提前通知期。"
            "常见表述：may terminate this Agreement for convenience / terminate without cause / "
            "at any time upon written notice / termination for convenience / "
            "upon thirty (30) days' prior written notice / may terminate immediately upon notice"
        ),
        criteria=(
            "**只有『任意终止权（for convenience）』算命中**：一方无需理由即可终止本合同（含其提前通知期）。"
            "⚠️ 违约终止（`terminate upon ... breach` / `cure period`）**不算命中** —— 属法律默认权利，"
            "且 CUAD 金标准不标注（见 mvp_scope.md §6.0 口径变更）。"
        ),
        boundaries=(
            "不算命中：`Terminated Employee`（指雇佣关系终止）；"
            "`terminate upon ... breach` / `cure period`（违约终止，见 §6.0）；"
            "`The Term shall commence ... and continue until December 31, 2026`（只是期限约定，无终止权）；"
            "自动续期的『不续期通知期』归第 7 类。"
        ),
        risk_tiers=(
            "前提：本类已命中（存在任意终止权）；条款缺失时风险填 na。"
            "高＝只有对方有任意终止权（我方无）+ 通知期 ≤30 天 + 无补偿；"
            "中＝双向任意终止 + 通知期 60~90 天，或终止后结算与存续条款不清晰；"
            "低＝双向任意终止 + 通知期 ≥90 天 + 终止后结算与存续条款清晰。"
        ),
    ),
    ClauseSpec(
        clause_id=ClauseId.RENEWAL,
        name_zh="自动续期",
        name_en="Renewal Term",
        query_text=(
            "自动续期条款：合同到期后是否自动延长、提前多少天通知可以不续期、最多续几次。"
            "常见表述：shall automatically renew / auto-renewal / for successive one-year terms / "
            "unless either party provides written notice of non-renewal / at least ... days prior to "
            "the end of the then-current Term / shall not renew more than ... times"
        ),
        criteria="出现『自动续期』机制即算存在（含续期时长 / 不续期通知期 / 续期次数上限）。",
        boundaries=(
            "不算命中：`may be renewed only by a written amendment signed by both parties`（需另行签署，非自动）；"
            "只有期限约定而无续期机制。"
        ),
        risk_tiers=(
            "高＝自动续期 + 不续期通知期 ≥120 天或通知方式苛刻 + 无续期次数上限；"
            "中＝自动续期 + 通知期 60~90 天；"
            "低＝有续期次数上限 + 通知期合理（≤60 天）；或**无自动续期**（到期自然结束，规则更清晰）。"
        ),
    ),
    ClauseSpec(
        clause_id=ClauseId.CAP_ON_LIABILITY,
        name_zh="责任上限",
        name_en="Cap on Liability",
        query_text=(
            "责任上限条款：一方在合同项下承担赔偿的最高金额、是否排除间接损失、哪些责任不受上限约束。"
            "常见表述：liability shall not exceed / aggregate liability shall be limited to / "
            "cap on liability / in no event shall ... be liable for more than / "
            "consequential indirect special or punitive damages / except for the Excluded Claims"
        ),
        criteria=(
            "必须是**一般性的责任上限**：出现『累计责任不超过某金额/某基数』或『一般性排除间接损失』即算存在。"
            "⚠️ 绑定**特定情形/特定事件**的额度限制或免责**不算** —— 例如『延迟交货的罚金不超过货值 5%』属第 9 类违约金的细节，"
            "『双方均不就延迟交货承担间接损失』只限制了一个情形、给不出整体敞口天花板（见 mvp_scope.md §8.0）。"
        ),
        boundaries=(
            "单独出现不算：`Nothing ... shall limit either party's liability for fraud or willful misconduct`"
            "（只是例外声明，没有设定上限）；`Supplier shall maintain insurance of ...`（属保险条款）；"
            "**绑定特定情形的额度限制**：`the penalty ... shall not exceed 5% of the total value of the goods involved in the late delivery`；"
            "**绑定特定事件的免责**：`neither party shall have liability for consequential damages pertaining to late delivery`。"
        ),
        risk_tiers=(
            "高＝上限过低或单向只保护对方，或例外清单极宽（保密/IP 侵权/数据泄露全不受限），或我方义务无上限而对方有上限；"
            "中＝双向对等上限但例外清单偏宽，或索赔时效过短；"
            "低＝双向对等 + 上限与合同金额匹配 + 例外窄且明确 + 索赔时效合理。"
        ),
    ),
    ClauseSpec(
        clause_id=ClauseId.LIQUIDATED_DAMAGES,
        name_zh="违约金",
        name_en="Liquidated Damages",
        query_text=(
            "违约金条款：事先约定一旦发生某种违约就按固定金额或公式赔偿，不用举证实际损失。"
            "常见表述：liquidated damages / as liquidated damages and not as a penalty / "
            "for each day of delay / an amount equal to ...% of the total fees / "
            "up to a maximum of ... / as the sole and exclusive remedy"
        ),
        criteria="出现『事先约定的赔偿金额或计算公式』即算存在（触发事件 + 计算方式 + 上限）。",
        boundaries=(
            "不算命中：`shall be liable for all damages arising out of any breach`（普通违约赔偿义务，金额未约定）；"
            "`insurance of at least US$1,000,000`（保险条款）；`liability shall not exceed ...`（责任上限，属第 8 类）。"
        ),
        risk_tiers=(
            "高＝约束我方且金额高/触发宽，或约束对方但金额过低，或约定为唯一救济且金额偏低；"
            "中＝约束对方、金额与损失大致匹配但缺总额上限或触发事件偏窄；"
            "低＝约束对方 + 计算方式明确 + 累计上限明确 + 明确不影响我方索赔其他救济。"
        ),
    ),
    ClauseSpec(
        clause_id=ClauseId.IP_OWNERSHIP,
        name_zh="知识产权归属",
        name_en="IP Ownership Assignment",
        query_text=(
            "知识产权归属条款：合作过程中新产生的成果（代码、设计、报告、发明）归谁所有，背景知识产权如何保留，"
            "是否需要许可。常见表述：hereby assigns all right title and interest in and to the Work Product / "
            "shall own / shall be the sole and exclusive property of / Background IP / "
            "grants a non-exclusive worldwide royalty-free license / Work Product / Deliverables"
        ),
        criteria=(
            "**看动作**：出现知识产权**归属或转让的约定动作**即算存在 —— `assign` / `transfer` / `shall be owned by` / "
            "明确列明标的（域名、NDA、商标等）的归属安排，以及转让的配套义务（`Recordation`、配合完善手续）。"
            "标的**可以是**合同项下新产生的成果，**也可以是**明确列明的既有 IP。"
            "⚠️ **不算**：单纯『谁拥有自己的 IP』的**现状声明**（`retain ownership` / `is the owner of` / "
            "`licensors own all right, title and interest`）、单纯『不转让』声明、单纯授权（见 mvp_scope.md §10.0）。"
        ),
        boundaries=(
            "不算命中：只有 `\"Intellectual Property\" means ...`（定义句，无归属规则）；"
            "`Customer shall not assign this Agreement`（标的物是合同，属第 3 类）；"
            "**现状声明**：`Licensor is the owner of all rights ...` / "
            "`Distributor acknowledges that Google and/or its licensors own all right, title and interest`（实测误报来源）；"
            "**纯不转让声明**：`Nothing ... shall be construed as transferring the IP of either Party`；"
            "单纯授权（License Grant）≠ 归属。"
            "✅ 但 `THC will assign ... the THC ERB Domains` 与 `recordation of the transfers` **算命中**（有归属/转让动作）。"
        ),
        risk_tiers=(
            "高＝成果归承包方，或我方只拿到非排他不可转让的使用许可，或归属未约定，或承包方可复用成果卖给竞争对手；"
            "中＝成果归我方但附条件且条件不完全由我方控制，或未约定不可撤销/配合完善手续，或承包方保留通用工具所有权范围过宽；"
            "低＝成果全部归我方 + 不可撤销 + 配合完善手续 + 背景 IP 已列明并授予充分许可。"
        ),
    ),
)

BY_ID: dict[ClauseId, ClauseSpec] = {spec.clause_id: spec for spec in CLAUSES}


def check_registry() -> None:
    """自检：登记表必须与 ClauseId 枚举一一对应（防止改了标准却漏改这里）"""
    registry_ids = {spec.clause_id for spec in CLAUSES}
    enum_ids = set(ClauseId)
    missing = enum_ids - registry_ids
    extra = registry_ids - enum_ids
    if missing or extra:
        raise RuntimeError(f"条款登记表与 ClauseId 不一致：缺少 {missing}，多余 {extra}")
