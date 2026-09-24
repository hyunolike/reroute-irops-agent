# NVIDIA NemoClaw

[NemoClaw](https://github.com/NVIDIA/NemoClaw) (alpha) is NVIDIA's reference stack for running always-on
agents (OpenClaw by default, also Hermes and LangChain Deep Agents) inside OpenShell sandboxes with guided
onboarding, managed inference, policy presets, skills and lifecycle commands.

ReRoute uses NemoClaw in the role it is built for — a **governed agent runtime around ReRoute**:

```bash
# 1. Install and onboard (creates the OpenShell gateway, inference provider and sandbox)
curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash
nemoclaw onboard --name ops-copilot            # choose NVIDIA Endpoints / Nemotron when prompted

# 2. Allow the assistant to reach ReRoute - read + start tasks, never approve (deny_rules)
nemoclaw ops-copilot policy add --from-file nvidia/nemoclaw/presets/reroute.yaml --dry-run
nemoclaw ops-copilot policy add --from-file nvidia/nemoclaw/presets/reroute.yaml

# 3. Teach it how to operate ReRoute
nemoclaw ops-copilot skill install nvidia/skills/reroute-irops

# 4. Talk to it (e.g. from the ops chat channel)
nemoclaw ops-copilot connect
#   > "KE123 결항됐어, 재배정안 만들고 요약해줘"
#   The assistant calls POST /api/agent/tasks, follows the events and summarises the plan.
#   Approval stays with the human operator in the ReRoute console.
```

What is and is not claimed:

- The ReRoute recovery agent itself is a Python service (`app.agent.worker`) and runs directly in an
  **OpenShell** sandbox (`nvidia/openshell/scripts/create-sandbox.sh`); NemoClaw is not required for that.
- NemoClaw is used to run a **conversational operator copilot** that drives ReRoute through its API, under a
  NemoClaw policy preset and a NemoClaw-installable skill. Both files follow the formats documented in the
  NemoClaw repository (`nemoclaw-blueprint/policies/presets/*.yaml`, `SKILL.md` with `name` frontmatter).
- These steps were written against NemoClaw's docs (tag v0.0.129) and not executed in this repository's CI.
