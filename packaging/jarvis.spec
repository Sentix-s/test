# PyInstaller spec: one folder with JARVIS.exe (window) and jarvis-cli.exe (console).
# Build:  pyinstaller packaging/jarvis.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("piper", "faster_whisper", "ctranslate2", "onnxruntime", "openwakeword", "webrtcvad",
            "sounddevice", "_sounddevice_data", "tokenizers", "huggingface_hub"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # package not installed on this platform (e.g. openwakeword on Linux py3.12)
        continue
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("jarvis") + ["tzdata", "scipy.signal"]
datas += [("jarvis.ico", ".")]

# Heavy things JARVIS never uses (openWakeWord pulls them in optionally).
excludes = ["tkinter", "matplotlib", "torch", "tflite_runtime", "tensorflow", "IPython", "pytest",
            "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick"]

common = dict(pathex=[".."], binaries=binaries, datas=datas, hiddenimports=hiddenimports, excludes=excludes,
              noarchive=False)
gui_a = Analysis(["jarvis_gui.py"], **common)
cli_a = Analysis(["jarvis_cli.py"], **common)
MERGE((gui_a, "jarvis_gui", "JARVIS"), (cli_a, "jarvis_cli", "jarvis-cli"))

gui_pyz = PYZ(gui_a.pure)
cli_pyz = PYZ(cli_a.pure)
gui_exe = EXE(gui_pyz, gui_a.scripts, [], exclude_binaries=True, name="JARVIS", icon="jarvis.ico",
              console=False, upx=False)
cli_exe = EXE(cli_pyz, cli_a.scripts, [], exclude_binaries=True, name="jarvis-cli", icon="jarvis.ico",
              console=True, upx=False)
coll = COLLECT(gui_exe, gui_a.binaries, gui_a.datas, cli_exe, cli_a.binaries, cli_a.datas,
               name="JARVIS", upx=False)
