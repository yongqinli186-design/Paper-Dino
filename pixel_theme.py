"""像素风主题：纯前端 CSS/JS 资源（不参与任何后台逻辑）。

- PIXEL_CSS：注入到 st.markdown 的样式表，通过 __PSP__ / __VT__ 占位符嵌入内联字体。
- CLICK_JS：通过 st.components.v1.html 注入的点击特效（在父页面冒像素星星）。
"""

from pixelfonts import FONT_PSP_B64, FONT_VT323_B64

PIXEL_CSS_TEMPLATE = r"""
<style>
/* ================= 字体 ================= */
@font-face {
  font-family: "Press Start 2P";
  src: url(data:font/woff2;base64,__PSP__) format("woff2");
  font-display: swap;
}
@font-face {
  font-family: "VT323";
  src: url(data:font/woff2;base64,__VT__) format("woff2");
  font-display: swap;
}

:root {
  --edge: #0c2a1c;
  --fg: #10331f;
  --fg-soft: #2c6b4a;
  --mint: #d6ffe6;
  --sky: #d6f2ff;
  --teal: #0f5f4d;
  --lime: #8dffb2;
  --cyan: #5fe3ff;
  --panel: #f4fff8;
  --panel-2: #e4fcee;
  --shadow: rgba(12, 42, 28, .85);
  --shadow-soft: rgba(12, 42, 28, .16);
  --font-px: "Press Start 2P", "VT323", "Microsoft YaHei", monospace;
  --font-body: "VT323", "Microsoft YaHei", "PingFang SC", monospace;
  color-scheme: light;
}

/* ================= 环境流光背景 + 像素网格 ================= */
.stApp {
  background: transparent !important;
  color: var(--fg) !important;
}

[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stHeader"],
header[data-testid="stHeader"] {
  background: transparent !important;
}
[data-testid="stHeader"] {  }
[data-testid="stAppDeployButton"], [data-testid="stToolbar"] { display: none !important; }

.pixel-layer {
  position: fixed; inset: -8%; z-index: 0; pointer-events: none; overflow: hidden;
  background: linear-gradient(120deg, #dbffea 0%, #d3f4ff 26%, #c8fbe4 52%, #cde9ff 76%, #e8fff1 100%);
  transform: translate3d(0, 0, 0) scale(1.08);
  transform-origin: 50% 50%;
  
  backface-visibility: hidden;
  
}
.pixel-layer::before {
  content: ""; position: absolute; inset: 0;
  background-image:
    radial-gradient(circle at 1px 1px, rgba(12, 42, 28, .20) 1.2px, transparent 1.5px),
    linear-gradient(rgba(12, 42, 28, .055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(12, 42, 28, .055) 1px, transparent 1px);
  background-size: 18px 18px, 90px 90px, 90px 90px;
}
.pixel-layer::after {
  content: ""; position: absolute; inset: 0;
  background: repeating-linear-gradient(0deg, rgba(12, 42, 28, .05) 0 2px, transparent 2px 5px);
  opacity: .55;
}
.star {
  position: absolute; width: 6px; height: 6px; background: var(--lime);
  box-shadow: -6px 0 0 var(--lime), 6px 0 0 var(--lime), 0 -6px 0 var(--lime), 0 6px 0 var(--lime),
              0 0 0 2px rgba(12, 42, 28, .55);
  
}
.star.s1 { left: 8%;  top: 16%;  }
.star.s2 { left: 22%; top: 64%;  transform: scale(.7); }
.star.s3 { left: 47%; top: 9%;   transform: scale(.55); }
.star.s4 { left: 63%; top: 78%;  }
.star.s5 { left: 86%; top: 24%;  transform: scale(.8); }
.star.s6 { left: 92%; top: 66%;  transform: scale(.6); }

/* ================= 布局 ================= */
[data-testid="stMainBlockContainer"], .block-container {
  position: relative; z-index: 1;
  max-width: 1180px !important;
  padding-top: 2.2rem !important;
}
[data-testid="stSidebar"] {
  position: relative; z-index: 2;
  background: linear-gradient(180deg, #e9fff2 0%, #def8ff 100%) !important;
  border-right: 4px solid var(--edge) !important;
}
[data-testid="stSidebarUserContent"] { padding-top: .9rem !important; }
[data-testid="stBottomBlockContainer"], [data-testid="stBottom"] { z-index: 3; }

/* ================= 毛玻璃 → 像素面板 ================= */
div[class*="st-key-glass"] {
  background: var(--panel) !important;
  border: 3px solid var(--edge) !important;
  border-radius: 0 !important;
  box-shadow: 6px 6px 0 var(--shadow), 10px 10px 0 var(--shadow-soft) !important;
  padding: 14px 14px 10px !important;
  position: relative;
}
div[class*="st-key-glass"]::after {
  content: ""; position: absolute; right: -3px; top: -3px;
  width: 12px; height: 12px;
  background: var(--lime);
  border-left: 3px solid var(--edge); border-bottom: 3px solid var(--edge);
}

.card-title {
  font-family: var(--font-px);
  font-size: .72rem; line-height: 1.7;
  letter-spacing: .02em; text-transform: uppercase;
  color: var(--teal);
  margin: 0 0 10px; padding-bottom: 8px;
  border-bottom: 3px solid var(--edge);
  display: flex; align-items: center; gap: 8px;
}
.card-title::before {
  content: ""; width: 10px; height: 10px; flex: 0 0 auto;
  background: var(--lime);
  box-shadow: 3px 0 0 var(--edge), 0 3px 0 var(--edge);
}

/* ================= 按钮：硬角 + 3D 像素阴影 ================= */
button[data-testid^="stBaseButton"],
.stbutton button,
.stButton button,
.stDownloadButton button,
[data-testid="stChatInputSubmitButton"] {
  border-radius: 0 !important;
  border: 3px solid var(--edge) !important;
  background: linear-gradient(180deg, #c9ffdd 0%, #8dffb2 100%) !important;
  color: var(--edge) !important;
  font-family: var(--font-px) !important;
  font-size: .72rem !important;
  line-height: 1.6 !important;
  letter-spacing: .01em !important;
  padding: .62rem .9rem !important;
  box-shadow: 4px 4px 0 var(--edge), 8px 8px 0 var(--shadow-soft) !important;
  transition: transform .06s steps(2, end), background .12s steps(2, end), color .12s !important;
  text-transform: none !important;
}
button[data-testid^="stBaseButton"]:hover,
.stbutton button:hover,
.stButton button:hover,
.stDownloadButton button:hover,
[data-testid="stChatInputSubmitButton"]:hover {
  background: linear-gradient(180deg, #a6ffc8 0%, #4dff9a 100%) !important;
  color: #04170d !important;
  transform: translate(-1px, -1px) !important;
  box-shadow: 6px 6px 0 var(--edge), 10px 10px 0 var(--shadow-soft) !important;
}
button[data-testid="stBaseButton-primary"]:hover {
  background: linear-gradient(180deg, #9be9ff 0%, #42d6ff 100%) !important;
}
button[data-testid^="stBaseButton"]:active,
.stbutton button:active,
.stDownloadButton button:active {
  transform: translate(4px, 4px) !important;
  box-shadow: 0 0 0 var(--edge) !important;
}
button[data-testid^="stBaseButton"]:focus-visible,
.stbutton button:focus-visible {
  outline: 3px solid var(--cyan) !important;
  outline-offset: 2px !important;
}
button[data-testid^="stBaseButton"]:disabled,
.stbutton button:disabled,
.stDownloadButton button:disabled {
  background: repeating-linear-gradient(45deg, #dfeee6 0 6px, #d3e6dc 6px 12px) !important;
  color: #6d8c7b !important;
  box-shadow: 4px 4px 0 rgba(12, 42, 28, .35) !important;
  transform: none !important;
}
button[data-testid="stBaseButton-headerNoPadding"] {
  box-shadow: none !important; border: 0 !important; background: transparent !important;
  padding: .2rem !important;
}
button[data-testid="stBaseButton-headerNoPadding"]:hover { background: var(--mint) !important; }
[data-testid="stChatMessage"] button,
[data-testid="stChatMessage"] .stDownloadButton button {
  font-size: .62rem !important;
  padding: .46rem .7rem !important;
  box-shadow: 3px 3px 0 var(--edge) !important;
}

/* ================= 输入控件 ================= */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input {
  border: 2px solid var(--edge) !important;
  border-radius: 0 !important;
  background: #fbfff6 !important;
  color: var(--fg) !important;
  font-family: var(--font-body) !important;
  font-size: 1.12rem !important;
  line-height: 1.4 !important;
  padding: 8px 10px !important;
  box-shadow: inset 3px 3px 0 rgba(12, 42, 28, .12) !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus,
[data-testid="stChatInputTextArea"]:focus {
  outline: none !important;
  box-shadow: inset 3px 3px 0 rgba(12, 42, 28, .10),
              0 0 0 3px var(--cyan), 0 0 0 7px rgba(95, 227, 255, .32) !important;
  
}

/* 聊天输入框：外框像素化，内部 textarea 透明 */
[data-testid="stChatInput"] > div:first-child {
  border: 3px solid var(--edge) !important;
  border-radius: 0 !important;
  background: #f9fff4 !important;
  box-shadow: 5px 5px 0 var(--edge), 9px 9px 0 var(--shadow-soft) !important;
  padding: 4px 6px !important;
}
[data-testid="stChatInputTextArea"] {
  border: 0 !important;
  border-radius: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  font-family: var(--font-body) !important;
  font-size: 1.1rem !important;
  color: var(--fg) !important;
}
[data-testid="stChatInputSubmitButton"] {
  border-radius: 0 !important;
  min-height: 34px !important;
  padding: .2rem .4rem !important;
  box-shadow: 3px 3px 0 var(--edge) !important;
}

[data-testid="stBottomBlockContainer"] { padding-bottom: 46px !important; }

/* ================= 单选 / 复选 / 开关：像素芯片 ================= */
[data-testid="stRadioGroup"] { gap: 8px !important; }
[data-testid="stRadioOption"] {
  border: 2px solid var(--edge) !important;
  border-radius: 0 !important;
  background: #eafff1 !important;
  padding: 6px 8px !important;
  margin: 0 !important;
  box-shadow: 3px 3px 0 rgba(12, 42, 28, .8) !important;
  transition: background .1s steps(2, end), transform .06s steps(2, end) !important;
}
[data-testid="stRadioOption"]:hover { background: #d4ffe4 !important; transform: translate(-1px, -1px); }
[data-testid="stRadioOption"][data-selected="true"],
[data-testid="stRadioOption"][aria-checked="true"] {
  background: linear-gradient(180deg, #b8ffd2, #8dffb2) !important;
}
[data-testid="stRadioOption"] p {
  font-family: var(--font-body) !important;
  font-size: 1.02rem !important;
  line-height: 1.25 !important;
  color: var(--fg) !important;
}
/* 结构：label > div(行容器) > [ div(圆点框) > div(点) , div(stMarkdownContainer) ] */
[data-testid="stRadioOption"] > div:first-of-type {
  display: flex !important;
  align-items: center !important;
  gap: 8px !important;
}
[data-testid="stRadioOption"] > div:first-of-type > div:first-child {
  width: 14px !important; height: 14px !important;
  min-width: 14px !important;
  border: 2px solid var(--edge) !important;
  border-radius: 0 !important;
  background: #ffffff !important;
  box-shadow: 2px 2px 0 rgba(12, 42, 28, .55) !important;
  display: flex !important; align-items: center !important; justify-content: center !important;
  flex: 0 0 auto !important;
}
[data-testid="stRadioOption"] > div:first-of-type > div:first-child > div {
  width: 6px !important; height: 6px !important;
  border-radius: 0 !important; background: transparent !important;
}
[data-testid="stRadioOption"][data-selected="true"] > div:first-of-type > div:first-child > div {
  background: var(--edge) !important;
  border-radius: 0 !important;
}
[data-testid="stRadioOption"] [data-testid="stMarkdownContainer"] {
  flex: 1 1 auto !important;
  min-width: 0 !important;
}

[data-testid="stCheckbox"] label > div:first-of-type {
  border: 2px solid var(--edge) !important;
  border-radius: 0 !important;
  background: #fbfff2 !important;
  box-shadow: 2px 2px 0 rgba(12, 42, 28, .55) !important;
}
[data-testid="stCheckbox"] label:has(input:checked) > div:first-of-type {
  background: var(--lime) !important;
}
[data-testid="stCheckbox"] label > div:first-of-type svg { fill: var(--edge) !important; }

/* toggle：像素滑轨 */
[data-testid="stCheckbox"]:has(input[role="switch"]) label > div:first-of-type {
  width: 54px !important; height: 26px !important;
  border: 3px solid var(--edge) !important;
  border-radius: 0 !important;
  background: repeating-linear-gradient(90deg, #ecfff3 0 9px, #d7f7e3 9px 18px) !important;
  box-shadow: inset 3px 3px 0 rgba(12, 42, 28, .12), 3px 3px 0 rgba(12, 42, 28, .7) !important;
}
[data-testid="stCheckbox"]:has(input[role="switch"]:checked) label > div:first-of-type {
  background: repeating-linear-gradient(90deg, #a6ffc8 0 9px, #8dffb2 9px 18px) !important;
}
[data-testid="stCheckbox"]:has(input[role="switch"]) label > div:first-of-type > div {
  border-radius: 0 !important;
  background: var(--edge) !important;
  box-shadow: 2px 2px 0 rgba(12, 42, 28, .35);
}

/* ================= 文件上传 / 展开器 / 进度 / 提示 ================= */
[data-testid="stFileUploaderDropzone"] {
  border: 3px dashed var(--edge) !important;
  border-radius: 0 !important;
  background: #f2fff8 !important;
  box-shadow: 4px 4px 0 rgba(12, 42, 28, .45) !important;
  padding: 10px !important;
}
[data-testid="stFileUploaderDropzone"]:hover { background: #e7fff2 !important; }
[data-testid="stFileUploaderDropzoneInstructions"] { color: var(--fg-soft) !important; }

[data-testid="stExpander"] details {
  border: 3px solid var(--edge) !important;
  border-radius: 0 !important;
  background: #f7fffa !important;
  box-shadow: 4px 4px 0 rgba(12, 42, 28, .55) !important;
}
[data-testid="stExpander"] summary {
  background: linear-gradient(180deg, #dcffea, #bfffd8) !important;
  border-bottom: 2px solid var(--edge) !important;
  font-family: var(--font-body) !important;
  font-size: 1.02rem !important;
  padding: 6px 10px !important;
}
[data-testid="stExpander"] summary p { font-size: 1.02rem !important; }

[data-testid="stProgress"] > div {
  border: 2px solid var(--edge) !important;
  border-radius: 0 !important;
  background: #eafff2 !important;
  height: 16px !important;
}
[data-testid="stProgress"] div[role="progressbar"] > div {
  background: repeating-linear-gradient(90deg, var(--lime) 0 8px, #45e07c 8px 16px) !important;
  border-radius: 0 !important;
}
[data-testid="stSpinner"] svg { display: none !important; }
[data-testid="stSpinner"] > div::before {
  content: ""; display: inline-block; width: 16px; height: 16px;
  background: var(--lime);
  box-shadow: 4px 0 0 var(--lime), 0 4px 0 var(--lime), 4px 4px 0 var(--lime),
              0 0 0 2px var(--edge);
  
}
[data-testid="stAlert"], [data-testid="stAlertContainer"] {
  border: 3px solid var(--edge) !important;
  border-radius: 0 !important;
  background: #f3fff8 !important;
  box-shadow: 4px 4px 0 rgba(12, 42, 28, .5) !important;
  color: var(--fg) !important;
}
[data-testid="stCaptionContainer"] p { color: var(--fg-soft) !important; font-size: .95rem !important; }
[data-testid="stCodeBlock"], pre, code { border-radius: 0 !important; }
pre {
  border: 2px solid var(--edge) !important;
  background: #f2fff8 !important;
  box-shadow: 3px 3px 0 rgba(12, 42, 28, .4) !important;
}
code { font-family: "VT323", Consolas, monospace !important; font-size: 1.02rem !important; }

/* ================= 聊天气泡：RPG 对话框弹出 ================= */
[data-testid="stChatMessage"] {
  background: #f5fff9 !important;
  border: 3px solid var(--edge) !important;
  border-radius: 0 !important;
  padding: 14px 16px !important;
  margin: 12px 0 !important;
  box-shadow: 5px 5px 0 var(--shadow) !important;
  position: relative;
  
  transform-origin: 24px 100%;
}
[data-testid="stChatMessage"]::after {
  content: ""; position: absolute; left: 22px; bottom: -10px;
  width: 12px; height: 12px;
  background: #f5fff9;
  border-right: 3px solid var(--edge);
  border-bottom: 3px solid var(--edge);
  transform: rotate(45deg);
}
[data-testid="stChatMessage"] [data-testid^="stChatMessageAvatar"] {
  border-radius: 0 !important;
  border: 2px solid var(--edge);
  background: #ffffff !important;
  box-shadow: 2px 2px 0 rgba(12, 42, 28, .5);
  image-rendering: pixelated;
}
[data-testid="stChatMessage"] p,
[data-testid="stChatMessage"] li {
  font-family: var(--font-body) !important;
  font-size: 1.14rem !important;
  line-height: 1.5 !important;
  color: var(--fg) !important;
}
[data-testid="stChatMessage"] h1,
[data-testid="stChatMessage"] h2,
[data-testid="stChatMessage"] h3 {
  font-family: var(--font-px) !important;
  color: var(--teal) !important;
  letter-spacing: .01em;
  margin: .9rem 0 .5rem !important;
}
[data-testid="stChatMessage"] h1 { font-size: 1.15rem !important; }
[data-testid="stChatMessage"] h2 { font-size: 1rem !important; }
[data-testid="stChatMessage"] h3 { font-size: .9rem !important; }
[data-testid="stChatMessage"] table { border: 3px solid var(--edge) !important; }
[data-testid="stChatMessage"] th {
  background: #d8ffe8 !important; color: var(--edge) !important;
  border: 2px solid var(--edge) !important;
}
[data-testid="stChatMessage"] td { border: 2px solid rgba(12, 42, 28, .45) !important; }

/* ================= Banner ================= */
.hero {
  position: relative; display: flex; gap: 1.2rem; align-items: center;
  padding: 1.45rem 1.5rem; margin: .1rem 0 1rem;
  background:
    repeating-linear-gradient(45deg, rgba(12, 42, 28, .05) 0 8px, rgba(255, 255, 255, 0) 8px 16px),
    linear-gradient(120deg, #e9fff2 0%, #d9f6ff 55%, #ccffe6 100%);
  border: 4px solid var(--edge);
  box-shadow: 8px 8px 0 var(--edge), 14px 14px 0 var(--shadow-soft);
  overflow: hidden;
  image-rendering: pixelated;
}
.hero::before {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: repeating-linear-gradient(0deg, rgba(12, 42, 28, .05) 0 2px, transparent 2px 6px);
}
.hero > * { position: relative; z-index: 1; }
.hero-dino svg {
  
  filter: drop-shadow(3px 3px 0 rgba(12, 42, 28, .55));
}
.hero-kicker {
  font-family: var(--font-px);
  font-size: .58rem; letter-spacing: .26em;
  color: var(--teal);
  text-transform: uppercase;
}
.hero-title {
  font-family: var(--font-px);
  font-size: 2.2rem; line-height: 1.16;
  margin: .42rem 0 .3rem;
  background: linear-gradient(100deg, #0f5f4d 0%, #12b489 32%, #26a6ff 68%, #0f5f4d 100%);
  background-size: 220% 100%;
  -webkit-background-clip: text; background-clip: text;
  color: transparent;
  filter: drop-shadow(3px 3px 0 rgba(12, 42, 28, .9));
  
}
.hero-sub {
  font-family: var(--font-body);
  font-size: 1.14rem; line-height: 1.45;
  color: var(--fg); margin: .15rem 0 .6rem; max-width: 46rem;
}
.hero-chips { display: flex; flex-wrap: wrap; gap: .5rem; }
.chip {
  font-family: var(--font-px); font-size: .58rem; line-height: 1.7;
  padding: .38rem .55rem;
  background: #f3fff9; color: var(--teal);
  border: 3px solid var(--edge);
  box-shadow: 3px 3px 0 rgba(12, 42, 28, .75);
  transition: transform .08s steps(2, end), background .12s;
}
.chip:hover { transform: translate(-1px, -1px); background: var(--lime); color: var(--edge); }

/* ================= 任务胶囊 / 空状态 ================= */
.task-pill {
  display: inline-flex; align-items: center; gap: .55rem;
  font-family: var(--font-px); font-size: .62rem; line-height: 1.7;
  background: #f7fff8; color: var(--fg);
  border: 3px solid var(--edge);
  padding: .5rem .75rem; margin: .1rem 0 1rem;
  box-shadow: 4px 4px 0 rgba(12, 42, 28, .8);
}
.task-pill .pulse {
  width: 9px; height: 9px; background: var(--lime);
  box-shadow: 0 0 0 2px var(--edge);
  
}

.hint-card {
  display: flex; gap: .8rem; align-items: flex-start;
  background: #f4fff8;
  border: 3px dashed var(--edge);
  box-shadow: 5px 5px 0 rgba(12, 42, 28, .35);
  padding: .95rem 1.05rem;
  margin: .2rem 0 1rem;
  font-family: var(--font-body); font-size: 1.12rem; line-height: 1.5;
  color: var(--fg);
}
.hint-card b { color: var(--teal); }
.hint-emoji { font-size: 1.4rem; line-height: 1; }

/* ================= 加载动画：像素恐龙跑动 ================= */
.dino-loader {
  display: inline-flex; align-items: center; gap: .7rem;
  border: 3px solid var(--edge);
  background: #eafff2;
  padding: .55rem .9rem;
  box-shadow: 5px 5px 0 rgba(12, 42, 28, .8);
  font-family: var(--font-px); font-size: .62rem; line-height: 1.8;
  color: var(--fg);
}
.dino-loader svg {  }
.dino-loader .dots::after { content: "";  }

/* ================= 状态栏 ================= */
[data-testid="stBottomBlockContainer"]::after {
  content: var(--status-text, ">> SYSTEM READY");
  display: block;
  margin: 2px 0 6px 0;
  border: 3px solid var(--edge);
  background: #f2fff7;
  padding: .38rem .6rem;
  box-shadow: 4px 4px 0 rgba(12, 42, 28, .75);
  font-family: var(--font-px); font-size: .56rem; line-height: 1.9;
  color: var(--teal);
  letter-spacing: .02em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.status-bar .sb-caret {  color: var(--teal); }
.status-bar .sb-right { margin-left: auto; color: var(--fg-soft); }

/* ================= 右下角小宠物 ================= */
.pet {
  position: fixed; bottom: 150px; left: 372px; z-index: 1200;
  display: flex; align-items: flex-end; gap: 6px;
  pointer-events: none;
  
}
.pet-body svg {
  
  filter: drop-shadow(2px 2px 0 rgba(12, 42, 28, .5));
}
.pet-tag {
  font-family: var(--font-px); font-size: .52rem; line-height: 1.6;
  background: #f2fff7; border: 2px solid var(--edge);
  padding: 2px 4px; margin-bottom: 10px;
  box-shadow: 2px 2px 0 rgba(12, 42, 28, .6);
  color: var(--teal);
}

/* ================= 页脚 ================= */
.app-footer {
  margin: 2.6rem auto 1.2rem; padding: 1rem .8rem .5rem;
  text-align: center;
  font-family: var(--font-px); font-size: .58rem; line-height: 1.9;
  color: var(--fg-soft);
  border-top: 3px dashed rgba(12, 42, 28, .45);
}
.app-footer b { color: var(--teal); }
.app-footer .dot { margin: 0 .5rem; opacity: .5; }
.app-footer .made { margin-top: .4rem; font-size: .54rem; opacity: .8; }

/* ================= 细节 ================= */
hr { border: 0; border-top: 3px dashed rgba(12, 42, 28, .35) !important; }
::selection { background: var(--lime); color: var(--edge); }
::-webkit-scrollbar { width: 12px; height: 12px; }
::-webkit-scrollbar-track { background: #e8fff2; }
::-webkit-scrollbar-thumb {
  background: #7fe0a4; border: 3px solid var(--edge);
}
[data-testid="stSidebar"] .stMarkdown p { font-family: var(--font-body) !important; font-size: 1.02rem !important; }
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] span {
  font-family: var(--font-body) !important; font-size: 1.04rem !important;
  color: var(--fg) !important;
}
[data-testid="stTooltipContent"], [data-testid="stTooltipHoverTarget"] { border-radius: 0 !important; }
[data-testid="stTooltipContent"] {
  border: 2px solid var(--edge) !important; background: #f6fff9 !important;
  color: var(--fg) !important; font-family: var(--font-body) !important;
}

/* ================= 响应式 / 可访问性 ================= */
@media (min-width: 1100px) {
  [data-testid="stSidebar"] { min-width: 352px !important; width: 352px !important; }
}
@media (max-width: 760px) {
  .hero { flex-direction: column; align-items: flex-start; gap: .9rem; padding: 1.1rem; }
  .hero-title { font-size: 1.6rem; }
  .hero-dino svg { width: 76px; height: 76px; }
  .hero-sub { font-size: 1rem; }
  .pet { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .stApp, .star, .hero-dino svg, .hero-title, .pet, .pet-body svg,
  .dino-loader svg, .task-pill .pulse, [data-testid="stChatMessage"] {
    
  }
}
/* 深色模式下保持对比度（Streamlit 若被切到 dark 主题） */

/* ==========================================================================
   极简重构：抽屉面板 / 悬浮胶囊输入 / 居中限宽 / 图标化操作
   ========================================================================== */
[data-testid="stSidebar"]{display:none !important;}

div[class~="st-key-drawer_btn"]{position:fixed;top:14px;left:14px;z-index:1300;width:48px;}
div[class~="st-key-drawer_btn"] button{
  width:48px;height:48px;min-height:48px;padding:0;font-size:20px;line-height:1;
  background:var(--panel);border:3px solid var(--edge);
  box-shadow:4px 4px 0 rgba(12,42,28,.32);border-radius:0;
}
div[class~="st-key-drawer_btn"] button:hover{background:var(--lime);transform:translate(-1px,-1px);}

div[class~="st-key-drawer"]{
  position:fixed;top:0;left:0;bottom:0;width:392px;max-width:90vw;
  padding:26px 18px 210px;overflow-y:auto;z-index:1250;
  background:rgba(245,255,249,.86);
  
  
  border-right:3px solid var(--edge);
  box-shadow:9px 0 0 rgba(12,42,28,.16);
  transition:transform .42s cubic-bezier(.22,1,.36,1);
}
div[class~="st-key-drawer"]::-webkit-scrollbar{width:8px;}
div[class~="st-key-drawer"]::-webkit-scrollbar-thumb{background:rgba(12,42,28,.35);}
.drawer-title{font-family:var(--font-px);font-size:.58rem;letter-spacing:.16em;color:#2f6d46;padding:10px 0 0 52px;}
.drawer-label{font-family:var(--font-px);font-size:.5rem;letter-spacing:.12em;color:#3f7a53;margin:14px 0 4px;}
div[class~="st-key-drawer"] [data-testid="stExpander"] summary{font-family:var(--font-body);font-size:1rem;}

[data-testid="stMainBlockContainer"]{
  max-width:min(62vw,920px) !important;margin-inline:auto !important;
  padding-top:78px !important;
}

[data-testid="stBottomBlockContainer"]{background:transparent !important;}
[data-testid="stChatInput"]{background:transparent !important;max-width:min(720px,62vw);margin:0 auto;}
[data-testid="stChatInput"] > div:first-child{
  border:3px solid var(--edge) !important;border-radius:999px !important;
  background:rgba(255,255,255,.94) !important;
  box-shadow:5px 5px 0 rgba(12,42,28,.26) !important;
  padding:2px 6px;
  position:relative;
  transform:translateZ(0);
  transition:transform .24s ease;
  
}
[data-testid="stChatInput"] > div:first-child:focus-within{
  transform:translateZ(0) scale(1.02);
}
[data-testid="stChatInput"] > div:first-child:focus-within::after{
  content:"";
  position:absolute; inset:-7px;
  border-radius:999px;
  border:3px solid rgba(99,184,119,.55);
  pointer-events:none;
  transform:translateZ(0) scale(1);
  opacity:.18;
  
  
}
[data-testid="stChatInputTextArea"]{
  border-radius:999px !important;border:0 !important;background:transparent !important;
  font-family:var(--font-body) !important;font-size:1.02rem !important;
}
[data-testid="stChatInputSubmitButton"]{
  border:3px solid var(--edge) !important;border-radius:999px !important;
  background:var(--lime) !important;box-shadow:2px 2px 0 rgba(12,42,28,.3) !important;
}

.hero-min{
  display:flex;align-items:center;gap:22px;padding:22px 26px;margin:6px 0 16px;
  border:3px solid var(--edge);background:rgba(245,255,249,.82);
  box-shadow:8px 8px 0 rgba(12,42,28,.2);
}
.hero-min .hero-dino{
  flex:0 0 auto;filter:drop-shadow(4px 4px 0 rgba(12,42,28,.22));
  
}
.hero-min-text{min-width:0;}
.hero-min .hero-kicker{
  font-family:var(--font-px);font-size:.58rem;letter-spacing:.3em;
  color:#2f6d46;margin-bottom:8px;
}
.hero-min .hero-title{margin:0 0 6px;line-height:1.15;}
.hero-min .hero-sub{font-family:var(--font-body);font-size:1.04rem;color:#2c5c3e;margin:0;}

div[class~="st-key-quick_actions"]{
  display:flex;flex-direction:row;align-items:center;justify-content:flex-end;gap:8px;margin:2px 0 12px;
}
div[class~="st-key-quick_actions"] > div[data-testid="stVerticalBlock"]{flex-direction:row;gap:8px;}
div[class~="st-key-quick_actions"] button{
  width:38px;height:38px;min-height:38px;padding:0;border-radius:0;
  border:3px solid var(--edge);background:var(--panel);
  box-shadow:3px 3px 0 rgba(12,42,28,.24);
}
div[class~="st-key-quick_actions"] button:hover{background:var(--lime);transform:translate(-1px,-1px);}
div[class~="st-key-quick_actions"] button:disabled{opacity:.4;}
div[class~="st-key-quick_actions"] [data-testid="stCaptionContainer"]{
  margin-right:auto;font-family:var(--font-body);font-size:.92rem;color:#3f7a53;
}

div[class*="st-key-msg_actions"]{display:flex;flex-direction:row;gap:6px;margin-top:8px;}
div[class*="st-key-msg_actions"] > div[data-testid="stVerticalBlock"]{flex-direction:row;gap:6px;}
div[class*="st-key-msg_actions"] button{
  width:34px;height:34px;min-height:34px;padding:0;border-radius:0;
  border:2px solid rgba(12,42,28,.5);background:rgba(245,255,249,.92);
  box-shadow:2px 2px 0 rgba(12,42,28,.18);
}
div[class*="st-key-msg_actions"] button:hover{background:var(--lime);}

.app-footer{margin-top:28px;font-size:.86rem;}
.hint-card{padding:18px 20px;}

/* 状态栏与胶囊同宽居中 */
[data-testid="stBottomBlockContainer"]::after{
  max-width:min(720px,62vw) !important;margin-inline:auto !important;
}
.app-footer{font-size:.78rem !important;line-height:1.9;}
.app-footer .made{font-size:.72rem;opacity:.85;}

/* 顶部工具栏只是装饰：别挡住左上角的抽屉图标 */
header[data-testid="stHeader"], .stAppHeader{pointer-events:none !important;background:transparent !important;}
div[class~="st-key-drawer_btn"]{z-index:1400 !important;}

/* 下拉框（背景音乐）像素化 */
[data-testid="stSelectbox"] [data-baseweb="select"] > div{
  border:2px solid var(--edge) !important;border-radius:0 !important;
  background:#eafff1 !important;box-shadow:3px 3px 0 rgba(12,42,28,.5) !important;
  font-family:var(--font-body) !important;font-size:1.02rem !important;
  min-height:34px;
}
[data-testid="stSelectbox"] [data-baseweb="popover"] li{font-family:var(--font-body) !important;font-size:1rem !important;}
/* 1.66 的 selectbox 是 React Aria 实现（没有 data-baseweb），真正可见的框是 ComboBox 内的第一个 div */
[data-testid="stSelectbox"] div.react-aria-ComboBox > div{
  border:3px solid var(--edge) !important;border-radius:0 !important;
  background:#eafff1 !important;box-shadow:4px 4px 0 rgba(12,42,28,.55) !important;
  min-height:40px;
}
[data-testid="stSelectbox"] div.react-aria-ComboBox input{
  font-family:var(--font-body) !important;font-size:1.05rem !important;color:#14392a !important;
}
[data-testid="stSelectbox"] div.react-aria-ComboBox button svg{color:var(--edge) !important;}
[role="listbox"]{border:3px solid var(--edge) !important;border-radius:0 !important;background:#f4fff8 !important;
  box-shadow:4px 4px 0 rgba(12,42,28,.55) !important;font-family:var(--font-body);}
[role="option"]{font-family:var(--font-body) !important;font-size:1.02rem !important;color:#14392a !important;}
[role="option"][data-focused]{background:#d7ffe4 !important;}


/* 知识库列表 */
.lib-row{font-family:var(--font-body);font-size:.94rem;line-height:1.32;color:#1d4a2c;padding:2px 0 2px 2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
div[class~="st-key-lib_list"]{gap:2px !important;}
div[class~="st-key-lib_list"] [data-testid="stHorizontalBlock"]{align-items:center;gap:4px;}
div[class~="st-key-lib_list"] button,div[class~="st-key-read_queue_list"] button{width:23px !important;min-width:23px !important;height:23px !important;min-height:23px !important;padding:0 !important;font-size:.72rem !important;line-height:1;}
div[class~="st-key-lib_list"] button>div{padding:0 !important;}
div[class~="st-key-lib_list"] [data-testid="stHorizontalBlock"],div[class~="st-key-read_queue_list"] [data-testid="stHorizontalBlock"]{flex-wrap:nowrap !important;align-items:center;gap:3px;}
div[class~="st-key-lib_list"] [data-testid="stHorizontalBlock"]>div,div[class~="st-key-read_queue_list"] [data-testid="stHorizontalBlock"]>div{min-width:0 !important;}
div[class~="st-key-read_queue_list"]{gap:2px !important;}
div[class~="st-key-lib_list"] button,div[class~="st-key-read_queue_list"] button{box-shadow:2px 2px 0 rgba(12,42,28,.55) !important;}
div[class~="st-key-lib_list"] .lib-row,div[class~="st-key-read_queue_list"] .lib-row{max-width:100%;}
div[class~="st-key-drawer"] [data-testid="stCaptionContainer"] p{color:#2b5c3f !important;font-size:.82rem;}

/* ================= 新手引导 Banner ================= */
.start-banner{
  position:relative;margin:2px 0 14px 0;padding:22px 24px 20px 24px;
  border:4px solid var(--edge);
  background:linear-gradient(135deg,#c9ffd8 0%,#9ef0c2 38%,#7fe6ff 100%);
  box-shadow:8px 8px 0 rgba(12,42,28,.82);
  overflow:hidden;
}
.start-banner::after{
  content:"";position:absolute;inset:0;pointer-events:none;
  background:
    repeating-linear-gradient(90deg,rgba(255,255,255,.28) 0 2px,transparent 2px 6px);
  opacity:.5;
}
.start-kicker{
  font-family:var(--font-px);font-size:.5rem;letter-spacing:.34em;
  color:#14512f;opacity:.85;
}
.start-title{
  font-family:var(--font-px);font-size:1.32rem;line-height:1.55;
  margin:10px 0 6px 0;color:#0b2f1c;letter-spacing:.02em;
  text-shadow:3px 3px 0 rgba(255,255,255,.65);
}
.start-sub{
  font-family:var(--font-body);font-size:1.02rem;line-height:1.6;
  color:#17452c;margin:0 0 14px 0;
}
.flow-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;}
.flow-step{
  display:inline-flex;align-items:center;gap:7px;
  padding:7px 11px;border:3px solid var(--edge);background:#f4fff8;
  box-shadow:3px 3px 0 rgba(12,42,28,.55);
  font-family:var(--font-body);font-size:.98rem;color:#14392a;white-space:nowrap;
  transition:transform .16s ease,background .16s ease;
}
.flow-step:hover{transform:translate(-1px,-2px);background:#e4ffef;}
.flow-step b{
  font-family:var(--font-px);font-size:.54rem;color:#0b2f1c;
  background:var(--lime);border:2px solid var(--edge);padding:2px 4px;
}
.flow-arrow{font-size:1rem;color:#14512f;opacity:.8;}
div[class~="st-key-banner_cta"] button{
  min-height:52px !important;font-size:.62rem !important;
  background:linear-gradient(135deg,#a9ffc6,#63c887) !important;
  box-shadow:6px 6px 0 rgba(12,42,28,.8) !important;
  margin-bottom:12px;
}
div[class~="st-key-banner_cta"] button:hover{
  background:linear-gradient(135deg,#c7ffd8,#7ddfa0) !important;
}

/* ================= 右下角静音球 ================= */
div[class~="st-key-mute_ball"]{
  position:fixed;right:18px;bottom:18px;z-index:1500;
  width:auto;background:rgba(245,255,249,.92);
  border:3px solid var(--edge);padding:7px 12px 7px 10px;
  box-shadow:4px 4px 0 rgba(12,42,28,.75);
  
}
div[class~="st-key-mute_ball"] label p{
  font-family:var(--font-px) !important;font-size:.52rem !important;
  color:#14392a !important;letter-spacing:.04em;
}
div[class~="st-key-mute_ball"] [data-testid="stWidgetLabel"]{margin-bottom:0 !important;}

/* ================= 系统自检行 ================= */
.check-row{
  display:flex;align-items:center;gap:8px;flex-wrap:wrap;
  border:2px solid var(--edge);background:#f4fff8;
  padding:6px 9px;margin-bottom:7px;
  font-family:var(--font-body);font-size:.96rem;color:#14392a;
  box-shadow:2px 2px 0 rgba(12,42,28,.45);
}
.check-row b{font-family:var(--font-px);font-size:.5rem;letter-spacing:.04em;}
.check-row span{margin-left:auto;}
.check-row i{font-style:normal;font-size:.78rem;color:#5a7a68;width:100%;}

/* 精读队列里的自动分级徽章 */
.rq-badge{
  display:inline-block;margin-left:6px;padding:1px 6px;
  border:2px solid var(--edge);background:#d8ffe6;
  font-family:var(--font-px);font-size:.5rem;letter-spacing:.06em;color:#14532d;
  box-shadow:2px 2px 0 rgba(12,42,28,.5);
}
div[class~="st-key-arxiv_search"]{margin-bottom:6px;}
div[class~="st-key-read_queue_list"]{gap:2px;}

/* ============ 性能层：GPU 合成 + 渲染隔离（不减任何特效） ============ */
div[class*="st-key-glass"],
div[class~="st-key-drawer"],
div[class~="st-key-drawer_btn"],
.hero, .hero-min, .panel, .hint-card, .task-pill, .app-footer,
div[class~="st-key-msg_actions"], div[class~="st-key-quick_actions"] {
  contain: paint;
  transform: translateZ(0);
  backface-visibility: hidden;
}

.hero-dino, .dino-loader svg, .pet, .pet-body, .pixel-layer {
  
  transform: translateZ(0);
  backface-visibility: hidden;
}

.pixel-layer, .pixel-layer::before, .pixel-layer::after {
  pointer-events: none !important;
  contain: strict;
}

[data-testid="stChatMessage"] {
  transform: translateZ(0);
  contain: content;
  backface-visibility: hidden;
}

div[class~="st-key-drawer"] {  }

@media (prefers-reduced-motion: reduce) {
  .pixel-layer::after {  }
}

@media (max-width: 900px) {
  .pixel-layer::after { display: none !important; }
}

/* ============ 性能层 2：持续动画元素交给 GPU ============ */
.star, .hero-title, .task-pill, .task-pill .pulse, .status-bar,
.dino-loader, .dino-loader svg, .pet, .pet-body, .pixel-layer {
  transform: translateZ(0);
  
  backface-visibility: hidden;
}
[data-testid="stChatMessage"] {
  transform: translateZ(0);
  
}

/* ============ 通行证（鉴权页） ============ */
.auth-card {
  max-width: 620px; margin: 6vh auto 1.4rem; padding: 1.5rem 1.6rem;
  border: 4px solid var(--edge);
  background: rgba(244, 255, 248, .92);
  box-shadow: 8px 8px 0 rgba(12, 42, 28, .22);
  contain: paint; transform: translateZ(0);
}
.auth-kicker {
  font-family: var(--font-px); font-size: .52rem; letter-spacing: .28em;
  color: #2f6d46; margin-bottom: .5rem;
}
.auth-title {
  font-family: var(--font-px); font-size: 1.12rem; line-height: 1.7;
  margin: 0 0 .5rem; color: #10341f !important; letter-spacing: .04em;
}
.auth-sub {
  font-family: var(--font-body); font-size: 1.06rem; line-height: 1.7;
  color: #2c5340 !important; margin: 0;
}
div[class*="st-key-gate_key"] input,
div[class*="st-key-gate_code"] input {
  font-family: var(--font-body); font-size: 1.05rem;
  border: 3px solid var(--edge) !important; border-radius: 0 !important;
  background: #f2fff7 !important;
}
div[class*="st-key-gate_submit"] button { min-height: 48px; }
div[class*="st-key-logout_btn"] button { border-color: #7a2f2f !important; color: #7a2f2f !important; }



</style>
"""

PIXEL_CSS = PIXEL_CSS_TEMPLATE.replace("__PSP__", FONT_PSP_B64).replace("__VT__", FONT_VT323_B64)


CLICK_JS = ""  # 已停用：按“删除所有 animation/@keyframes”的要求关闭点击像素星特效



IRIS_JS = ""  # 已停用：光圈扩散转场依赖 @keyframes，随动画一起移除

