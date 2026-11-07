import tempfile
import unittest
from pathlib import Path
from notification_center.incident_skills import SkillStore, skill_policy

DOCUMENT = '''---
name: restore-probe
description: Проверка конкретного отказа источника перед восстановлением.
---
## Диагностика
Проверить свежую независимую пробу и подтверждённое влияние.
## Исправление
Использовать штатный перезапуск только после отказа процесса.
## Проверка
Повторить исходную пробу, записать время и результат.
## Границы
Не повторять UNKNOWN и не обходить HumanStop. Не применять к другим источникам.
'''

class SkillTests(unittest.TestCase):
    def test_family_reuses_skill_but_never_other_project_or_problem(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillStore(Path(directory))
            saved = store.save('health-monitor', 'router', 'route', DOCUMENT, ['evidence:qa'])
            found = store.lookup('health-monitor', 'router', 'route')
            self.assertEqual(saved['sha256'], found['sha256'])
            self.assertIn('## Проверка', found['document'])
            self.assertIsNone(store.lookup('other', 'router', 'route'))
            self.assertIsNone(store.lookup('health-monitor', 'router', 'cpu'))
            self.assertEqual(saved['sha256'], store.save('health-monitor','router','route',DOCUMENT,['evidence:qa'])['sha256'])

    def test_rejects_secret_and_unverified_generic_dump(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillStore(Path(directory))
            for body in [DOCUMENT+'\nAuthorization: Bearer test-secret-value', DOCUMENT+'\npassword=secret', DOCUMENT+'\nACCESS_TOKEN=synthetic-secret', 'raw log']:
                with self.assertRaises(ValueError): store.save('p','s','cpu',body,['proof'])
            with self.assertRaises(ValueError): store.save('p','s','cpu',DOCUMENT,[])

    def test_detects_changed_file_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillStore(Path(directory))
            saved = store.save('p','s','cpu',DOCUMENT,['proof'])
            Path(saved['path']).write_text(DOCUMENT+'unreviewed')
            with self.assertRaises(ValueError): store.lookup('p','s','cpu')
            with self.assertRaises(ValueError): store.save('', 's','cpu',DOCUMENT,['proof'])

    def test_learning_defaults_and_explicit_disable(self):
        self.assertEqual({'learn':True,'publish':True},skill_policy())
        self.assertEqual({'learn':False,'publish':False},skill_policy({'learn':False,'publish':True}))
        with self.assertRaises(ValueError): skill_policy({'learn':'false','publish':True})

    def test_executor_prompt_reads_before_repair_and_saves_before_finish(self):
        from notification_center.agent_job_helper import _repair_first_message
        incident = {'id':'inc_qa','project':'health-monitor','source_id':'router','signal_type':'route'}
        message = _repair_first_message({}, {'health':{}}, incident)
        self.assertIn('noticeplace-skills lookup',message)
        self.assertIn('noticeplace-skills save',message)
        self.assertIn('--publish',message)
        disabled = _repair_first_message({}, {'health':{'incident_skill_policy':{'learn':False,'publish':True}}}, incident)
        self.assertIn('noticeplace-skills lookup',disabled)
        self.assertNotIn('noticeplace-skills save',disabled)
