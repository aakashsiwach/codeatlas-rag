# Evaluation

How CodeAtlas is measured, and how to reproduce every number in the README.

## The question set

`eval/petclinic-dev.yaml` (40 questions) and `eval/petclinic-test.yaml` (24) evaluate retrieval on
[Spring PetClinic](https://github.com/spring-projects/spring-petclinic) at commit **500158f**. Each
question has:

- a **category**: `flow`, `locate`, `endpoint`, `endpoint-callers`, `impact`, `config`, `model` or
  `unanswerable`
- **expected sources** a correct answer must use. A source is a path suffix, optionally with a symbol:
  `owner/OwnerController.java` or `owner/OwnerController.java::OwnerController.processFindForm`. Java paths
  are relative to `src/main/java/org/springframework/samples/petclinic/`.
- `answerable: false` for the 11 questions about things PetClinic does not contain (Kafka, OAuth, Flyway…),
  which the system should refuse
- a **reference answer** with `file:line` citations, used by the LLM judge only once it is marked
  `reviewed: true`

Every expected source was verified against `500158f`. A source that no longer resolves fails the run
instead of silently scoring zero.

### Dev and test split

The 64 questions are split by category (stratified) into dev (40) and held-out test (24). All tuning
(ranking rules, reranker choice, candidate pool, graph slots, refusal thresholds) was done on dev only.
Before the test split was run, two setups were frozen and recorded in the README:

| Setup | Mode | Settings |
|---|---|---|
| `fast` | `vector` | AST chunks, `bge-small-en-v1.5`, k = 5, refusal at vector similarity 0.6177 |
| `quality` | `hybrid+rerank+graph` | plus BM25, `gte-reranker-modernbert-base` over 20 candidates, 2 graph slots, refusal at rerank score 0.868 |

`codeatlas eval` refuses a `*-test.yaml` set without `--final`, so the split cannot be run by accident.
Nothing was changed after the test numbers were seen.

## Scoring

**Matching a retrieved chunk to an expected source.**

- **File-level source:** matches any chunk of that file.
- **`Class.method` source:** first resolved to the method's definition, from the declaration (annotations
  and signature) to the closing brace, without the Javadoc above it. A chunk from that file matches only if
  it overlaps the definition by at least **min(5 lines, half the definition)**.
- **Class outlines** list only member signatures, so an outline counts for its class but not for the
  class's methods.
- **Same rule for every chunker,** so naive and AST chunks are scored alike.

**Metrics** (answerable questions only, unless stated):

| Metric | Definition |
|---|---|
| recall@k | Fraction of a question's expected sources matched in the top k, averaged over questions |
| hit@k | Fraction of questions with at least one expected source in the top k |
| MRR | Mean reciprocal rank of the first matching chunk, within the top k (so MRR@k) |
| p50 / p95 latency | Retrieval time per question after a warm-up query, on the machine that ran it (CPU) |
| Refusal accuracy | With answers: refused unanswerable questions plus answered answerable ones, over all questions |
| Citation precision | With answers: fraction of an answer's citations that point at an expected source, over answered answerable questions |
| Faithfulness | With the judge: supported claims / all claims in the answer |
| Answer accuracy | With the judge: agreement with the reviewed reference answer |

## Reproducing the README numbers

Each report in `results/` records its run metadata:
- the CodeAtlas and PetClinic commits (with `-dirty` if the working tree had changes)
- the models and thresholds
- the index size and the chunker

Retrieval runs use local models only and make no LLM calls.

### Setup

```bash
git clone https://github.com/spring-projects/spring-petclinic repos/spring-petclinic
git -C repos/spring-petclinic checkout 500158f
codeatlas index repos/spring-petclinic                                            # AST: 83 files, 327 chunks
codeatlas --index-dir .codeatlas-naive index repos/spring-petclinic --chunker naive   # 188 chunks
```

### Test-split table (README "Results")

These come from committed reports, recorded at CodeAtlas **72143e2**:

| Rows | Report | Command |
|---|---|---|
| baseline | `results/eval-20261113-183425.{md,json}` | `codeatlas --index-dir .codeatlas-naive eval eval/petclinic-test.yaml -k 5 --deep-k 10 --configs vector --final` |
| fast, quality | `results/eval-20261113-174905.{md,json}` | `codeatlas eval eval/petclinic-test.yaml -k 5 --deep-k 10 --configs vector,hybrid+rerank+graph --final` |

The test split was run once per frozen setup. It is not re-run to check reproducibility. The dev re-run
below shows that the retrieval code has not changed since.

### Dev-split numbers (README "Frozen setups" and "Honest findings")

The dev numbers were first measured at **426f15c** and **1cad3ef**. Those reports were not committed, and
two were recorded with uncommitted, non-retrieval UI work in the tree. To check them, the dev split was
re-run on 2026-11-14 at **e8db2cf**, with only uncommitted documentation in the tree. The MiniLM run finished
at **460a8cf**; the commits in between changed indexing of hidden directories, webhooks and traces, but not
retrieval. The commands were:

```bash
codeatlas --index-dir .codeatlas-naive eval eval/petclinic-dev.yaml -k 5 --deep-k 10 --configs vector
codeatlas eval eval/petclinic-dev.yaml -k 5 --deep-k 10 --configs vector,hybrid,hybrid+graph,hybrid+rerank,hybrid+rerank+graph
CODEATLAS_RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2 \
  codeatlas eval eval/petclinic-dev.yaml -k 5 --deep-k 10 --configs hybrid+rerank,hybrid+rerank+graph
```

| Setup (dev) | recall@5 | recall@10 | hit@5 | MRR | README | Re-run (2026-11-14) |
|---|---|---|---|---|---|---|
| naive + vector | 0.752 | 0.879 | 0.939 | 0.720 | — | identical |
| AST + vector (`fast`) | 0.816 | 0.919 | 1.000 | 0.665 | Frozen setups | identical |
| hybrid | 0.808 | 0.950 | 0.970 | 0.679 | Frozen setups, Honest findings | identical |
| hybrid + graph | 0.773 | 0.939 | 0.939 | 0.654 | — | identical |
| hybrid + ModernBERT rerank | 0.889 | 0.965 | 0.970 | 0.806 | — | identical |
| hybrid + ModernBERT rerank + graph (`quality`) | 0.894 | 0.967 | 1.000 | 0.791 | Frozen setups | identical |
| hybrid + MiniLM rerank | 0.745 | 0.881 | 0.939 | 0.563 | Honest findings | identical |

The re-run reports are committed: `results/eval-20261114-002731` (naive), `-002751` (AST, ModernBERT) and
`-013818` (MiniLM).

Retrieval is deterministic on the same index, so recall, hit and MRR reproduce exactly. Latencies depend
on the machine and its load. The README's are from a laptop CPU, and re-runs under load are slower.

### Refusal thresholds

`codeatlas eval eval/petclinic-dev.yaml -k 5 --configs vector,hybrid+rerank+graph --calibrate` prints the
threshold that best separates answerable from unanswerable dev questions (balanced accuracy):

| Model | Threshold | Balanced accuracy (dev) | False refusals | Unanswerable refused |
|---|---|---|---|---|
| `bge-small-en-v1.5` (vector) | 0.6177 | 0.786 | 0/33 | 4/7 |
| `gte-reranker-modernbert-base` | 0.868 | 0.939 | 4/33 | 7/7 |

They rest on only 7 unanswerable dev questions. See the README's Limitations.

## Answer quality (pending)

LLM-judged faithfulness, citation precision and refusal accuracy for the `quality` setup on the test split
are measured with `scripts/run-judge.ps1` / `scripts/run-judge.sh`. These scripts:

1. refuse to start while any reference answer has `reviewed: false`
2. run `codeatlas doctor --llm`
3. make one answer and one judge call, and estimate the cost of N passes against `--budget`
4. ask for confirmation
5. run the passes and print the README table, stopping as soon as the spend exceeds the budget

Numbers go into the README only from the committed reports of those runs.
