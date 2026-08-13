# CXBA Production Profile

Install this directory with Hermes' native profile distribution command. It is
the single source of truth for the CXBA Skills under `skills/cxba/`. Before
starting the Gateway, set the local OpenAI-compatible model name, base URL,
context length, and any required reasoning options in this profile's
`config.yaml` through `hermes config set` or the deployment configuration
process.

The shipped config resolves the model name and endpoint from deployment environment variables. Missing local model configuration must fail explicitly, and `fallback_providers` stays empty so the Run does not switch to an external model.

Every CXBA material-analysis Run first loads `cxba-analysis-router`. The Router
selects the smallest focused, reconciliation, pattern, temporal, full-case, or
review workflow. All material-reading workflows share `cxba-analysis-notebook`
for immediate per-file external memory, and all material-derived final answers
share `cxba-claim-delivery` for source-located claims. A new technique therefore
adds one Router branch, one specialist Skill, and its contract tests without
copying the notebook or claim format.

`scripts/cxba-gateway.sh start` copies this managed profile's complete Skill
directories into the runtime profile before startup. This keeps references,
scripts, and agents together with `SKILL.md`; adding a resourceful specialist
Skill must not result in a runtime installation containing only its entry file.

The Workbench deep-thinking switch is session-scoped.  The Bailian provider
declares `extra_body.enable_thinking`; a local llama.cpp/vLLM provider must
instead declare the server-supported nested switch so Hermes can update it for
each Run without affecting other Sessions:

```yaml
extra_body:
  chat_template_kwargs:
    enable_thinking: false
```

Set these deployment environment variables before starting Hermes; the profile
contains references only and never stores their values:

```text
CXBA_LOCAL_MODEL=<local OpenAI-compatible model name>
CXBA_LOCAL_MODEL_BASE_URL=<local OpenAI-compatible endpoint>
CXBA_BAILIAN_API_KEY=<Bailian API key, required for the current production profile>
CXBA_GATEWAY_PRIVATE_TOKEN=<at least 32 random characters, used only by Spring's private WebSocket>
CXBA_CASE_STORAGE_ROOT=<absolute host path outside the Sandbox>
CXBA_SPRING_MCP_URL=<Spring MCP endpoint>
CXBA_SPRING_MCP_TOKEN=<Spring MCP bearer token>
```

Spring sends `CXBA_GATEWAY_PRIVATE_TOKEN` in the `x-cxba-gateway-token` WebSocket
header. Only that private connection may supply `cxba_context` and `run_context`.
The `cxba_spring` MCP entry is deliberately marked `cxba_trusted_context: true`;
ordinary MCP servers must not use that flag.

The CXBA Sandbox image is built from the repository root:

```text
terminal(command="docker/cxba-sandbox/build.sh cxba-hermes-sandbox:local")
```

During the first development and test phase the Sandbox keeps Docker network
access (`terminal.docker_network: true`) and contains all approved dependencies
at image build time. Runtime `pip`, `npm`, `apt`, or other package installation
remains prohibited. For the later closed intranet deployment, set
`terminal.docker_network: false`; no Skill or case script should depend on the
Internet before that switch.
Command and container lifetime limits intentionally use Hermes' native defaults;
the profile does not replace them with unlimited zero values.
