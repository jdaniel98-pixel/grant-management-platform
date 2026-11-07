"""Private pilot isolation and real cross-process JSON save checks; temp data only."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import hashlib
import json
import multiprocessing
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest
import app
import grant_drafter
import matcher
import notebook_store
import opportunity_workflow as workflow
import pilot_storage as storage
import research_notebook


class Session(dict):
    def __getattr__(self, key):
        return self[key]
    def __setattr__(self, key, value):
        self[key] = value


def concurrent_writer(root, number, notebook_id):
    # Model authenticated paths without sharing a Streamlit session across processes.
    with patch.object(storage, 'workspace_root', return_value=Path(root)):
        notebook_store.add_entry(notebook_id, 'text', f'Entry {number}', f'token-{number}',
                                 path=Path(root) / 'research_notebooks.json')
        workflow.record_decision(f'Idea {number}', 'MRC Impact Acceleration Awards', 'Reject',
                                 {'overall_result': {'match_percentage': 20}})
        fields = dict(json.loads((storage.ROOT / 'data' / 'funding_needs.json').read_text())[0],
                      title=f'Concurrent idea {number}')
        research_notebook.save_research_idea(fields)
        file = BytesIO(f'Attachment {number}'.encode()); file.name = f'material-{number}.txt'
        app.save_research_attachments([file], Path(root) / 'uploads' / notebook_id)


class PilotStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.codes = {'alice': 'A' * 43, 'bob': 'B' * 43}
        self.records = {name: hashlib.sha256(code.encode()).hexdigest() for name, code in self.codes.items()}
        self.env = patch.dict(os.environ, {'PILOT_MODE': '1', 'PILOT_USERS_JSON': json.dumps(self.records),
                                          'WORKSPACE_STORAGE_ROOT': self.temp.name,
                                          'RAILWAY_ENVIRONMENT_ID': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.session = Session()
        patcher = patch.object(st, 'session_state', self.session)
        patcher.start()
        self.addCleanup(patcher.stop)

    def login(self, name):
        self.session.clear()
        self.session['_pilot_identity'] = storage.authenticate(name, self.codes[name])
        return storage.workspace_root()

    def test_access_is_not_a_workspace_identifier(self):
        self.assertIsNone(storage.authenticate('alice', self.codes['bob']))
        self.assertIsNone(storage.authenticate('unknown', self.codes['alice']))
        with self.assertRaises(PermissionError):
            storage.workspace_root()
        self.session['_pilot_identity'] = {'name': 'bob', 'credential': self.records['alice']}
        with self.assertRaises(PermissionError):
            storage.workspace_root()
        self.login('alice')
        self.records.pop('alice')
        os.environ['PILOT_USERS_JSON'] = json.dumps(self.records)
        with self.assertRaises(PermissionError):
            storage.workspace_root()

    def test_all_saved_data_isolated_and_survives_new_session(self):
        alice = self.login('alice')
        a = notebook_store.create_notebook('Alice notebook')
        notebook_store.add_entry(a['id'], 'text', 'Alice private text', 'one')
        fields = dict(matcher.load_json('funding_needs.json')[0], title='Alice new idea')
        research_notebook.save_research_idea(fields)
        analysis = {'overall_result': {'match_percentage': 73}}
        workflow.record_decision(fields['title'], 'MRC Impact Acceleration Awards', 'Pursue', analysis)
        workflow.save_application_draft(fields['title'], 'MRC Impact Acceleration Awards', 'Alice draft', ['Confirm evidence'])
        workflow.save_reminder_preference(fields['title'], 'MRC Impact Acceleration Awards', {'timing': 'In 1 week'})
        self.assertEqual(grant_drafter.load_sources(fields['title'], 'MRC Impact Acceleration Awards')['research_idea']['title'], fields['title'])
        file = BytesIO(b'Alice attachment'); file.name = 'research.txt'
        directory = app.uploads_directory() / a['id']
        app.save_research_attachments([file], directory)
        attachment = app.load_research_attachments(directory)[0]
        notebook_store.add_entry(a['id'], 'attachment', attachment, 'file')
        target = directory / attachment['stored_filename']
        bob = self.login('bob')
        self.assertNotEqual(alice, bob)
        self.assertEqual(notebook_store.load_notebooks(), [])
        self.assertEqual(workflow.load_opportunities(), [])
        self.assertNotIn(fields['title'], [i['title'] for i in matcher.load_json('funding_needs.json')])
        with self.assertRaises(StopIteration):
            notebook_store.get_notebook(a['id'])
        with self.assertRaises(StopIteration):
            notebook_store.delete_entry(a['id'], 'anything', app.uploads_directory())
        workflow.reset_test_opportunities()
        self.assertTrue(target.exists())
        self.login('alice')  # new session / process reads durable saved JSON
        self.assertEqual(len(notebook_store.get_notebook(a['id'])['entries']), 2)
        record = workflow.load_opportunities()[0]
        self.assertEqual(record['application_draft']['initial_application_draft'], 'Alice draft')
        self.assertEqual(record['reminder_preference']['timing'], 'In 1 week')
        self.assertIn(fields['title'], [i['title'] for i in matcher.load_json('funding_needs.json')])
        entry = notebook_store.get_notebook(a['id'])['entries'][-1]
        notebook_store.delete_entry(a['id'], entry['id'], app.uploads_directory())
        self.assertFalse(target.exists())
        self.assertEqual(len(notebook_store.get_notebook(a['id'])['entries']), 1)

    def test_seed_only_once_and_railway_requires_volume(self):
        self.login('alice')
        target = storage.ideas_path()
        content = target.read_bytes()
        storage.ideas_path()
        self.assertEqual(content, target.read_bytes())
        with patch.dict(os.environ, {'RAILWAY_ENVIRONMENT_ID': 'test', 'RAILWAY_VOLUME_MOUNT_PATH': ''}):
            with self.assertRaises(ValueError):
                storage.users()
        with patch.dict(os.environ, {'RAILWAY_ENVIRONMENT_ID': 'test', 'RAILWAY_VOLUME_MOUNT_PATH': self.temp.name}):
            storage.users()

    def test_parallel_process_saves_preserve_all_entries_and_decisions(self):
        root = self.login('alice')
        notebook = notebook_store.create_notebook('Concurrent notebook')
        context = multiprocessing.get_context('spawn')
        workers = [context.Process(target=concurrent_writer, args=(str(root), n, notebook['id'])) for n in range(8)]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(20)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(len(notebook_store.get_notebook(notebook['id'])['entries']), 8)
        self.assertEqual(len(workflow.load_opportunities()), 8)
        self.assertEqual(len({r['matched_research_idea'] for r in workflow.load_opportunities()}), 8)
        self.assertEqual(sum(i['title'].startswith('Concurrent idea ') for i in matcher.load_json('funding_needs.json')), 8)
        metadata = app.load_research_attachments(app.uploads_directory() / notebook['id'])
        self.assertEqual(len(metadata), 8)
        self.assertTrue(all((app.uploads_directory() / notebook['id'] / item['stored_filename']).exists() for item in metadata))


class PilotAccessUITests(unittest.TestCase):
    def test_reset_keeps_login_and_other_tester_records(self):
        with tempfile.TemporaryDirectory() as root:
            code = 'D' * 43
            records = {'tester': hashlib.sha256(code.encode()).hexdigest(),
                       'other': hashlib.sha256(('E' * 43).encode()).hexdigest()}
            paths = {}
            for name in records:
                folder = hashlib.sha256(('pi-pilot:' + name).encode()).hexdigest()
                paths[name] = Path(root) / 'workspaces' / folder / 'opportunities.json'
                storage.atomic_json(paths[name], [{'grant_title': 'MRC Impact Acceleration Awards',
                    'matched_research_idea': 'Improving Lipid Nanoparticles for mRNA Delivery'}])
            other_bytes = paths['other'].read_bytes()
            with patch.dict(os.environ, {'PILOT_MODE': '1', 'PILOT_USERS_JSON': json.dumps(records),
                                          'WORKSPACE_STORAGE_ROOT': root, 'RAILWAY_ENVIRONMENT_ID': ''}):
                script = '''import streamlit as st
import app, pilot_access
pilot_access.require_access()
if st.session_state.get('show_reset'):
 app.confirm_test_reset()
elif st.button('Open reset'):
 st.session_state.show_reset = True
 st.rerun()
'''
                at = AppTest.from_string(script).run()
                at.text_input[0].set_value('tester'); at.text_input[1].set_value(code)
                at.button[0].click().run()
                next(b for b in at.button if b.label == 'Open reset').click().run()
                next(b for b in at.button if b.label == 'Reset test data').click().run()
                self.assertFalse(at.exception)
                self.assertEqual(at.session_state['_pilot_identity']['name'], 'tester')
                self.assertEqual(json.loads(paths['tester'].read_text()), [])
                self.assertEqual(paths['other'].read_bytes(), other_bytes)

    def test_login_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            code = 'C' * 43
            credentials = json.dumps({'tester': hashlib.sha256(code.encode()).hexdigest()})
            with patch.dict(os.environ, {'PILOT_MODE': '1', 'PILOT_USERS_JSON': credentials,
                                          'WORKSPACE_STORAGE_ROOT': root, 'RAILWAY_ENVIRONMENT_ID': ''}):
                at = AppTest.from_string('import app\napp.main()').run()
                self.assertFalse(at.exception)
                self.assertEqual(at.title[0].value, 'Private pilot access')
                at.text_input[0].set_value('tester')
                at.text_input[1].set_value(code)
                at.button[0].click().run()
                self.assertFalse(at.exception)
                self.assertEqual(at.title[0].value, 'Who are you?')
            with patch.dict(os.environ, {'PILOT_MODE': '1', 'PILOT_USERS_JSON': '{}'}):
                at = AppTest.from_string('import app\napp.main()').run()
                self.assertFalse(at.exception)
                self.assertEqual(len(at.error), 1)
                self.assertEqual(len(at.button), 0)


if __name__ == '__main__':
    unittest.main()
