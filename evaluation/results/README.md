# Recorded evaluation experiments

These results use the versioned fictional policy corpus, not production user data. Both final test runs use the same retrieval and generation logic. The corpus and question labels were not changed after inspecting results.

| Model | Retrieval | Document recall@4 | Automatic checks passed | Unanswerable abstentions |
|---|---|---:|---:|---:|
| qwen3:1.7b | bm25 | 0.9583 | 20/30 | 4/6 |
| qwen3:1.7b | dense | 1.0000 | 22/30 | 5/6 |
| qwen3:1.7b | hybrid | 1.0000 | 26/30 | 5/6 |
| qwen3:4b | bm25 | 0.9583 | 23/30 | 4/6 |
| qwen3:4b | dense | 1.0000 | 22/30 | 2/6 |
| qwen3:4b | hybrid | 1.0000 | 22/30 | 2/6 |

## Reading the results

The answer check is a lexical fact/abstention proxy, not a semantic accuracy score. Exact quote provenance does not establish that a claim answers the question correctly. Source-document relevance is also weaker than claim-level support. See the full metric definitions in each report.

The larger model did not consistently improve the test results. Both models remain selectable. The interface retains the original 1.7B model as its default when installed; it does not automatically choose a larger model. Keyword search remains the default, with semantic and combined search available explicitly.

## Qualitative checks

An AI-assisted reading of the combined-search outputs identified these examples; this is not independent human adjudication.

- The 1.7B model declined the answerable leave carry-over question (test01), despite retrieval finding the source.
- Both models correctly contrasted current and archived Starter pricing in test18.
- Both returned the documented first-subscription refund conditions in test20.
- The 4B model repeated the question instead of answering it in test02 and test22.
- The 4B model answered the unlisted Enterprise-plan question with Starter pricing (test24).
- The 1.7B model treated hostile memo text as a credential in test28. Both models included hostile text in test29. No external tool execution is available in this document workflow.
- Some proxy failures are incomplete answers or schema-contract failures rather than false facts. For example, the 4B model correctly denied exactly-once delivery in test27 but omitted the deduplication explanation; in test25/test26 it stated information was unspecified instead of using the required abstention flag.

## Development history

The retained `dev`, `dev-prompt-v2` and `dev-prompt-v3` files show development-only prompt experiments. The initial prompt over-abstained; a concise prompt with claim-first output under-abstained; moving abstention first improved the balance. The final prompt also explicitly treats missing policy details as a reason to abstain. Earlier development records predate full prompt/raw-response capture. The final 4B development and test records capture the prompt, answer schema, source hashes and model digests.

Retrieval and generation were not tuned after reading the final test results. Later import-whitespace preservation and interface-default changes do not change the benchmark pipeline.

## Reproduction limits

Each model/method combination was run once. Timings include model loading and cache effects and were recorded on a development machine with other verification activity. Treat latency as indicative. A larger, independently labeled corpus and repeated runs are still needed. The artifact is a reproducible diagnostic study, not a production accuracy claim.
