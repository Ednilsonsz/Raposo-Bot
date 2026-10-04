import sys as _r11_sys
from pathlib import Path as _r11_Path
_r11_app = str(_r11_Path(__file__).resolve().parents[1] / "app")
if _r11_app not in _r11_sys.path:
    _r11_sys.path.insert(0, _r11_app)
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from main_v386_r11 import acknowledge_control, wait_for_bullex_workspace
from demo_executor_v386_r11 import DemoExecutor
from ui_overlay_massa_v386_r11 import BullexOverlay


options = Options()
options.add_argument('--headless=new')
options.add_argument('--window-size=1440,900')
options.add_argument('--disable-gpu')
driver = webdriver.Chrome(options=options)
try:
    driver.get('data:text/html;charset=utf-8,<html><head><style>body span{font-size:22px!important}body button{min-width:140px!important}</style></head><body style="margin:0;background:%23101922;color:white"><main id="broker" style="min-height:900px;padding:24px"><h1>BULLEX TRADEROOM</h1><canvas width="800" height="400" style="width:800px;height:400px;background:%231b2838"></canvas><div style="position:fixed;right:30px;top:300px"><button id="up" style="width:180px;height:55px;background:%2300a85a">ACIMA</button><button id="down" style="display:block;width:180px;height:55px;background:%23d13b55">ABAIXO</button></div></main></body></html>')
    assert wait_for_bullex_workspace(driver, timeout=3)
    executor = SimpleNamespace(armed=False, get_expiry_status=lambda: '1 MIN')
    analyzer = SimpleNamespace(paused=True, executor=executor, last_score=None, adaptive=None)
    cfg = {'asset_id': '76', '_asset_label': 'EURUSD-OTC', '_user_paused': True,
           '_dashboard_url': 'http://127.0.0.1:8765/dashboard.html?pin=123456',
           'demo_stake': 10, 'version': '3.86', 'revision': 'R11', 'build_revision': 'V3.86 · R11'}
    overlay = BullexOverlay(driver, cfg, analyzer)
    overlay.set_asset('76', 'EURUSD-OTC')
    overlay.update(force=True)
    contract = driver.execute_script("""
      const root=document.getElementById('bullex-pro-overlay'), broker=document.getElementById('broker');
      return {width:Math.round(root.getBoundingClientRect().width),brokerLeft:Math.round(broker.getBoundingClientRect().left),
        nav:[...root.querySelectorAll('#bxmodes button')].map(x=>x.textContent),restore:!!root.querySelector('#bxrestoreui'),
        embeddedLog:!!root.querySelector('#bxlogwrap'),upLeft:Math.round(document.getElementById('up').getBoundingClientRect().left),
        downLeft:Math.round(document.getElementById('down').getBoundingClientRect().left)};
    """)
    assert 340 <= contract['width'] <= 341 and contract['brokerLeft'] == 340, contract
    assert len(contract['nav']) == 3 and contract['nav'][1:] == ['LOG', 'DASHBOARD'], contract
    assert not contract['restore'] and not contract['embeddedLog'], contract
    assert contract['upLeft'] >= 340 and contract['downLeft'] >= 340, contract
    driver.set_window_size(1360, 650)
    overlay.update(force=True)
    compact = driver.execute_script("""
      const classic=document.getElementById('bxclassic'), nav=[...document.querySelectorAll('#bxmodes button')];
      return {rootWidth:Math.round(document.getElementById('bullex-pro-overlay').getBoundingClientRect().width),
        headerHeight:Math.round(document.getElementById('bxh').getBoundingClientRect().height),
        classicOverflow:classic.scrollHeight-classic.clientHeight,
        navWidths:nav.map(x=>Math.round(x.getBoundingClientRect().width)),
        assetFont:parseFloat(getComputedStyle(document.getElementById('bxasset')).fontSize)};
    """)
    assert 340 <= compact['rootWidth'] <= 341 and compact['headerHeight'] == 126, compact
    assert compact['classicOverflow'] <= 1 and max(compact['navWidths'])-min(compact['navWidths']) <= 1, compact
    assert compact['assetFont'] <= 11, compact
    locator = DemoExecutor(driver, cfg)
    assert locator.validate_order_buttons_startup(timeout=2), locator.safety_status()
    assert locator._probe_button_no_click('ACIMA') and locator._probe_button_no_click('ABAIXO')

    for index in range(20):
        driver.find_element('id', 'bxpause').click()
        command, command_id = driver.execute_script("const r=document.getElementById('bullex-pro-overlay');return [r.dataset.raposoCommand,r.dataset.raposoCommandId]")
        analyzer.paused = command == 'PAUSE'
        cfg['_user_paused'] = analyzer.paused
        acknowledge_control(driver, command_id, command, analyzer.paused)
        overlay.update(force=True)
        state = driver.execute_script("const b=document.getElementById('bxpause');return [b.textContent,b.disabled]")
        assert state == (['RETOMAR', False] if analyzer.paused else ['PAUSAR', False]), (index, command, state)

    driver.find_element('id', 'bxpause').click()
    time.sleep(2.7)
    timed_out = driver.find_element('id', 'bxpause').text
    assert timed_out == 'FALHA - TENTE NOVAMENTE', timed_out
    time.sleep(1.5)
    overlay.update(force=True)
    assert driver.find_element('id', 'bxpause').text == 'RETOMAR'

    driver.execute_script("document.querySelector('#bxmodes [data-mode=log]').click()")
    assert driver.find_element('id', 'bxlogview').is_displayed()
    opened = driver.execute_script("window.open=(u,n)=>{window.__openedDashboard=[u,n]};document.querySelector('#bxmodes [data-mode=dashboard]').click();return window.__openedDashboard")
    assert opened == ['http://127.0.0.1:8765/dashboard.html?pin=123456','raposo-r11-dashboard'], opened
    assert driver.execute_script("return document.getElementById('bullex-pro-overlay').dataset.uiMode") == 'classic'
    screenshot = ROOT/'V386_R11_INTERFACE_SMOKE.png'
    driver.save_screenshot(str(screenshot))
    print(f'BROWSER_SMOKE_OK screenshot={screenshot}')
finally:
    driver.quit()


