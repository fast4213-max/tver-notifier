"""
state.py
--------
data/programs.json （登録番組リスト）と data/seen.json （既読エピソード記録）
の読み書きをまとめて担当するファイルです。

初心者向けメモ：
- 「JSONファイルを読む」「JSONファイルを書く」処理をここに集約しています。
- 他のファイル（main.py など）は、このファイルの関数を呼ぶだけで
  ファイルの中身を気にせず使えるようにしています。
"""

import json
import os

# このファイル(state.py)から見て、一つ上のフォルダにある data/ を指す
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAMS_PATH = os.path.join(BASE_DIR, "data", "programs.json")
SEEN_PATH = os.path.join(BASE_DIR, "data", "seen.json")

# 1シリーズあたり、既読IDを何件まで保持するか（それを超えたら先頭から切り捨て）
#
# ★なぜ大きめの値にしてあるか★
# 切り捨てられたエピソードが再び配信中だった場合、「未読」とみなされて
# もう一度通知されてしまう。つまり切り捨ては少ないほど安全。
# またエピソード一覧は「シーズンごとに古い→新しい」順であって、
# シーズンをまたいだ厳密な時系列にはなっていない（TVer APIが
# シーズン横断の並び順を返さないため）。そのため切り捨てが起きると
# 本編より特典・予告シーズンのIDが残ってしまう可能性がある。
# IDは1件約12文字しかなくファイルサイズはたかが知れているので、
# 「通常は絶対に切り捨てが起きない」水準まで余裕を持たせ、
# ここは純粋に無限肥大を防ぐ最後の歯止めとして扱う。
# （週1回放送の番組なら500件で約10年分に相当する）
SEEN_LIMIT_PER_SERIES = 500


def _load_programs_file():
    """
    data/programs.json を丸ごと読み込む共通処理。
    programs / exclude_keywords / exclude_season_keywords を
    それぞれ別関数から読むため、ここに集約している。
    """
    with open(PROGRAMS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_programs():
    """
    data/programs.json を読み込んで、登録されている番組(シリーズ)の
    リストを返す。

    戻り値の例:
    [
        {"name": "番組メモ", "url": "https://tver.jp/series/srXXXXXXXX"},
        ...
    ]
    """
    return _load_programs_file().get("programs", [])


def load_global_exclude_keywords():
    """
    data/programs.json のトップレベルにある "exclude_keywords" を読み込む。
    ここに書いたキーワードは、全番組共通で
    「タイトルにこの単語が含まれていたら通知しない」対象になる。

    例: ["ダイジェスト", "解説", "インタビュー"]
    """
    return _load_programs_file().get("exclude_keywords", [])


def load_global_exclude_season_keywords():
    """
    data/programs.json のトップレベルにある "exclude_season_keywords" を読み込む。

    こちらは「エピソードのタイトル」ではなく「シーズン名」に対する除外設定。
    TVerは1つの番組を
      本編 / メイキング / ダイジェスト / ナビ・予告 / 解説放送版 ...
    のようにシーズン単位で分けているので、
    「本編以外は要らない」という用途にはシーズン名で外す方が確実。

    エピソードのタイトルで弾く方式だと、たとえば
    「『VIVANT』続編7月スタート発表記念ムービー」のように
    予告シーズンに属しているのにタイトルへ「予告」の字が
    1文字も入っていないものを取りこぼしてしまうため。

    例: ["ナビ", "予告", "メイキング", "ダイジェスト", "解説放送"]
    """
    return _load_programs_file().get("exclude_season_keywords", [])


def get_exclude_season_keywords_for_program(program, global_season_keywords):
    """
    1つの番組について、実際に適用するシーズン除外キーワードの一覧を返す。
    「全体共通」＋「その番組だけの追加分」を合体させたもの。

    programs.json の1件にこう書くと、その番組だけ追加で外せる：
    {
        "name": "番組メモ",
        "url": "https://tver.jp/series/srXXXXXXXX",
        "exclude_season_keywords": ["悪役会議室"]
    }
    """
    program_keywords = program.get("exclude_season_keywords", [])
    return list(set(global_season_keywords) | set(program_keywords))


def is_season_excluded(season_title, exclude_season_keywords):
    """
    シーズン名に除外キーワードのどれか1つでも含まれていたら
    True（＝そのシーズンは丸ごと通知対象外）を返す。

    ★この方式にしている理由★
    「本編シーズンだけ通知する」という決め打ちにすると、
    局が特番を独立シーズン（例:「新春スペシャル」）に切り出したときに
    丸ごと取りこぼしてしまう。
    逆に「要らないシーズン名を外す」方式なら、
    見覚えのない新シーズンは通知される側に倒れるので取りこぼしが起きない。
    """
    return is_title_excluded(season_title, exclude_season_keywords)


def get_exclude_keywords_for_program(program, global_keywords):
    """
    1つの番組(program)について、実際に適用する除外キーワードの一覧を返す。
    「全体共通のキーワード」＋「その番組だけの追加キーワード(exclude_keywords)」
    を合体させたもの。

    programs.json の1件にこう書くと、その番組だけの追加キーワードを設定できる：
    {
        "name": "番組メモ",
        "url": "https://tver.jp/series/srXXXXXXXX",
        "exclude_keywords": ["未公開", "総集編"]
    }
    """
    program_keywords = program.get("exclude_keywords", [])
    # 重複を除きつつ、順序はあまり気にしなくてよい（判定にしか使わないため）
    return list(set(global_keywords) | set(program_keywords))


def is_title_excluded(title, exclude_keywords):
    """
    エピソードのタイトルに、除外キーワードのどれか1つでも含まれていたら
    True（＝通知しない）を返す。含まれていなければ False。
    大文字・小文字や全角半角までは吸収しない、単純な部分一致判定。
    """
    for keyword in exclude_keywords:
        if keyword and keyword in title:
            return True
    return False


def load_seen():
    """
    data/seen.json を読み込んで、シリーズIDごとの既読エピソードID一覧を返す。

    戻り値の例:
    {
        "sru35hwdd2": ["epaaaaaaaa", "epbbbbbbbb"],
        "srtxft431v": ["epcccccccc"]
    }
    """
    if not os.path.exists(SEEN_PATH):
        return {}
    with open(SEEN_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("series", {})


def save_seen(seen_dict):
    """
    既読エピソードID一覧を data/seen.json に書き込む。
    各シリーズごとに直近 SEEN_LIMIT_PER_SERIES 件だけ残し、
    古いものは切り捨てる（ファイルが無限に大きくならないようにするため）。

    ※ seen_dict の各リストは「古い→新しい」順に積まれている前提。
       この順序が崩れると新しいエピソードの方を切り捨ててしまい、
       再通知の原因になる。
    """
    trimmed = {}
    for series_id, episode_ids in seen_dict.items():
        trimmed[series_id] = episode_ids[-SEEN_LIMIT_PER_SERIES:]

    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump({"series": trimmed}, f, ensure_ascii=False, indent=2)
        f.write("\n")


def add_seen_episode(seen_dict, series_id, episode_id):
    """
    メモリ上の seen_dict（辞書）に、通知済みのエピソードIDを1件追加する。
    ※ ファイルへの保存は save_seen() を別途呼ぶまで行われない。
    """
    if series_id not in seen_dict:
        seen_dict[series_id] = []
    if episode_id not in seen_dict[series_id]:
        seen_dict[series_id].append(episode_id)
