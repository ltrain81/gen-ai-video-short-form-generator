import pytest

from shorts import frames, render, safeurl


def test_safeurl_rejects_non_https_and_foreign_hosts():
    for bad in ("file:///etc/passwd", "http://bucket.s3.amazonaws.com/x", "https://evil.example.com/x", "ftp://x"):
        with pytest.raises(ValueError):
            safeurl.check(bad)
    assert safeurl.check("https://b.s3.us-east-1.amazonaws.com/k?X-Amz-Signature=1")
    assert safeurl.check("https://api.elevenlabs.io/v1", ("api.elevenlabs.io",))


def test_frames_and_render_refuse_unsafe_inputs():
    with pytest.raises(ValueError):
        frames.frame_at("file:///etc/passwd", 0)
    with pytest.raises(ValueError):
        render.run(["ffmpeg", "-i", "http://169.254.169.254/latest/meta-data", "out.mp4"])
    with pytest.raises(render.RenderError):
        render.run(["sh", "-c", "id"])


def test_contact_sheet_still_works(tmp_path):
    import subprocess
    src = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:d=3", str(src)], check=True)  # nosec B603
    orig = safeurl.check
    try:
        frames.safeurl.check = lambda u, *a, **k: u  # local file stands in for a presigned URL
        sheet = frames.contact_sheet(str(src), [0.5, 1.5, 2.5], cols=3, tile_width=160)
    finally:
        frames.safeurl.check = orig
    assert sheet[:3] == b"\xff\xd8\xff"
