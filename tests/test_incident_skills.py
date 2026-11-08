import tempfile
import unittest
import json
import subprocess
import os
from pathlib import Path
from unittest.mock import patch
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


class LocalPublication:
    """Real Git index/blobs/trees; refs, commit and network are simulated."""
    remote = 'https://github.com/megamen32/noticeplace-incident-skills.git'

    def __init__(self, store, *, at_commit=None, fail_push=False):
        self.store = store
        self.run = store._run
        self.at_commit = at_commit
        self.fail_push = fail_push
        self.head = self.remote_head = 'base'
        self.pushes = []
        subprocess.run(['git', 'init', '-q', str(store.root)], check=True)
        empty = subprocess.run(['git', 'mktree'], cwd=store.root, input='', text=True,
                               check=True, capture_output=True).stdout.strip()
        self.trees = {'base': empty}

    def __call__(self, *args, **kwargs):
        if args[:2] == ('git', 'remote'): return self.remote
        if args[0] == 'gh': return 'true'
        if args[:2] == ('git', 'branch'): return 'main'
        if args[:2] == ('git', 'ls-remote'): return self.remote_head+'\trefs/heads/main'
        if args[:2] == ('git', 'rev-parse'):
            return self.head if args[2] == 'HEAD' else 'base'
        if args[:3] == ('git', 'diff', '--cached'):
            return self.run('git', 'diff', '--cached', self.trees[self.head], *args[3:], **kwargs)
        if args[:2] == ('git', 'commit'):
            if self.at_commit: self.at_commit()
            paths = args[args.index('--')+1:]
            self.run('git', 'add', '--', *paths)  # commit --only rereads these files
            self.trees['candidate'] = self.run('git', 'write-tree')
            self.head = 'candidate'
            return ''
        if args[:2] == ('git', 'diff-tree'):
            return self.run('git', 'diff', '--name-only', self.trees['base'], self.trees[args[-1]])
        if args[:2] == ('git', 'ls-tree'):
            return self.run('git', 'ls-tree', self.trees[args[2]], *args[3:], **kwargs)
        if args[:2] == ('git', 'show') and not args[2].startswith(':'):
            ref, path = args[2].split(':', 1)
            return self.run('git', 'show', self.trees[ref]+':'+path, **kwargs)
        if args[:2] == ('git', 'push'):
            self.pushes.append(args)
            if self.fail_push: raise RuntimeError('fixture push unavailable')
            self.remote_head = args[-1].split(':', 1)[0]
            return ''
        return self.run(*args, **kwargs)


class SkillTests(unittest.TestCase):
    def test_publication_checks_snapshot_and_pushes_exact_object(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillStore(Path(directory))
            saved = store.save('p', 's', 'cpu', DOCUMENT, ['fixture'])
            git = LocalPublication(store)
            with patch.object(store, '_run', side_effect=git):
                receipt = store.publish(saved)
            self.assertEqual('published', receipt['status'])
            self.assertEqual([('git', 'push', git.remote, 'candidate:refs/heads/main')], git.pushes)
            self.assertEqual(saved['sha256'], receipt['sha256'])

    def test_failed_push_can_retry_same_validated_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillStore(Path(directory))
            saved = store.save('p', 's', 'cpu', DOCUMENT, ['fixture'])
            git = LocalPublication(store, fail_push=True)
            with patch.object(store, '_run', side_effect=git):
                with self.assertRaisesRegex(RuntimeError, 'fixture push'): store.publish(saved)
                git.fail_push = False
                receipt = store.publish(saved)
            self.assertEqual('candidate', receipt['commit'])
            self.assertEqual(2, len(git.pushes))

    def test_commit_time_mutation_cannot_cross_push_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillStore(Path(directory))
            saved = store.save('p', 's', 'cpu', DOCUMENT, ['fixture'])
            git = LocalPublication(store, at_commit=lambda: Path(saved['path']).write_text(DOCUMENT+'changed\n'))
            with patch.object(store, '_run', side_effect=git):
                with self.assertRaises(ValueError): store.publish(saved)
                self.assertEqual([], git.pushes)
                # Retrying must not launder the rejected local commit.
                Path(saved['path']).write_text(DOCUMENT)
                with self.assertRaises(ValueError): store.publish(saved)
                self.assertEqual([], git.pushes)

    def test_receipt_path_cannot_publish_another_family(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillStore(Path(directory))
            saved = store.save('p', 's', 'cpu', DOCUMENT, ['fixture'])
            with patch.object(store, '_run') as run:
                with self.assertRaises(ValueError): store.publish({**saved, 'path': str(store.root/'SKILL.md')})
                run.assert_not_called()

    def test_save_detects_directory_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillStore(Path(directory))
            saved = store.save('p', 's', 'cpu', DOCUMENT, ['fixture'])
            folder = Path(saved['path']).parent
            replace = os.replace
            moved = []

            def replace_and_move(*args, **kwargs):
                replace(*args, **kwargs)
                if not moved:
                    folder.rename(folder.with_name('moved-fixture'))
                    folder.mkdir()
                    moved.append(True)

            with patch('notification_center.incident_skills.os.replace', side_effect=replace_and_move):
                with self.assertRaises(ValueError):
                    store.save('p', 's', 'cpu', DOCUMENT+'changed\n', ['fixture'])

    def test_rejects_quoted_secret_keys_in_document_and_evidence(self):
        from notification_center.incident_skills import _validate
        for text in ('{"password": "SYNTHETIC_ONLY"}', '{"nested": {"api_key": "SYNTHETIC_ONLY"}}',
                     "'ACCESS_TOKEN': 'SYNTHETIC_ONLY'", '"Authorization": "Basic SYNTHETIC_ONLY"'):
            for document, evidence in ((DOCUMENT+'\n'+text, ['fixture']), (DOCUMENT, [text])):
                with self.assertRaises(ValueError) as error:
                    _validate(document, evidence)
                self.assertNotIn('SYNTHETIC_ONLY', str(error.exception))

    def test_publish_stages_only_validated_files(self):
        # Real isolated Git index, stop before commit: no external calls or commits.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q', directory], check=True)
            store = SkillStore(root)
            saved = store.save('p', 's', 'cpu', DOCUMENT, ['fixture'])
            folder = Path(saved['path']).parent
            (folder/'extra-unvalidated.md').write_text('password=SYNTHETIC_ONLY\n')
            run = store._run

            def stage_only(*args, **kwargs):
                if args[:2] == ('git', 'remote'):
                    return 'https://github.com/megamen32/noticeplace-incident-skills.git'
                if args[0] == 'gh': return 'true'
                if args[:2] == ('git', 'branch'): return 'main'
                if args[:2] == ('git', 'rev-parse'): return 'base'
                if args[:2] == ('git', 'ls-remote'): return 'base\trefs/heads/main'
                if args[:2] == ('git', 'commit'): raise RuntimeError('fixture stop before commit')
                return run(*args, **kwargs)

            with patch.object(store, '_run', side_effect=stage_only):
                with self.assertRaisesRegex(RuntimeError, 'fixture stop'):
                    store.publish(saved)
            staged = subprocess.run(['git', 'diff', '--cached', '--name-only'], cwd=root,
                                    check=True, capture_output=True, text=True).stdout.splitlines()
            self.assertEqual(sorted(str((folder/name).relative_to(root)) for name in ('SKILL.md', 'metadata.json')),
                             sorted(staged))

    def test_publish_rejects_stale_receipt_and_symlink_before_git(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillStore(Path(directory))
            saved = store.save('p', 's', 'cpu', DOCUMENT, ['fixture'])
            path = Path(saved['path'])
            path.write_text(DOCUMENT+'changed\n')
            with patch.object(store, '_run') as run:
                with self.assertRaises(ValueError): store.publish(saved)
                run.assert_not_called()
            path.unlink()
            outside = Path(directory)/'outside.md'
            outside.write_text(DOCUMENT)
            path.symlink_to(outside)
            with patch.object(store, '_run') as run:
                with self.assertRaises(ValueError): store.publish(saved)
                run.assert_not_called()

    def test_lookup_rejects_unvalidated_metadata_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SkillStore(Path(directory))
            saved = store.save('p', 's', 'cpu', DOCUMENT, ['fixture'])
            path = Path(saved['path']).with_name('metadata.json')
            metadata = json.loads(path.read_text())
            metadata['password'] = 'SYNTHETIC_ONLY'
            path.write_text(json.dumps(metadata))
            with self.assertRaises(ValueError) as error: store.lookup('p', 's', 'cpu')
            self.assertNotIn('SYNTHETIC_ONLY', str(error.exception))

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
