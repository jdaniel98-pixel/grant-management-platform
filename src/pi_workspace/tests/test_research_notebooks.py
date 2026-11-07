"""Notebook persistence and UI checks without OpenAI or production data writes."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import functools
import tempfile
import unittest
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import app
import notebook_store as store
import research_notebook


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "notebooks.json"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("load_notebooks", "create_notebook", "get_notebook", "update_notebook", "add_entry", "set_entry_edit", "edit_entry", "delete_entry"):
            original = getattr(store, name)
            self.stack.enter_context(patch.object(store, name, functools.partial(original, path=self.path)))
        self.stack.enter_context(patch.object(app, "UPLOADS_DIRECTORY", Path(self.temp.name) / "uploads"))

    def ui(self):
        return AppTest.from_string('''import app
import streamlit as st
from ui_styles import apply_design_system
apply_design_system()
st.session_state.setdefault("notebook", {"rough_note":"", "draft":None, "saved_title":None,"error":None})
app.my_notes()
''').run()

    def button(self, ui, label):
        return next(button for button in ui.button if button.label == label)

    def test_persistence_isolation_and_idempotency(self):
        first = store.create_notebook("First idea")
        second = store.create_notebook("Second idea")
        self.assertNotEqual(first['id'], second['id'])
        store.update_notebook(first['id'], {'title':'Renamed idea', 'pending_text':'unfinished'})
        self.assertTrue(store.add_entry(first['id'], 'text', 'Saved text', 'save-1'))
        self.assertFalse(store.add_entry(first['id'], 'text', 'Saved text', 'save-1'))
        store.add_entry(first['id'], 'transcript', 'Saved speech', 'audio-1')
        store.add_entry(first['id'], 'attachment', {'original_filename':'lab.pdf'}, 'file-1')
        reloaded = store.get_notebook(first['id'])
        self.assertEqual(reloaded['title'], 'Renamed idea')
        self.assertEqual(len(reloaded['entries']), 3)
        self.assertEqual(store.structuring_source(reloaded), 'Saved text\n\nSaved speech')
        self.assertEqual(store.get_notebook(second['id'])['entries'], [])
        self.assertEqual(reloaded['pending_text'], '')

    def test_ui_create_save_reopen_and_unsaved_text(self):
        ui = self.ui()
        self.button(ui, '+ New notebook').click().run()
        ui.text_input[0].input('Notebook A')
        self.button(ui, 'Create notebook').click().run()
        self.assertFalse(ui.exception)
        record = store.load_notebooks()[0]
        ui.text_area[0].input('First saved thought').run()
        self.button(ui, 'Save thought').click().run()
        self.assertFalse(ui.exception)
        self.assertEqual(len(store.get_notebook(record['id'])['entries']), 1)
        ui.run()
        self.assertEqual(len(store.get_notebook(record['id'])['entries']), 1)
        ui.text_area[0].input('Unfinished second thought').run()
        self.button(ui, '← All notebooks').click().run()
        self.button(ui, 'Open notebook →').click().run()
        self.assertEqual(ui.text_area[0].value, 'Unfinished second thought')
        # New Streamlit session simulates reopening after an app restart.
        fresh = self.ui()
        self.button(fresh, 'Open notebook →').click().run()
        self.assertEqual(fresh.text_area[0].value, 'Unfinished second thought')
        self.assertFalse(fresh.exception)

    def test_structuring_uses_saved_entries_and_keeps_review(self):
        record = store.create_notebook('Idea')
        store.add_entry(record['id'], 'text', 'Saved source', 'text-1')
        store.update_notebook(record['id'], {'pending_text':'Exclude unfinished text'})
        idea = research_notebook.ResearchIdea(**{field:'Reviewed ' + field for field in app.IDEA_FIELDS})
        with patch.object(research_notebook, 'structure_idea', return_value=idea) as structure:
            ui = self.ui()
            self.button(ui, 'Open notebook →').click().run()
            self.button(ui, 'Structure research idea').click().run()
            self.assertFalse(ui.exception)
            self.assertEqual(structure.call_args.args[0], 'Saved source')
            self.assertEqual(store.get_notebook(record['id'])['review_draft'], idea.model_dump())
            fresh = self.ui()
            self.button(fresh, 'Open notebook →').click().run()
            self.assertTrue(any(h.value == 'Review and edit' for h in fresh.subheader))
            self.assertEqual(structure.call_count, 1)

    def test_attachment_storage_and_notebook_association(self):
        record = store.create_notebook('Files')
        file = BytesIO(b'local research material')
        file.name = '../../research.txt'
        directory = app.UPLOADS_DIRECTORY / record['id']
        self.assertEqual(app.save_research_attachments([file], directory), 1)
        attachment = app.load_research_attachments(directory)[0]
        store.add_entry(record['id'], 'attachment', attachment, 'attachment:' + attachment['sha256'])
        self.assertEqual(app.save_research_attachments([file], directory), 0)
        self.assertFalse(store.add_entry(record['id'], 'attachment', attachment, 'attachment:' + attachment['sha256']))
        self.assertEqual((directory / attachment['stored_filename']).read_bytes(), b'local research material')
        self.assertEqual(attachment['original_filename'], 'research.txt')
        self.assertEqual(store.structuring_source(store.get_notebook(record['id'])), '')

    def test_transcript_correction_save_reset_and_failure_retention(self):
        record = store.create_notebook('Speech')
        store.update_notebook(record['id'], {'pending_transcript': {'text':'Original technical term', 'digest':'audio-hash'}})
        ui = self.ui()
        self.button(ui, 'Open notebook →').click().run()
        old_audio_id = ui.get('audio_input')[0].proto.id
        transcript = next(area for area in ui.text_area if area.label == 'Review and correct transcript')
        transcript.input('Corrected technical term').run()
        self.assertEqual(store.get_notebook(record['id'])['pending_transcript']['text'], 'Corrected technical term')
        with patch.object(store, 'add_entry', side_effect=OSError('Simulated disk failure')):
            self.button(ui, 'Save transcript').click().run()
            self.assertFalse(ui.exception)
            self.assertEqual(ui.get('audio_input')[0].proto.id, old_audio_id)
            self.assertEqual(store.get_notebook(record['id'])['pending_transcript']['text'], 'Corrected technical term')
        self.button(ui, 'Save transcript').click().run()
        self.assertFalse(ui.exception)
        saved = store.get_notebook(record['id'])
        self.assertEqual(saved['entries'][0]['content'], 'Corrected technical term')
        self.assertIsNone(saved['pending_transcript'])
        self.assertNotEqual(ui.get('audio_input')[0].proto.id, old_audio_id)
        self.assertEqual(ui.get('audio_input')[0].value, None)
        ui.run()
        self.assertEqual(len(store.get_notebook(record['id'])['entries']), 1)
        self.assertFalse(ui.error)

    def test_single_upload_card_across_selection_save_navigation_and_reruns(self):
        record = store.create_notebook('Upload reruns')
        selected = []
        def uploader(*args, **kwargs):
            # Simulate browser file selection without an external upload/API.
            app.st.session_state[kwargs['key']] = list(selected)
            return list(selected)
        with patch.object(app.st, 'file_uploader', side_effect=uploader):
            ui = self.ui()
            def check_home():
                self.assertFalse(ui.exception)
                self.assertEqual(sum(h.value == '📎 Upload research material' for h in ui.subheader), 0)
                self.assertFalse(any(button.label == 'Save attachments' for button in ui.button))
            check_home()
            self.button(ui, 'Open notebook →').click().run()
            def check_card():
                self.assertFalse(ui.exception)
                self.assertEqual(sum(h.value == '📎 Upload research material' for h in ui.subheader), 1)
                headings = [h.value for h in ui.subheader]
                self.assertLess(headings.index('🎙️ Record a research thought'), headings.index('✏️ Write a research thought'))
                self.assertLess(headings.index('✏️ Write a research thought'), headings.index('📎 Upload research material'))
            check_card()
            file = BytesIO(b'Research attachment')
            file.name = 'research.txt'
            selected.append(file)
            ui.run()
            check_card()
            self.button(ui, 'Save attachments').click().run()
            check_card()
            self.assertEqual(len(store.get_notebook(record['id'])['entries']), 1)
            self.button(ui, 'Save attachments').click().run()
            for _ in range(3):
                ui.run()
                check_card()
            self.assertEqual(len(store.get_notebook(record['id'])['entries']), 1)
            self.button(ui, '← All notebooks').click().run()
            check_home()
            for _ in range(3):
                ui.run()
                check_home()
            self.button(ui, 'Open notebook →').click().run()
            check_card()
            self.assertEqual(len(list((app.UPLOADS_DIRECTORY / record['id']).glob('*.txt'))), 1)

    def test_recording_transcribes_once_and_resets_only_on_save(self):
        record = store.create_notebook('Audio')
        original_audio_input = app.st.audio_input
        def audio_widget(*args, **kwargs):
            original_audio_input(*args, **kwargs)
            return BytesIO(b'recorded speech') if kwargs['key'].endswith('_0') else None
        with patch.object(app.st, 'audio_input', side_effect=audio_widget), patch.object(research_notebook, 'transcribe_recording', return_value='Transcript') as transcribe:
            ui = self.ui()
            self.button(ui, 'Open notebook →').click().run()
            self.button(ui, 'Transcribe recording').click().run()
            self.assertFalse(ui.exception)
            self.assertEqual(transcribe.call_count, 1)

            ui.run()
            self.assertEqual(transcribe.call_count, 1)
            next(area for area in ui.text_area if area.label == 'Review and correct transcript').input('Corrected transcript').run()
            self.button(ui, 'Save transcript').click().run()
            self.assertFalse(ui.exception)
            self.assertTrue(self.button(ui, 'Transcribe recording').disabled)
            self.assertEqual(ui.session_state[f"recording_generation_{record['id']}"], 1)
            self.assertEqual(len(store.get_notebook(record['id'])['entries']), 1)
            self.assertEqual(transcribe.call_count, 1)

    def test_titles_and_confirmed_deletion_ui(self):
        record = store.create_notebook('Titles')
        store.add_entry(record['id'], 'text', 'Original scientific thought', 'text-token')
        original = store.get_notebook(record['id'])['entries'][0].copy()
        ui = self.ui()
        self.assertFalse(any(h.value == 'Build your research idea over time' for h in ui.subheader))
        self.button(ui, 'Open notebook →').click().run()
        self.assertEqual(sum(h.value == 'Build your research idea over time' for h in ui.subheader), 1)
        self.button(ui, 'Edit note').click().run()
        self.assertEqual(next(field for field in ui.text_input if field.label == 'Entry title').value, original['title'])
        self.assertEqual(next(field for field in ui.text_area if field.label == 'Edit note').value, original['content'])
        next(field for field in ui.text_input if field.label == 'Entry title').input('New title').run()
        next(field for field in ui.text_area if field.label == 'Edit note').input('Corrected thought').run()
        ui.run()
        self.button(ui, 'Save changes').click().run()
        self.assertEqual(sum(h.value == 'Build your research idea over time' for h in ui.subheader), 1)
        saved = store.get_notebook(record['id'])['entries'][0]
        self.assertEqual(saved['title'], 'New title')
        self.assertEqual(saved['content'], 'Corrected thought')
        self.assertEqual(saved['id'], original['id'])
        self.assertEqual(saved['created_at'], original['created_at'])
        self.button(ui, 'Delete').click().run()
        self.assertEqual(len(store.get_notebook(record['id'])['entries']), 1)
        self.button(ui, 'Cancel').click().run()
        self.assertEqual(len(store.get_notebook(record['id'])['entries']), 1)
        self.button(ui, 'Delete').click().run()
        self.button(ui, 'Delete entry').click().run()
        ui.run()
        self.assertFalse(ui.exception)
        self.assertEqual(store.get_notebook(record['id'])['entries'], [])

    def test_attachment_title_and_reference_safe_deletion(self):
        first = store.create_notebook('Files')
        other = store.create_notebook('Other')
        directory = app.UPLOADS_DIRECTORY / first['id']
        directory.mkdir(parents=True)
        (directory / 'file.txt').write_text('Preserve file contents')
        attachment = {'original_filename': 'Research material.txt', 'stored_filename': 'file.txt'}
        store.add_entry(first['id'], 'attachment', attachment, 'a')
        store.add_entry(first['id'], 'attachment', attachment, 'b')
        store.add_entry(other['id'], 'text', 'Other notebook', 'c')
        entries = store.get_notebook(first['id'])['entries']
        store.edit_entry(first['id'], entries[0]['id'], None, title='Display title')
        self.assertEqual(store.get_notebook(first['id'])['entries'][0]['content'], attachment)
        store.delete_entry(first['id'], entries[0]['id'], app.UPLOADS_DIRECTORY)
        self.assertTrue((directory / 'file.txt').exists())
        store.delete_entry(first['id'], entries[1]['id'], app.UPLOADS_DIRECTORY)
        self.assertFalse((directory / 'file.txt').exists())
        self.assertEqual(len(store.get_notebook(other['id'])['entries']), 1)
        self.assertEqual(store.entry_title({'kind':'attachment', 'content':attachment}), 'Research material')

    def test_attachment_filename_display_without_editor(self):
        record = store.create_notebook('Attachment display')
        directory = app.UPLOADS_DIRECTORY / record['id']
        directory.mkdir(parents=True)
        file = directory / 'stored.txt'
        file.write_text('Existing attachment')
        attachment = {'original_filename':'Research_material.txt', 'stored_filename':'stored.txt',
                      'content_type':'text/plain'}
        store.add_entry(record['id'], 'attachment', attachment, 'file-token')
        entry = store.get_notebook(record['id'])['entries'][0]
        # Legacy title and unfinished title edits must not replace the filename.
        store.edit_entry(record['id'], entry['id'], None, title='Old display title')
        store.set_entry_edit(record['id'], entry['id'], title='Unfinished title')
        ui = self.ui()
        self.button(ui, 'Open notebook →').click().run()
        self.assertEqual(sum(h.value == 'Research_material.txt' for h in ui.subheader), 1)
        self.assertFalse(any(b.label == 'Edit title' for b in ui.button))
        self.assertFalse(any(f.label == 'Entry title' for f in ui.text_input))
        self.assertEqual(len(ui.get('download_button')), 1)
        self.button(ui, 'Delete').click().run()
        self.assertTrue(file.exists())
        self.button(ui, 'Cancel').click().run()
        self.assertTrue(file.exists())
        ui.run()
        self.assertFalse(ui.exception)
        self.button(ui, 'Delete').click().run()
        self.button(ui, 'Delete entry').click().run()
        self.assertFalse(file.exists())
        self.assertEqual(store.get_notebook(record['id'])['entries'], [])

    def test_edit_entry_preserves_metadata_and_cancel(self):
        record = store.create_notebook('Edit')
        store.add_entry(record['id'], 'transcript', 'Original text', 'voice-token')
        before = store.get_notebook(record['id'])['entries'][0].copy()
        ui = self.ui()
        self.button(ui, 'Open notebook →').click().run()
        self.button(ui, 'Edit note').click().run()
        next(area for area in ui.text_area if area.label == 'Edit note').input('Corrected saved note').run()
        # Unfinished entry edits survive reopening in a new app session.
        ui = self.ui()
        self.button(ui, 'Open notebook →').click().run()
        self.assertEqual(next(area for area in ui.text_area if area.label == 'Edit note').value, 'Corrected saved note')
        self.button(ui, 'Save changes').click().run()
        self.assertFalse(ui.exception)
        saved = store.get_notebook(record['id'])['entries']
        self.assertEqual(len(saved), 1)
        for key in ('id', 'created_at', 'kind', 'save_token'):
            self.assertEqual(saved[0][key], before[key])
        self.assertEqual(saved[0]['content'], 'Corrected saved note')
        self.assertIn('modified_at', saved[0])
        self.button(ui, 'Edit note').click().run()
        next(area for area in ui.text_area if area.label == 'Edit note').input('Discard this edit').run()
        self.button(ui, 'Cancel').click().run()
        self.assertEqual(store.get_notebook(record['id'])['entries'][0]['content'], 'Corrected saved note')
        self.assertFalse(ui.exception)


if __name__ == '__main__':
    unittest.main()
