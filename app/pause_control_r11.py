"""Shared pause request handling. No order submission or automatic resume."""

def select_command(overlay, remote):
    commands={str(c or '').upper() for c in (overlay,remote)}
    if 'ARM' in commands:
        commands.add('RESUME')
    # A concurrent pause always wins over a resume.
    return next((c for c in ('VOICE_ON','VOICE_OFF','STOP','RESTART','PAUSE','RESUME') if c in commands),None)


def synchronize_pause(driver, paused, command, analyzer=None, executor=None):
    def hold():
        if analyzer is not None:
            analyzer.paused=True
        if executor is not None:
            executor.disarm()

    if command in ('PAUSE','STOP','RESTART'):
        requested=True
        hold()  # Apply before browser I/O; a JS failure must not cancel a pause.
    elif command=='RESUME':
        requested=False
    else:
        try:
            value=driver.execute_script("return (typeof window.__RAPOSO_PAUSE_REQUEST__ === 'boolean') ? window.__RAPOSO_PAUSE_REQUEST__ : null;")
            requested=paused if value is None else bool(value)
        except Exception:
            requested=True
    try:
        driver.execute_script('window.__RAPOSO_PAUSE_REQUEST__=arguments[0];',bool(requested))
    except Exception:
        requested=True
    if requested:
        hold()
    # Resume is completed in main only after setting the next-candle barrier.
    return requested
