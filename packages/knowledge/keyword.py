"""Small-corpus BM25 and rank fusion."""

import math
import re
import unicodedata
from collections import Counter

K1 = 1.2
B = 0.75
RRF_K = 60
TOKENIZER = "han-unigram-bigram-ascii-identifiers-v1"
HAN = r"[\u3400-\u4dbf\u4e00-\u9fff]"
WORDS = re.compile(rf"{HAN}+|[a-z0-9]+(?:[._-][a-z0-9]+)*")


def tokenize(text):
    tokens = []
    for word in WORDS.findall(unicodedata.normalize("NFKC", text).casefold()):
        if re.fullmatch(HAN, word[0]):
            tokens.extend(word)
            tokens.extend(a + b for a, b in zip(word, word[1:]))
        else:
            tokens.append(word)
    return tokens


def _order(row):
    return (-row["score"], row["scope_key"], row["document_key"], row["ordinal"])


def bm25(query, candidates, *, k):
    terms = sorted(set(tokenize(query)))
    if not terms or not candidates:
        return []
    counts = [Counter(tokenize(row["body"])) for row in candidates]
    lengths = [counter.total() for counter in counts]
    average = sum(lengths) / len(counts)
    if not average:
        return []
    frequencies = Counter(term for counter in counts for term in counter if term in terms)
    idf = {
        term: math.log1p((len(counts) - df + 0.5) / (df + 0.5)) for term, df in frequencies.items()
    }
    results = []
    for row, counter, length in zip(candidates, counts, lengths, strict=True):
        norm = K1 * (1 - B + B * length / average)
        score = sum(
            idf[term] * counter[term] * (K1 + 1) / (counter[term] + norm)
            for term in terms
            if counter[term]
        )
        if score > 0:
            results.append(dict(row, score=score))
    return sorted(results, key=_order)[:k]


def fuse_ranks(vector, keyword, *, k):
    combined = {}
    for mode, rows in (("vector", vector), ("bm25", keyword)):
        for rank, row in enumerate(rows, 1):
            item = combined.setdefault(row["chunk_id"], dict(row, score=0.0, ranks={}))
            if mode not in item["ranks"]:
                item["ranks"][mode] = rank
                item["score"] += 1 / (RRF_K + rank)
    return sorted(combined.values(), key=_order)[:k]
