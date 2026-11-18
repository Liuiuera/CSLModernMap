"""Keep renderer tests independent of personal settings and POI mappings."""
import pytest

from csmodernmap import i18n


@pytest.fixture(autouse=True)
def isolated_user_data(monkeypatch, tmp_path):
    monkeypatch.setenv("CSLMM_USER_DATA_DIR", str(tmp_path / "UserData"))
    language = i18n.LANG
    i18n.set_lang("zh-CN")
    yield
    i18n.set_lang(language)
