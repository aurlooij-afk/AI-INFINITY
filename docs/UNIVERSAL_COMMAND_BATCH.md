# Universal command batches

AI Infinity supports one command per request and dependency-aware task graphs through the same canonical dispatcher. The batch coordinator does not create a second execution engine.

## Command-box syntax

Enter explicit numbered or bulleted commands in the existing canonical command box:

~~~text
1. Research current standards for accessible video captions.
2. Create a 60-second English explainer about accessible captions.
3. [from:step-2] Change the current project's aspect ratio to 9:16.
~~~

Every line must begin with a number (1. or 1)) or a bullet (- or *) for batch mode to activate. A single unnumbered paragraph continues to use the existing single-command route.

[from:step-N] binds a step to the project_id returned by that earlier step. The command-box shorthand refers to a preceding step. The API contract below supports dependencies on any declared task ID, including forward references, and rejects unknown references and cycles.

## API contract

POST /infinity/canonical/batches

~~~json
{
  "idempotency_key": "creator-workflow-2026-11-04-001",
  "max_concurrency": 3,
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

The endpoint accepts up to 20 tasks and a max_concurrency value from 1 to 4 (default 3). IDs must be unique. Dependencies form a validated directed acyclic graph. Each task is sent to ai_infinity_canonical.dispatch_command; this layer does not implement a second creator engine.

## Execution and safety semantics

- Independent ready tasks execute in parallel up to max_concurrency. A task becomes ready only after all dependencies have been accepted or completed. Results retain the request's display order.
- A failed task blocks its descendants. By default, a failure or authorization gate stops work that has not yet started; continue_on_error: true on the failed task permits independent branches to continue. Already-running tasks are allowed to settle.
- continue_on_error: true is an explicit per-step opt-in to continue independent work after a failure.
- Create steps receive deterministic batch-scoped idempotency keys.
- Repeating a request with the same idempotency key and identical command payload returns the stored batch result. Reusing that key for a different payload returns HTTP 409.
- Task identity, dependency edges, inputs, state, progress, idempotency key, execution configuration and verification limitations are recorded in canonical_command_batches. The state is checkpointed before dispatch and after result settlement. GET /infinity/canonical/batches/{batch_id} retrieves the recorded state.
- A queued or existing project is reported as accepted, not as completed media. Project rendering and truth verification remain separate.
- An interrupted or uncertain running step is not automatically replayed. The GET response exposes reconciliation_required when a running task remains recorded. Inspect the target resource and evidence before issuing another write. Automatic retries and cancellation are not claimed by this coordinator.
- Inline credentials are rejected. Keep secrets in the service/repository secret stores.
- Publish, send, delete, purchase, payment, transfer, and other protected actions remain subject to their existing authorization and confirmation rules.

The coordinator's verification field explicitly states that independent verification was not performed by the batch layer. Domain-specific verification remains the responsibility of the invoked capability and its evidence engine. The command batch is an execution coordinator over existing capabilities, not a claim that every external API or provider is available. Unsupported or unconfigured routes remain explicitly blocked or degraded.


## Cancellation

An owner-authenticated caller can request cancellation while a batch is running:

~~~http
POST /infinity/canonical/batches/{batch_id}/cancel
~~~

The request is persisted in the same database row as the batch. The coordinator polls that durable flag between task dispatches and marks tasks that have not started as `cancelled`. Commands already dispatched are allowed to settle; this endpoint does not kill worker threads, stop a queued Creator Studio render, or reverse an external side effect. Their final outcome must be read from the batch/project evidence. If the process is interrupted, a recorded running task remains reconciliation-required rather than being replayed. Cancellation-state read failures halt additional dispatch (fail-closed).


The HTTP handler offloads the blocking DAG coordinator to Starlette's worker pool. This keeps the ASGI event loop available to accept a concurrent cancellation request while the batch is executing.
