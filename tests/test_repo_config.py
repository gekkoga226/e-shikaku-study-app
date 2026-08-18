"""リポジトリ構成とデプロイ設定の守り。

守っている約束:

  - appsscript.json の設定を壊さない
      timeZone / runtimeVersion / exceptionLogging を維持する
      webapp は access=MYSELF, executeAs=USER_DEPLOYING
      oauthScopes は spreadsheets と drive.readonly だけ（権限を広げない）
  - 対象スプレッドシートIDを変えない
  - 認証情報ファイルをGitで管理しない
  - CI / デプロイのワークフローが存在し、検査に合格してからデプロイする
"""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gas_source import SRC, read  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SPREADSHEET_ID = '1hAsu5eR3dtHu6S34joxyCaNmLZBtSnivIguVqJ61bh8'

ALLOWED_SCOPES = {
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.readonly',
}


class TestAppsScriptManifest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((SRC / 'appsscript.json').read_text(encoding='utf-8'))

    def test_core_settings_preserved(self):
        self.assertEqual(self.manifest['timeZone'], 'Asia/Tokyo')
        self.assertEqual(self.manifest['runtimeVersion'], 'V8')
        self.assertEqual(self.manifest['exceptionLogging'], 'STACKDRIVER')

    def test_webapp_is_private_to_the_owner(self):
        webapp = self.manifest.get('webapp')
        self.assertIsNotNone(webapp, 'webapp 設定がありません')
        self.assertEqual(webapp['access'], 'MYSELF',
                         'Webアプリが自分専用になっていません')
        self.assertEqual(webapp['executeAs'], 'USER_DEPLOYING',
                         'Webアプリの実行者設定が想定と違います')

    def test_oauth_scopes_are_not_widened(self):
        scopes = set(self.manifest['oauthScopes'])
        self.assertEqual(
            scopes, ALLOWED_SCOPES,
            f'必要以上の権限が要求されています: {sorted(scopes - ALLOWED_SCOPES)}',
        )


class TestSpreadsheetTarget(unittest.TestCase):

    def test_spreadsheet_id_unchanged(self):
        code = read('Code.gs')
        self.assertIn(f"SPREADSHEET_ID: '{SPREADSHEET_ID}'", code,
                      '対象スプレッドシートIDが変わっています')

    def test_sheet_names_unchanged(self):
        code = read('Code.gs')
        for sheet in ('00_設定', '02_マインドマップ', '03_問題台帳', '04_学習ログ', '06_ダッシュボード'):
            self.assertIn(f"'{sheet}'", code, f'シート名 {sheet} の参照がありません')


class TestRepositoryHygiene(unittest.TestCase):

    def test_credentials_are_git_ignored(self):
        gitignore = (ROOT / '.gitignore').read_text(encoding='utf-8')
        for entry in ('.clasprc.json', '.clasp.json', '.env'):
            self.assertIn(entry, gitignore,
                          f'{entry} が .gitignore にありません')

    def test_no_credential_files_are_tracked(self):
        try:
            tracked = subprocess.run(
                ['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True
            ).stdout.split('\n')
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.skipTest('git を実行できません')

        forbidden = re.compile(
            r'(^|/)(\.clasprc\.json|\.clasp\.json|credentials\.json|\.env)$'
            r'|(^|/)client_secret.*\.json$'
            r'|\.(pem|p12)$'
        )
        offenders = [f for f in tracked if f and forbidden.search(f)]
        self.assertEqual(offenders, [], f'認証情報がGit管理下にあります: {offenders}')

    def test_claspignore_allows_only_apps_script_files(self):
        """.claspignore は「全部除外 → 必要なものだけ許可」の形式であること。"""
        lines = [
            line.strip()
            for line in (ROOT / '.claspignore').read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        self.assertEqual(lines[0], '**/**',
                         '.claspignore が「まず全部除外」で始まっていません')
        allowed = {line[1:] for line in lines if line.startswith('!')}
        self.assertEqual(
            allowed,
            {
                'appsscript.json', 'Code.gs', 'ImageManifest.gs', 'ImageSupport.gs',
                'ImageSelfTest.gs', 'Index.html', 'Styles.html', 'Client.html',
            },
            'Apps Scriptへ送るファイルの一覧が想定と違います',
        )

    def test_no_spreadsheet_data_is_committed(self):
        """学習データそのもの（回答ログ等）をGitへ置いていないこと。"""
        for pattern in ('*.xlsx', '*.xlsm', '*.gsheet'):
            found = list(ROOT.rglob(pattern))
            found = [p for p in found if '.git' not in p.parts and 'node_modules' not in p.parts]
            self.assertEqual(found, [], f'スプレッドシートのコピーがあります: {found}')


class TestWorkflows(unittest.TestCase):

    def test_workflow_files_exist(self):
        for path in (
            '.github/workflows/ci.yml',
            '.github/workflows/deploy.yml',
            '.github/actions/verify/action.yml',
        ):
            self.assertTrue((ROOT / path).exists(), f'{path} がありません')

    def test_deploy_requires_verification(self):
        text = (ROOT / '.github/workflows/deploy.yml').read_text(encoding='utf-8')
        self.assertIn('needs: verify',
                      text.replace('needs:  verify', 'needs: verify'),
                      'デプロイジョブが検査ジョブの成功を待っていません')
        self.assertIn('branches: [main]', text, 'main への push で動く設定がありません')

    def test_deploy_does_not_print_secrets(self):
        text = (ROOT / '.github/workflows/deploy.yml').read_text(encoding='utf-8')
        for secret in ('CLASPRC_JSON', 'CLASP_JSON', 'DEPLOYMENT_ID'):
            self.assertNotRegex(
                text, r'echo\s+"?\$\{?\{?\s*secrets\.' + secret,
                f'{secret} をログへ出力しています',
            )
            self.assertNotRegex(
                text, r'echo\s+"?\$' + secret + r'\b',
                f'{secret} をログへ出力しています',
            )

    def test_verify_action_runs_all_checks(self):
        text = (ROOT / '.github/actions/verify/action.yml').read_text(encoding='utf-8')
        for command in ('check:files', 'check:syntax', 'check:secrets', 'test:python'):
            self.assertIn(command, text, f'検査に {command} が含まれていません')


if __name__ == '__main__':
    unittest.main(verbosity=2)
