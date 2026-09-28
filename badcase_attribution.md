# Badcase 归因表（Step 4 交付物 · 自动生成）

> 生成方式：`python -X utf8 scripts/attribute_badcases.py --k 5`
> **零 LLM 调用** —— 漏报的『检索层 / 模型层』判定由本地 embedding 重跑检索得出，可一键复现。

扫描 300 条判定，判错 **31** 条（漏报 17 / 误报 14）。

## 一、漏报（FN）：检索层 vs 模型层

| 归因层 | 数量 | 占比 |
|---|---|---|
| 检索层 | 14 | 82% |
| 模型层 | 3 | 18% |

| 合同 | 条款 | 归因层 | 证据 | 模型置信度 |
|---|---|---|---|---|
| IntegrityFunds_20200121_485BPOS_EX-99. | 01_governing_law | **检索层** | **未召回**（金标准 22796-22881，最近片段相距 1704 字符） | 0.95 |
| BONTONSTORESINC_04_20_2018-EX-99.3-AGE | 03_anti_assignment | **检索层** | **未召回**（金标准 105765-106250，最近片段相距 5900 字符） | 0.95 |
| FTENETWORKS,INC_02_18_2016-EX-99.4-STR | 03_anti_assignment | **检索层** | **未召回**（金标准 27199-27445，最近片段相距 1542 字符） | 0.95 |
| IntegrityFunds_20200121_485BPOS_EX-99. | 03_anti_assignment | **检索层** | **未召回**（金标准 21595-21704，最近片段相距 287 字符） | 0.95 |
| ChinaRealEstateInformationCorp_2009092 | 05_exclusivity | **检索层** | **未召回**（金标准 7857-8211，最近片段相距 2204 字符） | 0.95 |
| DovaPharmaceuticalsInc_20181108_10-Q_E | 05_exclusivity | **检索层** | **未召回**（金标准 27927-28451，最近片段相距 21518 字符） | 0.95 |
| PREMIERBIOMEDICALINC_05_14_2020-EX-10. | 05_exclusivity | **检索层** | **未召回**（金标准 14754-15163，最近片段相距 1530 字符） | 0.95 |
| ZEBRATECHNOLOGIESCORP_04_16_2014-EX-10 | 05_exclusivity | **检索层** | **未召回**（金标准 39603-40687，最近片段相距 13222 字符） | 0.95 |
| BONTONSTORESINC_04_20_2018-EX-99.3-AGE | 06_termination_notice | **检索层** | **未召回**（金标准 67047-67461，最近片段相距 6062 字符） | 0.95 |
| OPERALTD_04_30_2020-EX-4.14-SERVICE AG | 07_renewal | **检索层** | **未召回**（金标准 10015-10579，最近片段相距 3263 字符） | 0.95 |
| MetLife, Inc. - Remarketing Agreement | 08_cap_on_liability | **检索层** | **未召回**（金标准 83497-83957，最近片段相距 3631 字符） | 0.95 |
| ZEBRATECHNOLOGIESCORP_04_16_2014-EX-10 | 08_cap_on_liability | **检索层** | **未召回**（金标准 111395-111593，最近片段相距 10986 字符） | 0.95 |
| DovaPharmaceuticalsInc_20181108_10-Q_E | 09_liquidated_damages | **检索层** | **未召回**（金标准 143450-143918，最近片段相距 3265 字符） | 0.95 |
| HERTZGLOBALHOLDINGS,INC_07_07_2016-EX- | 10_ip_ownership | **检索层** | **未召回**（金标准 11369-11659，最近片段相距 207 字符） | 0.95 |
| INTRICONCORP_03_10_2009-EX-10.22-Strat | 05_exclusivity | **模型层** | 已召回（片段 6852-8001 覆盖金标准 7418-7533） | 0.95 |
| Freecook_20180605_S-1_EX-10.3_11233807 | 06_termination_notice | **模型层** | 已召回（片段 4519-5598 覆盖金标准 4924-5145） | 0.95 |
| DOMINIADVISORTRUST_02_18_2005-EX-99.(H | 08_cap_on_liability | **模型层** | 已召回（片段 7240-7852 覆盖金标准 7252-7291） | 0.95 |

## 二、误报（FP）：口径落差 vs 其他

| 归因层 | 数量 |
|---|---|
| 口径落差 | 12 |
| 待人工判定 | 2 |

| 合同 | 条款 | 归因层 | 模型引用的证据 |
|---|---|---|---|
| ADAMSGOLFINC_03_21_2005-EX-10.17-ENDOR | 02_change_of_control | 口径落差 | 31. ASSIGNMENT AND CHANGE OF CONTROL |
| ChinaRealEstateInformationCorp_2009092 | 10_ip_ownership | 口径落差 | 4.1. Ownership. Licensee acknowledges that, as between the Parties, Licensor (or its third party providers) is |
| GLOBALTECHNOLOGIESLTD_06_08_2020-EX-10 | 05_exclusivity | 待人工判定 | The Company further agrees that neither it nor its employees, affiliates or assigns, shall enter into, or othe |
| GLOBALTECHNOLOGIESLTD_06_08_2020-EX-10 | 06_termination_notice | 口径落差 | Either Party shall have the right to terminate this Agreement with notice, and the effective date of terminati |
| IntegrityFunds_20200121_485BPOS_EX-99. | 06_termination_notice | 口径落差 | This Agreement is terminable with respect to the Fund, without penalty, (a) on 60 days' written notice, by vot |
| KIROMICBIOPHARMA,INC_05_11_2020-EX-10. | 02_change_of_control | 口径落差 | Company may assign this Agreement to any entity that succeeds to substantially all of the business or assets o |
| LIMEENERGYCO_09_09_1999-EX-10-DISTRIBU | 04_non_compete | 待人工判定 | (B)      Distributor further agrees that it will not interfere                            with or  otherwise   |
| LIMEENERGYCO_09_09_1999-EX-10-DISTRIBU | 06_termination_notice | 口径落差 | If Company  terminates  the  Agreement  without  cause and for                   reasons other than  Distribut |
| LohaCompanyltd_20191209_F-1_EX-10.16_1 | 09_liquidated_damages | 口径落差 | the Sellers agree to pay a penalty which shall be deducted by the paying bank from the payment. |
| ON2TECHNOLOGIES,INC_11_17_2006-EX-10.3 | 02_change_of_control | 口径落差 | provided, however, that either party may assign all or part of its rights and obligations under this Agreement |
| OPERALTD_04_30_2020-EX-4.14-SERVICE AG | 10_ip_ownership | 口径落差 | 5. INTELLECTUAL PROPERTY RIGHTS 5.1 Nothing in this Agreement shall be construed as transferring the Intellect |
| ReynoldsConsumerProductsInc_20191115_S | 06_termination_notice | 口径落差 | (b) a termination date elected by a Party in a written notice delivered to the other Party any time after the  |
| ReynoldsConsumerProductsInc_20191115_S | 09_liquidated_damages | 口径落差 | If Buyer fails to pay Seller an amount owed under this Agreement by the invoice due date, then Buyer will owe  |
| WHITESMOKE,INC_11_08_2011-EX-10.26-PRO | 10_ip_ownership | 口径落差 | 7.1 Distributor acknowledges that Google and/or its licensors own all right, title and interest, including all |

## 三、已知口径落差的对照说明

| 条款 | 说明 |
|---|---|
| 02_change_of_control | 转让例外/转让允许条款被判成控制权变更（BC-006：CUAD 问『什么构成控制权变更』，我们问『有没有针对它的安排』） |
| 06_termination_notice | 无理由终止权被判命中，但 CUAD 未标注该类（金标准覆盖不全） |
| 09_liquidated_damages | penalty / Late Fees 被判成违约金（我们的定义宽于 CUAD 的 `Liquidated Damages`） |
| 10_ip_ownership | 背景 IP 保留声明被判成归属条款（§10.3 待收窄） |
| 01_governing_law | 管辖/仲裁句被判命中，但 CUAD 的 `Governing Law` 只覆盖『适用法律』 |
