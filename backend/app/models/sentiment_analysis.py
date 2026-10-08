from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.models.sentiment import ForumComment

Emotion = Literal["positive", "negative", "neutral"]


class ClassifiedComment(ForumComment):
    sentiment: Emotion = Field(...)


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    sentiment: Emotion


class Predictions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Prediction] = Field(max_length=200)


class SentimentAnalysis(BaseModel):
    symbol: str
    model: str
    sample_count: int = Field(ge=1, le=200)
    counts: dict[Emotion, int]
    labels: dict[str, Emotion]
    preview: list[ClassifiedComment] = Field(max_length=8)
    reused_count: int
    sample_start: datetime
    sample_end: datetime
    source_cached_at: datetime
    source_stale: bool
    analysed_at: datetime

    @model_validator(mode="after")
    def complete_counts(self):
        expected = {label: sum(value == label for value in self.labels.values())
                    for label in ("positive", "negative", "neutral")}
        if self.counts != expected or len(self.labels) != self.sample_count:
            raise ValueError("incomplete sentiment counts")
        if any(self.labels.get(item.id) != item.sentiment or item.symbol != self.symbol for item in self.preview):
            raise ValueError("preview does not match classified snapshot")
        return self


class AnalysisView(BaseModel):
    symbol: str
    status: Literal["idle", "running", "ready", "failed", "unconfigured"]
    job_id: str | None = None
    completed: int = 0
    total: int = 0
    error: str | None = None
    result: SentimentAnalysis | None = None
    checked_at: datetime | None = None
