# TVer新着通知bot

TVerに登録した番組（シリーズ）の新着エピソードを、1時間ごとに自動でDiscordへ通知します。

- タイトルと画像をDiscordに通知
- 1回の通知は最大10件まで（Discordのレート制限対策）。11件以上あった場合は、残りを消さずに次回の実行に持ち越します
- TVerの構造変化などで取得できなかった場合は、その旨をDiscordにログとして通知します
- 動作確認用に「最新1件だけ通知する」テストモードがあります

---

## 1. 全体の仕組み（まずはここだけ理解すればOK）

```
[cron-job.org（外部サービス）]
   │ 1時間ごとに「GitHubさん、動いて」という信号を送るだけ
   ▼
[GitHub Actions]
   │ 信号を受け取ったら、Pythonスクリプトを実行
   ▼
[Pythonスクリプト]
   │ TVerから新着エピソードを調べて、Discordに通知する
   ▼
[Discord]
```

**なぜGitHub Actions単体の「時間になったら自動実行」機能（schedule）を使わないのか？**
→ 今回はあえて使わず、外部の無料cronサービス（cron-job.org）から「動いて」と
GitHubに信号を送ってもらう形にしています。GitHub Actions側は「信号を受け取ったら動く」
待ち受け専用の設定だけを持っています。

---

## 2. ファイル構成

```
tver-notifier/
├── .github/workflows/
│   ├── check.yml     ← 本番用。外部cronからの信号で動く
│   └── test.yml       ← テスト用。手動ボタンでのみ動く
├── src/
│   ├── main.py                # 全体の流れを制御する司令塔
│   ├── tver_client.py         # TVerから情報を取ってくる係
│   ├── discord_notifier.py    # Discordに送る係
│   └── state.py                # ファイル（programs.json/seen.json）の読み書き係
├── data/
│   ├── programs.json           # あなたが手動で編集する：通知してほしい番組の一覧
│   └── seen.json                # 自動で更新される：もう通知した話数の記録
└── requirements.txt              # 必要なPythonライブラリ一覧
```

**あなたが普段さわるのは基本的に `data/programs.json` だけ**です。
`data/seen.json` はスクリプトが自動で書き換えるので触らなくて大丈夫です。

---

## 3. セットアップ手順

### ステップ1: このリポジトリをGitHubに作成する

1. GitHubで新しい**公開（Public）リポジトリ**を作成します（例：`tver-notifier`）
2. ここにある全ファイルをそのリポジトリにアップロードします
   （GitHubの画面から「Add file」→「Upload files」でドラッグ＆ドロップでOKです）

### ステップ2: DiscordのWebhook URLを取得する

1. 通知を送りたいDiscordのチャンネルの設定を開く
2. 「連携サービス（Integrations）」→「ウェブフック（Webhooks）」→「新しいウェブフック」
3. 作成したウェブフックの「ウェブフックURLをコピー」をクリック
4. このURLは**絶対に人に見せないでください**（誰でもそのチャンネルに投稿できてしまいます）

### ステップ3: DiscordのWebhook URLをGitHubに登録する

1. あなたのリポジトリの画面で `Settings`（設定）タブを開く
2. 左メニューの `Secrets and variables` → `Actions` を選ぶ
3. `New repository secret` ボタンを押す
4. 以下のように入力する
   - Name: `DISCORD_WEBHOOK_URL`
   - Secret: （ステップ2でコピーしたURLを貼り付け）
5. `Add secret` で保存

これで、コードの中には一切Webhook URLを書かずに、安全に使えるようになります。

### ステップ4: GitHubの個人アクセストークン（PAT）を発行する

これは「外部のcronサービスが、あなたのGitHubリポジトリに向かって
"動いて"と信号を送るための鍵」です。

1. GitHubの右上のアイコン → `Settings`
2. 左メニュー一番下あたりの `Developer settings`
3. `Personal access tokens` → `Tokens (classic)`
4. `Generate new token` → `Generate new token (classic)`
5. Note（メモ）に「tver-notifier用」などわかりやすい名前をつける
6. 有効期限（Expiration）はお好みで（`No expiration`＝無期限も選べます）
7. 権限（Scopes）は **`repo` にチェックを入れる**（配下の項目も全部チェックが入ります）
8. `Generate token` を押す
9. 表示されたトークン（`ghp_`から始まる文字列）を**その場でコピー**しておく
   （このページを閉じると二度と表示されないので注意）

⚠️ **このトークンはパスワードと同じくらい重要です。** 人に見せたり、コードの中に直接書いたりしないでください。

### ステップ5: cron-job.orgを設定する

1. https://cron-job.org にアクセスし、無料アカウントを作成
2. ログイン後、`Create cronjob`（新規cronジョブ作成）
3. 以下のように設定します

   | 項目 | 設定値 |
   |---|---|
   | Title | 何でもOK（例：TVer通知） |
   | Address(URL) | `https://api.github.com/repos/あなたのユーザー名/リポジトリ名/dispatches` |
   | Schedule | 1時間ごと（`Every hour` などを選択） |
   | Request method | `POST` |

4. 詳細設定（Advanced/Headers）で以下のヘッダーを追加

   | ヘッダー名 | 値 |
   |---|---|
   | `Authorization` | `token ghp_あなたが発行したトークン` |
   | `Accept` | `application/vnd.github+json` |
   | `Content-Type` | `application/json` |

5. リクエストボディ（Body/Data）に以下を設定

   ```json
   {"event_type": "tver-check"}
   ```

6. 保存して有効化

これで1時間ごとに、cron-job.org → GitHub Actions → TVer確認 → Discord通知、が自動で回るようになります。

### ステップ6: 番組を登録する

`data/programs.json` を開いて、通知したいTVerのシリーズURLを追加します。

TVerのサイトで見たい番組のページを開くと、URLが
`https://tver.jp/series/srXXXXXXXX` のような形になっています。これをコピーして登録します。

```json
{
  "programs": [
    {
      "name": "お気に入りの番組（メモ用、通知には使いません）",
      "url": "https://tver.jp/series/srXXXXXXXX"
    },
    {
      "name": "もう1つの番組",
      "url": "https://tver.jp/series/srYYYYYYYY"
    }
  ]
}
```

編集したら保存して、GitHubにコミット（アップロード）してください。

### ステップ7: テスト実行してみる

1. リポジトリの `Actions` タブを開く
2. 左側の `TVer Test Notify (Manual)` を選ぶ
3. 右側の `Run workflow` ボタン → 再度 `Run workflow` で実行
4. 数十秒〜1分ほど待つと、Discordに1件だけ通知が届けば成功です

もし失敗した場合は、実行結果のログ（赤い×マークの部分をクリック）に
エラーメッセージが表示されるので、そこを確認してください。
（またはDiscordにエラーログが届く場合もあります）

---

## 4. 日常の運用

- **番組を追加したいとき**：`data/programs.json` を編集してコミットするだけ
- **番組を削除したいとき**：同じく `data/programs.json` からその行を消してコミット
- **通知が来なくなったら**：`Actions` タブで `TVer Check and Notify` の実行履歴を確認
  - 赤い×がついていたら、そこをクリックしてエラー内容を確認
  - TVerがサイト構造を変えた場合、Discordに「⚠️ TVer通知botエラー」というメッセージが届きます

---

## 5. よくある質問

**Q. 1時間より短い間隔にできますか？**
A. cron-job.orgの設定を変えれば可能ですが、TVer側への負荷や規約の観点から、
あまり高頻度にしすぎないことをおすすめします。

**Q. seen.jsonが際限なく増え続けませんか？**
A. 増えません。1シリーズあたり最新50件までしか保存しないように
自動で古い記録を切り捨てる仕組みになっています（`src/state.py` 内の
`SEEN_LIMIT_PER_SERIES` という数値で調整できます）。

**Q. 11件以上新着があった場合、超えた分は消えますか？**
A. 消えません。今回通知した10件だけが「既読」として記録され、
残りは次回（1時間後）の実行時に自動的に続きとして処理されます。

**Q. TVerの仕様が変わって動かなくなったら、自分で直せますか？**
A. `src/tver_client.py` の中の、TVerへのアクセス部分（URLの並びなど）を
修正する必要があります。この部分はTVer非公式APIの仕様変更の影響を
直接受けるため、将来的にメンテナンスが必要になる可能性があります。

---

## 6. 免責事項

このツールはTVerが公式に提供していないAPIを利用しています。
TVerの利用規約や仕様変更により、動作しなくなったり、
将来的にアクセスできなくなる可能性があります。個人の非商用利用の範囲で、
自己責任にてご利用ください。
