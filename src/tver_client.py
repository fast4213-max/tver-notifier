"""
tver_client.py
--------------
TVerには「公式の外部向けAPI」は存在しません。
ここで使っているのは、TVerのWebサイトやアプリが内部的に使っている
「非公式API」です。有名なダウンロードツール yt-dlp が実際に使っている
エンドポイントを参考にしています。

★重要な注意点★
非公式なので、TVer側の仕様変更でこのファイルの処理が
"予告なく"動かなくなる可能性があります。
そのため、このファイルの中で何かエラーが起きたときは
TverApiError という専用のエラーを投げるようにしています。
呼び出し側(main.py)がこのエラーを受け取って、
「構造が変わって取得できなかった」とDiscordに知らせる仕組みです。
"""

import re
import requests

# TVerのWeb版が使っているのと同じヘッダーを真似ています。
# これが無いとエラーになる場合があるため付けています。
HEADERS = {
    "x-tver-platform-type": "web",
    "Origin": "https://tver.jp",
    "Referer": "https://tver.jp/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

REQUEST_TIMEOUT = 20  # 秒。TVer側の反応が遅い時にいつまでも待たないようにする


class TverApiError(Exception):
    """
    TVer非公式APIの呼び出しで何か問題が起きたときに使う専用のエラー。
    「構造が変わって取得できませんでした」という通知に使う想定。
    """
    pass


def extract_series_id(series_url):
    """
    "https://tver.jp/series/srXXXXXXXX" のようなURLから
    シリーズID "srXXXXXXXX" だけを取り出す。

    programs.json にはURLで番組を登録してもらう設計なので、
    実際にAPIへ渡すためにはこの関数でIDに変換する必要がある。
    """
    match = re.search(r"tver\.jp/series/([a-zA-Z0-9]+)", series_url)
    if not match:
        raise TverApiError(
            f"URLからシリーズIDを取り出せませんでした（URLの形式が想定と違います）: {series_url}"
        )
    return match.group(1)


def _get_json(url, method="GET", data=None, params=None, note="", extra_headers=None):
    """
    requestsでJSONを取得する共通処理。
    失敗した場合は TverApiError にまとめて変換する。
    """
    headers = {**HEADERS, **(extra_headers or {})}
    try:
        if method == "POST":
            resp = requests.post(
                url, data=data, headers=headers, timeout=REQUEST_TIMEOUT
            )
        else:
            resp = requests.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        # サーバーが返してきた本文（エラー詳細）もログに残す。原因調査がしやすくなる。
        body_preview = ""
        try:
            body_preview = resp.text[:300]
        except Exception:
            pass
        raise TverApiError(
            f"{note}: 通信エラーが発生しました ({e})"
            + (f" / レスポンス内容: {body_preview}" if body_preview else "")
        )
    except requests.exceptions.RequestException as e:
        raise TverApiError(f"{note}: 通信エラーが発生しました ({e})")
    except ValueError as e:
        # resp.json() が失敗した場合（＝返ってきたのがJSONじゃなかった）
        raise TverApiError(f"{note}: 想定していないレスポンス形式でした ({e})")


def create_session():
    """
    TVerのAPIを呼ぶ前に必要な「セッション情報」を取得する。
    platform_uid と platform_token という2つの値をこの後のAPI呼び出しで
    毎回クエリパラメータとして付ける必要がある。

    このリクエストは "device_type=pc" というフォームデータを
    Content-Type: application/x-www-form-urlencoded として送る必要がある。
    （明示的に指定しないと、TVer側に正しく解釈されず400エラーになることがある）
    """
    data = _get_json(
        "https://platform-api.tver.jp/v2/api/platform_users/browser/create",
        method="POST",
        data="device_type=pc",
        extra_headers={"Content-Type": "application/x-www-form-urlencoded"},
        note="セッション作成",
    )
    try:
        result = data["result"]
        return {
            "platform_uid": result["platform_uid"],
            "platform_token": result["platform_token"],
        }
    except KeyError as e:
        raise TverApiError(f"セッション作成: 想定していたキーがありませんでした ({e})")


def _call_platform_api(path, session, note=""):
    """
    platform-api.tver.jp を叩く共通処理。
    session（create_session()の戻り値）をクエリパラメータに付けて呼ぶ。
    """
    url = f"https://platform-api.tver.jp/service/api/{path}"
    return _get_json(url, method="GET", params=session, note=note)


def get_series_title(series_id, session):
    """
    シリーズの正式タイトルを取得する。
    programs.json の "name" はメモ用なので、通知にはこちらの
    正式タイトルを使う。
    """
    data = _call_platform_api(
        f"v2/callSeries/{series_id}", session, note=f"シリーズ情報取得({series_id})"
    )
    try:
        return data["result"]["content"]["content"]["title"]
    except KeyError as e:
        raise TverApiError(
            f"シリーズ情報取得({series_id}): 想定していたキーがありませんでした ({e})"
        )


def get_latest_episodes(series_id, session):
    """
    シリーズIDから「そのシリーズの現在配信中の全エピソード」の一覧を取得する。

    TVerの構造は
      シリーズ(series) → シーズン(season) → エピソード(episode)
    という3階層になっているため、まずシーズン一覧を取り、
    各シーズンのエピソード一覧を集める、という2段階の処理になっている。

    戻り値: [{"episode_id": "epXXXXXXXX", "title": "...", "thumbnail_url": "..."}, ...]
    """
    seasons_data = _call_platform_api(
        f"v1/callSeriesSeasons/{series_id}",
        session,
        note=f"シーズン一覧取得({series_id})",
    )

    try:
        contents = seasons_data["result"]["contents"]
    except KeyError as e:
        raise TverApiError(
            f"シーズン一覧取得({series_id}): 想定していたキーがありませんでした ({e})"
        )

    season_ids = [
        c["content"]["id"]
        for c in contents
        if c.get("type") == "season" and "content" in c and "id" in c["content"]
    ]

    if not season_ids:
        raise TverApiError(
            f"シーズン一覧取得({series_id}): シーズン情報が1件も見つかりませんでした"
        )

    episodes = []
    for season_id in season_ids:
        episodes_data = _call_platform_api(
            f"v1/callSeasonEpisodes/{season_id}",
            session,
            note=f"エピソード一覧取得(season={season_id})",
        )
        try:
            ep_contents = episodes_data["result"]["contents"]
        except KeyError as e:
            raise TverApiError(
                f"エピソード一覧取得(season={season_id}): "
                f"想定していたキーがありませんでした ({e})"
            )

        for c in ep_contents:
            if c.get("type") != "episode":
                continue
            content = c.get("content", {})
            episode_id = content.get("id")
            title = content.get("title")
            if not episode_id or not title:
                # 1件くらい欠けていても全体は止めず、その1件だけスキップする
                continue
            episodes.append(
                {
                    "episode_id": episode_id,
                    "title": title,
                    "thumbnail_url": (
                        f"https://statics.tver.jp/images/content/thumbnail/"
                        f"episode/xlarge/{episode_id}.jpg"
                    ),
                }
            )

    return episodes
