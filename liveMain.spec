# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['page/liveMain.py'],
    pathex=[],
    binaries=[('D:/mayun/live-gui/venv/Lib/site-packages/py_mini_racer/mini_racer.dll', '.'), ('D:/mayun/live-gui/venv/Lib/site-packages/py_mini_racer/icudtl.dat', '.'), ('D:/mayun/live-gui/venv/Lib/site-packages/py_mini_racer/snapshot_blob.bin', '.')],
    datas=[('js/sign.js', 'js'), ('js/a_bogus.js', 'js'), ('utils/ac_signature.py', 'utils'), ('scripts/restart_ws.sh', 'scripts'), ('scripts/kill_8765.sh', 'scripts'), ('scripts/kill_8765.ps1', 'scripts'), ('protobuf', 'protobuf'), ('assets/images/printer3d_bg.jpg', 'assets/images'), ('assets/images/printer3d_bg_noh.jpg', 'assets/images'), ('assets/images/printer3d_head.png', 'assets/images'), ('assets/images/printer3d_arm_patch.png', 'assets/images'), ('assets/images/printer3d_arm_base.png', 'assets/images'), ('assets/images/printer3d_arm_lower.png', 'assets/images'), ('assets/images/printer3d_arm_upper.png', 'assets/images'), ('assets/images/printer3d_arm_joint.png', 'assets/images'), ('assets/images/printer3d_arm_grip.png', 'assets/images')],
    hiddenimports=['execjs', 'requests', 'protobuf', 'protobuf.douyin', 'usb.core', 'usb.backend.libusb1', 'server.live_server',
                   'utils.dy_cookie_import', 'utils.console_safe', 'page.printer3d', 'browser_cookie3', 'shadowcopy', 'wmi',
                   'win32crypt', 'win32api', 'Cryptodome', 'lz4'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='liveMain',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['favicon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='liveMain',
)
