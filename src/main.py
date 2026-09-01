"""
main.py
-------
このスクリプトが全体の司令塔です。GitHub Actionsから実行されるのはこのファイルです。

実行モードは3つ：
  --mode=baseline : 初回セットアップ用。Discordには何も通知せず、
                    その時点で配信されている全エピソードを「既読」として
                    一気にseen.jsonへ登録する。
  --mode=normal   : 本番用。未読エピソードを最大10件通知し、seen.jsonを更新する
  --mode=test     : テスト用。全番組の中から最新1件だけ通知する（seen.jsonは更新しない）

★baselineモードはなぜ必要か★
新しく番組をprograms.jsonに登録した直後にいきなり --mode=normal を実行すると、
その番組の「配信中の全話」がまだ一度もseen.jsonに記録されていないため、
すべて「新着」とみなされて大量通知されてしまいます（過去話も含めて）。
そこで、番組を登録した直後に一度だけ --mode=baseline を実行しておくと、
「今配信されている分はもう知っている」という状態を作れます。
以降 --mode=normal を回したときは、本当に新しく追加された話数だけが
通知されるようになります。

流れ（normalモードの場合）：
  1. data/programs.json から登録番組(シリーズ)一覧を読む
  2. data/seen.json から既読エピソードID一覧を読む
  3. TVer非公式APIで各シリーズの最新エピソード一覧を取得
  4. 既読と突き合わせて「未読」だけを抜き出す
  5. 未読を最大10件までDiscordに通知する（11件以上は次回に持ち越し）
  6. 通知に成功した分だけ既読に追加し、data/seen.json を更新する
  7. 途中でエラーがあれば、その番組はスキップしてDiscordにエラーを通知し、
     他の番組の処理は続ける
"""

import argparse
import sys

import discord_notifier
import state
import tver_client

MAX_NOTIFY_PER_RUN = 10  # Discordのレート制限対策。1回の実行で通知する最大件数


def parse_args():
    parser = argparse.ArgumentParser(description="TVer新着通知スクリプト")
    parser.add_argument(
        "--mode",
        choices=["normal", "test", "baseline"],
        default="normal",
        help=(
            "normal=本番実行 / "
            "test=テスト実行（最新1件のみ、既読化しない） / "
            "baseline=初回登録用（通知せず全話を既読化する）"
        ),
    )
    return parser.parse_args()


def collect_unread_episodes(programs, seen_dict, session, error_messages):
    """
    登録されている全番組について、未読エピソードを集める。

    戻り値: [
        {
            "series_id": "srXXXXXXXX",
            "episode_id": "epXXXXXXXX",
            "title": "エピソードタイトル",
            "series_title": "番組名",
            "thumbnail_url": "https://...",
        },
        ...
    ]
    （新着が古い順に並ぶよう、番組ごとに整理してから結合する）

    1番組の取得でエラーが起きても、そのエラーメッセージを error_messages に
    追加した上で、その番組だけスキップして処理を続ける。
    """
    unread_list = []

    for program in programs:
        series_url = program.get("url", "")
        try:
            series_id = tver_client.extract_series_id(series_url)
            series_title = tver_client.get_series_title(series_id, session)
            episodes = tver_client.get_latest_episodes(series_id, session)
        except tver_client.TverApiError as e:
            error_messages.append(f"[番組取得エラー] URL={series_url}\n{e}")
            continue

        already_seen = set(seen_dict.get(series_id, []))

        # episodes は「配信中の全エピソード」なので、
        # まだ通知していないもの（＝新着）だけに絞る。
        # TVer側のAPIの並び順に依存しすぎないよう、
        # 一覧の順序をそのまま「古い→新しい」とみなして処理する。
        for ep in episodes:
            if ep["episode_id"] in already_seen:
                continue
            unread_list.append(
                {
                    "series_id": series_id,
                    "episode_id": ep["episode_id"],
                    "title": ep["title"],
                    "series_title": series_title,
                    "thumbnail_url": ep["thumbnail_url"],
                }
            )

    return unread_list


def run_normal():
    programs = state.load_programs()

    if not programs:
        print("programs.json に登録された番組がありません。何もせず終了します。")
        return

    seen_dict = state.load_seen()
    error_messages = []

    try:
        session = tver_client.create_session()
    except tver_client.TverApiError as e:
        # セッション作成自体が失敗した場合は、全番組が処理不能なので
        # ここで打ち切ってエラー通知のみ行う
        discord_notifier.send_error_log(f"セッション作成に失敗し、処理全体を中止しました。\n{e}")
        sys.exit(1)

    unread_list = collect_unread_episodes(programs, seen_dict, session, error_messages)

    # 未読のうち先頭10件だけ今回通知する。残りは何もしない＝次回に自動で持ち越される
    to_notify = unread_list[:MAX_NOTIFY_PER_RUN]
    carried_over_count = len(unread_list) - len(to_notify)

    if to_notify:
        discord_notifier.send_episode_notifications(to_notify)
        for ep in to_notify:
            state.add_seen_episode(seen_dict, ep["series_id"], ep["episode_id"])
        state.save_seen(seen_dict)
        print(f"{len(to_notify)}件通知しました。")
    else:
        print("新着エピソードはありませんでした。")

    if carried_over_count > 0:
        print(f"{carried_over_count}件は次回の実行に持ち越します。")

    if error_messages:
        combined = "\n\n".join(error_messages)
        discord_notifier.send_error_log(combined)
        print("一部の番組でエラーが発生し、Discordに通知しました。")


def run_baseline():
    """
    初回セットアップ用モード。

    Discordには一切通知せず、登録されている全番組について
    「現在配信されているエピソードすべて」をseen.jsonに書き込む。

    使いどころ：
    - programs.jsonに新しい番組を追加した直後
    - 「過去話も含めて全部通知されると困る、今後の新着だけでいい」という場合
    """
    programs = state.load_programs()

    if not programs:
        print("programs.json に登録された番組がありません。何もせず終了します。")
        return

    seen_dict = state.load_seen()
    error_messages = []

    try:
        session = tver_client.create_session()
    except tver_client.TverApiError as e:
        discord_notifier.send_error_log(f"[初回登録処理] セッション作成に失敗しました。\n{e}")
        sys.exit(1)

    total_registered = 0

    for program in programs:
        series_url = program.get("url", "")
        try:
            series_id = tver_client.extract_series_id(series_url)
            episodes = tver_client.get_latest_episodes(series_id, session)
        except tver_client.TverApiError as e:
            error_messages.append(f"[番組取得エラー] URL={series_url}\n{e}")
            continue

        for ep in episodes:
            state.add_seen_episode(seen_dict, series_id, ep["episode_id"])
        total_registered += len(episodes)
        print(f"{series_url} : {len(episodes)}件を既読登録しました。")

    state.save_seen(seen_dict)
    print(f"合計 {total_registered} 件を既読として登録しました。（Discord通知はしていません）")

    if error_messages:
        combined = "\n\n".join(error_messages)
        discord_notifier.send_error_log(combined)
        print("一部の番組でエラーが発生し、Discordに通知しました。")


def run_test():
    """
    テストモード：全番組を確認し、最新1件だけをDiscordに通知する。
    既読(seen.json)は更新しない＝何度実行しても同じ1件が通知される。
    """
    programs = state.load_programs()

    if not programs:
        print("programs.json に登録された番組がありません。何もせず終了します。")
        return

    error_messages = []

    try:
        session = tver_client.create_session()
    except tver_client.TverApiError as e:
        discord_notifier.send_error_log(f"[テスト実行] セッション作成に失敗しました。\n{e}")
        sys.exit(1)

    # テストなので「既読」は無視し、各番組の最新1件（一覧の最後の要素）を候補にする
    candidates = []
    for program in programs:
        series_url = program.get("url", "")
        try:
            series_id = tver_client.extract_series_id(series_url)
            series_title = tver_client.get_series_title(series_id, session)
            episodes = tver_client.get_latest_episodes(series_id, session)
        except tver_client.TverApiError as e:
            error_messages.append(f"[番組取得エラー] URL={series_url}\n{e}")
            continue

        if episodes:
            latest = episodes[-1]
            candidates.append(
                {
                    "title": latest["title"],
                    "series_title": series_title,
                    "thumbnail_url": latest["thumbnail_url"],
                }
            )

    if candidates:
        discord_notifier.send_episode_notifications(candidates[:1])
        print("テスト通知を1件送信しました。（seen.jsonは更新していません）")
    else:
        print("通知できるエピソードが見つかりませんでした。")

    if error_messages:
        combined = "\n\n".join(error_messages)
        discord_notifier.send_error_log(combined)
        print("一部の番組でエラーが発生し、Discordに通知しました。")


def main():
    args = parse_args()
    if args.mode == "test":
        run_test()
    elif args.mode == "baseline":
        run_baseline()
    else:
        run_normal()


if __name__ == "__main__":
    main()
