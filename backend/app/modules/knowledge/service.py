"""Ответы про условия покупки только из утверждённого текста."""

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[4] / "data" / "curated" / "purchase_terms.json"


@dataclass(frozen=True)
class TermsTopic:
    id: str
    title: str
    text: str
    sources: tuple[str, ...]
    caveats: tuple[str, ...]


class PurchaseTermsService:
    def __init__(self, path: Path = DEFAULT_PATH):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.version = data["version"]
        self._keywords = {t["id"]: [k.lower() for k in t["keywords"]] for t in data["topics"]}
        self.topics = {
            t["id"]: TermsTopic(t["id"], t["title"], t["text"], tuple(t["sources"]), tuple(t.get("caveats", [])))
            for t in data["topics"]
        }

    def topic_ids(self) -> list[str]:
        return list(self.topics)

    def get(self, topic_ids: list[str]) -> list[TermsTopic]:
        return [self.topics[t] for t in topic_ids if t in self.topics]

    def match(self, question: str) -> list[TermsTopic]:
        q = question.lower()
        return [self.topics[tid] for tid, words in self._keywords.items() if any(w in q for w in words)]
