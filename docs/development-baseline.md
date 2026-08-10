# CXBA Hermes 开发基线

## 1. 仓库基线

| 项目 | 当前值 |
| --- | --- |
| 官方仓库 | `https://github.com/NousResearch/hermes-agent.git` |
| 官方基线提交 | `d92bfa0a384486050cb78c8640d28895fd636007` |
| 当前开发分支 | `codex/build-agent-case-workbench` |
| `upstream` | Hermes 官方仓库，保留 fetch/push 地址 |
| `origin` | 暂不配置，等待本项目内部仓库地址 |

本目录由官方仓库干净克隆后切到上述精确提交。创建项目规则和本文档之前，工作树无修改；没有从实测仓库复制 `.git`、源代码修改、运行状态或案件资料。

## 2. 已验证开发环境

| 项目 | 本机验证值 |
| --- | --- |
| 操作系统 | macOS 26.5，Darwin 25.5.0 arm64 |
| Python | 3.11.14，由 `.python-version` 和 `uv` 创建在仓库 `.venv` |
| uv | 0.12.3 |
| 依赖安装 | `uv sync --extra dev --frozen` |

`.venv` 和测试耗时缓存由官方 `.gitignore` 排除，不进入仓库。

## 3. 原始基线验证

在未修改 Hermes 业务代码前，使用官方测试入口执行：

```bash
scripts/run_tests.sh -j 3 \
  tests/test_project_metadata.py \
  tests/test_hermes_state.py \
  tests/tui_gateway/test_gateway_owned_session_reap.py \
  -q
```

结果：3 个测试文件、204 个测试全部通过，0 失败。覆盖项目依赖元数据、SessionDB 基础行为和 Gateway 持有 Session 的回收行为。该结果是基础验证，不等同于全量测试；后续功能开发仍需按修改范围补充专项测试和回归测试。

## 4. 持久化实测资产参考边界

只读参考根目录：

- Hermes 实测源码：`/Users/sunhm3/work/jj/cxba-hermes-eval-persistent/hermes-agent`
- 实测运行配置与 Skills：`/Users/sunhm3/work/jj/cxba-hermes-eval-persistent/runtime`
- Round 1 案件底稿与进度：`/Users/sunhm3/work/jj/cxba-hermes-eval-persistent/rounds/round1`

实测源码与本项目使用同一官方基线提交，但当前有 15 个已跟踪文件修改和 2 个未跟踪文件。它只能作为逐项补丁参考，不能作为产品仓库基线。

| 实测资产 | 允许用途 | 处理规则 |
| --- | --- | --- |
| 对话、压缩、提示词及流式重复保护相关补丁 | 对照 Qwen 本地实测问题 | 逐项审查原生代码、规格和测试后再决定是否迁移 |
| Docker 环境、文件工具和代码执行补丁 | 对照 Sandbox 挂载及长任务行为 | 逐项迁移，不复制脏工作树 |
| `cxba-case-investigation`、`cxba-case-investigator`、`cxba-evidence-reviewer`、`cxba-material-profiling`、`cxba-raw-material-investigation`、`cxba-safe-tabular-analysis` | 参考案件调查 Skill 的职责拆分和验证经验 | 逐个审阅，消除重复和实测硬编码后再纳入产品 |
| `runtime/docker/Dockerfile` 与 `build-image.sh` | 参考离线工具镜像依赖 | 不直接发布；按产品 Sandbox 规格重新核对 |
| `SOUL.md`、`config.yaml`、`cxba-case-main.yaml` | 参考模型与运行配置 | 检查密钥、路径和实测假设，禁止原样复制 |
| `run-*.sh`、`monitor-case.sh`、`case-*-request.txt` | 了解上一轮实验过程 | 属于实验控制脚本或请求，不进入产品代码 |
| `.git`、`.venv`、`state.db`、日志、缓存、对话状态 | 无 | 严禁复制 |
| `rounds/round1`、Sandbox、案件 Workspace 和原始材料 | 只在授权环境内做只读验证 | 严禁复制到代码仓库或测试夹具 |

后续任何参考资产与 OpenSpec 或既定职责边界冲突时，停止实现并交由主控 Agent 向用户确认。
