import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.sentiment import SentimentService, summarize
from app.services.sentiment_analysis import NoSentimentSamples, SentimentAnalysisService
from app.services.sentiment_classifier import ClassificationError, DeepSeekSentimentClassifier
from test_sentiment import CommentsProvider, sample, setup


class Classifier:
    configured = True
    model_name = "deepseek-flash"

    def __init__(self):
        self.calls = []
        self.failure = False
        self.gate = None

    async def classify(self, items):
        self.calls.append(items)
        if self.gate:
            await self.gate.wait()
        if self.failure:
            raise ClassificationError("controlled failure")
        return {item["id"]: "positive" if "看好" in item["text"] else "negative" if "看空" in item["text"]
                else "neutral" for item in items}

    async def aclose(self):
        pass


async def finish(service):
    await asyncio.wait_for(asyncio.gather(*list(service._jobs.values())), 3)


def test_only_explicit_start_classifies_coalesces_and_reuses_after_restart(tmp_path):
    async def run():
        engine, sessions, watch = setup(tmp_path)
        provider = CommentsProvider()
        provider.items = [sample("看好", "post:1"), sample("看空", "post:2"), sample("今天回购了吗？", "post:3")]
        raw = SentimentService(provider, sessions, watch)
        model = Classifier()
        analysis = SentimentAnalysisService(raw, sessions, model)
        try:
            snapshot = await raw.get("300750")
            assert (await analysis.view("300750")).status == "idle" and not model.calls
            model.gate = asyncio.Event()
            views = await asyncio.gather(*(analysis.start("300750") for _ in range(3)))
            assert len({view.job_id for view in views}) == 1
            model.gate.set()
            await finish(analysis)
            result = (await analysis.view("300750")).result
            assert result.counts == {"positive": 1, "negative": 1, "neutral": 1}
            assert result.source_cached_at == snapshot.cached_at and len(model.calls) == 1
            assert result.sample_count == sum(result.counts.values()) == len(result.labels)
            new_model = Classifier()
            restarted = SentimentAnalysisService(raw, sessions, new_model)
            assert (await restarted.view("300750")).result == result
            await restarted.start("300750")
            await finish(restarted)
            assert not new_model.calls and (await restarted.view("300750")).result.reused_count == 3
            await restarted.aclose()
        finally:
            await analysis.aclose()
            await raw.aclose()
            engine.dispose()
    asyncio.run(run())


def test_changed_text_reclassifies_failure_keeps_previous_result_and_no_sample_is_not_neutral(tmp_path):
    async def run():
        engine, sessions, watch = setup(tmp_path)
        raw = SentimentService(CommentsProvider(), sessions, watch)
        model = Classifier()
        analysis = SentimentAnalysisService(raw, sessions, model)
        try:
            with pytest.raises(NoSentimentSamples):
                await analysis.start("300750")
            assert not model.calls
            await raw.get("300750")
            await analysis.start("300750")
            await finish(analysis)
            previous = (await analysis.view("300750")).result
            changed = summarize("300750", [sample("看空", "post:1")])
            await raw.store.get("300750", lambda: asyncio.sleep(0, result=changed), revalidate=True)
            model.failure = True
            await analysis.start("300750")
            await finish(analysis)
            view = await analysis.view("300750")
            assert view.status == "failed" and view.result == previous and view.error
            assert view.result.counts["neutral"] == 0
            model.failure = False
            await analysis.start("300750")
            await finish(analysis)
            result = (await analysis.view("300750")).result
            assert result.counts["negative"] == 1 and result.reused_count == 0
            assert result.preview[0].text == "看空" and result.preview[0].sentiment == "negative"
            assert len(model.calls) == 3
        finally:
            await analysis.aclose()
            await raw.aclose()
            engine.dispose()
    asyncio.run(run())


def test_questions_and_assertions_are_not_deduplicated_into_one_emotion():
    report = summarize("300750", [sample("看好!", "post:1"), sample("看好！", "post:2"), sample("看好？", "post:3")])
    assert report.sample_count == 2 and report.duplicate_count == 1


def test_api_get_is_read_only_and_post_starts_task_without_source_refetch(tmp_path):
    provider = CommentsProvider()
    with TestClient(create_app(provider=provider, database_url=f"sqlite:///{(tmp_path/'api.db').as_posix()}", prefetch_enabled=False)) as client:
        service = client.app.state.sentiment_analysis_service
        client.portal.call(service.classifier.aclose)
        model = Classifier()
        service.classifier = model
        path = "/api/stocks/300750/sentiment/analysis"
        assert client.get(path).json()["data"]["status"] == "idle"
        assert client.post(path).status_code == 409 and not model.calls and not provider.calls
        assert client.get("/api/stocks/300750/sentiment").status_code == 200
        assert client.post(path).status_code == 202
        client.portal.call(finish, service)
        data = client.get(path).json()["data"]
        assert data["status"] == "ready" and data["result"]["counts"]["positive"] == 1
        assert provider.calls == ["300750"] and len(model.calls) == 1
        for _ in range(3):
            assert client.get(path).status_code == 200
        assert len(model.calls) == 1
        model.configured = False
        assert client.post(path).status_code == 503


@pytest.mark.parametrize("items,finish_reason", [
    ([], "stop"), ([{"id": "wrong", "sentiment": "positive"}], "stop"),
    ([{"id": "a", "sentiment": "unknown"}], "stop"),
    ([{"id": "a", "sentiment": "positive"}] * 2, "stop"),
    ([{"id": "a", "sentiment": "positive", "reason": "extra"}], "stop"),
    ([{"id": "a", "sentiment": "positive"}], "length"),
])
def test_model_rejects_missing_duplicate_wrong_labels_or_truncated_json(items, finish_reason):
    async def run():
        calls = []
        def respond(request):
            calls.append(request)
            body = json.loads(request.content)
            assert body["model"] == "deepseek-flash" and body["thinking"]["type"] == "disabled"
            assert body["response_format"] == {"type": "json_object"}
            return httpx.Response(200, json={"choices": [{"finish_reason": finish_reason,
                "message": {"content": json.dumps({"items": items}), "reasoning_content": "PRIVATE_REASONING"}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = DeepSeekSentimentClassifier(Settings(model_api_key="test-only", model_base_url="https://test.invalid/v1"), client=client)
            with pytest.raises(ClassificationError) as caught:
                await model.classify([{"id": "a", "text": "看好"}])
            assert "PRIVATE_REASONING" not in str(caught.value) and len(calls) == 2
    asyncio.run(run())
