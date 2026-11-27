"""Check the first paint and return links with a deliberately delayed session check."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import browser_smoke as runner


def check_browser(url, directory):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as playwright:
        use_webkit = '--webkit' in sys.argv
        browser = (playwright.webkit.launch(headless=True) if use_webkit
                   else playwright.chromium.launch(channel='chrome', headless=True))
        try:
            device = playwright.devices['iPhone 13'] if use_webkit else {}
            context = browser.new_context(**device)
            page = context.new_page()
            page.add_init_script('''
                if (location.pathname === '/') {
                    window.sessionChecks = 0;
                    window.loginWasVisible = false;
                    const originalFetch = window.fetch.bind(window);
                    window.fetch = async (input, options) => {
                        const response = await originalFetch(input, options);
                        if (new URL(input, location.href).pathname === '/api/auth/session/') {
                            await new Promise(resolve => {
                                window.releaseSessionCheck = resolve;
                                window.sessionChecks += 1;
                            });
                        }
                        return response;
                    };
                    const observeLogin = () => {
                        const login = document.getElementById('loginForm');
                        if (login?.getClientRects().length && getComputedStyle(login).visibility === 'visible') {
                            window.loginWasVisible = true;
                        }
                        requestAnimationFrame(observeLogin);
                    };
                    requestAnimationFrame(observeLogin);
                }
            ''')
            loading = page.locator('#sessionLoading')
            login = page.locator('#loginForm')
            menu = page.locator('#viewMainMenu')

            def assert_loading(check_number):
                page.wait_for_function('count => window.sessionChecks === count', arg=check_number)
                expect(loading).to_be_visible()
                expect(menu).to_be_hidden()
                expect(login).to_be_hidden()
                assert loading.locator('.spinner-border').evaluate('''async spinner => {
                    const start = getComputedStyle(spinner).transform;
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    return getComputedStyle(spinner).transform !== start;
                }'''), 'The loading circle did not rotate.'
                assert not page.evaluate('window.loginWasVisible')

            page.goto(url)
            assert_loading(1)
            page.evaluate('window.releaseSessionCheck()')
            expect(login).to_be_visible()
            expect(loading).to_be_hidden()
            runner.login(page, 'admin', open_import=False)
            expect(menu).to_be_visible()

            for link, path in (('a[href="/notificaciones/"]', 'notificaciones/'), ('#btnAdminister', 'administracion/')):
                menu.locator(link).click()
                expect(page).to_have_url(url + path)
                page.get_by_role('link', name='← Volver a los planos').click()
                assert_loading(1)
                if path == 'notificaciones/':
                    page.evaluate("window.dispatchEvent(new Event('app-session-changed'))")
                    page.evaluate('window.releaseSessionCheck()')
                    assert_loading(2)
                page.evaluate('window.releaseSessionCheck()')
                expect(menu).to_be_visible()
                expect(loading).to_be_hidden()
                assert not page.evaluate('window.loginWasVisible')

            page.wait_for_function('navigator.serviceWorker.controller !== null')
            context.set_offline(True)
            page.reload(wait_until='domcontentloaded')
            expect(menu).to_be_visible()
            expect(loading).to_be_hidden()
            assert not page.evaluate('window.loginWasVisible')
            print('PASS: loading spinner on return from both sections, hidden login, and offline session startup')
        finally:
            browser.close()


if __name__ == '__main__':
    runner.check_browser = check_browser
    runner.main()
