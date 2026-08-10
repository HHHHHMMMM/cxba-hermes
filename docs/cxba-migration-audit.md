# CXBA Hermes 实测资产迁移审计

记录日期：2026-08-09

## 结论

持久实测仓库与产品仓库使用同一官方基线 `d92bfa0a384486050cb78c8640d28895fd636007`，但实测工作树包含未提交补丁、实验控制脚本和案件运行产物，不能整体复制。产品迁移必须逐文件审查、独立测试。

## 源码补丁分类

### 迁入或改造后迁入

- `agent/stream_repetition_guard.py`、`agent/chat_completion_helpers.py`、`agent/conversation_loop.py`、`agent/conversation_compression.py`：保留Qwen重复流检测、异常流关闭和不完整回答丢弃能力，阈值改为可测试配置，并发出明确恢复事件。
- `agent/prompt_builder.py`、`tools/code_execution_tool.py`、`tools/file_tools.py`：统一terminal、file和execute_code的容器配置传播。
- `tools/file_operations.py`：按原始字节判断UTF-8，避免中文采样边界被误判为二进制。
- 对应测试只在同步修正旧断言并全部通过后迁入。

### 不进入CXBA生产路径

- `tools/environments/docker.py` 的跨进程旧容器复用增强不用于CXBA。CXBA每个Run创建独立Sandbox，禁止跨Run或跨进程复用。
- `run-hermes.sh`、`run-case.sh`、`run-case-segmented.sh`、`monitor-case.sh`、`case-*-request.txt`、实验 `SOUL.md` 和硬编码 `config.yaml` 不迁入。
- `.git`、`.venv`、`state.db`、日志、缓存、对话状态、round1案件底稿和任何真实材料不迁入。

## 原生能力边界

Hermes Gateway可直接复用Session创建、恢复、历史、分支、Prompt、Steer、Redirect、Interrupt和实时事件；MCP可直接复用stdio、HTTP、SSE、资源、提示和结构化结果；Docker环境可作为terminal、file和execute_code的统一基础。

仍需在本项目Hermes中扩展：

1. 控制面注入且模型不可覆盖的Case、业务Session和Run上下文；
2. 按Run独立Sandbox、动态挂载及子Agent继承；
3. 当前工具完成后不再启动下一步的安全停止；
4. 按Run暂存并在Gateway重连后补取真实事件；
5. 能保留消息元数据的精确历史编辑分支；
6. MCP调用链自动注入可信Run上下文；
7. Spring审批结果在同一Run内续跑。

原生Interrupt会中断当前Turn及前台工具，只用于强制停止；安全停止必须单独实现。Spring不得重建Agent循环或直接修改Hermes SessionDB。

## 生产关闭清单

生产CXBA profile必须同时关闭或移除：

- 全局memory与user profile；
- `memory`、`session_search`、`skill_manage`工具；
- curator、后台Skill review/creation、Skill sync和运行时Skill写入；
-自动加载上下文文件和SOUL身份。

运行时只允许列出和读取项目维护的CXBA Skills，跨Session或跨案件读取必须走Spring权限工具或案件内只读挂载。

## Skills与工具迁移

保留材料独立底稿、证据/反证、原位置定位、可复现脚本和结果分区方法；删除固定双Agent、固定复核人、强制全案通读、一轮一个工具、禁止并行、扫描顺序Fxxxx编号和运行时安装依赖等实验限制。

材料工具必须使用Spring提供的稳定材料记录ID；表格工具要流式处理大文件和精确金额；流水材料必须运行完整账户枚举；文档工具保留页码、Sheet、幻灯片和表格位置。镜像构建时预装DuckDB/Parquet、OCR、Office转换、压缩包、绘图和文档解析依赖。

## 已执行的参考测试

- dirty补丁定向测试：181通过、2个旧断言失败；失败已记录，证明不能整体搬运。
- 重复流恢复新增路径：2通过。
- Gateway、Steer、Redirect、Interrupt定向测试：36通过。
- MCP身份、资源、中断与时限定向测试：31通过。

后续迁入产品仓库的代码必须使用官方 `scripts/run_tests.sh` 重新验证，不能以持久实测仓库的结果替代产品测试。

## 产品仓库原生能力实测

以下验证均在 `/private/tmp/cxba-hermes-native-verify`、独立 `HERMES_HOME` 和专用端口内完成，只使用合成数据与本地 OpenAI 协议假服务；未连接本地Qwen，未读取案件材料，也未触碰既有实测进程。

- 原生命令：`hermes serve --host 127.0.0.1 --port 19119 --isolated`。WebSocket `/api/ws` 实测通过 `session.create/list/history/close/resume/branch` 与 `prompt.submit`；收到消息增量、reasoning、terminal工具开始/完成和消息完成事件。工具执行中 `session.steer` 返回 `queued`，模型请求中 `session.redirect` 返回 `redirected` 并按新指令完成，`session.interrupt` 返回 `interrupted` 且完成事件状态为 `interrupted`。
- Spring 控制调用保留上述 Hermes 原生 `status`，并统一返回
  `control_outcome=accepted|rejected|idle`；Spring 按 `control_outcome`
  判断是否接管成功，原生 `status` 仅用于诊断和界面展示。
- 原生MCP客户端：以 `MCPServerTask` 启动临时stdio MCP子进程，实际完成SDK初始化、`tools/list`发现 `echo_case_marker`、`tools/call`并返回 `mcp-native-ok:synthetic`，随后正常shutdown。可直接复用stdio客户端、工具发现、结构化调用和关闭生命周期。
- 原生Docker环境：以唯一 `task_id=cxba-native-verify-isolated` 启动临时 `DockerEnvironment`，`network=false` 实际检查为 `none`；合成输入目录只读挂载、输出目录可写挂载均符合预期，数据库环境变量在 `deny_database_credentials=true` 下未进入容器。命令返回0，输出文件回写宿主后，临时容器已强制清理；未复用、停止或修改其他Hermes容器。
- 协议实测客户端命令：`.venv/bin/python /private/tmp/cxba-hermes-native-verify/gateway_verify.py`、`.venv/bin/python /private/tmp/cxba-hermes-native-verify/mcp_client_verify.py`、`.venv/bin/python /private/tmp/cxba-hermes-native-verify/docker_environment_verify.py`，三项均通过。
- 官方Gateway回归命令：`scripts/run_tests.sh tests/test_tui_gateway_server.py -q -k 'session_steer or session_redirect or interrupt_only_clears_own_session_pending or interrupt_clears_multiple_own_pending or run_prompt_submit_registers_turn_thread_for_interrupt or interrupt_drops_queued_prompt_for_session or interrupt_before_agent_ready_prevents_late_turn_start or session_branch_writes_to_parent_profile_db or session_branch_installs_parent_profile_secret_scope or history_to_messages_preserves_tool_calls_for_resume_display or history_to_messages_keeps_reasoning_only_assistant_turn or session_resume_uses_parent_lineage_for_display or session_resume_follows_compression_tip'`，19通过、0失败。
- 官方MCP、Docker与协议回归命令：`scripts/run_tests.sh -j 3 tests/tui_gateway/test_protocol.py tests/run_agent/test_steer.py tests/tools/test_mcp_dynamic_discovery.py tests/tools/test_mcp_stdio_encoding_handler.py tests/tools/test_docker_environment.py -q`，134通过、0失败。

结论：OpenSpec 3.3所需Session与实时控制接口、3.4所需MCP客户端和Docker执行底座均有可复用的Hermes原生实现。CXBA只应补充Case/Run可信上下文、按Run挂载与Spring控制面适配，不应另写Agent循环或MCP/Docker通用框架。
