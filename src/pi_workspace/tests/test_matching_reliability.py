"""Local fault-injection checks; never calls OpenAI or writes opportunity data."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import unittest
from unittest.mock import patch
from collections import Counter
import httpx
from openai import APITimeoutError
from streamlit.testing.v1 import AppTest
import matcher
import prototype


def response(grant):
    criterion = matcher.CriterionAssessment(score=4, reasoning='Local test evidence.')
    return matcher.calculate_result(matcher.ModelAnalysis(
        summary='Local test summary.', deadline=grant['deadline'],
        assessments=matcher.FitAssessment(**{name: criterion for name in matcher.FitAssessment.model_fields}),
        hard_constraint=matcher.HardConstraint(category='none', reasoning='Local test.'),
    ))


class ReliabilityChecks(unittest.TestCase):
    def setUp(self):
        self.grants = matcher.load_json('grants.json')
        self.idea = matcher.load_json('funding_needs.json')[0]

    def test_repeated_partial_failure_and_cache_retry(self):
        for workers in (1, 4):
            for attempt in range(12):
                failed = self.grants[attempt % len(self.grants)]['title']
                cache, failures, calls = {}, [], Counter()
                def evaluate(profile, idea, grant):
                    calls[grant['title']] += 1
                    if grant['title'] == failed:
                        raise APITimeoutError(request=httpx.Request('POST', 'https://api.openai.com/v1/responses'))
                    return response(grant)
                with patch.object(prototype, 'available_grants', return_value=self.grants), patch.object(matcher, 'match', evaluate):
                    best = prototype.best_alternative({}, self.idea, self.grants, '', cache, max_workers=workers, on_failure=failures.append)
                self.assertNotEqual(best['title'], failed)
                self.assertEqual(len(cache), 3)
                self.assertEqual(failures[0]['opportunity'], failed)
                self.assertEqual(failures[0]['source'], 'OpenAI API')
                self.assertTrue(all(count == 1 for count in calls.values()))
                with patch.object(prototype, 'available_grants', return_value=self.grants), patch.object(matcher, 'match', side_effect=lambda p,i,g:response(g)) as retry:
                    prototype.best_alternative({}, self.idea, self.grants, '', cache, max_workers=workers)
                self.assertEqual(retry.call_count, 1)

    def test_all_failures_have_diagnostics(self):
        failures = []
        with patch.object(prototype, 'available_grants', return_value=self.grants), patch.object(matcher, 'match', side_effect=matcher.IncompleteMatchError('Incomplete response status: incomplete')):
            with self.assertRaises(prototype.SearchEvaluationError):
                prototype.best_alternative({}, self.idea, self.grants, '', {}, max_workers=4, on_failure=failures.append)
        self.assertEqual(len(failures), 4)
        self.assertTrue(all(f['source']=='Structured-output parsing' for f in failures))

    def test_cached_result_survives_new_failure(self):
        cache = {self.grants[0]['title']:response(self.grants[0]).model_dump(mode='json')}
        with patch.object(prototype, 'available_grants', return_value=self.grants), patch.object(matcher, 'match', side_effect=ValueError('Local application error')):
            best = prototype.best_alternative({}, self.idea, self.grants, '', cache, max_workers=4)
        self.assertEqual(best['title'], self.grants[0]['title'])

    def test_diagnostics_redact_secret_and_validation_inputs(self):
        with patch.dict('os.environ', {'OPENAI_API_KEY':'sk-test-secret'}):
            diagnostic=prototype.failure_diagnostic(ValueError('token sk-test-secret Bearer secret-token'))
        self.assertNotIn('sk-test-secret', diagnostic['message'])
        self.assertNotIn('secret-token', diagnostic['message'])
        try:
            matcher.CriterionAssessment(score=9, reasoning='Do not log this input')
        except Exception as error:
            diagnostic=prototype.failure_diagnostic(error)
        self.assertEqual(diagnostic['source'],'Structured-output parsing')
        self.assertNotIn('input_value', diagnostic['message'])

    def test_real_find_funding_flow_and_loading_failure(self):
        import app
        for all_fail in (False, True):
            for repeat in range(3):
                app_test=AppTest.from_string('import app; app.main()').run()
                app_test.button(key='entry_researcher').click().run()
                app_test.button(key='researcher_nav_Find Funding').click().run()
                def evaluate(profile, idea, grant):
                    if all_fail or grant['title']==self.grants[0]['title']:
                        raise matcher.IncompleteMatchError('Local incomplete response')
                    return response(grant)
                with patch.object(prototype,'available_grants',return_value=self.grants), patch.object(matcher,'match',side_effect=evaluate) as api, patch.object(app,'funding_insight_markup',side_effect=ValueError('Local insight-render failure')):
                    next(b for b in app_test.button if b.label=='Find funding opportunities').click().run()
                self.assertFalse(app_test.exception)
                self.assertEqual(api.call_count, 4)
                workflow=app_test.session_state['researcher_workflows'][self.idea['title']]
                self.assertTrue(workflow['search_diagnostics'])
                if all_fail:
                    self.assertTrue(workflow['search_error'])
                    self.assertTrue(any('Unable to finish reviewing' in e.value for e in app_test.error))
                else:
                    self.assertFalse(workflow['search_error'])
                    self.assertIsNotNone(workflow['grant'])
                    self.assertEqual(len(workflow['analyses']),3)


if __name__=='__main__':
    unittest.main()
