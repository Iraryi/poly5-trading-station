from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parent
url=(root/'interactive_mock.html').as_uri()
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1536,'height':940},device_scale_factor=1)
    failures=[]
    page.on('pageerror',lambda err:failures.append(str(err)))
    page.set_content((root/'interactive_mock.html').read_text(encoding='utf8'),wait_until='domcontentloaded')
    assert page.get_by_text('现金余额',exact=True).count()>=1
    assert page.locator('#page-account').is_hidden()
    page.locator('[data-page="account"]').click()
    assert page.get_by_text('账户中心',exact=True).count()>=1
    assert page.locator('#page-account').is_visible()
    assert page.locator('#page-account').get_by_text('—',exact=True).count()>=1
    page.screenshot(path=str(root/'account_preview_v3.png'),full_page=True)
    page.locator('[data-page="orders"]').click()
    assert page.locator('#page-orders').is_visible()
    page.locator('[data-subtab="positions"]').click()
    assert page.locator('[data-subpanel="positions"]').is_visible()
    assert page.locator('[data-subpanel="active"]').is_hidden()
    page.locator('[data-page="modules"]').click()
    assert page.locator('#page-modules').is_visible()
    assert page.get_by_text('未来 · 未实现').count()>=2
    page.locator('[data-page="overview"]').click()
    assert page.locator('#page-overview').is_visible()
    page.locator('.jump[data-target="orders"]').first.click()
    assert page.locator('#page-orders').is_visible()
    page.locator('#toggle').click()
    assert page.locator('#copilot').evaluate('(e)=>e.classList.contains("hidden")')
    page.locator('#openhandle').click()
    assert not page.locator('#copilot').evaluate('(e)=>e.classList.contains("hidden")')
    print('GUI CHECKS: 11 user flows verified')
    print('JS errors:',failures)
    if failures: raise AssertionError(failures)
    browser.close()
