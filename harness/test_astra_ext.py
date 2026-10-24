import json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import astra_ext as ext

GOOD = """# Brief: API retry backoff
## Goal
Add exponential backoff to the API client.
## Done when
- `bash tools/build_all.sh` rc 0; 0 lint errors
## Owns
- `src/api/*`, `docs/api_spec/*`
## Out of scope
The UI.
## Do not change
The public API surface.
## Report
changed / checks with numbers / flagged, not done
"""


class Brief(unittest.TestCase):
    def test_good_brief(self):
        self.assertEqual(ext.brief_problems(GOOD), [])
        self.assertEqual(ext.owns(GOOD), ['src/api/*', 'docs/api_spec/*'])

    def test_bold_and_plain_forms(self):
        t = "**Goal:** fix it\nDone when: tests pass\n**Owns:** tools/a.py, tools/b.py\nOut of scope: x\nDo not change: y\nReport: z\n"
        self.assertEqual(ext.brief_problems(t), [])
        self.assertEqual(ext.owns(t), ['tools/a.py', 'tools/b.py'])

    def test_vague_go_ahead(self):
        p = ext.brief_problems("then continue")
        self.assertEqual(len(p), 4)

    def test_synonym_headings_of_real_worker_briefs(self):
        t = ("# Fix pass: v2.2 -> v2.3\n\n## Your clone and ownership (do not step outside it)\n"
             "Clone: `/home/me/proj/api`. You own `src/api/` and `tools/api_*` (except `SCHEMA.json`).\n\n"
             "Hand-offs: note changes to `REPORT.md` in FIX_NOTES.\n\nDone means: tests green, 0 lint errors.\n\n"
             "## Owner decisions: do NOT change\nThe public API surface.\n")
        self.assertEqual(ext.brief_problems(t), [])
        self.assertEqual(ext.owns(t), ['src/api/*', 'tools/api_*'])
        self.assertFalse(ext.owns_explicit(t))
        self.assertTrue(ext.owns_explicit(GOOD))

    def test_sentence_is_not_heading(self):
        self.assertNotIn('goal', ext.sections("The goal is simple.\n"))

    def test_overlap(self):
        self.assertTrue(ext.overlap('src/api/*', 'src/api/client.py'))
        self.assertTrue(ext.overlap('reference_design/bom/*', 'reference_design/*'))
        self.assertFalse(ext.overlap('src/api/*', 'src/ui/*'))


class Gate(unittest.TestCase):
    def test_owner_warns_claude_refuses(self):
        said = []
        os.environ.pop('CLAUDECODE', None)
        ext.gate(['x'], False, said.append)
        self.assertEqual(said, ['WARNING: x'])
        os.environ['CLAUDECODE'] = '1'
        try:
            with self.assertRaises(SystemExit):
                ext.gate(['x'], False, said.append)
            ext.gate(['x'], True, said.append)  # --force
        finally:
            os.environ.pop('CLAUDECODE')


class Repo(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.r = Path(self.t.name) / 'repo'
        self.r.mkdir()
        g = lambda *a: subprocess.run(['git', '-C', str(self.r), *a], check=True, capture_output=True)
        g('init', '-q')
        g('config', 'user.email', 't@t')
        g('config', 'user.name', 't')
        (self.r / 'a.txt').write_text('1')
        g('add', '.')
        g('commit', '-qm', 'init')
        self.runs = Path(self.t.name) / 'runs'
        self.runs.mkdir()

    def tearDown(self):
        self.t.cleanup()

    def test_milestone_watcher_commits_tracked_and_owned_only(self):
        said = []
        w = ext.MilestoneWatcher(self.r, said.append, owns=['src/api/*'])
        (self.r / 'a.txt').write_text('2')                      # tracked, modified: committed
        (self.r / 'src' / 'api').mkdir(parents=True)
        (self.r / 'src' / 'api' / 'client.py').write_text('b')  # new, inside Owns: committed
        (self.r / '.playwright-mcp').mkdir()
        (self.r / '.playwright-mcp' / 'x.log').write_text('scratch')  # new, outside Owns: left alone
        (self.r / 'MILESTONE').write_text('backoff done')
        w.commit()
        g = lambda *a: subprocess.run(['git', '-C', str(self.r), *a], capture_output=True, text=True).stdout.strip()
        self.assertEqual(g('log', '-1', '--format=%s'), 'milestone: backoff done')
        self.assertEqual(sorted(g('show', '--name-only', '--format=', 'HEAD').split()), ['a.txt', 'src/api/client.py'])
        self.assertFalse((self.r / 'MILESTONE').exists())
        self.assertTrue(any('outside Owns not committed: .playwright-mcp/x.log' in s for s in said))
        self.assertTrue(any('milestone committed' in s for s in said))

    def test_milestone_ignores_foreign_staged_files_and_retries_on_failure(self):
        said = []
        g = lambda *a: subprocess.run(['git', '-C', str(self.r), *a], capture_output=True, text=True)
        (self.r / 'other.txt').write_text('another session')
        g('add', 'other.txt')                                    # staged by someone else: must not be committed
        (self.r / 'a.txt').write_text('3')
        hook = self.r / '.git' / 'hooks' / 'pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        (self.r / 'MILESTONE').write_text('first try')
        w = ext.MilestoneWatcher(self.r, said.append, owns=['src/*'])
        w.commit()
        self.assertTrue((self.r / 'MILESTONE').exists())        # failed commit keeps the request
        self.assertTrue(any('MILESTONE kept' in s for s in said))
        hook.unlink()
        w.commit()
        self.assertFalse((self.r / 'MILESTONE').exists())
        self.assertEqual(g('show', '--name-only', '--format=', 'HEAD').stdout.split(), ['a.txt'])
        self.assertIn('A  other.txt', g('status', '--porcelain').stdout)  # still staged, not committed

    def test_retry_keeps_staged_owned_files_and_renames(self):
        said = []
        g = lambda *a: subprocess.run(['git', '-C', str(self.r), *a], capture_output=True, text=True)
        (self.r / 'src').mkdir()
        (self.r / 'src' / 'new.py').write_text('n')
        g('mv', 'a.txt', 'src/a_moved.txt')               # a rename of a tracked file into Owns
        hook = self.r / '.git' / 'hooks' / 'pre-commit'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        (self.r / 'MILESTONE').write_text('rename + new')
        w = ext.MilestoneWatcher(self.r, said.append, owns=['src/*'])
        w.commit()                                        # fails: files are now staged by our own attempt
        self.assertTrue((self.r / 'MILESTONE').exists())
        hook.unlink()
        w.commit()
        self.assertFalse((self.r / 'MILESTONE').exists())
        files = g('show', '--name-status', '--no-renames', '--format=', 'HEAD').stdout.split()
        self.assertEqual(sorted(files), sorted(['D', 'a.txt', 'A', 'src/a_moved.txt', 'A', 'src/new.py']))

    def test_stop_joins_thread_without_error(self):
        said = []
        w = ext.MilestoneWatcher(self.r, said.append, every=0.01)
        w.start()
        (self.r / 'a.txt').write_text('4')
        (self.r / 'MILESTONE').write_text('race')
        time.sleep(0.05)
        w.stop()
        self.assertFalse(any('error' in s for s in said), said)
        self.assertEqual(sum('milestone committed' in s for s in said), 1)

    def test_pathspec(self):
        self.assertEqual(ext.pathspec('src/api/*'), 'src/api/')
        self.assertEqual(ext.pathspec('tools/api_*'), ':(glob)tools/api_*')
        self.assertEqual(ext.pathspec('tools/x.py'), 'tools/x.py')

    def test_preflight_overlap_and_outside_owns(self):
        said = []
        r1 = self.runs / '1-do-a'; r1.mkdir()
        os.environ.pop('CLAUDECODE', None)
        e1 = ext.preflight_do(GOOD, self.r, r1, self.runs, False, said.append)
        r2 = self.runs / '2-do-b'; r2.mkdir()
        ext.preflight_do(GOOD, self.r, r2, self.runs, False, said.append)  # same owns, worker 1 "alive" (our pid)
        self.assertTrue(any('overlaps running worker 1-do-a' in s for s in said))
        (self.r / 'tools').mkdir()
        (self.r / 'tools' / 'x.py').write_text('x')
        ext.after_do(e1, r1, None, said.append)
        self.assertTrue(any('outside Owns: tools/x.py' in s for s in said))

    def test_history_budget(self):
        for i in range(6):
            d = self.runs / f'2026101{i}-000000-do-x{i}'
            d.mkdir()
            (d / 'log.txt').write_text(f'10:00:00 gpt-6-astra @ high in /w (workspace-write) -> {d}\n10:{10 + i * 5:02d}:00 exit 0; session s\n')
            (d / 'events.jsonl').write_text('{}\n' * (100 + i))
        t, ev = ext.history_budget(self.runs, 'do', 'gpt-6-astra')
        self.assertEqual(t, 120)  # p90 x 1.5 = 52 min, but history never shortens below the old default
        self.assertEqual(ev, 105)
        self.assertEqual(ext.history_budget(self.runs, 'review', 'x'), (None, None))
        for i in range(6):  # long builds: history extends the timeout
            d = self.runs / f'2026102{i}-000000-do-y{i}'
            d.mkdir()
            (d / 'log.txt').write_text(f'10:00:00 gpt-6-astra @ ultra in /w (workspace-write) -> {d}\n13:{i:02d}:00 exit 0; session s\n')
        self.assertEqual(ext.history_budget(self.runs, 'do', 'gpt-6-astra')[0], round(184 * 1.5))  # p90 of 12 runs
        (self.runs / '20261030-000000-do-bad').mkdir()
        (self.runs / '20261030-000000-do-bad' / 'log.txt').write_text('xx:yy:zz gpt-6-astra -> x\nab:cd:ef exit 0\n')
        self.assertIsInstance(ext.history_budget(self.runs, 'do', 'gpt-6-astra'), tuple)  # bad log never raises

    def test_owns_skips_outside_paths(self):
        self.assertEqual(ext.owns("## Owns\n- `../other/x`, `src/api/*`\n"), ['src/api/*'])


class Plan(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.run = Path(self.t.name)
        os.environ.pop('CLAUDECODE', None)

    def tearDown(self):
        self.t.cleanup()

    def fake(self, critic_json):
        calls = []

        def run_agent(model, effort, prompt, cwd, out_dir, sandbox, timeout, schema=None):
            calls.append((model, sandbox, schema is not None))
            return {'final': '1. edit src/api/x\n2. run tests' if schema is None else critic_json}
        return run_agent, calls

    def test_pass_appends_plan(self):
        ra, calls = self.fake('{"pass": true, "problems": []}')
        said = []
        add = ext.plan_gate(ra, 'gpt-6-astra', 'ultra', GOOD, '.', self.run, False, said.append)
        self.assertIn('2. run tests', add)
        self.assertEqual(calls, [('gpt-6-astra', 'read-only', False), ('claude:opus', 'read-only', True)])
        self.assertTrue((self.run / 'plan.md').exists())

    def test_fail_warns_owner_stops_claude(self):
        ra, _ = self.fake('{"pass": false, "problems": ["touches DIGITAL"]}')
        said = []
        ext.plan_gate(ra, 'claude:opus', 'max', GOOD, '.', self.run, False, said.append)
        self.assertTrue(any('FAIL' in s for s in said))
        os.environ['CLAUDECODE'] = '1'
        try:
            with self.assertRaises(SystemExit):
                ext.plan_gate(ra, 'claude:opus', 'max', GOOD, '.', self.run, False, said.append)
        finally:
            os.environ.pop('CLAUDECODE')


class Misc(unittest.TestCase):
    def test_stalled(self):
        s = {}
        for _ in range(2):
            s['check'] = {'rc': 1, 'tail': 'FAIL test_x'}
            ext.note_check(s)
        self.assertTrue(ext.stalled(s))
        s['check'] = {'rc': 0}
        ext.note_check(s)
        self.assertFalse(ext.stalled(s))

    def test_usd(self):
        t = {'in': 3_000_000, 'cached': 2_000_000, 'out': 100_000, 'reasoning': 100_000}
        self.assertAlmostEqual(ext.usd('m', t, {'m': {'in': 2.0, 'cached': 0.5, 'out': 10.0}}), 2 + 1 + 2)
        self.assertIsNone(ext.usd('other', t, {}))


if __name__ == '__main__':
    unittest.main()
