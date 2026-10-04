from browser_capture import screenshot_png
"""Read current rendered broker cards, including canvas, without clicking."""
import io
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unicodedata


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text)
                   if unicodedata.category(c) != 'Mn').lower().strip()


def parse_money(text):
    """Parse a broker-rendered dollar amount without guessing malformed OCR."""
    match = re.fullmatch(r'\s*\$\s*(\d{1,3}(?:(?:[.,])\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*', text)
    if not match:
        return None
    value = match.group(1)
    if ',' in value and '.' in value:
        decimal = ',' if value.rfind(',') > value.rfind('.') else '.'
        thousands = '.' if decimal == ',' else ','
        value = value.replace(thousands, '').replace(decimal, '.')
    elif ',' in value:
        value = value.replace(',', '.') if len(value.rsplit(',', 1)[1]) <= 2 else value.replace(',', '')
    elif '.' in value and len(value.rsplit('.', 1)[1]) == 3:
        value = value.replace('.', '')
    try:
        return float(value)
    except ValueError:
        return None


def parse_words(words):
    """Coordinates are CSS pixels relative to the right-hand screenshot crop."""
    rows = []
    for word in sorted(words, key=lambda w: (float(w['y']), float(w['x']))):
        cy = float(word['y']) + float(word['height']) / 2
        row = next((r for r in rows if abs(r['cy'] - cy) <= 6), None)
        if row is None:
            row = {'cy': cy, 'words': []}
            rows.append(row)
        row['words'].append(word)
    for row in rows:
        row['words'].sort(key=lambda w: float(w['x']))
        row['text'] = ' '.join(w['text'] for w in row['words'])
        row['x'] = min(float(w['x']) for w in row['words'])
    # O saldo da conta não participa da validação nem do cálculo de resultado.
    result = {'amount': None, 'expiry': None, 'source': 'SCREEN_OCR'}
    invest = [r for r in rows if normalize(r['text']) in ('invest', 'investimento', 'investment')]
    expiry = [r for r in rows if normalize(r['text']) in ('expiracao', 'expiration')]
    # Both layouts require a unique broker investment label.
    if len(invest) != 1 or len(expiry) > 1:
        return result
    a = invest[0]
    def below(label, limit):
        return [r for r in rows if 7 < r['cy'] - label['cy'] < limit
                and abs(r['x'] - label['x']) < 35]
    if expiry:
        e = expiry[0]
        if not (25 < e['cy'] - a['cy'] < 120 and abs(e['x'] - a['x']) < 30):
            return result
        amount_rows = below(a, min(48, e['cy'] - a['cy'] - 5))
        expiry_rows = below(e, 48)
    else:
        # Compact Bullex card: investment, then a numeric duration without a title.
        # Restrict to the adjacent right-hand card; never search the full page.
        amount_rows = below(a, 32)
        expiry_rows = [r for r in rows if 32 < r['cy'] - a['cy'] < 72
                       and 15 < r['x'] - a['x'] < 40]
    if len(amount_rows) == 1:
        result['amount'] = parse_money(amount_rows[0]['text'])
    if len(expiry_rows) == 1:
        match = re.fullmatch(r'\s*(\d+)\s*(min(?:uto)?s?|s|seg(?:undo)?s?)\s*', normalize(expiry_rows[0]['text']))
        if match:
            result['expiry'] = match[1] + (' MIN' if match[2].startswith('min') else ' SEG')
    return result


def ocr_image(image):
    """Run the installed Windows OCR engine on an enlarged local image."""
    script_path = Path(__file__).with_name('bullex_controls_ocr.ps1')
    if not script_path.is_file():
        # R11 keeps support scripts under Raposo/scripts rather than app/.
        script_path = Path(__file__).resolve().parent.parent / 'scripts' / 'bullex_controls_ocr.ps1'
    script = script_path.read_text(encoding='utf-8-sig')
    with tempfile.TemporaryDirectory(prefix='bullex_controls_') as folder:
        path = Path(folder) / 'controls.png'
        image.resize((image.width * 3, image.height * 3)).save(path)
        env = dict(os.environ, BULLEX_OCR_IMAGE=str(path))
        proc = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', '-'],
                              input=script, text=True, encoding="utf-8", capture_output=True, env=env,
                              timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if proc.returncode or proc.stderr.strip():
            raise RuntimeError('Windows OCR failed: ' + proc.stderr[-300:])
        words = json.loads(proc.stdout.strip().lstrip('\ufeff'))
        for word in words:
            for key in ('x', 'y', 'width', 'height'):
                word[key] = float(word[key]) / 3
        return words


def remove_known_caret(image):
    """Remove only the observed one-pixel purple caret, never OCR digits.

    Bullex's historical screenshot has a uniform RGB(140,120,251) vertical
    caret 18 CSS pixels high. Require an isolated straight line of that color;
    different themes/shapes are left intact and may consequently block reading.
    """
    image = image.copy()
    pixels = image.load()
    color = (140, 120, 251)
    for x in range(1, image.width - 1):
        y = 0
        while y < image.height:
            if pixels[x, y] != color:
                y += 1
                continue
            start = y
            while y < image.height and pixels[x, y] == color:
                y += 1
            if 16 <= y - start <= 20 and all(
                    pixels[x-1, row] != color and pixels[x+1, row] != color
                    for row in range(start, y)):
                for row in range(start, y):
                    pixels[x, row] = pixels[x+1, row]
    return image


_CONTROL_STATE_JS = """
      const r = document.querySelector('#bullex-pro-overlay');
      return {token:location.href+'|'+performance.timeOrigin,
        width:innerWidth,height:innerHeight,dpr:devicePixelRatio,
        overlay:r?{x:r.getBoundingClientRect().x,y:r.getBoundingClientRect().y,
          width:r.getBoundingClientRect().width,height:r.getBoundingClientRect().height}:null};
    """
def _capture_control_cards(driver, crop_height=400):
    from PIL import Image, ImageDraw
    started = time.monotonic()
    before = driver.execute_script(_CONTROL_STATE_JS)
    image = Image.open(io.BytesIO(screenshot_png(driver))).convert('RGB')
    after = driver.execute_script(_CONTROL_STATE_JS)
    if before != after or not before or before['width'] < 400:
        raise RuntimeError('Viewport/document changed during capture')
    # Normalize screenshot pixels to CSS units, then mask the entire Raposo panel.
    image = image.resize((int(before['width']), int(before['height'])))
    overlay = before.get('overlay')
    if overlay:
        x, y, w, h = (overlay[k] for k in ('x', 'y', 'width', 'height'))
        ImageDraw.Draw(image).rectangle((x-3, y-3, x+w+3, y+h+3), fill='black')
    image = image.crop((int(image.width * .75), 0, image.width, min(image.height, crop_height) if crop_height is not None else image.height))
    image = remove_known_caret(image)
    if time.monotonic() - started > 30:
        raise RuntimeError('Control capture stale')
    return image, before, started


def verify_controls_snapshot(driver, cache):
    """Fresh pixel proof without OCR delays or a remembered confirmation."""
    if not cache or cache.get('result', {}).get('expiry') != '1 MIN':
        return None
    image, state, started = _capture_control_cards(driver)
    account_box = (0, 0, image.width, min(image.height, 70))
    if (cache.get('state') != state or
            hashlib.sha256(image.crop(cache['box']).tobytes()).hexdigest() != cache.get('pixels') or
            hashlib.sha256(image.crop(account_box).tobytes()).hexdigest() != cache.get('account_pixels')):
        return None
    if time.monotonic() - started > 4:
        return None
    result = dict(cache['result'])
    result.update(source='SCREEN_PIXELS_VERIFIED_PRECLICK', captured_at=started)
    return result


def rendered_expiry_dom(driver, words, viewport_width):
    """Exact visible duration anchored to the recognized broker card, never chart M1."""
    labels=[w for w in words if normalize(w['text']) in ('expiracao','expiration')]
    invests=[w for w in words if normalize(w['text']) in ('invest','investimento','investment')]
    if len(labels)==1:
        a=labels[0]; y0=a['y']+a['height']+2; y1=y0+40
    elif not labels and len(invests)==1:
        a=invests[0]; y0=a['y']+32; y1=a['y']+96
    else:return None
    x0=viewport_width*.75+a['x']-5; x1=x0+115
    return driver.execute_script(r"""
      const [x0,x1,y0,y1]=arguments;
      const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>8&&r.height>7&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity)>0};
      if([...document.querySelectorAll('[role=listbox],[aria-expanded=true]')].some(visible))return null;
      const matches=[];
      for(const e of document.querySelectorAll('span,div,button,input')){
        if(e.closest('#bullex-pro-overlay')||!visible(e))continue;
        const r=e.getBoundingClientRect();
        if(r.left<x0||r.right>x1||r.top<y0||r.bottom>y1)continue;
        const t=String(e.value||e.innerText||'').trim().toLowerCase().replace(/\s+/g,' ');
        if(!/^(1\s*min(?:uto)?s?|5\s*(s|seg(?:undo)?s?))$/.test(t))continue;
        matches.push({e,t});
      }
      const leaves=matches.filter(a=>!matches.some(b=>b.e!==a.e&&a.e.contains(b.e)));
      if(leaves.length!==1)return null;
      return leaves[0].t.startsWith('1')?'1 MIN':'5 SEG';
    """,x0,x1,y0,y1)

def read_controls(driver, cache=None):
    image, before, started = _capture_control_cards(driver)
    def fingerprint(box):
        return hashlib.sha256(image.crop(box).tobytes()).hexdigest()
    # Reuse OCR only when a NEW screenshot proves the complete labeled cards
    # are pixel-identical in the same document and viewport. This is not a
    # timer-based assumption that controls remained unchanged.
    account_box = (0, 0, image.width, min(image.height, 70))
    if (cache and cache.get('state') == before
            and fingerprint(cache['box']) == cache['pixels']
            and fingerprint(account_box) == cache.get('account_pixels')):
        result = dict(cache['result'])
        result['source'] = 'SCREEN_PIXELS_VERIFIED'
        result['captured_at'] = started
        return result
    words = ocr_image(image)
    # The flag icon immediately before expiration is not text. Re-read only
    # the value region located from the recognized label, never fixed screen x/y.
    labels = [w for w in words if normalize(w['text']) in ('expiracao', 'expiration')]
    invest_words = [w for w in words if normalize(w['text']) in ('invest', 'investimento', 'investment')]
    if len(labels) == len(invest_words) == 1:
        left = min(labels[0]['x'], invest_words[0]['x']) - 5
        right = max(labels[0]['x'], invest_words[0]['x']) + 100
        words = [w for w in words if left <= w['x'] <= right]
    def refine_value(label, x_offset, width, height=32):
        # Re-read the value directly below a recognized broker-card label.
        # The crop is anchored to the live label, never to fixed screen coordinates.
        x, y = int(label['x']), int(label['y'] + label['height'])
        box = (max(0, x + x_offset), y + 2, min(image.width, x + width), min(image.height, y + height))
        if box[2] <= box[0] or box[3] <= box[1]:
            return
        refined = ocr_image(image.crop(box))
        # Replace only a fully recognized value. Never coerce I/T into 1.
        centers = [w['y'] + w['height']/2 for w in refined]
        if not centers or max(centers)-min(centers) > 6:
            return
        text = ' '.join(w['text'] for w in sorted(refined, key=lambda w: w['x']))
        pattern = (r'\s*\d+\s*(?:min(?:uto)?s?|s|seg(?:undo)?s?)\s*'
                   if x_offset > 0 else r'\s*\$\s*\d+(?:[.,]\d{1,2})?\s*')
        if not re.fullmatch(pattern, normalize(text)):
            return
        # The expiry crop excludes the flag, so also remove its old OCR words
        # from the value band. Otherwise 'pa 1 min' fails the strict parser.
        replace_left = max(0, x - 4) if x_offset > 0 else box[0]
        words[:] = [w for w in words if not (replace_left <= w['x'] < box[2] and box[1] <= w['y'] < box[3])]
        for word in refined:
            word['x'] += box[0]
            word['y'] += box[1]
        words.extend(refined)

    preliminary=parse_words(words)
    # Only refine a field OCR has not already recognized unambiguously.
    if len(labels) == 1 and preliminary.get('expiry') is None:
        refine_value(labels[0], 19, 85)
    elif not labels and len(invest_words) == 1 and preliminary.get('expiry') is None:
        # The compact card omits 'Expiracao'. Locate its visible duration unit,
        # then reread the adjacent number; I/T are never accepted as digits.
        invest = invest_words[0]
        units = [w for w in words
                 if re.fullmatch(r'min(?:uto)?s?|s|seg(?:undo)?s?', normalize(w['text']))
                 and 32 < w['y'] - invest['y'] < 72
                 and 15 < w['x'] - invest['x'] < 90]
        if len(units) == 1:
            anchor = {'x': invest['x'], 'y': units[0]['y'] - 4, 'height': 0}
            refine_value(anchor, 19, 85, height=24)
    # Investment has no reliable icon/text separator. Re-read the full value line
    # because Windows OCR can recognize the label but drop the small '$ 10'.
    if len(invest_words) == 1 and preliminary.get('amount') is None:
        refine_value(invest_words[0], -4, 82)
    result = parse_words(words)
    if result.get('expiry') is None:
        result['expiry']=rendered_expiry_dom(driver,words,before['width'])
    if time.monotonic() - started > 30 or driver.execute_script(_CONTROL_STATE_JS) != before:
        raise RuntimeError('Control capture stale or document changed')
    result['captured_at'] = started
    invest_labels = [w for w in words if normalize(w['text']) in ('invest', 'investimento', 'investment')]
    if cache is not None and len(invest_labels) == 1 and len(labels) <= 1 and result['amount'] is not None and result['expiry'] is not None:
        a = invest_labels[0]
        e = labels[0] if labels else a
        box = (max(0, int(min(a['x'], e['x'])) - 12), max(0, int(a['y']) - 10),
               min(image.width, int(max(a['x'], e['x'])) + 100), min(image.height, int(e['y']) + (46 if labels else 95)))
        cache.clear()
        cache.update(state=before, box=box, pixels=fingerprint(box),
                     account_pixels=fingerprint(account_box), result=dict(result))
        # OCR can exceed four seconds: revalidate the recognized card against
        # a NEW screenshot rather than presenting an old capture as fresh.
        verified=verify_controls_snapshot(driver,cache)
        if verified:
            result.update(verified)
    return result
