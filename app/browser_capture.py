"""Capture the controlled page without activating its Chrome window."""
import base64


def screenshot_css_image(driver):
    """Return a stable viewport image in the CSS units used by CDP mouse input."""
    import io
    from PIL import Image
    script="return {width:innerWidth,height:innerHeight,dpr:devicePixelRatio,token:location.href+'|'+performance.timeOrigin}"
    before=driver.execute_script(script)
    data=screenshot_png(driver)
    after=driver.execute_script(script)
    if before!=after or not before or before['width']<=0 or before['height']<=0:
        raise RuntimeError('Viewport/zoom changed during button capture')
    return Image.open(io.BytesIO(data)).convert('RGB').resize((int(before['width']),int(before['height'])))


def screenshot_png(driver):
    result = driver.execute_cdp_cmd('Page.captureScreenshot', {
        'format': 'png', 'fromSurface': True, 'captureBeyondViewport': False,
    })
    return base64.b64decode(result['data'], validate=True)
