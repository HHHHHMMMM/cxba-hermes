# 交易发现临时脚本参考逻辑

本参考用于全案调查中的临时脚本设计。它提供覆盖顺序、伪代码、自检和常见错误，不规定案件字段名、人员、账户、金额阈值或答案。脚本必须先读取真实材料结构，把示例角色映射到原件中的精确字段；无法唯一确定时保留`GAP`，不得猜列名或方向。

## 1. 先确定调查对象的可用匹配范围，强标识不是前提

交易发现使用流水和案件现有信息中实际可用的字段。人员主档归并所需的强标识不作为前提；已知关系可以同时整理，但只用于后续补强：

```python
target_names = set()             # 用户、案件说明或材料中已知的姓名/别名
target_account_ids = set()       # 已知账户或卡号；允许暂时为空
target_customer_ids = set()      # 流水中已有客户号；允许暂时为空
known_role_memberships = []      # 已知的当前/历史任职关系，用于候选补强
known_related_subject_ids = set()# 已知的亲属、关联人员或企业，用于候选补强
```

- 账户归属在准备写成身份事实或主档关系时必须有开户资料、客户资料、主档、用户明确说明或其他证据支持；发现阶段可以先保留“疑似调查对象账户”或来源内姓名节点，并披露匹配依据和歧义。
- 同事集合来自任职单位、部门和期间；当前同事与历史同事分别保留，不因已离任而删除，但没有这类资料时仍先执行账户级交易发现。
- 同姓、同地址、发生交易或姓名相似不能建立亲属、同事、账户归属或任职关系。
- 交易对手有账户或客户号时优先作为图节点；只有姓名时使用`来源标识 + 字段角色 + 规范化姓名`形成来源内候选节点，不跨来源自动合并同名；连姓名也没有时使用观测级唯一ID。以上均可标记`IDENTITY_UNRESOLVED`继续分析。身份未知不等于无关，不能从候选中删除。

示例节点选择逻辑：

```python
def candidate_node(row, side, source_id, source_row):
    account = normalized_account(row.get(f"{side}_account"))
    if account:
        return f"ACCOUNT:{account}", "ACCOUNT_LEVEL"
    customer = normalized_customer_id(row.get(f"{side}_customer_id"))
    if customer:
        return f"CUSTOMER:{source_id}:{customer}", "CUSTOMER_LEVEL"
    name = normalized_name(row.get(f"{side}_name"))
    if name:
        return f"NAME:{source_id}:{side}:{name}", "SOURCE_NAME_LEVEL"
    return f"OBS:{source_id}:{source_row}:{side}", "OBSERVATION_LEVEL"
```

这里的节点键用于本案统计和图计算，不等同于人员主档ID，也不授权把同名记录自动归并。

## 2. 每种真实布局写一个明确适配器

不要写“寻找包含金额两个字的第一列”之类模糊规则。先检查真实Sheet、表头行和字段值，再显式映射：

```python
FIELD_MAP = {
    "account": "原件中的本方账号列",
    "counterparty_account": "原件中的对方账号列",
    "counterparty_name": "原件中的对方户名列",
    "income_amount": "原件中的收入金额列",
    "expense_amount": "原件中的支出金额列",
    "timestamp": "原件中的交易时间列",
    "currency": "原件中的币种列",
    "event_id": "原件中的强流水号列",
}
required = set(FIELD_MAP.values())
missing = required - set(frame.columns)
if missing:
    raise ValueError(f"缺少精确字段: {sorted(missing)}")
```

不同银行可能使用借贷标志，也可能分别提供收入、支出金额。必须读取真实取值后明确方向规则，不能把钞汇标志、账户状态或任意第一个“标志”列当借贷方向。

```python
income = to_decimal(row[FIELD_MAP["income_amount"]])
expense = to_decimal(row[FIELD_MAP["expense_amount"]])
if income > 0 and expense == 0:
    payer_id, receiver_id = counterparty_id(row), own_account_id(row)
elif expense > 0 and income == 0:
    payer_id, receiver_id = own_account_id(row), counterparty_id(row)
else:
    record_failure(row, "方向不唯一")
```

金额先去除经确认的千分位符号，再用`Decimal`转换。不得对字符串直接`sum()`，不得把解析失败或空金额当0。规范化事件的`amount`只保存方向确定后的正数金额，原始文本和符号另存`source_amount`；负数不能仅凭符号或“收款/汇入/贷记”等摘要词判定方向。必须使用真实收入/支出列、借贷标志取值、余额变化、成对记录或已验证的解析规则进行校验。方向不唯一时记`DIRECTION_UNKNOWN`，保留原始定位但不进入收支合计和图路径。

## 3. 先做完整交易对手普查

对调查对象每个账户分别统计全部直接收付，不先加“大额”阈值，也不先限定当前部门同事：

```python
for target_node in sorted(resolve_target_nodes(events, target_names, target_account_ids, target_customer_ids)):
    target_events = [event for event in events if target_node in {event.payer_id, event.receiver_id}]
    mark_target_node_covered(target_node, len(target_events))
    for event in target_events:
        if event.receiver_id == target_node:
            add_direct_edge(event.payer_id, target_node, "IN", event)
        elif event.payer_id == target_node:
            add_direct_edge(target_node, event.receiver_id, "OUT", event)
```

输出至少包含：目标节点覆盖数、每个账户/客户号/来源内姓名节点的事件数、全部交易对手数、收付方向、笔数、金额、期间、原始定位、节点层级和身份解析状态。完整明细留在`/workspace/results`，对话只读取有限候选。

## 4. 候选发现后再识别主体与关系

先根据金额、频次、时间、方向、汇聚、发散、回转和路径连续性形成账户级候选；再使用开户资料、客户资料、人事档案、主档和其他原件识别有限候选。不能先把交易对手限定为已知同事或亲属。碰撞的是完整交易对手普查结果，不是文件名或预先挑出的少数大额记录：

```python
for edge in direct_edges:
    identity = resolve_candidate_identity(edge.counterparty_account_id)
    memberships = memberships_for(identity.subject_id) if identity.resolved else []
    if not identity.resolved:
        emit_unresolved_candidate(edge, status="IDENTITY_UNRESOLVED")
        continue
    for membership in memberships:
        relation_time = compare_time(edge.timestamp, membership.start_at, membership.end_at)
        emit_candidate(
            edge=edge,
            relationship=membership.role,
            relation_time=relation_time,  # 任职期内、任职期外或期间未知
            source_refs=edge.source_refs + [membership.source_ref],
        )
```

必须遍历全部命中。函数不能在第一个匹配后`return`；需要停止内层搜索时使用明确的`break`并继续处理下一条交易。历史同事在交易发生时是否仍同部门要单列时间关系，不能直接删除，也不能仅凭历史同事身份推断交易性质。

身份补查结果分开记录：

- `IDENTITY_RESOLVED`：账户所有人有明确原件依据；
- `RELATION_RESOLVED`：主体与调查对象的同事、亲属、任职或企业关系有明确原件依据；
- `IDENTITY_UNRESOLVED`：当前只能定位账户或观测，仍作为交易候选输出；
- `RELATION_UNRESOLVED`：主体已知但关系未知，不能强行归类。

未知候选最终可以形成`HYPOTHESIS`或`GAP`，至少写明账户、金额、笔数、期间、路径、原始定位、已检查的身份材料以及下一步调证；不得要求用户先提供姓名或关系后才开始分析。

## 5. 一跳和两跳只生成候选

一跳示例：关联企业或关联人员直接转给调查对象。两跳示例：来源主体先转给中间人，中间人随后转给调查对象。

```python
for first in events_to_intermediaries:
    for second in events_from(first.receiver_id, after=first.timestamp):
        if second.receiver_id not in target_nodes:
            continue
        if first.currency != second.currency:
            continue
        elapsed = second.timestamp - first.timestamp
        retained = min(first.amount, second.amount) / max(first.amount, second.amount)
        if elapsed <= selected_window:
            emit_two_hop_candidate(first, second, elapsed, retained)
```

- 时间窗口必须来自数据分布、时间精度和敏感性比较，不凭感觉选一个方便阈值。
- 同币种、严格先后、金额连续性只产生路径候选，不能证明是同一笔钱或利益输送。
- 同一事件不能与所有后续事件无限配对；优先使用`cxba-temporal-graph-analysis`的标准有界匹配和路径输出。
- 有多个合理匹配时披露歧义、未匹配余额和其他解释。

## 6. 跨来源与重复控制

- 客户资料和开户资料用于账户归属，不能代替交易明细。
- 汇总表、加工表和原始流水先用`cxba-source-reconciliation`判断重复、包含、补充或冲突关系，再决定计算范围。
- 只有完整字节相同的文件或稳定业务键与关键字段完全一致的记录可以自动去重。
- 同一事件出现在收付双方或多个Sheet时优先使用强流水号碰撞；没有强键时保留重复风险。
- 分开识别完全相同文件、跨文件/Sheet重复业务事件、付款方与收款方镜像、汇总与明细、冲正退款及真实相似交易。冲正和退款是与原事件关联的新事件，不是可直接删除的重复；同时报告毛额和已知净影响。
- 合并同一事件时保留全部`source_observations`，同时输出原始观测行数和去重后事件数。没有稳定事件键或可核验的配对依据时不得按姓名、金额、日期接近或相邻行自动去重，也不得声称已经得到唯一总额。

## 7. Skill、知识和案件记录的边界

- 收付方向、负金额、币种、镜像重复、冲正退款、去重和计算守恒等通用正确性规则，写入对应Skill、reference或脚本。
- 某家银行、某个系统或某种导出模板的字段和格式解析经验，本质是技术适配规则；多案复用时优化现有Skill/reference或形成Skill候选，不进入`10-新型数据源`。
- `10-新型数据源`指当前案件材料中尚未提供、但后续可能依法调取并用于核查的新材料来源，例如外卖及收货地址、打车、代驾、物流、通信、门禁或其他活动记录。它记录能解决的问题、调取条件、字段形态、限制和验证方式，不记录当前文件的表头技巧。
- 跨案件可复用的材料碰撞方法、判断依据和反证路径可以沉淀为`20-技战法`；本案具体人员、账户、金额、流水、疑点和结论只留案件Workspace、案件记忆、疑点或线索。

## 8. 脚本结束前的守恒检查

脚本必须失败而不是带病交付：

```python
assert covered_target_nodes == target_nodes
assert accepted_rows + excluded_rows == source_rows
assert all(item.source_file and item.source_row for item in candidates)
assert all(item.direction in {"IN", "OUT"} for item in direct_edges)
assert all(item.amount is not None for item in direct_edges)
assert raw_observation_count >= deduplicated_event_count
assert not any(item.direction == "IN" and item.amount < 0 for item in direct_edges)
```

另外输出并检查：

- 未解析身份数及原因；
- 未确定方向、金额、币种或时间的行数；
- 原始观测行数、去重后事件数、镜像合并数、冲正/退款数及无法确认重复风险的行数；
- 每个目标账户、客户号或来源内姓名节点是否实际读取交易明细；
- 当前同事、历史同事、亲属、关联人员和企业各自命中数；
- 身份已确认、身份未知、关系已确认和关系未知的候选数；
- 直接、一跳、两跳候选数及被截断数量；
- 无命中维度的真实检查范围和材料限制。

出现目标覆盖不全、方向字段误用、负金额被当入账、字符串金额、重复或镜像误合并、冲正被删除、提前返回、只读取客户资料、只检查当前部门、缺少原始定位或候选被无界展开时，先修复同一脚本并重跑，不得在报告中手工补数。
