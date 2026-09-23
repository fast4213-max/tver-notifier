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
import time

import requests

DISCORD_EMBED_LIMIT_PER_MESSAGE = 10  # Discordの仕様上、1メッセージに入れられるEmbedの上限
DISCORD_EMBED_TITLE_LIMIT = 256  # Discordの仕様上、Embedのtitleに入れられる文字数の上限
DISCORD_CONTENT_LIMIT = 2000  # Discordの仕様上、contentに入れられる文字数の上限
REQUEST_TIMEOUT = 15
RATE_LIMIT_MAX_WAIT = 30  # 429(レート制限)時に待つ秒数の上限。これを超える指示なら諦めて失敗扱いにする


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


def _post_webhook(webhook_url, payload):
    """
    Webhookへ1回POSTする。
    Discordがレート制限(429)を返した場合は、指示された秒数だけ待って1回だけ再送する。
    （429をそのまま失敗扱いにすると、seen.jsonが更新されず、
      続けて送るエラー通知まで429で落ちてしまうため）
    """
    resp = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 429:
        try:
            wait = float(resp.json()["retry_after"])
        except (ValueError, KeyError, TypeError):
            wait = 1.0
        if wait <= RATE_LIMIT_MAX_WAIT:
            time.sleep(wait)
            resp = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp


def _build_embed(title, thumbnail_url, series_title):
    """
    1件のエピソード情報から、Discordの「Embed」1件分のデータを組み立てる。
    要件通り「タイトル」と「画像」のみのシンプルな構成。
    """
    # Discord側のtitle文字数上限(256文字)を超えると、そのEmbed 1件だけでなく
    # バッチ全体(最大10件)が400エラーで送信失敗になってしまうため、ここで切り詰める。
    if len(title) > DISCORD_EMBED_TITLE_LIMIT:
        title = title[: DISCORD_EMBED_TITLE_LIMIT - 1] + "…"

    # 値が無いキーは None を入れずに、キーごと省く。
    # Discordに "image": null のような形で送るのを避けるため。
    embed = {"title": title}
    if series_title:
        embed["description"] = f"番組: {series_title}"
    if thumbnail_url:
        embed["image"] = {"url": thumbnail_url}
    return embed


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
        _post_webhook(webhook_url, payload)
    except requests.exceptions.RequestException as e:
        raise DiscordNotifyError(f"Discordへの通知送信に失敗しました: {e}")


def send_error_log(message):
    """
    「TVerの構造変化などで取得に失敗した」等のエラーログをDiscordに送る。
    通常のエピソード通知とは別枠で、シンプルなテキストメッセージとして送信する。

    ここは「最後の手段」として呼ばれる関数なので、Webhook URL未設定も含めて
    ここで何が起きてもこの関数自体は例外を投げない
    （呼び出し側をクラッシュさせないため。標準出力にだけ残す）。
    """
    # Discordのcontentは2000文字までで、超えると送信自体が400エラーになる。
    # エラー通知が丸ごと届かなくなるのが一番困るので、ここで切り詰めておく。
    header = "⚠️ **TVer通知botエラー**\n```\n"
    footer = "\n```"
    omitted_note = "\n（長すぎるため以降は省略しました）"
    budget = DISCORD_CONTENT_LIMIT - len(header) - len(footer)
    if len(message) > budget:
        message = message[: budget - len(omitted_note)] + omitted_note

    payload = {"content": f"{header}{message}{footer}"}

    try:
        webhook_url = _get_webhook_url()
        _post_webhook(webhook_url, payload)
    except (DiscordNotifyError, requests.exceptions.RequestException) as e:
        # ここで失敗しても、これ以上通知する手段がないので標準出力にだけ残す
        # （GitHub Actionsのログで確認できるようにするため）
        print(f"[ERROR] エラーログのDiscord送信自体にも失敗しました: {e}")
