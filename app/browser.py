from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from pathlib import Path
import os

def start_bullex(url, startup_script=None):
    o = Options()
    chrome_exe = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if chrome_exe.is_file():
        o.binary_location = str(chrome_exe)
    o.add_argument("--start-maximized")
    if os.environ.get('RAPOSO_UI_VARIANT') == 'premium':
        # Keep broker controls at native 100% for reliable OCR.
        o.add_experimental_option('prefs',{'partition.default_zoom_level':{'x':0.0}})
    # Remove Chrome's automation infobar to recover viewport height.
    # Performance logging/CDP capture remain configured below.
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    d = webdriver.Chrome(options=o)
    try:
        try:
            d.execute_cdp_cmd("Network.enable", {})
        except Exception:
            pass
        if startup_script:
            d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source":startup_script})
        d.get(url)
        return d
    except Exception:
        # Navigation errors such as ERR_NAME_NOT_RESOLVED must not leave an
        # orphaned Chrome/ChromeDriver process behind after startup aborts.
        try:
            d.quit()
        except Exception:
            pass
        raise
