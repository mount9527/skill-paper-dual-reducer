"""内部重复指纹：13 字滚动哈希 + 倒排索引。

为什么是 13 字
--------------
中文查重行业的公开口径是「连续 13 字重合」计为重复片段，语义上大致等于一个完整分句。
这里用同一尺度做**篇内**自检——它不比对任何语料库（那需要授权），只回答一个问题：
这篇文章里有没有自己抄自己，或者同一段话换个说法反复出现。

实现
----
Rabin-Karp 多项式滚动哈希，O(n) 建表。先把文本归一化（去标点空白、拉丁转小写），
这样「模型—模型」「模型  —  模型」不会因为排版差异漏检。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re

BASE = 1_000_003
MOD = (1 << 61) - 1
NGRAM = 13

_PUNCT = re.compile(r"[\s\u3000-\u303f\uff00-\uffef!-/:-@\[-`{-~]+")


def normalize(text: str) -> str:
    """去标点、空白，拉丁字母转小写。只用于指纹，不改写原文。"""
    return _PUNCT.sub("", text).lower()


def hashes(text: str, n: int = NGRAM) -> list[int]:
    """返回每个长度为 n 的窗口的滚动哈希（按位置对齐）。"""
    s = normalize(text)
    if len(s) < n:
        return []
    power = pow(BASE, n - 1, MOD)
    h = 0
    for ch in s[:n]:
        h = (h * BASE + ord(ch)) % MOD
    out = [h]
    for i in range(n, len(s)):
        h = (h - ord(s[i - n]) * power) % MOD
        h = (h * BASE + ord(s[i])) % MOD
        out.append(h)
    return out


def gram_set(text: str, n: int = NGRAM) -> set[int]:
    return set(hashes(text, n))


@dataclass
class Match:
    pid: str
    length: int
    sample: str

    def json(self) -> dict:
        return {"pid": self.pid, "chars": self.length, "sample": self.sample}


class FingerprintIndex:
    """hash -> 出现过的 pid 集合。"""

    def __init__(self, n: int = NGRAM):
        self.n = n
        self.index: dict[int, set[str]] = defaultdict(set)
        self.grams: dict[str, list[int]] = {}
        self.norm: dict[str, str] = {}

    def add(self, pid: str, text: str) -> None:
        norm = normalize(text)
        self.norm[pid] = norm
        hs = hashes(text, self.n)
        self.grams[pid] = hs
        for h in hs:
            self.index[h].add(pid)

    def dup_ratio(self, pid: str) -> float:
        """本段有多少比例的 13 字片段在别的段落里也出现过。"""
        hs = self.grams.get(pid, [])
        if not hs:
            return 0.0
        shared = sum(1 for h in hs if len(self.index[h]) > 1)
        return round(shared / len(hs), 4)

    def pair_similarity(self, a: str, b: str) -> float:
        ga, gb = set(self.grams.get(a, [])), set(self.grams.get(b, []))
        if not ga or not gb:
            return 0.0
        return round(len(ga & gb) / len(ga | gb), 4)

    def duplicate_spans(self, min_chars: int = NGRAM, limit: int = 200) -> list[Match]:
        """把连续共享的窗口合并成片段，返回长度大于 min_chars 的重复块。"""
        out: list[Match] = []
        for pid, hs in self.grams.items():
            norm = self.norm[pid]
            i, n = 0, len(hs)
            while i < n and len(out) < limit:
                if len(self.index[hs[i]]) > 1:
                    j = i
                    while j < n and len(self.index[hs[j]]) > 1:
                        j += 1
                    length = (j - i) + self.n - 1
                    if length >= min_chars:
                        out.append(Match(pid, length, norm[i:i + length][:40]))
                    i = j
                else:
                    i += 1
        out.sort(key=lambda m: -m.length)
        return out[:limit]

    def stats(self) -> dict:
        total = sum(len(v) for v in self.grams.values())
        shared = sum(len(pids) for pids in self.index.values() if len(pids) > 1)
        return {
            "paragraphs": len(self.grams),
            "grams": total,
            "shared_grams": shared,
            "repeat_rate": round(shared / total, 4) if total else 0.0,
        }
