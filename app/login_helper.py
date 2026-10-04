import time
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


def _visible(driver, selectors):
    for by, sel in selectors:
        try:
            for e in driver.find_elements(by, sel):
                if e.is_displayed() and e.is_enabled():
                    return e
        except Exception:
            pass
    return None


def auto_fill_login(driver, username, password, timeout=8):
    """Preenche login somente se campos visíveis forem encontrados. Nunca imprime credenciais."""
    if not username:
        return False
    end = time.time() + timeout
    while time.time() < end:
        user = _visible(driver, [
            (By.CSS_SELECTOR, "input[type='email']"),
            (By.CSS_SELECTOR, "input[name*='email' i]"),
            (By.CSS_SELECTOR, "input[name*='user' i]"),
            (By.CSS_SELECTOR, "input[type='text']"),
        ])
        pwd = _visible(driver, [(By.CSS_SELECTOR, "input[type='password']")])
        if user:
            try:
                user.click(); user.send_keys(Keys.CONTROL, 'a'); user.send_keys(username)
                if not password:
                    return True
                if pwd:
                    pwd.click(); pwd.send_keys(Keys.CONTROL, 'a'); pwd.send_keys(password)
                    submit = _visible(driver, [
                        (By.CSS_SELECTOR, "button[type='submit']"),
                        (By.XPATH, "//button[contains(translate(.,'ENTRARLOGINACESSAR','entrarloginacessar'),'entrar') or contains(translate(.,'LOGIN','login'),'login') or contains(translate(.,'ACESSAR','acessar'),'acessar')]"),
                    ])
                    if submit:
                        submit.click()
                        return True
                    pwd.send_keys(Keys.ENTER)
                    return True
                return True
            except Exception:
                return False
        time.sleep(.4)
    return False
