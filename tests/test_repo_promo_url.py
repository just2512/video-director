"""链接 → 内容（不落地大文件）。

GitHub 仓库地址 → raw README 文本直链（不 clone、不占硬盘——100 个地址
就是 100 次 HTTP 抓单文件）；其余任意网页走既有 import_article 抓取链。
"""
from video_director import repo_promo


def test_is_github_url():
    assert repo_promo.is_github_url("https://github.com/owner/repo")
    assert repo_promo.is_github_url("https://github.com/owner/repo.git")
    assert repo_promo.is_github_url("https://www.github.com/o/r")
    assert not repo_promo.is_github_url("D:/Projects/my-app")
    assert not repo_promo.is_github_url("https://gitlab.com/o/r")
    assert not repo_promo.is_github_url("")


def test_github_url_maps_to_raw_readme():
    assert (repo_promo.github_readme_url("https://github.com/octo/hello")
            == "https://raw.githubusercontent.com/octo/hello/HEAD/README.md")
    assert (repo_promo.github_readme_url("https://github.com/octo/hello.git")
            == "https://raw.githubusercontent.com/octo/hello/HEAD/README.md")
    assert (repo_promo.github_readme_url("https://www.github.com/a/b/")
            == "https://raw.githubusercontent.com/a/b/HEAD/README.md")


def test_non_github_url_passthrough():
    assert (repo_promo.github_readme_url("https://example.com/article")
            == "https://example.com/article")
    assert repo_promo.github_readme_url("") == ""


def test_article_endpoint_converts_github_url():
    """/api/source/article 收到 GitHub 地址必须转 raw README 再抓。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "webui" / "server.py").read_text(encoding="utf-8")
    assert "github_readme_url" in src
    assert "raw.githubusercontent.com" not in src  # 转换逻辑在 repo_promo，不在 server 硬编码