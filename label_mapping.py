"""
CUAD 41 类原始标签 → 本项目 10 类条款 的映射表（Step 2.1 产出，Step 2.2 修订）

数据来源：data/cuad/CUAD_v1.json
    510 份合同 × 41 类 = 20,910 条标注（已实测核对）

每条 qa 结构：{id: "合同名__类别名", question, answers: [{text, answer_start}], is_impossible}

四类归属规则
-----------
1. DIRECT_MAP    ：一对一映射，产出本项目 10 类条款的正样本（10 类全部覆盖）
2. CROSS_REF     ：主类别归一个类，同时记录它涉及的另一个类（不计入 F1，供归因分析）
3. BOUNDARY_ONLY ：不参与指标计算，作为「边界测试样本」，用于验证我们定下的边界规则
4. DROPPED       ：与本项目 10 类无关，直接丢弃

⚠️ 口径变更（已确认，详见 mvp_scope.md §0.6）
   第 9 类原定「赔偿条款（Indemnification）」，但 CUAD 41 类中**没有**该标签（已实测），
   现改为「违约金（Liquidated Damages）」。Indemnification 移入 Step 5.8 backlog。
"""

from schemas import ClauseId

# ================================================================
# 1) 直接映射：CUAD 标签 → 本项目条款（10 类全覆盖）
# ================================================================
DIRECT_MAP: dict[str, ClauseId] = {
    "Governing Law": ClauseId.GOVERNING_LAW,                      # → 01 争议解决与适用法律
    "Change Of Control": ClauseId.CHANGE_OF_CONTROL,              # → 02 控制权变更
    "Anti-Assignment": ClauseId.ANTI_ASSIGNMENT,                  # → 03 转让限制
    "Non-Compete": ClauseId.NON_COMPETE,                          # → 04 竞业限制
    "Exclusivity": ClauseId.EXCLUSIVITY,                          # → 05 独家排他
    "Termination For Convenience": ClauseId.TERMINATION_NOTICE,   # → 06 终止与通知期
    "Renewal Term": ClauseId.RENEWAL,                             # → 07 自动续期
    "Cap On Liability": ClauseId.CAP_ON_LIABILITY,                # → 08 责任上限
    "Liquidated Damages": ClauseId.LIQUIDATED_DAMAGES,            # → 09 违约金（替代 Indemnification）
    "Ip Ownership Assignment": ClauseId.IP_OWNERSHIP,             # → 10 知识产权归属
}

# ================================================================
# 2) 交叉引用（1 类）
# ================================================================
# CUAD 的 "Notice Period To Terminate Renewal" 问的是「续期终止的通知期」，
# 它同时涉及我们的 07 自动续期（§7.1 三要素之一）和 06 终止与通知期。
# 处理：主类别归 07；同时记录它涉及 06，但**不计入 06 的 F1**，避免指标口径变松。
CROSS_REF: dict[str, dict[str, object]] = {
    "Notice Period To Terminate Renewal": {
        "primary": ClauseId.RENEWAL,
        "also_involves": [ClauseId.TERMINATION_NOTICE],
        "counts_for_f1": False,
    },
}

# ================================================================
# 3) 边界测试样本：不参与 F1，用于验证我们定的边界规则（Step 4 归因时抽样）
# ================================================================
BOUNDARY_ONLY: dict[str, str] = {
    # 条款 4 竞业限制的边界
    "Competitive Restriction Exception": "§4.4：竞业限制的例外条款，削弱限制力度但不单独构成命中",
    "No-Solicit Of Employees": "§4.4：限制的是『挖人』，不是『从事竞争业务』→ 单独出现不算竞业",
    "No-Solicit Of Customers": "§4.4：同上",
    # 条款 5 独家排他的边界
    "Most Favored Nation": "§5.4：最惠待遇与独家相邻但不同",
    "Minimum Commitment": "§5.5：最低采购量是独家条款的核心配套，本身不是独家",
    # 条款 6 / 7 的边界
    "Expiration Date": "§6.4：只是期限约定，不含终止权",
    # 条款 8 责任上限的边界
    "Uncapped Liability": "§8.3：第 8 类只覆盖『存在上限』；明确写『不受限制』不算命中",
    # 条款 9 违约金的边界
    "Insurance": "§9.4：投保 ≠ 约定赔偿额",
    "Covenant Not To Sue": "§9.4：承诺不起诉 ≠ 违约金",
    # 条款 10 知识产权的边界
    "License Grant": "§10.4：授权 ≠ 归属",
    "Joint Ip Ownership": "§10.4：共有归属，属知识产权归属的相邻形态",
    "Non-Transferable License": "§10.4：相邻形态",
    "Irrevocable Or Perpetual License": "§10.4：相邻形态",
}

# ================================================================
# 4) 丢弃：与本项目 10 类无关（元数据类 + 其他商业条款）
# ================================================================
DROPPED: list[str] = [
    "Affiliate License-Licensee",
    "Affiliate License-Licensor",
    "Agreement Date",
    "Audit Rights",
    "Document Name",
    "Effective Date",
    "Non-Disparagement",
    "Parties",
    "Post-Termination Services",
    "Price Restrictions",
    "Revenue/Profit Sharing",
    "Rofr/Rofo/Rofn",
    "Source Code Escrow",
    "Third Party Beneficiary",
    "Unlimited/All-You-Can-Eat-License",
    "Volume Restriction",
    "Warranty Duration",
]


# ================================================================
# 自检：确认 41 个 CUAD 类别全部有归属，一个都不漏、也不重复
# ================================================================
def check_coverage(all_categories: list[str]) -> dict[str, list[str]]:
    """返回 {'未归类': [...], '重复归类': [...]}，两者都为空 = 映射表健康"""
    groups = {
        "DIRECT_MAP": set(DIRECT_MAP),
        "CROSS_REF": set(CROSS_REF),
        "BOUNDARY_ONLY": set(BOUNDARY_ONLY),
        "DROPPED": set(DROPPED),
    }
    all_categories = set(all_categories)
    uncovered = sorted(all_categories - set().union(*groups.values()))
    duplicated = sorted(
        c for c in all_categories if sum(c in g for g in groups.values()) > 1
    )
    return {"未归类": uncovered, "重复归类": duplicated}


# ================================================================
# DECISIONS：Step 2.1 提出、Step 2.2 已确认
# ================================================================
DECISIONS: dict[str, str] = {
    "#1 第 9 类口径": "已确认改为 Liquidated Damages（违约金）；Indemnification 移入 Step 5.8 backlog",
    "#2 Uncapped Liability": "已确认只作边界样本，不算第 8 类正样本（依据 §8.3）",
    "#3 No-Solicit（招揽）": "已确认维持 §4.4：单独出现不算竞业限制",
    "#4 Notice Period To Terminate Renewal": "已确认主类别归 07，不计入 06 的 F1",
    "#5 其余类别": "已确认全部丢弃，其中 13 类放进 BOUNDARY_ONLY",
    "#6 主评测集规模": "已确认用 198 份官方原始文本全量；299 份 PDF 转换文本作鲁棒性集单列",
}
