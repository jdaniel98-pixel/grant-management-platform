"""Reminder transitions using local fixtures, never OpenAI or user data."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from streamlit.testing.v1 import AppTest
import app
import opportunity_workflow as workflow

SCRIPT = '''import streamlit as st
import app, matcher, opportunity_workflow
from ui_styles import apply_design_system
apply_design_system()
st.session_state.setdefault('researcher_workflows', {})
st.session_state.setdefault('application_workspace', None)
st.session_state.setdefault('researcher_area', 'Find Funding')
grant = next(g for g in matcher.load_json('grants.json') if g['title'] == 'Wellcome Discovery Awards')
idea = {'title':'Reminder test idea'}
state = st.session_state.researcher_workflows.setdefault(idea['title'], {'grant':grant, 'decision':None, 'analyses':{grant['title']:{'overall_result':{'match_percentage':77}, 'suggested_next_action':'Investigate further'}}})
if st.session_state.application_workspace:
    app.application_workspace()
elif st.session_state.researcher_area == 'Saved Applications / Opportunities':
    app.saved_opportunities()
else:
    app.show_funding_result({}, idea, [grant], state, st.empty())
'''

class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'opportunities.json'
        for patcher in (patch.object(workflow, 'OPPORTUNITIES_PATH', self.path),
                        patch.object(app, 'show_opportunity_header', lambda *args: None),
                        patch.object(app, 'show_match', lambda *args: None)):
            patcher.start(); self.addCleanup(patcher.stop)
    def button(self, ui, label):
        return next(button for button in ui.button if button.label == label)
    def pursue(self):
        ui = AppTest.from_string(SCRIPT).run()
        self.button(ui, 'Pursue').click().run()
        self.assertFalse(ui.exception)
        self.assertTrue(any(b.label == 'Not now' for b in ui.button))
        return ui
    def test_first_pursue_dismiss_manage_save_return(self):
        ui = self.pursue()
        self.button(ui, 'Not now').click().run()
        saved = workflow.load_opportunities()[0]
        self.assertTrue(saved['reminder_intro_dismissed'])
        self.assertNotIn('reminder_preference', saved)
        ui.run()
        self.assertFalse(any(b.label == 'Not now' for b in ui.button))
        self.button(ui, 'Manage reminder').click().run()
        next(box for box in ui.selectbox if box.label == 'When would you like to return?').select('In 2 weeks').run()
        self.button(ui, 'Save preference').click().run()
        saved = workflow.load_opportunities()[0]
        self.assertEqual(saved['reminder_preference']['timing'], 'In 2 weeks')
        self.assertEqual(saved['current_status'], 'Pursuing')
        self.button(ui, 'Manage reminder').click().run()
        self.assertEqual(next(box for box in ui.selectbox if box.label == 'When would you like to return?').value, 'In 2 weeks')
        self.button(ui, 'Not now').click().run()
        reopened = AppTest.from_string(SCRIPT + '\n').run()
        self.button(reopened, 'Pursue').click().run()
        self.assertFalse(any(b.label == 'Not now' for b in reopened.button))
        self.assertFalse(reopened.exception)
    def test_native_close_persists_dismissal(self):
        ui = self.pursue()
        # Native close uses this same registered on_dismiss callback.
        with patch.object(app.st, 'session_state', {'reminder_dialog_identity':('Reminder test idea','Wellcome Discovery Awards')}):
            app.dismiss_reminder_dialog()
        self.assertTrue(workflow.load_opportunities()[0]['reminder_intro_dismissed'])

    def test_workspace_header_draft_and_deadline_layout(self):
        ui = self.pursue()
        self.button(ui, 'Not now').click().run()
        self.assertEqual(sum(b.label == 'Generate Draft' for b in ui.button), 1)
        self.assertFalse(any(b.label == 'Start draft' for b in ui.button))
        self.assertFalse(any('Recommendation:' in item.value for item in ui.markdown))
        self.assertFalse(any(e.label == 'Full deadline details' for e in ui.expander))
        reminder = next(e for e in ui.expander if e.label == '🔔 Reminder settings')
        self.assertFalse(reminder.proto.expanded)
        self.button(ui, 'Requirements').click().run()
        self.assertTrue(any(item.value == '**Deadline**' for item in ui.markdown))
        self.assertTrue(any('applications can currently be submitted at any time' in item.value for item in ui.markdown))
        draft = SimpleNamespace(initial_application_draft='Local draft fixture', missing_information_pi_input_required=[])
        with patch.object(app.grant_drafter, 'generate_draft', return_value=draft) as generate:
            self.button(ui, 'Generate Draft').click().run()
        generate.assert_called_once_with('Reminder test idea', 'Wellcome Discovery Awards')
        self.assertFalse(ui.exception)
        self.assertTrue(any(item.value == 'Local draft fixture' for item in ui.markdown))
        self.assertFalse(any(b.label == 'Generate Draft' for b in ui.button))
        self.assertTrue(any(b.label == 'Resume draft' for b in ui.button))

    def test_draft_edit_save_cancel_and_reopen(self):
        ui = self.pursue()
        self.button(ui, 'Not now').click().run()
        draft = SimpleNamespace(initial_application_draft='Original AI narrative',
                                missing_information_pi_input_required=['Confirm project budget'])
        with patch.object(app.grant_drafter, 'generate_draft', return_value=draft), \
                patch.object(app.st, 'status', wraps=app.st.status) as status:
            self.button(ui, 'Generate Draft').click().run()
        self.assertTrue(any(call.args[0] == 'Just a moment, I’m working on your draft' for call in status.call_args_list))
        saved = workflow.load_opportunities()[0]
        self.assertEqual(saved['suggested_next_step'], 'Resume draft')
        self.assertEqual(saved['application_draft']['original_ai_draft'], 'Original AI narrative')
        self.button(ui, 'Edit draft').click().run()
        next(area for area in ui.text_area if area.label == 'Initial draft').input('Unsaved change').run()
        ui.run()
        self.assertEqual(workflow.load_opportunities()[0]['application_draft']['initial_application_draft'], 'Original AI narrative')
        self.button(ui, 'Requirements').click().run()
        self.button(ui, 'Draft').click().run()
        self.assertEqual(next(area for area in ui.text_area if area.label == 'Initial draft').value, 'Unsaved change')
        self.button(ui, 'Cancel editing').click().run()
        self.button(ui, 'Edit draft').click().run()
        self.assertEqual(next(area for area in ui.text_area if area.label == 'Initial draft').value, 'Original AI narrative')
        next(field for field in ui.text_input if field.label == 'Project title').input('Reviewed project title').run()
        next(area for area in ui.text_area if area.label == 'Initial draft').input('Reviewed researcher narrative').run()
        self.button(ui, 'Save changes').click().run()
        saved = workflow.load_opportunities()[0]
        self.assertEqual(saved['current_status'], 'Pursuing')
        self.assertEqual(saved['application_draft']['original_ai_draft'], 'Original AI narrative')
        self.assertEqual(saved['application_draft']['initial_application_draft'], 'Reviewed researcher narrative')
        self.assertEqual(saved['application_draft']['project_title'], 'Reviewed project title')
        self.assertEqual(saved['application_draft']['missing_information_pi_input_required'], ['Confirm project budget'])
        ui = AppTest.from_string(SCRIPT).run()
        self.button(ui, 'Pursue').click().run()
        self.button(ui, 'Resume draft').click().run()
        self.assertTrue(any(item.value == 'Reviewed researcher narrative' for item in ui.markdown))
        self.button(ui, 'Edit draft').click().run()
        self.assertEqual(next(field for field in ui.text_input if field.label == 'Project title').value, 'Reviewed project title')
        self.button(ui, 'Cancel editing').click().run()
        next(button for button in ui.button if button.label.startswith('Missing Information')).click().run()
        self.assertTrue(any('Confirm project budget' in item.value for item in ui.markdown))
        self.assertFalse(ui.exception)
        with self.assertRaises(ValueError):
            workflow.save_application_draft('Reminder test idea', 'Wellcome Discovery Awards', 'Overwrite', [])
        self.assertEqual(workflow.load_opportunities()[0]['application_draft']['initial_application_draft'], 'Reviewed researcher narrative')

    def test_generation_failure_leaves_application_unchanged(self):
        ui = self.pursue()
        self.button(ui, 'Not now').click().run()
        before = self.path.read_bytes()
        with patch.object(app.grant_drafter, 'generate_draft', side_effect=ValueError('Incomplete response')):
            self.button(ui, 'Generate Draft').click().run()
        self.assertFalse(ui.exception)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(any('Unable to generate' in error.value for error in ui.error))
    def test_timing_only_future_choices(self):
        now = datetime(2026,10,8,12,tzinfo=ZoneInfo('Europe/London'))
        choices = app.reminder_choices('15 October 2026', now)
        self.assertTrue(all((value.get('return_date') or value.get('return_at'))[:10] > '2026-10-08' for value in choices.values()))

    def test_save_review_listing_skip_refresh_and_reopen(self):
        ui = AppTest.from_string(SCRIPT).run()
        self.button(ui, 'Save for review').click().run()
        self.assertFalse(ui.exception)
        self.assertIsNone(ui.session_state['application_workspace'])
        self.assertEqual(workflow.load_opportunities()[0]['current_status'], 'Considering')
        self.assertTrue(any(b.label == 'Skip for now' for b in ui.button))
        self.button(ui, 'Skip for now').click().run()
        ui.run()
        self.assertFalse(any(b.label == 'Skip for now' for b in ui.button))
        self.assertEqual(len(workflow.load_opportunities()), 1)
        ui = AppTest.from_string(SCRIPT + '\nst.write("Fresh session")').run()
        self.button(ui, 'Save for review').click().run()
        next(box for box in ui.selectbox if box.label == 'Review timing').select('In 1 week').run()
        self.button(ui, 'Save preference').click().run()
        self.assertIsNone(ui.session_state['application_workspace'])
        self.assertEqual(workflow.load_opportunities()[0]['reminder_preference']['timing'], 'In 1 week')
        self.assertEqual(len(workflow.load_opportunities()), 1)
        ui.run()
        self.assertFalse(any(b.label == 'Skip for now' for b in ui.button))
        self.button(ui, 'Open application workspace').click().run()
        self.assertFalse(ui.exception)
        self.assertEqual(ui.session_state['application_workspace'], ('Reminder test idea', 'Wellcome Discovery Awards'))
        self.assertFalse(any(b.label == 'Skip for now' for b in ui.button))

    def test_review_native_close_clears_one_time_event(self):
        state = {'review_confirmation_identity':('Idea', 'Grant')}
        with patch.object(app.st, 'session_state', state):
            app.dismiss_review_confirmation()
        self.assertNotIn('review_confirmation_identity', state)
