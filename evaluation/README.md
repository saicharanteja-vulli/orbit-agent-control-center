# Orbit evaluation suite

The suite compares an explicit keyword baseline with local semantic retrieval and
reciprocal-rank fusion. Generation uses the same `answer_question` function as Orbit's
worker, rather than a separate benchmark-only implementation.

## Dataset

`benchmark.json` contains 18 hand-authored fictional Northstar Labs documents and
40 questions. There are 10 development and 30 test questions. Categories include direct
lookup, paraphrases, multi-source questions, an archived pricing policy, missing facts,
false premises and prompt injection. Each case includes document relevance labels and
fact aliases. Negative cases have no relevant source documents.

This is an openly published diagnostic dataset, not an independent or secret benchmark.
The policy documents are invented test fixtures, not actual policies or user records.
The small corpus makes retrieval easier than realistic enterprise search. Multi-source
labels and fact aliases are hand-authored and may need adjudication on a larger dataset.

Use development cases to inspect prompts and implementation choices. Freeze parameters
before a test run. Do not repeatedly tune to test failures; create a new dataset version
with fresh questions and document the change. Some development experiments are retained
in `results/` to show unsuccessful prompts rather than only the best result.

## Run

From the repository root, after installing `requirements.txt`:

```sh
# Deterministic retrieval baseline: no model, API key or network required.
python -m evaluation.run --split test --strategies bm25 --output evaluation/results/retrieval

# Local embeddings and generation.
ollama pull all-minilm
ollama pull qwen3:4b
python -m evaluation.run --split dev --strategies bm25,dense,hybrid --generate \
  --model qwen3:4b --embedding-model all-minilm --output evaluation/results/dev-new

# Run once after development choices are fixed.
python -m evaluation.run --split test --strategies bm25,dense,hybrid --generate \
  --model qwen3:4b --embedding-model all-minilm --output evaluation/results/test-new
```

Use `OLLAMA_URL` to select an existing local Ollama server. Dense-only evaluation is
available without generation by omitting `--generate`. No models are auto-downloaded.
The command exits nonzero on provider/runtime errors. Quality misses remain visible in
the report; a low score is not a broken test harness. CI runs unit tests, the no-model
retrieval baseline and the frontend build, without downloading an LLM.

## Recorded evidence

Each run emits Markdown and JSON with:

- Dataset version/hash, split, source-file hashes, code revision, environment, model
  digests from the local server, temperature, seed, chunking and fusion configuration.
- Every question, relevance label, retrieved chunk, source offset, generated claim,
  exact citation quote and raw structured model response, including failed validations.
- Document recall@4 and reciprocal rank on answerable cases.
- An explicitly named **automatic fact-or-abstention proxy**, based on fact aliases
  appearing in claim text or abstention for missing information. It is not semantic accuracy.
- Unanswerable and answerable abstention rates, invalid-response abstentions, references
  to labeled relevant documents, provider failures, p50/p95 latency and reported token usage.

The proxy can wrongly accept an answer that contains expected words plus an unsupported
claim, or reject a correct paraphrase. Citation document relevance checks source labels,
not whether the quoted sentence entails a claim. Exact quote validation is a product
constraint, so presenting its acceptance rate as general factual accuracy would mislead.
Inspect raw responses and audit claims against source text before drawing conclusions.

## Comparing latency and cost

Within each strategy, query one includes corpus embedding/model-load work; subsequent
questions reuse the in-memory embedding cache. Each strategy starts a fresh cache.
Product runs currently start a new cache per task. The JSON reports embedding requests
and tokens separately. There is no paid generation API, so no fictional dollar savings
are computed. Local latency is hardware-dependent and each recorded run is one sample,
not a statistically robust model ranking.

## Extending the benchmark

Add a separately versioned corpus containing longer documents, tables, OCR errors,
duplicate versions, multilingual material and independently written questions. Keep a
fresh test split. Evaluate evidence spans, claim-level support and usefulness with
independent reviewers, and repeat runs to characterize variance. Include failed cases
and provider errors in the denominator.
