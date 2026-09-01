"""
discord_notifier.py
--------------------
Discordへの通知送信をまとめて担当するファイルです。

初心者向けメモ：
- Discordの「Webhook」という仕組みを使っています。
  DiscordのチャンネルであらかじめWebhook URLを発行しておけば、
  そのURLに向けてHTTPでリクエストを送るだけでメッセージが投稿されます。
- 1回のリクエストで最大10件の「Embed（見た目が整ったカード状のメッセージ）」を
  まとめて送れます。この仕組みを使い、10件を超える通知は
  自動的に複数回のリクエストに分けて送信します。
"""

import os
import requests

DISCORD_EMBED_LIMIT_PER_MESSAGE = 10  # Discordの仕様上、1メッセージに入れられるEmbedの上限
REQUEST_TIMEOUT = 15


class DiscordNotifyError(Exception):
    """Discordへの送信自体が失敗したときに使うエラー"""
    pass


def _get_webhook_url():
    """
    環境変数からDiscordのWebhook URLを取得する。
    GitHub Actions側で Secrets → 環境変数として渡す想定。
    コードやログには絶対にURLそのものを書かない・出力しない。
    """
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        raise DiscordNotifyError(
            "環境変数 DISCORD_WEBHOOK_URL が設定されていません。"
            "GitHubのSecretsを確認してください。"
        )
    return url


def _build_embed(title, thumbnail_url, series_title):
    """
    1件のエピソード情報から、Discordの「Embed」1件分のデータを組み立てる。
    要件通り「タイトル」と「画像」のみのシンプルな構成。
    """
    return {
        "title": title,
        "description": f"番組: {series_title}" if series_title else None,
        "image": {"url": thumbnail_url} if thumbnail_url else None,
    }


def send_episode_notifications(episodes):
    """
    エピソード情報のリストをDiscordに送信する。
    episodes は最大10件までを想定（呼び出し側でバッチ分割済みのものを渡す）。

    episodes の例:
    [
        {
            "title": "エピソードタイトル",
            "thumbnail_url": "https://...",
            "series_title": "番組名",
        },
        ...
    ]
    """
    if not episodes:
        return

    if len(episodes) > DISCORD_EMBED_LIMIT_PER_MESSAGE:
        raise ValueError(
            f"send_episode_notifications には最大{DISCORD_EMBED_LIMIT_PER_MESSAGE}件までしか渡せません。"
            "呼び出し側で分割してください。"
        )

    webhook_url = _get_webhook_url()

    embeds = [
        _build_embed(
            title=ep["title"],
            thumbnail_url=ep.get("thumbnail_url"),
            series_title=ep.get("series_title"),
        )
        for ep in episodes
    ]

    payload = {"embeds": embeds}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise DiscordNotifyError(f"Discordへの通知送信に失敗しました: {e}")


def send_error_log(message):
    """
    「TVerの構造変化などで取得に失敗した」等のエラーログをDiscordに送る。
    通常のエピソード通知とは別枠で、シンプルなテキストメッセージとして送信する。
    """
    webhook_url = _get_webhook_url()

    payload = {
        "content": f"⚠️ **TVer通知botエラー**\n```\n{message}\n```"
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        # ここで失敗しても、これ以上通知する手段がないので標準出力にだけ残す
        # （GitHub Actionsのログで確認できるようにするため）
        print(f"[ERROR] エラーログのDiscord送信自体にも失敗しました: {e}")
