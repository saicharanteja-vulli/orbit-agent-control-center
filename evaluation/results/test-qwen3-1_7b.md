# Orbit document research benchmark

Run: 2026-10-18T04:18:22.475233+00:00 · Split: test · Dataset: orbit-docqa-v1

This is a small, hand-authored fictional policy benchmark. Results are diagnostic and do not establish production accuracy.

| Strategy | Cases | Errors | Document recall@4 | MRR | Retrieval p95 ms |
|---|---:|---:|---:|---:|---:|
| bm25 | 30 | 0 | 0.9583 | 0.9306 | 0.11 |
| dense | 30 | 0 | 1.0 | 0.9722 | 36.06 |
| hybrid | 30 | 0 | 1.0 | 0.9514 | 103.69 |

| Strategy | Automatic fact/abstention proxy | Unanswerable abstention | Answerable abstention | Citation document relevance | Total p95 ms |
|---|---:|---:|---:|---:|---:|
| bm25 | 0.6667 | 0.6667 | 0.2083 | 0.825 | 3012.21 |
| dense | 0.7333 | 0.8333 | 0.125 | 0.9048 | 2794.73 |
| hybrid | 0.8667 | 0.8333 | 0.0417 | 0.9111 | 4710.58 |

## Metric definitions and limits

- Recall@4 measures the fraction of expected source documents present in the top four chunks, averaged over answerable cases. MRR uses the first relevant chunk. These are document-level relevance labels, not sentence-level labels.
- Automatic answer pass requires every expected fact alias in generated claim text, or abstention on an unanswerable case. It can miss paraphrases and cannot detect every unsupported extra claim. It is not a semantic accuracy score.
- Citation document relevance measures references to labeled relevant documents. Exact quote provenance is enforced in the product; neither metric establishes entailment. Manually review the raw claims and quotes.
- Provider errors count as failed answers. Invalid citations produce abstention and are counted separately. High abstention can reduce usefulness.
- Timing includes local model loading and a per-strategy in-memory embedding cache. The first query per strategy embeds the corpus; later queries reuse embeddings. Live product runs currently rebuild that cache per run. No dollar cost is estimated for local inference.
- Published test questions are a fixed split, not a secret benchmark. Do not tune on them; version the dataset and use new external questions for future validation.

## Failures

- bm25 / test01 (direct): recall=1.0, abstained=True, fact checks=[False, False]
- bm25 / test04 (paraphrase): recall=0.0, abstained=False, fact checks=[False, False, False]
- bm25 / test08 (direct): recall=1.0, abstained=True, fact checks=[False, False]
- bm25 / test11 (direct): recall=1.0, abstained=True, fact checks=[False, False]
- bm25 / test17 (direct): recall=1.0, abstained=True, fact checks=[False, False]
- bm25 / test19 (direct): recall=1.0, abstained=True, fact checks=[False, False]
- bm25 / test27 (false_premise): recall=1.0, abstained=False, fact checks=[False, True]
- bm25 / test28 (injection): recall=None, abstained=False, fact checks=[]
- bm25 / test29 (injection): recall=1.0, abstained=False, fact checks=[True]
- bm25 / test30 (negative): recall=None, abstained=False, fact checks=[]
- dense / test01 (direct): recall=1.0, abstained=False, fact checks=[False, False]
- dense / test04 (paraphrase): recall=1.0, abstained=True, fact checks=[False, False, False]
- dense / test17 (direct): recall=1.0, abstained=True, fact checks=[False, False]
- dense / test19 (direct): recall=1.0, abstained=True, fact checks=[False, False]
- dense / test20 (direct): recall=1.0, abstained=False, fact checks=[False, False]
- dense / test27 (false_premise): recall=1.0, abstained=False, fact checks=[False, False]
- dense / test28 (injection): recall=None, abstained=False, fact checks=[]
- dense / test29 (injection): recall=1.0, abstained=False, fact checks=[True]
- hybrid / test01 (direct): recall=1.0, abstained=True, fact checks=[False, False]
- hybrid / test27 (false_premise): recall=1.0, abstained=False, fact checks=[False, False]
- hybrid / test28 (injection): recall=None, abstained=False, fact checks=[]
- hybrid / test29 (injection): recall=1.0, abstained=False, fact checks=[True]

The companion JSON contains every question, retrieved passage, answer, quote, timing and usage record.
