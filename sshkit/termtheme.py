"""Terminal colour profiles and fonts (Settings in Digonto SSH). Saved on this computer, per user."""
import json
import os

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFontDatabase

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
# open-source fonts that come with the program (SIL Open Font Licence / Ubuntu Font Licence, licence texts are in assets/fonts)
BUNDLED_FILES = ["JetBrainsMono.ttf", "FiraCode.ttf", "SourceCodePro.ttf", "RobotoMono.ttf", "UbuntuMono-Regular.ttf",
                 "IBMPlexMono-Regular.ttf", "SpaceMono-Regular.ttf", "Cousine-Regular.ttf"]
SYSTEM_DEFAULTS = ["Cascadia Mono", "Menlo", "Consolas", "DejaVu Sans Mono", "Ubuntu Mono", "Liberation Mono", "Courier New"]
DEFAULT_PROFILE = "Digonto Green"
DEFAULT_SIZE = 16

_bundled = []          # family names that were loaded from FONT_DIR


def ensure_fonts():
    """load the bundled fonts once (needs the QApplication)"""
    global _bundled
    if _bundled:
        return _bundled
    fams = []
    for f in BUNDLED_FILES:
        p = os.path.join(FONT_DIR, f)
        if os.path.exists(p):
            fid = QFontDatabase.addApplicationFont(p)
            if fid >= 0:
                for fam in QFontDatabase.applicationFontFamilies(fid):
                    if fam not in fams:
                        fams.append(fam)
    # a variable font can announce each weight as its own family ("Fira Code Light"): keep the plain name only
    fams = [f for f in fams if not any(o != f and f.startswith(o + " ") for o in fams)]
    _bundled = fams
    return fams


def bundled_families():
    return list(ensure_fonts())


def installed_families(all_fonts=False):
    ensure_fonts()
    bundled = set(_bundled)
    out = []
    for f in QFontDatabase.families():
        if f in bundled or f.startswith("."):
            continue
        if all_fonts or QFontDatabase.isFixedPitch(f):
            out.append(f)
    return out


def _p(bg, fg, cursor, ansi, sel=None):
    """a profile: bg, fg, cursor, sel (hex), ansi = 16 colours"""
    assert len(ansi) == 16
    return {"bg": bg, "fg": fg, "cursor": cursor, "sel": sel or "", "ansi": list(ansi)}


_STD = ["#000000", "#cd0000", "#00cd00", "#cdcd00", "#0000ee", "#cd00cd", "#00cdcd", "#e5e5e5",
        "#7f7f7f", "#ff0000", "#00ff00", "#ffff00", "#5c5cff", "#ff00ff", "#00ffff", "#ffffff"]

PROFILES = {
    "Digonto Green": _p("#000000", "#28FE14", "#3CC82D",
                        ["#000000", "#C80000", "#00B800", "#C8C800", "#4A4AFF", "#C800C8", "#00B8C8", "#BFBFBF",
                         "#666666", "#FF3030", "#00E600", "#F0F000", "#6A6AFF", "#F030F0", "#00F0F0", "#FFFFFF"]),
    "Classic Dark": _p("#000000", "#e5e5e5", "#e5e5e5", _STD),
    "Classic Light": _p("#ffffff", "#1a1a1a", "#1a1a1a",
                        ["#000000", "#b00000", "#008000", "#8a7a00", "#0000b0", "#a000a0", "#007a8a", "#bfbfbf",
                         "#555555", "#e00000", "#00a000", "#a89800", "#3030e0", "#c000c0", "#009aaa", "#404040"]),
    "Solarized Dark": _p("#002b36", "#839496", "#93a1a1",
                         ["#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                          "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"]),
    "Solarized Light": _p("#fdf6e3", "#657b83", "#586e75",
                          ["#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                           "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"]),
    "Dracula": _p("#282a36", "#f8f8f2", "#f8f8f2",
                  ["#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
                   "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5", "#d6acff", "#ff92df", "#a4ffff", "#ffffff"]),
    "Nord": _p("#2e3440", "#d8dee9", "#d8dee9",
               ["#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
                "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4"]),
    "Monokai": _p("#272822", "#f8f8f2", "#f8f8f0",
                  ["#272822", "#f92672", "#a6e22e", "#f4bf75", "#66d9ef", "#ae81ff", "#a1efe4", "#f8f8f2",
                   "#75715e", "#f92672", "#a6e22e", "#f4bf75", "#66d9ef", "#ae81ff", "#a1efe4", "#f9f8f5"]),
    "Gruvbox Dark": _p("#282828", "#ebdbb2", "#ebdbb2",
                       ["#282828", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#a89984",
                        "#928374", "#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#ebdbb2"]),
    "Gruvbox Light": _p("#fbf1c7", "#3c3836", "#3c3836",
                        ["#fbf1c7", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#7c6f64",
                         "#928374", "#9d0006", "#79740e", "#b57614", "#076678", "#8f3f71", "#427b58", "#3c3836"]),
    "Tokyo Night": _p("#1a1b26", "#c0caf5", "#c0caf5",
                      ["#15161e", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
                       "#414868", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5"]),
    "One Dark": _p("#282c34", "#abb2bf", "#528bff",
                   ["#282c34", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
                    "#5c6370", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#ffffff"]),
    "One Light": _p("#fafafa", "#383a42", "#526fff",
                    ["#383a42", "#e45649", "#50a14f", "#c18401", "#0184bc", "#a626a4", "#0997b3", "#fafafa",
                     "#4f525e", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#ffffff"]),
    "Catppuccin Mocha": _p("#1e1e2e", "#cdd6f4", "#f5e0dc",
                           ["#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
                            "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8"]),
    "Material": _p("#263238", "#eeffff", "#ffcc00",
                   ["#000000", "#ff5370", "#c3e88d", "#ffcb6b", "#82aaff", "#c792ea", "#89ddff", "#ffffff",
                    "#546e7a", "#ff5370", "#c3e88d", "#ffcb6b", "#82aaff", "#c792ea", "#89ddff", "#ffffff"]),
    "Tango Dark": _p("#2e3436", "#d3d7cf", "#d3d7cf",
                     ["#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf",
                      "#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"]),
    "Ubuntu": _p("#300a24", "#eeeeec", "#eeeeec",
                 ["#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf",
                  "#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec"]),
    "Amber": _p("#140c00", "#ffb000", "#ffb000",
                ["#140c00", "#c84800", "#a8a000", "#d89000", "#a86800", "#c86000", "#c89000", "#e8b060",
                 "#6a4a00", "#ff6a00", "#d8c800", "#ffc000", "#d89000", "#ff8000", "#ffd060", "#fff0c0"]),
}


def selection_for(p):
    """the colour of selected text: the given one, else the text colour made see-through"""
    if p.get("sel"):
        c = QColor(p["sel"])
    else:
        c = QColor(p["fg"])
    if c.alpha() == 255:
        c.setAlpha(90)
    return c


# ---- saved settings
def _st():
    return QSettings("Digonto", "SSH")


def custom_profiles():
    try:
        d = json.loads(str(_st().value("term/custom", "{}")))
        out = {}
        for k, v in d.items():
            if isinstance(v, dict) and len(v.get("ansi", [])) == 16:
                out[str(k)] = _p(v["bg"], v["fg"], v["cursor"], v["ansi"], v.get("sel", ""))
        return out
    except Exception:  # noqa: BLE001
        return {}


def save_custom(d):
    _st().setValue("term/custom", json.dumps(d))


def all_profiles():
    d = dict(PROFILES)
    d.update(custom_profiles())
    return d


def profile_name():
    n = str(_st().value("term/profile", DEFAULT_PROFILE))
    return n if n in all_profiles() else DEFAULT_PROFILE


def family():
    ensure_fonts()
    f = str(_st().value("term/font", ""))
    have = set(QFontDatabase.families())
    if f and f in have:
        return f
    return next((x for x in SYSTEM_DEFAULTS if x in have), "monospace")


def size():
    try:
        return max(7, min(32, int(_st().value("term/size", DEFAULT_SIZE))))
    except (TypeError, ValueError):
        return DEFAULT_SIZE


def save_choice(name, fam, pt):
    s = _st()
    s.setValue("term/profile", name)
    s.setValue("term/font", fam)
    s.setValue("term/size", int(pt))


def apply_profile(p):
    """make the terminal widgets use this profile (they read these module values every time they paint)"""
    from . import termview as tv
    tv.BG = QColor(p["bg"])
    tv.FG = QColor(p["fg"])
    tv.CURSOR = QColor(p["cursor"])
    tv.SEL = selection_for(p)
    tv.ANSI[:] = list(p["ansi"])


def load_saved():
    """at start: apply the saved profile"""
    apply_profile(all_profiles()[profile_name()])
