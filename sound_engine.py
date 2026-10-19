"""背景音乐 + 交互音效。

设计要点（都是为了扛住 Streamlit 的 rerun）：
1. 音频元素和 JS 状态挂在**父页面** `window.__pxSound` 上，而不是 iframe 里。
   Streamlit 每次 rerun 都会重建 components iframe，但父页面的 <audio> 与监听器不会被销毁，
   所以音乐不会被切断、也不会叠出第二轨。
2. 每次 rerun 只做「配置同步」：把 Python 侧的选项塞进 payload，
   JS 发现音源没变就直接返回（`setBgm` 里 early-return），不会 restart。
3. 浏览器自动播放限制：首次 `pointerdown` / `keydown` 之前 `play()` 会被拒，
   这时记下 pending，等用户第一次真的点了页面再补播。
4. 选择同时写 localStorage（用户要求）与服务器侧 `.sound_settings.json`
   （这样刷新后 selectbox 显示的也是上次的选项，不会出现「音乐在放但下拉框写着安静」）。

音频来源（全部 CC0-1.0 / 免版权，jsDelivr 直链，带 `Access-Control-Allow-Origin: *`）：
- BGM: uncle-sheepsky/duru-ai-cc0-bgm（CC0-1.0，代码合成的 lofi / 8-bit）
- SFX: ETdoFresh/kenney.nl 的 kenney_interfacesounds（Kenney，CC0）
"""

import json
from pathlib import Path

import streamlit.components.v1 as components

SETTINGS_FILE = Path(__file__).with_name(".sound_settings.json")

# 音频文件已下载到 static/sound/，由 Streamlit 静态服务提供（离线可用）。
# 原始直链（CC0，jsDelivr）留在注释里，方便以后换成远程音源：
#   forest: https://cdn.jsdelivr.net/gh/uncle-sheepsky/duru-ai-cc0-bgm@main/mp3/duru-roomscene-lofi.mp3
#   pixel:  https://cdn.jsdelivr.net/gh/uncle-sheepsky/duru-ai-cc0-bgm@main/mp3/korobeiniki-boombap-loop.mp3
BGM_TRACKS = {
    "none": None,
    "forest": "/app/static/sound/bgm_forest.mp3",
    "pixel": "/app/static/sound/bgm_pixel.mp3",
}

# SFX 来自 Kenney 的 kenney_interfacesounds（CC0），同样已本地化：
#   https://cdn.jsdelivr.net/gh/ETdoFresh/kenney.nl@master/kenney_interfacesounds/Audio/click_001.ogg
#   https://cdn.jsdelivr.net/gh/ETdoFresh/kenney.nl@master/kenney_interfacesounds/Audio/toggle_001.ogg
SFX_TRACKS = {
    "click": "/app/static/sound/sfx_click.ogg",
    "toggle": "/app/static/sound/sfx_toggle.ogg",
}

BGM_VOLUME = 0.34
SFX_VOLUME = 0.42

DEFAULT_SETTINGS = {"bgm": "none", "sfx": True}


def load_sound_settings() -> dict:
    """读取上次的选择；文件坏了就退回默认值，绝不让页面崩。"""
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)
    bgm = raw.get("bgm")
    if bgm not in BGM_TRACKS:
        bgm = "none"
    return {"bgm": bgm, "sfx": bool(raw.get("sfx", True))}


def save_sound_settings(bgm: str, sfx: bool) -> None:
    try:
        SETTINGS_FILE.write_text(
            json.dumps({"bgm": bgm, "sfx": bool(sfx)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass  # 只影响「记住选择」，不影响播放


SOUND_JS = r"""
(function () {
  var CFG = __PAYLOAD__;

  var doc;
  try { doc = window.parent.document; } catch (err) { doc = document; }
  var win = doc.defaultView || window;
  var LS_BGM = "paperdino.bgm", LS_SFX = "paperdino.sfx";

  var E = win.__pxSound;
  if (!E) {
    E = win.__pxSound = {
      audio: null, pending: null, key: null, sfx: true,
      bound: false, failed: null
    };
  }
  E.cfg = CFG;

  function ensureAudio() {
    if (E.audio && doc.body && doc.body.contains(E.audio)) { return E.audio; }
    var el = doc.getElementById("px-bgm");
    if (!el) {
      el = doc.createElement("audio");
      el.id = "px-bgm";
      el.loop = true;
      el.preload = "auto";
      el.setAttribute("playsinline", "");
      el.style.display = "none";
      (doc.body || doc.documentElement).appendChild(el);
    }
    el.addEventListener("error", function () {
      E.failed = el.currentSrc || el.src || "unknown";
    });
    E.audio = el;
    return el;
  }

  function fadeTo(el, target, ms) {
    if (el.__fade) { clearInterval(el.__fade); }
    var from = el.volume;
    var t0 = Date.now();
    el.__fade = setInterval(function () {
      var k = Math.min(1, (Date.now() - t0) / ms);
      el.volume = Math.max(0, Math.min(1, from + (target - from) * k));
      if (k >= 1) { clearInterval(el.__fade); el.__fade = null; }
    }, 60);
  }

  function start(url) {
    var el = ensureAudio();
    if (el.dataset.src !== url) {
      el.dataset.src = url;
      el.src = url;
      el.load();
    }
    var p;
    try { p = el.play(); } catch (err) { p = null; }
    if (p && p.catch) {
      p.catch(function () { E.pending = url; });
    }
    return el;
  }

  E.setBgm = function (url) {
    var el = ensureAudio();
    if (!url) {
      E.pending = null;
      E.key = null;
      if (!el.paused) {
        fadeTo(el, 0, 500);
        setTimeout(function () { try { el.pause(); } catch (err) {} el.volume = CFG.volume; }, 560);
      }
      return;
    }
    // 同一首且正在播：什么都不做，避免 rerun 时重启音乐
    if (E.key === url && !el.paused && el.dataset.src === url) { return; }
    E.key = url;
    el.volume = 0;
    start(url);
    fadeTo(el, CFG.volume, 900);
  };

  E.setSfx = function (on) { E.sfx = !!on; };

  E.playSfx = function (kind) {
    var url = kind === "toggle" ? CFG.sfxToggle : CFG.sfxClick;
    if (!url) { return; }
    E.sfxCount = (E.sfxCount || 0) + 1;
    E.lastSfx = url;
    try {
      var s = new win.Audio(url);
      s.volume = CFG.sfxVolume;
      var p = s.play();
      if (p && p.catch) { p.catch(function () {}); }
      s.addEventListener("ended", function () {
        s.removeAttribute("src");
        try { s.load(); } catch (err) {}
      });
    } catch (err) {}
  };

  // 首次用户手势 → 允许播放；顺便把之前被拦下的音源补上
  function unlock() {
    var el = ensureAudio();
    if (E.pending) {
      var url = E.pending;
      E.pending = null;
      el.volume = 0;
      start(url);
      fadeTo(el, CFG.volume, 900);
    }
  }

  if (!E.bound) {
    E.bound = true;
    doc.addEventListener("pointerdown", unlock, true);
    doc.addEventListener("keydown", unlock, true);

    var CLICKABLE = "button, a, [role='button'], [role='switch'], [role='radio'], [role='checkbox'], label, summary, [data-testid='stRadioOption'], [data-testid='stSelectbox']";
    var TOGGLEISH = "[role='switch'], [role='checkbox'], [data-testid='stCheckbox'], [data-testid='stToggle']";

    doc.addEventListener("pointerdown", function (ev) {
      if (!E.sfx) { return; }
      var t = ev.target;
      if (!t || !t.closest) { return; }
      if (!t.closest(CLICKABLE)) { return; }
      E.playSfx(t.closest(TOGGLEISH) ? "toggle" : "click");
    }, true);

    doc.addEventListener("keydown", function (ev) {
      if (!E.sfx || ev.key !== "Enter") { return; }
      var t = ev.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) { E.playSfx("click"); }
    }, true);
  }

  try {
    win.localStorage.setItem(LS_BGM, CFG.bgmKey || "none");
    win.localStorage.setItem(LS_SFX, CFG.sfx ? "1" : "0");
  } catch (err) {}

  E.setSfx(CFG.sfx);
  ensureAudio();  // 无论选没选音乐，都先把隐藏的 <audio> 准备好
  // 只有在用户真的选了音乐时才主动开播；选「安静」时不要碰正在放的那一轨
  if (CFG.bgm) {
    E.setBgm(CFG.bgm);
  } else if (E.key) {
    E.setBgm(null);
  }
  win.__pxSoundReady = true;
})();
"""


def render_sound(bgm_key: str, sfx_on: bool) -> None:
    """把当前设置同步给父页面的播放器（iframe 每次 rerun 重跑，只会同步配置）。"""
    payload = {
        "bgmKey": bgm_key,
        "bgm": BGM_TRACKS.get(bgm_key),
        "sfx": bool(sfx_on),
        "sfxClick": SFX_TRACKS["click"],
        "sfxToggle": SFX_TRACKS["toggle"],
        "volume": BGM_VOLUME,
        "sfxVolume": SFX_VOLUME,
    }
    components.html(
        "<script>" + SOUND_JS.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False)) + "</script>",
        height=0,
    )
