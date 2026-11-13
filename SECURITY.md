# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | Yes |
| < 1.0 | No |

Security fixes land on `main` and in the next 1.0.x release.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Report it privately through GitHub:
**Security → Report a vulnerability** on this repository (GitHub private vulnerability reporting).

Include:

- what is affected (API endpoint, CLI command, UI, Docker image or Terraform), and the version or commit
- steps to reproduce, or a proof of concept
- the impact you expect (for example, reading files outside the indexed repository)

You can expect an acknowledgement within 7 days and an assessment within 30 days. Fixes are credited in
the changelog unless you prefer otherwise.

## Scope and design notes

The threat model, so reports can focus on what matters:

- **API authentication.** With `CODEATLAS_API_TOKEN` set, every `/api/*` route needs
  `Authorization: Bearer <token>`. The GitHub webhook uses its HMAC signature instead, and `/health` and
  the static UI are public. `codeatlas serve` refuses to listen on a non-loopback address without a token,
  unless `CODEATLAS_ALLOW_UNAUTHENTICATED=true` is set explicitly.
- **File access.** `/api/file` serves only files that are in the index. Indexing skips files whose real
  path (after symlinks or junctions) is outside the repository. `/api/index` re-indexes only the configured
  repository, unless `CODEATLAS_ALLOW_INDEX_ANY_PATH=true` is set.
- **Prompt injection.** Repository text reaches the LLM inside escaped `<block>` elements, and the system
  prompt treats it as data, never as instructions. This reduces the risk but cannot remove it: treat
  answers about untrusted code with the same care as the code itself.
- **Secrets.** Provider credentials come from the provider SDKs' standard sources (environment variables,
  `~/.aws` profiles), never from files in the repository. The deploy reads the API token from SSM
  Parameter Store. Traces record questions, retrieved chunk locations and token counts, but no credentials.
- **Deployment.** `docker-compose.yml` publishes the API on loopback only. The Terraform stack admits
  traffic to the load balancer only from CloudFront, and the task role may only invoke one Bedrock
  inference profile and its model.

Out of scope: running CodeAtlas with authentication disabled on a public network, and models or
repositories you choose to index.
