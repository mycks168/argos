import os
from fastapi.testclient import TestClient
from main import app, API_TOKEN

client = TestClient(app)


def test_select_phrase_unauthorized():
    """認証トークンがない、または不正な場合のエラーをテストする。"""
    # トークンなし
    response = client.post("/select", json={"text": "テスト"})
    assert response.status_code == 401

    # 不正なトークン
    response = client.post(
        "/select",
        json={"text": "テスト"},
        headers={"Authorization": "Bearer invalid"},
    )
    assert response.status_code == 401


def test_select_phrase_look():
    """「見て」などの語尾で「今見てみるね。」が選択されることをテストする。"""
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    for word in ["みて", "みせて", "見て", "見せて", "確認して", "確認してみて"]:
        response = client.post(
            "/select", json={"text": f"画面を{word}"}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["phrase"] == "今見てみるね。"


def test_select_phrase_knowledge_question():
    """「知ってる？」などの語尾で「確認するね。」が選択されることをテストする。"""
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    for word in [
        "知ってる",
        "知ってる?",
        "知ってる？",
        "知ってますか",
        "知ってますか?",
        "知ってますか？",
        "知っていますか",
        "知っていますか?",
        "知っていますか？",
    ]:
        response = client.post(
            "/select", json={"text": f"Graphitiって{word}"}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["phrase"] == "確認するね。"


def test_select_phrase_search():
    """「調べて」などの語尾で「すぐ調べるね。」が選択されることをテストする。"""
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    for word in [
        "しらべて",
        "調べて",
        "けんさくして",
        "検索して",
        "おしえて",
        "教えて",
        "調べてみて",
    ]:
        response = client.post(
            "/select", json={"text": f"天気を{word}"}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["phrase"] == "すぐ調べるね。"


def test_select_phrase_do():
    """「やって」などの語尾で「了解。やってみるね。」が選択されることをテストする。"""
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    for word in ["やって", "動かして", "やってみて", "実行して"]:
        response = client.post(
            "/select", json={"text": f"タスクを{word}"}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["phrase"] == "了解。やってみるね。"


def test_select_phrase_fallback_with_phrases():
    """語尾判定にマッチせず、かつ候補フレーズがある場合のフォールバックをテストする。"""
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    phrases = ["候補A", "候補B"]
    response = client.post(
        "/select",
        json={"text": "こんにちは", "phrases": phrases},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["phrase"] in phrases


def test_select_phrase_fallback_empty_phrases():
    """語尾判定にマッチせず、かつ候補フレーズが空の場合のフォールバックをテストする。"""
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    response = client.post(
        "/select", json={"text": "こんにちは", "phrases": []}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["phrase"] == "わかった。少し待ってね。"


def test_load_rules_file_not_found(monkeypatch):
    """ルールファイルが存在しない場合の挙動をテストする。"""
    monkeypatch.setattr("main.RULES_PATH", "non_existent_rules.yml")
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    response = client.post(
        "/select", json={"text": "画面を見て", "phrases": []}, headers=headers
    )
    assert response.status_code == 200
    # ルールが適用されないため、フォールバック値が返る
    assert response.json()["phrase"] == "わかった。少し待ってね。"


def test_load_rules_invalid_yaml(monkeypatch, tmp_path):
    """壊れたYAMLファイルの場合に例外をキャッチして安全にフォールバックすることをテストする。"""
    broken_file = tmp_path / "broken.yml"
    with open(broken_file, "w") as f:
        f.write("rules:\n  - keywords: [")  # 壊れたYAML

    monkeypatch.setattr("main.RULES_PATH", str(broken_file))
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    response = client.post(
        "/select", json={"text": "画面を見て", "phrases": []}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["phrase"] == "わかった。少し待ってね。"


def test_select_phrase_where():
    """「どこだっけ」などの語尾で「調べてみるね。」が選択されることをテストする。"""
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    for word in [
        "どこ",
        "どこだっけ",
        "どこだっけ？",
        "どこだっけ?",
        "どこだったっけ",
        "どこだったっけ？",
        "どこですか",
        "どこですか？",
        "どこにある",
        "どこにある？",
    ]:
        response = client.post(
            "/select", json={"text": f"コンビニって{word}"}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["phrase"] == "調べてみるね。"


def test_select_phrase_naiyone():
    """「ないよね」などの語尾で「やっぱり調べてみるね。」が選択されることをテストする。"""
    headers = {"Authorization": f"Bearer {API_TOKEN}"}
    for word in ["ないよね", "ないよね？", "ないよね?"]:
        response = client.post(
            "/select", json={"text": f"ガソリンスタンドって{word}"}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["phrase"] == "調べてみるね。"

