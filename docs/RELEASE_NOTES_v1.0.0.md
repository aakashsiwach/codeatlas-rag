# CodeAtlas v1.0.0

Ask questions about a Java/Spring Boot codebase and get answers that cite the exact files and lines.

CodeAtlas parses Java with tree-sitter, combines keyword and vector search with a cross-encoder reranker
and a static call graph, and answers with numbered citations. If retrieval finds nothing relevant, it
answers "I don't know". Every technique is measured on an evaluation set with a held-out test split.

## Highlights

- **Held-out test results** against a naive fixed-size-chunk vector baseline, on Spring PetClinic, 24
  questions:

  | | recall@5 | MRR | Impact-question recall@5 |
  |---|---|---|---|
  | Baseline | 0.70 | 0.63 | 0.42 |
  | `quality` | **0.81** | **0.70** | **0.85** |

  The impact figure rests on 5 impact questions, and AST chunking on its own did not help on the test split
  (see "Honest findings" in the README).
- **Two frozen setups:** `fast` (AST chunks + vector search, ~30 ms) and `quality` (hybrid + ModernBERT
  reranker + code graph, ~20–30 s on CPU). You pick one per question in the web UI.
- **Code graph** for "what breaks if I change X" and "who calls Y". It covers field-type and local-variable
  call resolution, interface dispatch and endpoint callers.
- **Web UI:**
  - streaming answers
  - citations that open the source with the lines highlighted
  - a "don't know" card
  - server progress for slow questions
- **One-command demo:** `codeatlas demo` clones and indexes PetClinic, then serves the API and UI with an
  offline LLM. No keys, no cost.
- **Providers:** Amazon Bedrock, the Anthropic API, and OpenAI-compatible servers (including Ollama). Cost
  is tracked against a price table, and an unpriced model shows "unpriced", never $0.
- **Deployable:**
  - Docker image (API + UI)
  - Terraform for ECS Fargate behind CloudFront, with a least-privilege Bedrock role, the API token in SSM
    and a budget alert
  - a demo-session guide with costs (≈ $0.30 of infrastructure for 3 hours)

## Getting started

```bash
pip install -e ".[local,openai,bedrock,dev]"
codeatlas demo          # http://127.0.0.1:8000
```

See the README Quickstart for Windows, Ollama and Bedrock.

## Documentation

`docs/` covers:
- [architecture](architecture.md)
- [evaluation and how to reproduce every number](evaluation.md)
- [configuration](configuration.md)
- [HTTP API](api.md)
- [deployment](deploy.md)
- [troubleshooting](troubleshooting.md)

## Not in this release

- **LLM-judged answer quality** (faithfulness, citation precision, refusal accuracy on the test split). It
  is pending the review of the reference answers and will follow in a minor release.
- **Retrieval changes from the review backlog:** constant-based mappings, overload-aware graph nodes,
  interface-method impact, and confidence after test demotion. They need a new dev cycle and a new frozen
  split.

Full list of changes: [CHANGELOG.md](../CHANGELOG.md).
