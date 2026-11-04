# Universal command batches

AI Infinity supports one command per request and an ordered list of commands through the same canonical router.

## Command-box syntax

Enter explicit numbered or bulleted commands in the existing canonical command box:

~~~text
1. Research current standards for accessible video captions.
2. Create a 60-second English explainer about accessible captions.
3. [from:step-2] Change the current project's aspect ratio to 9:16.
~~~

Every line must begin with a number (1. or 1)) or a bullet (- or *) for batch mode to activate. A single unnumbered paragraph continues to use the existing single-command route.

[from:step-N] binds a step to the project_id returned by that earlier step. It is intended for project edits and project-scoped commands. The dependency must refer to an earlier step.

## API contract

POST /infinity/canonical/batches

~~~json
{
  "idempotency_key": "creator-workflow-2026-11-04-001",
  "commands": [
    {
      "id": "research",
      "command": "Research current standards for accessible video captions."
    },
    {
      "id": "video",
      "command": "Create a 60-second English explainer about accessible captions."
    },
    {
      "id": "vertical-edit",
      "command": "Change the current project's aspect ratio to 9:16.",
      "depends_on": ["video"],
      "project_id_from": "video"
    }
  ]
}
~~~

The endpoint accepts up to 20 steps. IDs must be unique. Dependencies must refer to earlier steps. Each step is sent to ai_infinity_canonical.dispatch_command; this layer does not implement a second creator engine.

## Execution and safety semantics

- Steps execute in declared order. A failed step or an authorization gate stops later steps by default.
- continue_on_error: true is an explicit per-step opt-in to continue independent work after a failure.
- Create steps receive deterministic batch-scoped idempotency keys.
- Repeating a request with the same idempotency key and identical command payload returns the stored batch result. Reusing that key for a different payload returns HTTP 409.
- Progress is stored in canonical_command_batches and checkpointed before and after dispatch. GET /infinity/canonical/batches/{batch_id} retrieves the recorded state.
- A queued or existing project is reported as accepted, not as completed media. Project rendering and truth verification remain separate.
- An interrupted running step is not automatically replayed. Inspect its state and the target project before issuing another write.
- Inline credentials are rejected. Keep secrets in the service/repository secret stores.
- Publish, send, delete, purchase, payment, transfer, and other protected actions remain subject to their existing authorization and confirmation rules.

The command batch is an execution coordinator over existing capabilities, not a claim that every external API or provider is available. Unsupported or unconfigured routes remain explicitly blocked or degraded.
