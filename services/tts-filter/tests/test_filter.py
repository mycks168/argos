from pathlib import Path

from tts_filter import TTSFilter, delete_entry, load_config, normalize_for_tts, save_config, upsert_entry


def test_readme_extension_only():
    got = normalize_for_tts("README.md を読んで")
    assert "リードミー エムディー" in got


def test_md_inside_word_is_not_replaced():
    got = normalize_for_tts("amd64 や markdown はそのまま")
    assert "エムディー" not in got


def test_acronyms():
    got = normalize_for_tts("LLM と API と OpenClaw と Gitea と uv と pytest と POST")
    assert "エルエルエム" in got
    assert "エーピーアイ" in got
    assert "ポスト" in got


def test_off_is_read_as_japanese():
    got = normalize_for_tts("ミュートをOFFにして")
    assert "ミュートをオフにして" in got


def test_gitignore_is_read_as_japanese():
    got = normalize_for_tts("gitignore と .gitignore を確認して")
    assert "ギットイグノア と ギットイグノア を確認して" in got


def test_image_is_read_as_japanese():
    got = normalize_for_tts("image を表示して IMAGE を作って")
    assert "イメージ を表示して イメージ を作って" in got


def test_image_formats_are_read_as_japanese():
    got = normalize_for_tts("JPEG と PNG と sample.jpg と icon.png を確認して")
    assert "ジェイペグ と ピング と sample ジェイペグ と icon ピング を確認して" in got


def test_autossh_service_is_read_as_japanese():
    got = normalize_for_tts("SSH と autossh-clove.service と service を確認して")
    assert "エスエスエイチ と オートエスエスエイチ clove サービス と サービス を確認して" in got


def test_gpsd_is_read_as_japanese():
    got = normalize_for_tts("GPSD と gpsd を確認して")
    assert "ジーピーエスディー と ジーピーエスディー を確認して" in got


def test_reminder_is_read_as_japanese():
    got = normalize_for_tts("Reminder と REMINDER と argos-reminder を確認して")
    assert "リマインダー と リマインダー と アルゴス リマインダー を確認して" in got


def test_tmp_is_read_as_temp():
    got = normalize_for_tts("/tmp/argos と TMP と temp と tmpfs を確認して")
    assert "ルート スラッシュ テンプ スラッシュ アルゴス と テンプ と テンプ と テンプエフエス を確認して" in got


def test_tmux_is_read_as_technical_term():
    got = normalize_for_tts("tmux と TMUX と ttyd を確認して")
    assert "ティーマックス と ティーマックス と ティーティーワイディー を確認して" in got


def test_test_pass_reading_uses_tooru():
    got = normalize_for_tts("テストも通ってる。テストを通る。検証も通ってる。")
    assert "テストもとおってる。テストをとおる。検証もとおってる。" in got


def test_japanese_term_is_read_from_dictionary():
    got = normalize_for_tts("誤変換を直す")
    assert "ごへんかんを直す" in got


def test_five_tap_is_read_from_dictionary():
    got = normalize_for_tts("5タップされた")
    assert "ごタップされた" in got


def test_place_name_toba_is_read_from_dictionary():
    got = normalize_for_tts("鳥羽に行く")
    assert "とばに行く" in got


def test_gokenchi_is_read_from_dictionary():
    got = normalize_for_tts("誤検知をテストする")
    assert "ごけんちをテストする" in got


def test_phrase_rules_are_loaded_from_yaml(tmp_path: Path):
    path = tmp_path / "dictionary.yml"
    save_config(
        {
            "acronyms": {},
            "terms": {},
            "extensions": {},
            "phrase_rules": [{"pattern": "確認した", "replacement": "確認済み"}],
        },
        path,
    )
    tts_filter = TTSFilter.from_yaml(path)
    assert tts_filter.normalize("確認した") == "確認済み"


def test_path():
    got = normalize_for_tts("src/utils/readme.md を開く")
    assert "スラッシュ" in got
    assert "エムディー" in got


def test_version_date_time():
    got = normalize_for_tts("v1.2.3 を 2026-04-07 10:30 に出す")
    assert "バージョン 1.2.3" in got
    assert "2026年4月7日" in got
    assert "10時30分" in got


def test_url_and_email():
    got = normalize_for_tts("https://example.com/ と foo@example.com")
    assert "URL example.com" in got
    assert "メールアドレス foo アット example.com" in got


def test_inline_code_is_normalized_by_default():
    got = normalize_for_tts("`README.md` はそのまま")
    assert "リードミー エムディー" in got


def test_load_config_from_yaml():
    config = load_config(Path("src/tts_filter/dictionary.yml"))
    assert config["acronyms"]["LLM"] == "エルエルエム"
    assert config["acronyms"]["OFF"] == "オフ"
    assert config["terms"]["誤変換"] == "ごへんかん"
    assert config["extensions"]["md"] == "エムディー"
    assert config["phrase_rules"]


def test_load_config_recovers_yaml_boolean_keys(tmp_path: Path):
    path = tmp_path / "dictionary.yml"
    path.write_text("acronyms:\n  OFF: オフ\nextensions: {}\n", encoding="utf-8")
    config = load_config(path)
    assert config["acronyms"]["OFF"] == "オフ"


def test_upsert_and_delete_entry(tmp_path: Path):
    path = tmp_path / "dictionary.yml"
    save_config({"acronyms": {"LLM": "エルエルエム"}, "terms": {}, "extensions": {}, "phrase_rules": []}, path)
    upsert_entry("acronyms", "GITEA", "ギテア", path)
    upsert_entry("terms", "誤変換", "ごへんかん", path)
    updated = load_config(path)
    assert updated["acronyms"]["GITEA"] == "ギテア"
    assert updated["terms"]["誤変換"] == "ごへんかん"
    delete_entry("acronyms", "GITEA", path)
    delete_entry("terms", "誤変換", path)
    updated = load_config(path)
    assert "GITEA" not in updated["acronyms"]
    assert "誤変換" not in updated["terms"]


def test_inline_code_is_normalized():
    tts_filter = TTSFilter.from_yaml(Path("src/tts_filter/dictionary.yml"))
    got = tts_filter.normalize("`README.md` を読んで")
    assert "リードミー エムディー" in got


def test_code_block_literal_mode():
    tts_filter = TTSFilter.from_yaml(Path("src/tts_filter/dictionary.yml"))
    tts_filter.code_block_mode = "literal"
    got = tts_filter.normalize("```python\nREADME.md\n```")
    assert "リードミー エムディー" in got
