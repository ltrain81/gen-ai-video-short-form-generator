from shorts.render import layout_filter, parse_background, gradient_source


def test_source_region_is_cut_first():
    g = layout_filter("box", 1280, 720, 1080, 1920, [], 0.0,
                      {"box": {"x": 0, "y": 0.22, "w": 1, "h": 0.5625}, "source": {"x": 0.4, "y": 0, "w": 0.5625, "h": 1}})
    assert g.startswith("[0:v]crop=720:720:")
    assert "split=2" in g and g.endswith("[v]")


def test_box_zoom_and_gradient():
    g = layout_filter("box", 1280, 720, 1080, 1920, [], 0.0,
                      {"box": {"x": 0, "y": 0.22, "w": 1, "h": 0.5625}, "background": "gradient:#0b2a4a,#1fbf8f@160"},
                      ("1.2", "0.5", "0.5"))
    assert "gradients=s=1080x1920" in g and "eval=frame" in g and "crop=1080:1080" in g


def test_contain_box_has_exact_size():
    g = layout_filter("box", 1920, 1080, 1080, 1920, [], 0.0, {"box": {"w": 1, "h": 0.5}, "fit": "contain"})
    assert "scale=1080:606" in g


def test_native_layer_keeps_aspect():
    assert layout_filter("native", 720, 720, 1080, 1920, [], 0.0) == "[0:v]scale=1080:1080[v]"


def test_background_specs():
    assert parse_background("blur:40") == {"type": "blur", "amount": 40.0}
    assert parse_background("#112233") == {"type": "color", "color": "#112233"}
    b = parse_background({"colors": ["#000", "#fff"], "angle": 90})
    assert b["type"] == "gradient"
    assert "c1=0xffffff" in gradient_source(100, 200, b["colors"], 90)
