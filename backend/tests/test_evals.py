"""Evaluation dataset and deterministic scoring checks."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "evals"))

from run_eval import grade, load_cases, main, EVAL_CONTEXT  # noqa: E402
import pytest


def test_dataset_has_unique_cases_and_required_workflows() -> None:
    cases = load_cases().cases
    assert len(cases) == 42
    assert len({case.name for case in cases}) == 42
    assert len(load_cases("v01").cases) == 24
    assert len(load_cases("v02").cases) == 18
    assert any(len(case.metadata["tools"]) > 1 for case in cases)
    assert any(case.inputs.get("watchlist") for case in cases)
    assert any(case.name.startswith("compare_") for case in cases)


def test_scoring_uses_actual_tool_arguments_and_grounded_answer() -> None:
    expected = {
        "tools": [{"name": "get_stock_quote", "args": {"symbol": "300750"}}],
        "answer_contains": ["251.3"],
    }
    output = {
        "tools": [{"name": "get_stock_quote", "args": {"symbol": "300750"}, "status": "success"}],
        "answer": "价格 251.30 元", "error_status": None,
    }
    assert all(grade(expected, output).values())
    wrong_args = {**output, "tools": [{**output["tools"][0], "args": {"symbol": "002594"}}]}
    assert grade(expected, wrong_args) == {
        "tool_selection": True, "argument_accuracy": False, "task_success": False,
    }
    wrong_answer = {**output, "answer": "价格 999 元"}
    assert grade(expected, wrong_answer)["task_success"] is False


def test_unconfigured_eval_skips_without_scores(monkeypatch, capsys) -> None:
    monkeypatch.setenv("MODEL_NAME", "")
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setattr(sys, "argv", ["run_eval.py"])
    assert main() == 0
    output = capsys.readouterr().out
    assert output.startswith("SKIP:")
    assert '"metrics"' not in output


def evidence_case():
    identifier = 'E' + 'a' * 24
    evidence = {"evidence_id":identifier, "document_id":"ANN1", "text":"回购100万元", "title":"回购进展",
        "url":"https://example.org/ann", "source":"固定语料", "date":"2026-09-29"}
    expected = {"tools":[{"name":"search_stock_documents", "args":{"symbol":"300750"}}],
        "answer_contains":["100万"], "retrieval_documents":["ANN1"], "citations":True}
    output = {"tools":[{"name":"search_stock_documents", "args":{"symbol":"300750"}, "status":"success"}],
        "answer":"回购100万元。["+identifier+"]", "error_status":None,
        "retrieval_performed":True, "retrieved_evidence":[evidence], "saved_evidence":[dict(evidence)]}
    return expected, output


@pytest.mark.parametrize('mutation', ['unknown_id','different_text','wrong_document','no_citation'])
def test_evidence_scoring_rejects_false_correspondence(mutation):
    expected, output = evidence_case()
    if mutation == 'unknown_id': output['answer']=output['answer'].replace('a'*24,'b'*24)
    elif mutation == 'different_text': output['saved_evidence'][0]['text']='回购999万元'
    elif mutation == 'wrong_document': expected['retrieval_documents']=['OTHER']
    else: output['answer']='回购100万元'
    assert grade(expected,output)['task_success'] is False


def test_evidence_checks_pass_only_with_actual_retrieval_and_saved_snapshot():
    expected, output = evidence_case()
    scores = grade(expected, output)
    assert scores['task_success'] and scores['retrieval_hit'] and scores['citation_consistency']
    assert scores['no_evidence_degradation'] is None


def test_no_evidence_fallback_requires_a_real_search_and_no_citations():
    expected, output = evidence_case()
    expected.update(answer_contains=[],retrieval_documents=[],citations=False,no_evidence=True)
    output.update(answer='本次检索没有可支持回答的本地证据。',retrieved_evidence=[],saved_evidence=[])
    assert grade(expected,output)['no_evidence_degradation']
    output['retrieval_performed']=False
    assert not grade(expected,output)['retrieval_hit']
    assert not grade(expected,output)['task_success']


def test_grading_ignores_footer_matches_and_numeric_substrings():
    expected, output = evidence_case()
    output['answer']='金额尚未确定。\n### 证据来源\n回购100万元'
    assert not grade(expected,output)['task_success']
    output['answer']='回购1100万元。[E'+'a'*24+']'
    assert not grade(expected,output)['task_success']


def test_expected_partial_source_failure_can_succeed_without_hiding_error():
    expected={"tools":[{"name":"get_stock_money_flow","args":{"symbol":"300750"}}],
        "tool_statuses":{"get_stock_money_flow":"error"}, "answer_contains":["不可用"]}
    output={"tools":[{"name":"get_stock_money_flow","args":{"symbol":"300750"},"status":"error"}],
        "answer":"资金流不可用，保留已成功报价。", "error_status":None}
    assert grade(expected,output)['task_success']
    output['error_status']=503
    assert not grade(expected,output)['task_success']


def test_fixture_candles_have_correct_periods_and_consistent_quote_numbers():
    import asyncio
    from datetime import date
    from fixtures import EvalProvider
    async def run():
        provider = EvalProvider(snapshot_date=date(2026,9,29))
        quote = (await provider.get_quotes(['300750']))[0]
        assert quote.change_amount == pytest.approx(quote.price-quote.previous_close,abs=.0001)
        assert (quote.price/quote.previous_close-1)*100 == pytest.approx(quote.change_percent)
        daily = await provider.get_kline('300750','daily',5)
        weekly = await provider.get_kline('300750','weekly',5)
        assert len({row.close for row in daily}) == 5
        assert all(row.date.weekday()<5 for row in daily)
        assert all((a.date-b.date).days == 7 for a,b in zip(weekly,weekly[1:]))
        assert all(row.low <= min(row.open,row.close) <= max(row.open,row.close) <= row.high for row in weekly)
    asyncio.run(run())


def test_eval_context_does_not_turn_empty_watchlist_into_a_document_question():
    from app.agent.citations import wants_documents
    assert not wants_documents(EVAL_CONTEXT + '\n我现在的自选股列表有什么？')


@pytest.mark.parametrize('body,passed', [
    ('主体评级为 **AAA**，债项评级为“无”，原文未解释该字段。', True),
    ('| 主体评级 | AAA |\n| 债项评级 | 无 |', True),
    ('主体评级AAA，债项评级AAA；原文包含无字样。', False),
    ('主体评级无，债项评级AAA。', False),
    ('主体评级AAA。\n### 证据来源\n债项评级无', False),
])
def test_rating_alignment_checks_roles_in_body_not_just_keywords(body, passed):
    case = next(case for case in load_cases('v02').cases if case.name == 'v02_rating_field_alignment')
    expected = {'tools': [], 'answer_patterns': case.metadata['answer_patterns']}
    output = {'tools': [], 'answer': body, 'error_status': None}
    assert grade(expected, output)['task_success'] is passed


@pytest.mark.parametrize('body,passed', [
    ('251.30元，成交量没有基准，缩量无法由这份报价证明。', True),
    ('251.3元，成交量只有单点，无法判断是否缩量。', True),
    ('251.3元，成交量10万手，不能证明缩量。', True),
    ('251.3元。至于缩量，这份单时点报价证明不了，成交量只有单点。', True),
    ('251.3元，成交量只有单点，这份报价证明不了缩量。', True),
    ('251.3元，成交量10万手，已经明显缩量。', False),
    ('251.3元，成交量明显缩量，无法判断未来价格。', False),
])
def test_volume_scoring_accepts_negation_without_accepting_unsupported_claims(body, passed):
    case = next(case for case in load_cases('v02').cases if case.name == 'v02_volume_without_baseline')
    expected = {'tools': [], 'answer_contains': case.metadata['answer_contains'],
                'answer_patterns': case.metadata['answer_patterns']}
    assert grade(expected, {'tools': [], 'answer': body, 'error_status': None})['task_success'] is passed
