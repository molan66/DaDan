# -*- mode: python ; coding: utf-8 -*-
# ============================================================
#  build.spec - PyInstaller 打包配置（大蛋小助手）
#  说明：本 spec 全程使用相对路径（基于 SPECPATH），换机器/换目录均可直接打包：
#    1. icon 使用相对路径，避免换电脑后路径失效
#    2. datas 同时打包 icon_cache 目录下的预热头像（如有）
#    3. 显式声明 requests/websocket-client 的隐藏依赖
# ============================================================

import os
import json
block_cipher = None

# 路径均相对当前 spec 所在目录（更便于移植）
spec_dir = os.path.abspath(SPECPATH)
icon_ico = os.path.join(spec_dir, 'data', 'app_icon.ico')
icon_png = os.path.join(spec_dir, 'data', 'app_icon.png')

# ============ v4.4.0：生成 Windows 版本资源 ============
# 此前 EXE() 一直没有 version= 参数，打出来的 exe「属性 → 详细信息」里
# 文件版本/产品版本/公司名全是空白（实测 Get-AuthenticodeSignature = NotSigned，
# FileVersion 为 ''），用户拿到文件无法自证版本，排障时只能靠界面上那行小字。
# 这里在打包时从 version.json（面向用户的更新日志，同时也是版本号来源）读取版本号，
# 现场生成 PyInstaller 的 version_info 文本 —— 不引入额外手工维护的第二份版本号。
_version_file = os.path.join(spec_dir, 'build', 'version_info.txt')


def _load_app_version():
    """版本号来源优先级：version.json → core/updater.py 的 APP_VERSION → 0.0.0。"""
    try:
        with open(os.path.join(spec_dir, 'version.json'), 'r',
                  encoding='utf-8') as f:
            v = str(json.load(f).get('version') or '').strip()
        if v:
            return v
    except Exception:
        pass
    try:
        with open(os.path.join(spec_dir, 'core', 'updater.py'), 'r',
                  encoding='utf-8') as f:
            for line in f:
                if line.startswith('APP_VERSION'):
                    return line.split('=', 1)[1].strip().strip('\'"')
    except Exception:
        pass
    return '0.0.0'


_app_version = _load_app_version()
# Windows 的 FixedFileInfo 要求四段数值，多退少补
_quad_parts = [p for p in _app_version.split('.') if p.isdigit()][:4]
while len(_quad_parts) < 4:
    _quad_parts.append('0')
_quad = ', '.join(_quad_parts)

# 语言与代码页必须与 VarStruct 的 Translation 一致：0x0804=简体中文，1200=UTF-16
_VERSION_INFO_TXT = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(%(quad)s),
    prodvers=(%(quad)s),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404b0',
        [StringStruct('CompanyName', '大蛋小助手'),
         StringStruct('FileDescription', '大蛋小助手 - 英雄联盟客户端助手'),
         StringStruct('FileVersion', '%(ver)s'),
         StringStruct('InternalName', 'dadan-assistant'),
         StringStruct('LegalCopyright', 'GNU AGPL-3.0-or-later'),
         StringStruct('OriginalFilename', '大蛋小助手.exe'),
         StringStruct('ProductName', '大蛋小助手'),
         StringStruct('ProductVersion', '%(ver)s')])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""

try:
    os.makedirs(os.path.dirname(_version_file), exist_ok=True)
    with open(_version_file, 'w', encoding='utf-8') as _vf:
        _vf.write(_VERSION_INFO_TXT % {'quad': _quad, 'ver': _app_version})
    version_file = _version_file
except Exception as _e:          # 生成失败不能让打包直接挂掉，退化为无版本资源
    print('[build.spec] 版本资源生成失败, 本次打包含无版本信息:', _e)
    version_file = None

# datas: 列表里每一项是 (源路径相对/绝对, 打包后目录)
datas = []
# v4.4.0(P2 修复): 原先 png 与 ico 都打包, 但运行时 `main.py:_make_icon` 的
# 查找顺序是 ['data/app_icon.png', 'data/app_icon.ico'] —— png 恒先命中, 打进
# exe 的 app_icon.ico **永不生效**(托盘图标亦只用 png)。ico 仍然必须留在仓库
# data/ 下: 它是 `icon=exe_icon`(见文件末尾 EXE(...)) 的 exe 图标来源, 但与
# 运行时资源无关。故这里只打包 png; 万一 png 缺失/损坏, _make_icon 已有
# 手绘 64x64 兜底, 不会出现无图标。
if os.path.isfile(icon_png):
    datas.append((icon_png, 'data'))

# v16:英雄元数据缓存必须打包 —— 否则单文件 exe 离线启动时 _MEIPASS/data/
# 下没有 champion_cache.json,_load_cache 返回空 → 选择器永远"加载中"
champ_cache = os.path.join(spec_dir, 'data', 'champion_cache.json')
if os.path.isfile(champ_cache):
    datas.append((champ_cache, 'data'))
# v4.4.0: 删掉 champion_aliases.py / champion_pinyin.py 的 datas 条目。
# 这两个是**被 import 的模块**而非按路径读取的数据文件：
#   data/champion_aliases.py ← lcu/api.py:_champion_alias() 与 ui/champion_picker.py
#   data/champion_pinyin.py  ← ui/champion_picker.py 的搜索索引
# 实测旧 exe 的归档里它们**同时**出现在 PYZ（编译字节码）与 CArchive（原始 .py 源
# 文件，约 24KB）—— 前者才是运行时真正加载的，后者是纯冗余（且把源码明文带进 exe）。
# 已用 CArchiveReader/ZlibArchiveReader 确认 PyInstaller 的静态分析确实收进了 PYZ，
# 故 datas 条目可安全删除。若将来新增"按路径读取"的 data/*.py 才需要重新加回。

# v18:不再把 icon_cache 打包进 exe —— PyInstaller onefile 每次启动需把全部
# datas 解压到 _MEIPASS,236 张 PNG/6.9MB 是冷启动的主要浪费。运行时图标
# 读写本就落在 exe 同级 data/icon_cache(_get_icon_cache_dir),打包副本冗余。
# 首次联网连客户端后图标自动下载落盘;本机已有缓存不受影响。
# icon_cache_dir = os.path.join(spec_dir, 'data', 'icon_cache')
# if os.path.isdir(icon_cache_dir):
#     datas.append((icon_cache_dir, 'data/icon_cache'))


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'PyQt5.sip',
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        'websocket',
        'psutil',  # v31 点卡模块用来按进程名枚举 LOL 客户端窗口
    ],
    hookspath=[],
    hooksconfig={},
    # v4.1.66: 去掉 early_rth.py 启动计时 runtime hook —— 启动打点任务已完成,
    # 不再需要写 startup_timing.log(每次启动都在 exe 同级留一个测试日志)
    runtime_hooks=[],
    excludes=[
        'tkinter', 'tkinter.ttk', 'tkinter.dialog',
        'unittest', 'pytest', 'doctest',
        'xmlrpc', 'pydoc', 'pdb', 'profile', 'cProfile',
        'distutils', 'setuptools', 'pip', 'wheel',
        'matplotlib', 'numpy', 'scipy', 'pandas',
        'PIL', 'cv2', 'tensorflow', 'torch',
        'IPython', 'jupyter', 'notebook',
        'lib2to3', 'ensurepip', 'venv',
        # v4.4.2(P2-12): 删除 decimal/fractions 排除 —— 二者互相 import, 当前依赖链
        # 确实没命中, 但任何未来运行期 import 都会直接 ImportError, 而 console=False
        # 下无声; 体积收益 ≈0。
        # v4.1.65: 打包环境与其它项目共用, 装机变动会悄悄膨胀 exe —— 必须显式排除。
        # 现象: ROM 项目装的 androguard 带入 cryptography 50.x, 打包器顺着
        # urllib3.contrib.pyopenssl 的**可选**导入链把它收进来, 光
        # cryptography/hazmat/bindings/_rust.pyd 一个文件就 9.7MB(解压后),
        # exe 22.9MB → 27.1MB。本项目连 LCU 的 wss:// 全程用 stdlib ssl,
        # 从不调用 inject_into_urllib3(), 故整体排除。
        'cryptography', 'cffi', '_cffi_backend',
        'urllib3.contrib.pyopenssl', 'urllib3.contrib.securetransport',
    ],
    noarchive=False,
)

# ============ v19:Qt 冗余瘦身 —— 程序为纯 QWidget + QPainter(raster) + DWM 毛玻璃,
# 用不到 QML/Quick、QtNetwork、OpenGL 软件渲染全家与多语言翻译;且该 PyQt5 构建将
# PNG 编解码静态内建于 Qt5Gui.dll,imageformats 大部分插件化可整体裁掉。
# 打包前从归档剔除,使 exe ~40.6MB → ~25MB,显著缩短 onefile 启动自解压时间。
# v4.1.40 修正:PNG 内建不代表 imageformats 可全砍 —— **JPEG/WEBP 解码仍需插件**
# (qjpeg.dll/qwebp.dll)。v4.1.39 真头像(jpg)下载成功却显示不出的根因就是
# 整目录被删;英雄图标全是 PNG 才一直没暴露。现保留 qjpeg+qwebp,其余照删。
# v4.4.0(P2): 删除历史死变量 `_QT_BIN_KEEP = None` —— 全文件零引用, 实际保留
# 清单由下方 _DROP_SUB / _IF_KEEP 取反决定。顺带更正它对 vistastyle 的错误描述:
# 程序 main() 设的是 `setStyle('Fusion')`(Qt5Widgets 内建样式), 与 vistastyle 插件无关。
_DROP_SUB = (
    '/qt5/translations/',
    '/qt5/bin/qt5quick.dll', '/qt5/bin/qt5qml.dll', '/qt5/bin/qt5qmlmodels.dll',
    '/qt5/bin/qt5network.dll', '/qt5/bin/qt5websockets.dll',
    '/qt5/bin/qt5dbus.dll', '/qt5/bin/qt5svg.dll',
    '/qt5/bin/opengl32sw.dll', '/qt5/bin/libglesv2.dll',
    '/qt5/bin/libegl.dll', '/qt5/bin/d3dcompiler_47.dll',
    '/qt5/plugins/platforms/qwebgl.dll', '/qt5/plugins/platforms/qminimal.dll',
    '/qt5/plugins/platforms/qoffscreen.dll',
    '/qt5/plugins/generic/', '/qt5/plugins/platformthemes/',
    '/qt5/plugins/iconengines/',
)
# imageformats 目录只保留 qjpeg/qwebp(头像源是 jpg;webp 兜底),其余(qgif/qsvg/
# qtiff/qtga/qwbmp/qicns/qico)全部裁掉 —— PNG/GIF 内建可解,项目用不到其余格式。
_IF_KEEP = {'qjpeg.dll', 'qwebp.dll'}


def _keep_imageformats(name: str) -> bool:
    n = name.replace('\\', '/').lower()
    if '/qt5/plugins/imageformats/' not in n:
        return True
    return n.rsplit('/', 1)[-1] in _IF_KEEP


def _drop_qt_junk(name: str) -> bool:
    n = name.replace('\\', '/').lower()
    return any(s in n for s in _DROP_SUB)


# Analysis 产物 TOC 项形如 (name, path_or_bytes, typecode);按归档名过滤
a.binaries = [x for x in a.binaries
              if not _drop_qt_junk(x[0]) and _keep_imageformats(x[0])]
a.datas = [x for x in a.datas if not _drop_qt_junk(x[0])]
pyz = PYZ(a.pure, cipher=block_cipher)


# icon 缺失则回退到 None（PyInstaller 允许）
exe_icon = icon_ico if os.path.isfile(icon_ico) else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='大蛋小助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=exe_icon,
    # v4.4.0: 写入 Windows 版本资源(见文件开头), 让 exe「属性 → 详细信息」有版本号
    version=version_file,
)