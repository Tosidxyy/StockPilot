"""Human-labelled synthetic text with a real configured DeepSeek model."""
import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.services.sentiment_classifier import DeepSeekSentimentClassifier


async def run(output):
    path = ROOT / "evals/datasets/sentiment.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    settings = Settings(_env_file=ROOT / "backend/.env")
    model = DeepSeekSentimentClassifier(settings)
    if not model.configured:
        print("SKIP: no configured model key; no score generated")
        await model.aclose()
        return 0
    started = datetime.now(timezone.utc)
    try:
        predictions = await model.classify([{"id": row["id"], "text": row["text"]} for row in cases])
    finally:
        await model.aclose()
    labels = ("positive", "negative", "neutral")
    matrix = {truth: {predicted: 0 for predicted in labels} for truth in labels}
    for row in cases:
        matrix[row["expected"]][predictions[row["id"]]] += 1
    f1 = {}
    for label in labels:
        tp = matrix[label][label]
        fp = sum(matrix[truth][label] for truth in labels if truth != label)
        fn = sum(matrix[label][pred] for pred in labels if pred != label)
        f1[label] = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0
    accuracy = sum(row["expected"] == predictions[row["id"]] for row in cases) / len(cases)
    macro_f1 = sum(f1.values()) / len(f1)
    report = {"started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "mode": "Human-labelled synthetic text; real DeepSeek classification; not real investor-distribution accuracy.",
        "model": model.model_name, "sample_count": len(cases), "accuracy": accuracy,
        "macro_f1": macro_f1, "per_class_f1": f1, "confusion_matrix": matrix,
        "source_sha256": {str(file.relative_to(ROOT)): hashlib.sha256(file.read_bytes()).hexdigest()
                          for file in (path, Path(__file__), ROOT / "backend/app/services/sentiment_classifier.py")},
        "cases": [{**row, "predicted": predictions[row["id"]], "passed": row["expected"] == predictions[row["id"]]} for row in cases]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sample_count": len(cases), "accuracy": accuracy, "macro_f1": macro_f1}, ensure_ascii=False))
    return 0 if macro_f1 >= .85 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.output)))
