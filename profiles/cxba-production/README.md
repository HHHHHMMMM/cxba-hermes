# CXBA Production Profile

Install this directory with Hermes' native profile distribution command. It is
the single source of truth for the CXBA Skills under `skills/cxba/`. Before
starting the Gateway, verify the configured OpenAI-compatible model name,
loopback tunnel endpoint, context length, and reasoning options in this
profile's `config.yaml`.

The shipped config resolves the exact model name from `CXBA_LOCAL_MODEL` and
routes it through the deployment host's `http://127.0.0.1:18080/v1` SSH
tunnel. The model endpoint is not exposed directly to the network. Missing
tunnel/model availability must fail explicitly, and `fallback_providers` stays
empty so a Run cannot silently switch back to an external model.

Use the repository switcher whenever the remote GPU instance changes:

```text
./scripts/cxba-remote-model.sh bootstrap <ssh-host> <ssh-port>
./scripts/cxba-remote-model.sh switch <ssh-host> <ssh-port>
./scripts/cxba-remote-model.sh status
```

`bootstrap` is needed only once for a new server and prompts for that server's
SSH password while installing the dedicated public key. The password is never
stored. `switch` uses key authentication, reads `/v1/models` on the remote
host, rewrites the local LaunchAgent tunnel, updates the ignored mode-600
Gateway environment, applies the production Profile, restarts the Gateway,
and verifies the selected model. Optional arguments are remote API port and
model ID; they default to `8080` and the sole model returned by the server.
The script also reads that model's advertised `max_model_len` and applies it as
`CXBA_LOCAL_MODEL_CONTEXT_LENGTH`.

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

The generic offline presentation workflow lives at
`skills/productivity/ppt-master`. Hermes mounts the complete profile Skill tree
read-only at `/root/.hermes/skills` inside each Docker Sandbox. PPT Master uses
its bundled layouts, charts and icons; generated projects belong under
`/workspace`. The CXBA profile deliberately disables topic research, URL
ingestion, online image acquisition, cloud image generation, TTS and browser
live preview. Its Python document-conversion and PPTX-export dependencies are
baked into `cxba-hermes-sandbox:local`; no Skill-local `.venv` is distributed.

The Workbench deep-thinking switch is session-scoped.  The Bailian provider
declares `extra_body.enable_thinking`; a local llama.cpp/vLLM provider must
instead declare the server-supported nested switch so Hermes can update it for
each Run without affecting other Sessions:

```yaml
extra_body:
  chat_template_kwargs:
    enable_thinking: false
```

Set these deployment environment variables before starting Hermes. The model
name and loopback URL normally match the profile and are also used by the
Gateway startup health check:

```text
CXBA_LOCAL_MODEL=<local OpenAI-compatible model name>
CXBA_LOCAL_MODEL_BASE_URL=<local OpenAI-compatible endpoint>
CXBA_LOCAL_MODEL_CONTEXT_LENGTH=<model max context reported by /v1/models>
CXBA_GATEWAY_PRIVATE_TOKEN=<at least 32 random characters, used only by Spring's private WebSocket>
CXBA_CASE_STORAGE_ROOT=<absolute host path outside the Sandbox>
CXBA_KNOWLEDGE_VAULT_ROOT=<absolute shared Obsidian Vault host path>
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
