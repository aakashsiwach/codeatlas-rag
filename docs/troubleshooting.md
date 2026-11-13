# Troubleshooting

Start with `codeatlas doctor`. It checks Python, the index and its lock, the models and the LLM
configuration, and prints names but never secrets. `codeatlas doctor --llm` adds one 1-token LLM call,
which the provider bills.

## Index

**"The index at .codeatlas is in use by another CodeAtlas process"** (exit code 3). Embedded Qdrant allows
one process per index. A running `codeatlas serve`, an eval or another `index` holds it. Then:
- stop the other process, or
- use a separate index (`--index-dir .codeatlas-2` / `CODEATLAS_INDEX_DIR`), or
- point every process at a Qdrant server (`CODEATLAS_QDRANT_URL`) for concurrent access.

**"The existing index is incompatible (…). Re-run with --force."** The embedding model, its vector size or
the chunker changed since the index was built, so a mix of old and new vectors would be meaningless.
Rebuild it with `codeatlas index <repo> --force`.

**`--sources-only` answers come from the wrong files, or nothing new shows up.** `codeatlas index`
re-embeds only files whose content hash changed. Check what changed with `codeatlas runs`. A renamed file
counts as a delete plus an add.

## Windows

**`python` is not recognised, or exits with code 9009.** Python is not on PATH, and Windows runs the
Microsoft Store stub instead. Reinstall from python.org with "Add python.exe to PATH" ticked, or call the
full path (`…\Python312\python.exe -m venv .venv`).

**`.venv\Scripts\Activate.ps1` cannot be loaded.** PowerShell's execution policy blocks scripts. Allow
them for the current shell only: `Set-ExecutionPolicy -Scope Process Bypass`.

**Paths with spaces.** Quote them (`codeatlas index "C:\Projects\my repo"`). In Git Bash, use forward slashes
(`/c/Projects/my repo`).

**Line endings.** `.gitattributes` keeps `*.sh` files LF, so `scripts/run-judge.sh` runs in Git Bash even
with `core.autocrlf=true`. If you copied the repository without git, convert the script with
`dos2unix scripts/run-judge.sh`.

**`/api/file` returns 404 for a file you can see.** Only indexed files are served. Paths are matched with
forward slashes relative to the repository root, so use `src/main/java/…`, not `src\main\java\…`.

## LLM providers

`doctor --llm` and failed answers show the provider's error class, which maps to a `kind`:

| Kind | Typical error classes | What it means |
|---|---|---|
| `access` | `AccessDeniedException`, `UnrecognizedClientException`, `ExpiredTokenException` | Credentials missing or expired, or an IAM policy without `bedrock:InvokeModel` / `InvokeModelWithResponseStream` on the inference profile **and** its foundation model |
| `validation` | `ValidationException`, `ResourceNotFoundException` | The request was rejected: a wrong model id, a parameter the model does not accept (for example a thinking type), or no access to the model |
| `throttled` | `ThrottlingException`, `ServiceQuotaExceededException` | Too many requests or tokens per minute. CodeAtlas retries with backoff; lower the request rate or ask for a quota increase |
| `unavailable` / `timeout` / `network` | `ServiceUnavailableException`, read timeouts | Transient. Retried automatically. Raise `CODEATLAS_LLM_TIMEOUT_S` for long answers |

**Bedrock: `ValidationException: Operation not allowed`, or "model is not available for this account".**
The account cannot use the model yet. This happens to new accounts and to models that need a use-case form.
It is not an IAM problem. Request access in the Bedrock console, or open an AWS support case if access
cannot be requested there. Then retry `codeatlas doctor --llm`.

**Bedrock calls go to the wrong AWS account.** Set `CODEATLAS_AWS_PROFILE` to the named profile in
`~/.aws`. An `AWS_PROFILE` line in `.env` is not exported to the AWS SDK, because CodeAtlas reads `.env`
into its own settings only. `doctor` prints the profile and the credential source it found.

**The judge's verdict is `judge_truncated`, or the answer is `truncated`.** The model spent its output
budget, usually on thinking. Set the thinking type for that model, for example
`CODEATLAS_THINKING_BY_MODEL={"<model id>": {"judge": "<type>"}}`, using a value the model accepts. Then
rerun the single check with `codeatlas judge-check` before any full judged run.

**Cost shows "unpriced".** The model has no entry in `CODEATLAS_LLM_PRICES`. The key is
`"<provider>:<model>"`, so Bedrock and the Anthropic API are separate entries. Judged runs refuse to start
without a price, because the budget cannot be enforced.

**Ollama answers are cut off or ignore the context.** Ollama's default context window can be smaller than
CodeAtlas's prompt, and Ollama truncates it silently. Start Ollama with `OLLAMA_CONTEXT_LENGTH=8192`.

## Web UI

**The UI loads but every question fails with 401.** The server has `CODEATLAS_API_TOKEN` set. Enter the
token in **Settings**.

**"Retrieving with the quality setup…" takes 20–30 s.** That is expected on CPU: the cross-encoder
rescores 20 candidates. The bar shows the server's progress events. Use `fast` for interactive work.

**A deployed quality question fails after about 60 s.** CloudFront limits the wait for the first byte. The
stream sends a `progress` event at once and every 5 s to avoid this. If it still happens, a proxy is
buffering the response: it must pass `text/event-stream` through unbuffered.
