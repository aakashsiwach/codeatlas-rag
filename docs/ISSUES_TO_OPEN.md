# Issues to open

One GitHub issue for each item in [ROADMAP.md](../ROADMAP.md). For each one:
1. Copy the **title**.
2. Paste the body: everything between the title and the next horizontal rule.
3. Add the **labels**.

Create the labels `advanced`, `evaluation`, `retrieval`, `graph`, `ingestion` and `ui` first (see the
release notes for the full list of settings). `good first issue`, `help wanted`, `enhancement` and
`dependencies` already exist on GitHub, or were created by Dependabot.

---

## 1. Judged answer-quality runs on a real model

**Title:** Run the LLM-judged answer-quality evaluation on a real model
**Labels:** `help wanted`, `evaluation`

### Context

The README reports retrieval only: recall@5, MRR and impact recall on the held-out test split. Answer
quality (faithfulness, citation precision, refusal accuracy, answer accuracy) was planned but never
measured. The harness is ready, and it has been tested end to end with the offline fake LLM:

- `scripts/run-judge.ps1` / `scripts/run-judge.sh` refuse to start while any reference answer has
  `reviewed: false`.
- Then they run `codeatlas doctor --llm`, make one answer and one judge call, estimate the cost of N passes
  against a budget, and ask before spending.
- They stop as soon as the spend passes the budget.

All 64 reference answers are currently `reviewed: false`. A second-pass check found 6 to correct before
review:

- **Four "only" answers.** `vetrepository-impact`, `petvalidator-impact`, `addvisit-callers` and
  `lastname-query-callers` say "only" X uses the target, which is true for main code, but tests also call
  it. Say "in main code".
- **Two citations missing a supporting line.** `pet-type-conversion` should also cite
  `PetTypeFormatter.java:36-37`, and `mysql-config` should cite `application.properties:3-4`.

### What to do

1. **Review the reference answers** in `eval/petclinic-dev.yaml` and `eval/petclinic-test.yaml`, in a
   separate pull request. Correct only the `reference_answer` text, never the questions or expected
   sources. A maintainer sets `reviewed: true`.
2. **Configure the model.** Pick one (Amazon Bedrock, the Anthropic API, or a capable local model through
   Ollama). Add its price to `CODEATLAS_LLM_PRICES`, keyed `"<provider>:<model>"`: judged runs refuse an
   unpriced model. If the model needs a specific thinking setting for the judge, set
   `CODEATLAS_THINKING_BY_MODEL`.
3. **Run the single check**, then 2–3 passes with the frozen `quality` setup on the test split, for example
   `scripts/run-judge.sh --passes 3 --budget 5`.

### Acceptance criteria

- [ ] All 64 reference answers are reviewed, with corrections in their own commits.
- [ ] The judge's single check is clean: no truncated or failed judgments.
- [ ] Reports from 2–3 judged passes are committed in `results/`.
- [ ] The README's answer-quality section and `docs/evaluation.md` are filled in from those reports: the
      provider and model, the commit, the mean and spread across passes, and failure examples.
- [ ] The "Project status" section no longer says the judged evaluation was not run.

---

## 2. Sort graph reports by relevance

**Title:** List main-code dependents before test code in the graph impact report
**Labels:** `good first issue`, `graph`

### Context

`CodeGraph.impact()` in `codeatlas/graph.py` walks predecessors in sorted node-name order, so the report
(the "Direct dependents / Indirect dependents" text that goes into the LLM's context) lists dependents in
package order. A test class can therefore come first, ahead of the main code that matters. The demo shows
this on `Owner`.

### What to do

Within each depth, order the hits so that main code comes before test code (use `is_test_path()` from
`codeatlas/retrieval/retriever.py`, which `demote_tests` uses). Then order by relation, for example `calls` / `implemented_by`
before `uses`, and then by name, so the output stays deterministic.

### Acceptance criteria

- [ ] For each depth, the report lists main-code dependents before test-code dependents.
- [ ] A test in `tests/test_graph.py` covers the order, using the fixture repository.
- [ ] Retrieval metrics on the dev split are unchanged: run
      `codeatlas eval eval/petclinic-dev.yaml -k 5 --configs hybrid+rerank+graph` and report the result in
      the pull request. Only the order of the report text changes.

---

## 3. Implementations in the impact of an interface method

**Title:** Include implementing methods in the impact of an interface method
**Labels:** `good first issue`, `graph`, `retrieval`

### Context

`impact()` in `codeatlas/graph.py` is a reverse breadth-first search: it follows edges from dependent to
dependency, backwards. Implementing *types* appear in a type's impact because they point at the interface
through `implements` edges.

An `implemented_by` edge runs the other way, from the interface method to its override. So for a method
target, the overriding methods, which also break when the signature changes, are never reached. Only
callers are.

### What to do

When a target, or a node reached from one, is a method with outgoing `implemented_by` edges, add those
implementations as hits with the relation `implemented_by`.

### Acceptance criteria

- [ ] `codeatlas impact Interface.method` lists the implementing methods.
- [ ] A test covers it, using an interface with two implementations in the fixture repository (add one if
      needed).
- [ ] The dev-split impact recall is reported before and after, from a committed dev report. This changes
      retrieval, so it does not change the frozen setups or the README's test numbers.

---

## 4. Request mappings that use constants

**Title:** Handle request mappings that use constants (`@GetMapping(Paths.OWNER)`)
**Labels:** `help wanted`, `ingestion`, `retrieval`

### Context

`_mappings()` in `codeatlas/ingest/java.py` reads string literals from mapping annotations. A mapping whose
path is a constant, such as `@GetMapping(Paths.OWNER)`, is recorded with the class's base path only, so
endpoint questions and endpoint callers miss that handler.

### What to do

1. Record a constant path as an unresolved endpoint that keeps its identifier.
2. Resolve `static final String` constants declared in the same file, including simple concatenation.
3. Leave constants from other files unresolved, unless resolving them stays simple.

### Acceptance criteria

- [ ] Fixture tests cover a constant in the same class, a constant in another class (stays unresolved, with
      the identifier kept), and a concatenated constant.
- [ ] `codeatlas impact` and endpoint-caller queries find a handler mapped through a same-file constant.
- [ ] Dev-split results are reported before and after; no test-split run.

---

## 5. Rerank confidence after test code is demoted

**Title:** Compute rerank confidence after test code is demoted
**Labels:** `help wanted`, `retrieval`

### Context

In `Retriever.retrieve()` (`codeatlas/retrieval/retriever.py`), the confidence used for the "I don't know"
decision is the sigmoid of the top rerank score, taken before test chunks are demoted. When a test chunk
scores highest and is then pushed down, the confidence reflects a chunk that is no longer at the top.

### What to do

Add a ranking option, for example `confidence_after_demotion`, that computes the confidence from the top
chunk after demotion. Measure it on dev: the score distribution moves, so a new threshold has to be chosen
on dev.

### Acceptance criteria

- [ ] The option is added, off by default, with a unit test.
- [ ] The pull request reports dev-split answerable and unanswerable confidence ranges, and the refusal
      counts, with the option on and off.
- [ ] The frozen `quality` threshold (0.868) and the README's test numbers are unchanged. Making the option
      the default is a separate decision, and it needs a new held-out split.

---

## 6. One graph node per method overload

**Title:** Give each method overload its own graph node
**Labels:** `advanced`, `graph`, `retrieval`

### Context

Graph nodes are named `Type#method`, so overloads share one node. `Retriever.first_chunk()` returns the
first overload's first part only, so a graph neighbour added to the results can be the wrong overload, or
the wrong part of a long method.

### What to do

Give each overload its own node: the parameter types are available from tree-sitter. Call resolution then
has to pick an overload, or fall back to all of them when the argument types are unknown. Update the graph
storage, `impact`, `callers` and the graph slots in retrieval.

### Acceptance criteria

- [ ] A design note in the issue or pull request covers node naming, call resolution with unknown argument
      types, and migration of existing indexes (a forced re-index is acceptable).
- [ ] Fixture tests cover overloads with different callers.
- [ ] Dev-split results are reported before and after; no test-split run.

---

## 7. TypeScript 7 for the web UI

**Title:** Upgrade the web UI to TypeScript 7
**Labels:** `good first issue`, `ui`, `dependencies`

### Context

Dependabot's TypeScript 5.6 → 7 update was deferred, and `.github/dependabot.yml` ignores TypeScript
majors. The UI type-checks with `tsc --noEmit -p .` (`npm run typecheck`) and builds with Vite 8.

### What to do

1. Upgrade `typescript` in `ui/package.json`, and adjust `ui/tsconfig.json` if options changed.
2. Fix any new type errors.
3. Remove the TypeScript ignore from `.github/dependabot.yml`.

### Acceptance criteria

- [ ] `npm run typecheck`, `npm test`, `npx vite build` and `npm run e2e` pass on Node 24 and 26, both
      locally and in CI.
- [ ] `npm audit` reports no new vulnerabilities.
- [ ] The CHANGELOG is updated under `[Unreleased]`.

---

## 8. A cross-service (Feign) evaluation set

**Title:** Add a cross-service (Feign) evaluation set on spring-petclinic-microservices
**Labels:** `advanced`, `evaluation`

### Context

CodeAtlas links Feign clients and URL string literals to the endpoints they call. That is what
cross-service "who calls this endpoint" questions need, but the current evaluation (single-service
PetClinic) never measures it.

### What to do

1. Pin a revision of `spring-petclinic-microservices`.
2. Write questions verified against the code: endpoint callers across services, impact across services,
   and some unanswerable ones. Each needs expected sources with line ranges.
3. Split them into dev and test before running anything, as described in `docs/evaluation.md`.
4. Freeze setups on dev, then run test once with `--final`.

### Acceptance criteria

- [ ] `eval/microservices-dev.yaml` and `eval/microservices-test.yaml`, with the revision pinned.
- [ ] Committed reports for the baseline and the frozen setups, with the commit hashes stated.
- [ ] A README or `docs/evaluation.md` section with the results, including the findings that did not help.

---

## 9. A faster quality setup

**Title:** Make the quality setup faster (lighter reranker, fewer candidates or GPU)
**Labels:** `advanced`, `retrieval`

### Context

`quality` (hybrid + ModernBERT reranker over 20 candidates + code graph) takes about 30 s per question on
CPU, against about 35 ms for `fast` (p50 32.7 s vs 35 ms on test). Most of the time is the cross-encoder.

### What to do

On the dev split, compare lighter rerankers, smaller rerank pools and GPU inference for recall@5, MRR and
p50 latency. Pick a candidate and freeze it. Then evaluate it on a new held-out split: the current test
split has already been seen (see "A larger evaluation set").

### Acceptance criteria

- [ ] A dev comparison table, from committed reports, with latency measured on stated hardware.
- [ ] A frozen candidate, evaluated once on a new held-out split.
- [ ] The README keeps the existing `quality` numbers and adds the new setup as its own row.

---

## 10. A larger evaluation set

**Title:** Grow the evaluation set and freeze a larger held-out split
**Labels:** `help wanted`, `evaluation`

### Context

The test split has 24 questions (20 answerable, 1–5 per category), so a single question moves hit@5 by
0.05, and the per-category numbers are only indicative. The thresholds rest on 7 unanswerable dev
questions.

### What to do

Add verified questions, ideally including a second Spring Boot repository:
- each needs expected sources with line ranges and a category
- add more unanswerable questions

Then freeze a new held-out split before running any setup on it. Keep the current split and its numbers
for comparison.

### Acceptance criteria

- [ ] At least 100 questions in total, with every category represented by at least 5 test questions.
- [ ] The new split is committed before any test run, and its commit is named in the results.
- [ ] Baseline, `fast` and `quality` are reported on the new split, from committed reports.
