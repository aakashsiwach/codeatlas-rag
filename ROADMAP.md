# Roadmap

CodeAtlas is feature-complete and maintained on a best-effort basis. This is the open work, for anyone who
wants to pick it up. Each item has a ready-to-paste issue in [docs/ISSUES_TO_OPEN.md](docs/ISSUES_TO_OPEN.md).

**Labels:**
- **good first issue:** small, well defined, and needs no model access or AWS account.
- **help wanted:** a medium-sized piece of work, or one that needs access to an LLM.
- **advanced:** a design decision, a new evaluation cycle, or both.

Changes to retrieval follow the [evaluation rules](CONTRIBUTING.md#evaluation-rules). Measure them on dev,
and add them behind an option first. Making one the default needs a new frozen setup and a new held-out
split, because the current test split has already been seen.

| # | Item | Label | Area |
|---|---|---|---|
| 1 | [Judged answer-quality runs on a real model](#1-judged-answer-quality-runs-on-a-real-model) | help wanted | evaluation |
| 2 | [Sort graph reports by relevance](#2-sort-graph-reports-by-relevance) | good first issue | graph |
| 3 | [Implementations in the impact of an interface method](#3-implementations-in-the-impact-of-an-interface-method) | good first issue | graph, retrieval |
| 4 | [Request mappings that use constants](#4-request-mappings-that-use-constants) | help wanted | ingestion, retrieval |
| 5 | [Rerank confidence after test code is demoted](#5-rerank-confidence-after-test-code-is-demoted) | help wanted | retrieval |
| 6 | [One graph node per method overload](#6-one-graph-node-per-method-overload) | advanced | graph, retrieval |
| 7 | [TypeScript 7 for the web UI](#7-typescript-7-for-the-web-ui) | good first issue | ui, dependencies |
| 8 | [A cross-service (Feign) evaluation set](#8-a-cross-service-feign-evaluation-set) | advanced | evaluation |
| 9 | [A faster quality setup](#9-a-faster-quality-setup) | advanced | retrieval |
| 10 | [A larger evaluation set](#10-a-larger-evaluation-set) | help wanted | evaluation |

## 1. Judged answer-quality runs on a real model

The README has retrieval numbers only. LLM-judged faithfulness, citation precision and refusal accuracy
were never measured. The harness is ready (`scripts/run-judge.ps1` / `.sh`), but it refuses to run until
all 64 reference answers are reviewed, and none are yet. A second-pass check found 6 to correct before
review. Needs access to a model (Amazon Bedrock, the Anthropic API or a capable local model) and a small
budget; the script prints a cost estimate before it spends anything.

## 2. Sort graph reports by relevance

The impact report given to the LLM lists dependents in package order, so a test class can come before the
main code that matters (seen in the demo). Order them within each depth: main code before test code, then
by relation. Only the LLM context changes; the retrieval metrics should not.

## 3. Implementations in the impact of an interface method

For an interface *method*, `impact` lists its callers but not the classes that implement it, which also
break when its signature changes. Implementing *types* already appear, because they point at the
interface through `implements` edges. An `implemented_by` edge runs the other way, from the interface
method to its override, so the reverse search never reaches it. Follow `implemented_by` forward for method
targets.

## 4. Request mappings that use constants

`@GetMapping(Paths.OWNER)` is recorded as the class's base path, so endpoint questions and endpoint
callers miss these handlers. Keep the identifier as an unresolved endpoint, and resolve `static final
String` constants declared in the same file.

## 5. Rerank confidence after test code is demoted

The refusal decision uses the top rerank score before test chunks are pushed down, so it can reflect a
test chunk that is no longer at the top. Computing confidence after demotion changes the score
distribution, so the `quality` threshold has to be re-chosen on dev.

## 6. One graph node per method overload

Overloads share one graph node, and the graph's chunk lookup returns the first overload's first part
only. A graph neighbour can therefore be the wrong overload. This changes node identity throughout the
graph, its storage and the graph slots in retrieval.

## 7. TypeScript 7 for the web UI

Dependabot's TypeScript 7 update was deferred, and TypeScript majors are ignored in
`.github/dependabot.yml`. Upgrade, fix any type or config changes, and remove the ignore.

## 8. A cross-service (Feign) evaluation set

CodeAtlas resolves Feign clients and URL literals to endpoints, but that is never measured. Build a
dev/test question set on `spring-petclinic-microservices` at a pinned revision, focused on cross-service
callers, and report the baseline and the frozen setups.

## 9. A faster quality setup

`quality` takes about 30 s per question on CPU because the cross-encoder rescores 20 candidates. Try a
lighter reranker, fewer candidates or a GPU, measured on dev, with a new held-out split for the final
numbers.

## 10. A larger evaluation set

The test split has 24 questions (1–5 per category), so one question moves hit@5 by 0.05. Grow the set,
ideally with a second Spring Boot repository, and freeze a new, larger held-out split. Keep the old split
and its numbers for comparison.

## Ideas (no issue yet)

Trace export (OpenTelemetry or Langfuse), a Neo4j graph backend for very large repositories, and Kotlin
support. Open an issue to discuss before starting on any of these.
