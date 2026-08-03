# Task-scoped model execution TODO

This checklist tracks support for choosing a model command and native session
policy when a task is dispatched. Existing workers that only use `--executor`
must continue to work unchanged.

- [x] Define and validate the task `execution` schema.
- [x] Persist `execution` from workflow sync and incremental task creation.
- [x] Load named execution profiles and reject unapproved task commands.
- [x] Resolve `new`, `continue`, and `resume` native-session policies.
- [x] Build model commands through a provider-neutral launcher contract.
- [x] Persist Harness and provider-native session identifiers separately.
- [x] Serialize concurrent use of the same provider-native session.
- [x] Preserve the worker-level `--executor` compatibility path.
- [x] Add unit tests for validation, command resolution, persistence, and fallback.
- [x] Document configuration, task examples, wrapper I/O, and security rules.
