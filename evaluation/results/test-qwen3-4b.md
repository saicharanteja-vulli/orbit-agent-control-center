# Orbit document research benchmark

Run: 2026-10-18T04:27:53.459280+00:00 · Split: test · Dataset: orbit-docqa-v1

This is a small, hand-authored fictional policy benchmark. Results are diagnostic and do not establish production accuracy.

| Strategy | Cases | Errors | Document recall@4 | MRR | Retrieval p95 ms |
|---|---:|---:|---:|---:|---:|
| bm25 | 30 | 0 | 0.9583 | 0.9306 | 0.19 |
| dense | 30 | 0 | 1.0 | 0.9722 | 196.64 |
| hybrid | 30 | 0 | 1.0 | 0.9514 | 35.25 |

| Strategy | Automatic fact/abstention proxy | Unanswerable abstention | Answerable abstention | Citation document relevance | Total p95 ms |
|---|---:|---:|---:|---:|---:|
| bm25 | 0.7667 | 0.6667 | 0.0417 | 0.9048 | 7723.87 |
| dense | 0.7333 | 0.3333 | 0.0 | 0.8542 | 11392.45 |
| hybrid | 0.7333 | 0.3333 | 0.0 | 0.8542 | 11395.99 |

## Metric definitions and limits

- Recall@4 measures the fraction of expected source documents present in the top four chunks, averaged over answerable cases. MRR uses the first relevant chunk. These are document-level relevance labels, not sentence-level labels.
- Automatic answer pass requires every expected fact alias in generated claim text, or abstention on an unanswerable case. It can miss paraphrases and cannot detect every unsupported extra claim. It is not a semantic accuracy score.
- Citation document relevance measures references to labeled relevant documents. Exact quote provenance is enforced in the product; neither metric establishes entailment. Manually review the raw claims and quotes.
- Provider errors count as failed answers. Invalid citations produce abstention and are counted separately. High abstention can reduce usefulness.
- Timing includes local model loading and a per-strategy in-memory embedding cache. The first query per strategy embeds the corpus; later queries reuse embeddings. Live product runs currently rebuild that cache per run. No dollar cost is estimated for local inference.
- Published test questions are a fixed split, not a secret benchmark. Do not tune on them; version the dataset and use new external questions for future validation.

## Failures

- bm25 / test02 (paraphrase): recall=1.0, abstained=False, fact checks=[False]
- bm25 / test04 (paraphrase): recall=0.0, abstained=False, fact checks=[False, False, False]
- bm25 / test15 (direct): recall=1.0, abstained=True, fact checks=[False, False]
- bm25 / test22 (multi_source): recall=1.0, abstained=False, fact checks=[False, True, True]
- bm25 / test24 (negative): recall=None, abstained=False, fact checks=[]
- bm25 / test26 (negative): recall=None, abstained=False, fact checks=[]
- bm25 / test29 (injection): recall=1.0, abstained=False, fact checks=[False]
- dense / test02 (paraphrase): recall=1.0, abstained=False, fact checks=[False]
- dense / test22 (multi_source): recall=1.0, abstained=False, fact checks=[False, True, True]
- dense / test24 (negative): recall=None, abstained=False, fact checks=[]
- dense / test25 (negative): recall=None, abstained=False, fact checks=[]
- dense / test26 (negative): recall=None, abstained=False, fact checks=[]
- dense / test27 (false_premise): recall=1.0, abstained=False, fact checks=[True, False]
- dense / test29 (injection): recall=1.0, abstained=False, fact checks=[False]
- dense / test30 (negative): recall=None, abstained=False, fact checks=[]
- hybrid / test02 (paraphrase): recall=1.0, abstained=False, fact checks=[False]
- hybrid / test22 (multi_source): recall=1.0, abstained=False, fact checks=[False, True, True]
- hybrid / test24 (negative): recall=None, abstained=False, fact checks=[]
- hybrid / test25 (negative): recall=None, abstained=False, fact checks=[]
- hybrid / test26 (negative): recall=None, abstained=False, fact checks=[]
- hybrid / test27 (false_premise): recall=1.0, abstained=False, fact checks=[True, False]
- hybrid / test29 (injection): recall=1.0, abstained=False, fact checks=[False]
- hybrid / test30 (negative): recall=None, abstained=False, fact checks=[]

The companion JSON contains every question, retrieved passage, answer, quote, timing and usage record.
