from modules.linkedin import is_logged_in_url, truncate_message

def test_is_logged_in_url_false_on_login_page():
    assert is_logged_in_url("https://www.linkedin.com/login") is False

def test_is_logged_in_url_false_on_checkpoint():
    assert is_logged_in_url("https://www.linkedin.com/checkpoint/lg/login") is False

def test_is_logged_in_url_true_on_feed():
    assert is_logged_in_url("https://www.linkedin.com/feed/") is True

def test_truncate_message_under_limit():
    msg = "Short message"
    assert truncate_message(msg, 300) == msg

def test_truncate_message_truncates_at_word():
    msg = "word " * 100
    result = truncate_message(msg, 50)
    assert len(result) <= 53
    assert result.endswith("...")
