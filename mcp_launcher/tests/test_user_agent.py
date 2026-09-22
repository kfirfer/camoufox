from mcp_launcher.user_agent import clean_user_agent, needs_ua_refresh
def test_clean_ua_152():
    assert clean_user_agent(152) == ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) "
                                     "Gecko/20100101 Firefox/152.0")
def test_refresh_on_stale_major():   assert needs_ua_refresh(clean_user_agent(146), 152)
def test_refresh_on_brand_leak():    assert needs_ua_refresh("Mozilla/5.0 rv:152.0 Camoufox/152.0.4", 152)
def test_refresh_on_missing():       assert needs_ua_refresh("", 152)
def test_keep_matching():            assert not needs_ua_refresh(clean_user_agent(152), 152)
