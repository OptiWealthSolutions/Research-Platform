"""FinBERT sentiment + keyword extraction + extractive summarization.

Heavy ML imports (torch/transformers) are loaded lazily so importing this
module (e.g. from the FastAPI app) stays cheap. The FinBERT model is loaded
once and reused (singleton).
"""
import io
import re
import math
from collections import Counter
from functools import lru_cache

FINBERT_MODEL = "ProsusAI/finbert"
_MODEL = None
_TOKENIZER = None

# Domain stopwords on top of NLTK's English list — common research/boilerplate.
_EXTRA_STOP = {
    "paper", "study", "studies", "result", "results", "find", "finding",
    "findings", "model", "models", "data", "using", "use", "used", "show",
    "shows", "analysis", "research", "abstract", "however", "also", "may",
    "across", "within", "based", "approach", "effect", "effects", "evidence",
    "new", "two", "one", "three", "first", "second", "well", "due", "via",
    "given", "among", "around", "toward", "towards", "whether", "since",
}


@lru_cache(maxsize=1)
def _stopwords():
    try:
        from nltk.corpus import stopwords
        sw = set(stopwords.words("english"))
    except Exception:
        sw = {
            "the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
            "with", "is", "are", "was", "were", "be", "by", "that", "this",
            "it", "as", "at", "from", "we", "our", "their", "these", "those",
            "than", "then", "but", "not", "can", "will", "has", "have", "had",
        }
    return sw | _EXTRA_STOP


def _sentences(text: str):
    try:
        from nltk.tokenize import sent_tokenize
        return sent_tokenize(text)
    except Exception:
        return re.split(r"(?<=[.!?])\s+", text)


def _load_finbert():
    """Lazy-load FinBERT once."""
    global _MODEL, _TOKENIZER
    if _MODEL is None:
        import torch  # noqa: F401
        from transformers import (
            AutoTokenizer,
            AutoModelForSequenceClassification,
        )
        _TOKENIZER = AutoTokenizer.from_pretrained(FINBERT_MODEL)
        _MODEL = AutoModelForSequenceClassification.from_pretrained(FINBERT_MODEL)
        _MODEL.eval()
    return _TOKENIZER, _MODEL


def finbert_sentiment(text: str) -> dict:
    """Run FinBERT over (possibly long) text, averaging probabilities across
    512-token chunks. Returns label, signed score in [-1, 1] and per-class
    probabilities."""
    text = (text or "").strip()
    if not text:
        return {"label": "neutral", "score": 0.0,
                "detail": {"positive": 0.0, "negative": 0.0, "neutral": 1.0}}

    import torch
    tokenizer, model = _load_finbert()

    # Tokenize once, then window into <=512-token chunks (cap total work).
    ids = tokenizer.encode(text, add_special_tokens=False, truncation=False)
    max_len = 510  # leave room for [CLS]/[SEP]
    chunks = [ids[i:i + max_len] for i in range(0, len(ids), max_len)][:12]
    if not chunks:
        chunks = [ids]

    probs_sum = torch.zeros(3)
    with torch.no_grad():
        for chunk in chunks:
            input_ids = torch.tensor(
                [[tokenizer.cls_token_id] + chunk + [tokenizer.sep_token_id]]
            )
            logits = model(input_ids).logits
            probs_sum += torch.softmax(logits, dim=1)[0]
    probs = (probs_sum / len(chunks)).tolist()

    # FinBERT label order: 0=positive, 1=negative, 2=neutral
    id2label = {int(k): v.lower() for k, v in model.config.id2label.items()}
    detail = {id2label[i]: round(probs[i], 4) for i in range(len(probs))}
    pos = detail.get("positive", 0.0)
    neg = detail.get("negative", 0.0)
    label = max(detail, key=detail.get)
    score = round(pos - neg, 4)  # bullish (+) / bearish (-)
    return {"label": label, "score": score, "detail": detail}


def extract_keywords(text: str, top_n: int = 8) -> list:
    """RAKE-lite keyword extraction: build candidate phrases by splitting on
    stopwords/punctuation, score words by degree/frequency, rank phrases."""
    text = (text or "").strip()
    if not text:
        return []
    stop = _stopwords()
    lowered = text.lower()
    # Candidate phrases = runs of content words between stopwords/punctuation.
    tokens = re.split(r"[^a-z0-9\-]+", lowered)
    phrases, current = [], []
    for tok in tokens:
        if not tok or tok in stop or len(tok) < 3 or tok.isdigit():
            if current:
                phrases.append(current)
                current = []
        else:
            current.append(tok)
    if current:
        phrases.append(current)

    freq, degree = Counter(), Counter()
    for phrase in phrases:
        deg = len(phrase) - 1
        for w in phrase:
            freq[w] += 1
            degree[w] += deg
    if not freq:
        return []
    word_score = {w: (degree[w] + freq[w]) / freq[w] for w in freq}

    phrase_scores = {}
    for phrase in phrases:
        if len(phrase) > 4:
            continue
        key = " ".join(phrase)
        phrase_scores[key] = max(
            phrase_scores.get(key, 0.0), sum(word_score[w] for w in phrase)
        )
    ranked = sorted(phrase_scores.items(), key=lambda x: x[1], reverse=True)
    out, seen = [], set()
    for phrase, _ in ranked:
        norm = phrase.strip()
        words = norm.split()
        canon = words[0] if len(words) == 1 else norm
        if canon in seen:
            continue
        seen.add(canon)
        out.append(norm.title() if len(words) > 1 else norm.capitalize())
        if len(out) >= top_n:
            break
    return out


def summarize(text: str, max_sentences: int = 4) -> str:
    """Frequency-based extractive summary ("compte rendu")."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    sents = _sentences(text)
    if len(sents) <= max_sentences:
        return text
    stop = _stopwords()
    words = re.findall(r"[a-z0-9]+", text.lower())
    freq = Counter(w for w in words if w not in stop and len(w) > 2)
    if not freq:
        return " ".join(sents[:max_sentences])
    peak = max(freq.values())
    norm = {w: c / peak for w, c in freq.items()}

    scored = []
    for idx, s in enumerate(sents):
        sw = re.findall(r"[a-z0-9]+", s.lower())
        if not (3 <= len(sw) <= 60):
            continue
        score = sum(norm.get(w, 0.0) for w in sw) / math.sqrt(len(sw))
        if idx == 0:
            score *= 1.15  # lead-sentence bias
        scored.append((idx, score))
    if not scored:
        return " ".join(sents[:max_sentences])
    top = sorted(scored, key=lambda x: x[1], reverse=True)[:max_sentences]
    chosen = sorted(i for i, _ in top)
    return " ".join(sents[i] for i in chosen)


def extract_pdf_text(pdf_bytes: bytes, max_pages: int = 12) -> str:
    """Extract text from a PDF byte blob using pypdf."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = []
        for page in reader.pages[:max_pages]:
            txt = page.extract_text() or ""
            if txt:
                parts.append(txt)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()
    except Exception:
        return ""
