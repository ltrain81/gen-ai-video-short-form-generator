from shorts.renders import proxy_size


def test_proxy_size_matches_make_proxy():
    assert proxy_size(1920, 1080) == (1280, 720)
    assert proxy_size(1280, 720) == (1280, 720)
    assert proxy_size(3840, 2160) == (1280, 720)
    assert proxy_size(1080, 1920) == (720, 1280)
    assert proxy_size(2000, 2000) == (1280, 1280)
    assert proxy_size(640, 361) == (640, 362)
