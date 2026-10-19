"""点对点引导模式（沉浸式论文工坊）：完全独立的一屏，深绿森林风。

由 app.py 在 `st.session_state.page == "guide"` 时调用 `render_guide_page()`，
随后 `st.stop()`，因此主页的抽屉、胶囊输入框、聊天区都不会渲染。
"""

from __future__ import annotations

import streamlit as st
import time
from pathlib import Path

from my_agent import (
    GUIDE_STEPS,
    GUIDE_STEP_INDEX,
    GUIDE_TOTAL,
    api_key_error,
    build_example_prompt,
    build_guide_prompt,
    build_material_block,
    build_library_context,
    build_paragraph_rewrite_prompt,
    guide_paper_docx,
    has_api_key,
    load_workspace_draft,
    parse_guide_verdict,
    save_workspace_draft,
)

DRAFT_FILE_HINT = "workspace_draft.json"


# ---------------------------------------------------------------------------
# 森林主题 CSS（只在引导页注入）
# ---------------------------------------------------------------------------
FOREST_CSS = """
<style>
/* ---------- 隐藏主页外壳 ---------- */
[data-testid="stSidebar"],
[data-testid="stBottomBlockContainer"],
[data-testid="stHeader"],
[data-testid="stToolbar"],
div[class~="st-key-drawer"],
div[class~="st-key-drawer_btn"],
.pet,
.status-bar {
  display: none !important;
}

/* ---------- 流动深绿背景 ---------- */
.stApp {
  background: transparent !important;
  color: #d9ffe8 !important;
}
/* 性能优化：背景流光只动 transform（原来动 background-position，等于每帧重绘整屏） */
html body::before {
  content: "";
  position: fixed;
  inset: -14%;
  z-index: -1;
  pointer-events: none;
  background: linear-gradient(150deg, #04140c 0%, #0a2c1a 24%, #07211a 48%, #0b3324 72%, #04150e 100%);
  transform: translate3d(0, 0, 0) scale(1.08);
  will-change: transform;
  contain: strict;
  backface-visibility: hidden;
  animation: forestFlow 42s ease-in-out infinite alternate;
}
html body { background: #04150e !important; }
@keyframes forestFlow {
  0%   { transform: translate3d(-3%, -2%, 0) scale(1.06); }
  50%  { transform: translate3d(2%, 3%, 0) scale(1.12); }
  100% { transform: translate3d(3%, -1%, 0) scale(1.06); }
}
[data-testid="stAppViewContainer"], [data-testid="stMain"] { background: transparent !important; }

/* ---------- 星星 / 萤火 ---------- */
.forest-layer { position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; }
.forest-layer .fog {
  position: absolute; width: 42vw; height: 42vw; border-radius: 50%;
  background: radial-gradient(circle, rgba(60, 200, 130, .16), transparent 68%);
  filter: blur(30px);
}
.forest-layer .fog.f1 { left: -8vw; top: 4vh; animation: fogDrift 26s ease-in-out infinite; }
.forest-layer .fog.f2 { right: -10vw; top: 22vh; animation: fogDrift 31s ease-in-out infinite reverse; }
@keyframes fogDrift { 50% { transform: translate(4vw, 4vh) scale(1.1); } }

/* ---------- 底部像素树林 + 草地 ---------- */
.forest-ground {
  position: fixed; left: 0; right: 0; bottom: 0; height: 168px; z-index: 0;
  pointer-events: none; overflow: hidden;
}
.forest-ground .grass {
  position: absolute; left: 0; right: 0; bottom: 0; height: 34px;
  background:
    repeating-linear-gradient(90deg, #0f5836 0 10px, #0b4429 10px 20px, #12683f 20px 28px);
  border-top: 4px solid #073d24;
}
.forest-ground .tree {
  position: absolute; bottom: 26px;
  width: 54px; height: 108px;
  background:
    linear-gradient(#0d3f27, #0d3f27) 50% 100% / 10px 34px no-repeat,
    conic-gradient(from 180deg at 50% 0, transparent 0 0);
}
.forest-ground .tree::before,
.forest-ground .tree::after {
  content: ""; position: absolute; left: 50%; transform: translateX(-50%);
  background: #12643c;
}
.forest-ground .tree::before {
  bottom: 30px; width: 54px; height: 30px;
  clip-path: polygon(50% 0, 100% 100%, 0 100%);
}
.forest-ground .tree::after {
  bottom: 52px; width: 38px; height: 26px;
  background: #16804b;
  clip-path: polygon(50% 0, 100% 100%, 0 100%);
}
.forest-ground .tree.t1 { left: 4%;  transform: scale(.9); }
.forest-ground .tree.t2 { left: 13%; transform: scale(1.18); }
.forest-ground .tree.t3 { left: 26%; transform: scale(.78); opacity: .92; }
.forest-ground .tree.t4 { right: 6%;  transform: scale(1.05); }
.forest-ground .tree.t5 { right: 17%; transform: scale(.86); opacity: .95; }
.forest-ground .tree.t6 { right: 30%; transform: scale(.7); opacity: .85; }

/* ---------- 飘落树叶 ---------- */
.leaf {
  position: absolute; top: -40px; width: 12px; height: 12px;
  background: #3ddc84;
  clip-path: polygon(50% 0, 100% 50%, 50% 100%, 0 50%);
  opacity: .75;
  animation: leafFall linear infinite;
}
.leaf.l1 { left: 8%;  animation-duration: 13s; animation-delay: 0s;   background: #57ff9f; }
.leaf.l2 { left: 22%; animation-duration: 17s; animation-delay: 2.5s; background: #2fbf72; }
.leaf.l3 { left: 37%; animation-duration: 11s; animation-delay: 1.2s; background: #8dffb2; }
.leaf.l4 { left: 52%; animation-duration: 19s; animation-delay: 4s;   background: #3ddc84; }
.leaf.l5 { left: 68%; animation-duration: 15s; animation-delay: 3.1s; background: #26a86a; }
.leaf.l6 { left: 81%; animation-duration: 12s; animation-delay: .8s;  background: #8dffb2; }
.leaf.l7 { left: 93%; animation-duration: 18s; animation-delay: 5.4s; background: #57ff9f; }
@keyframes leafFall {
  0%   { transform: translateY(-60px) rotate(0deg);     opacity: 0; }
  10%  { opacity: .8; }
  50%  { transform: translateY(52vh) translateX(38px) rotate(180deg); }
  100% { transform: translateY(105vh) translateX(-24px) rotate(360deg); opacity: 0; }
}

/* ---------- 内容层 ---------- */
[data-testid="stMainBlockContainer"], .block-container {
  position: relative; z-index: 2;
  max-width: 960px !important;
  padding-top: 1.2rem !important;
  padding-bottom: 150px !important;
}
.g-head { display: flex; align-items: center; gap: .8rem; flex-wrap: wrap; margin-bottom: .4rem; }
.g-kicker {
  font-family: var(--font-px, "Press Start 2P", monospace);
  font-size: .56rem; letter-spacing: .22em; color: #7dffb4; text-transform: uppercase;
}
.g-title {
  font-size: 1.9rem; font-weight: 800; margin: .1rem 0 .3rem;
  background: linear-gradient(100deg, #8dffb2, #35e2a0 45%, #7dffd8);
  -webkit-background-clip: text; background-clip: text; color: transparent;
  filter: drop-shadow(2px 2px 0 rgba(0, 0, 0, .55));
}
.g-sub { color: #9fe6c2; font-size: .95rem; margin-bottom: .9rem; }

.g-card {
  border: 2px solid #2f8a5c; background: rgba(6, 34, 21, .82);
  padding: 14px 16px; margin: 10px 0 14px;
  box-shadow: 5px 5px 0 rgba(0, 0, 0, .45);
}
.g-card h4 {
  margin: 0 0 .5rem; font-size: .82rem; letter-spacing: .12em;
  color: #7dffb4; text-transform: uppercase;
  font-family: var(--font-px, "Press Start 2P", monospace);
}
.g-mission { color: #cfeeff; font-size: 1rem; line-height: 1.6; }
.g-check { margin-top: 10px; padding: 10px 12px; border-left: 4px solid #8dffb2; background: rgba(141,255,178,.12); color: #d8ffe8; font-size: .95rem; line-height: 1.62; }
.g-badge {
  display: inline-block; padding: 2px 8px; font-size: .72rem; font-weight: 700;
  border: 2px solid #0b2a1c; margin-left: .4rem;
}
.g-badge.pass { background: #8dffb2; color: #06301c; }
.g-badge.revise { background: #ffd166; color: #402a00; }
.g-badge.auto { background: #7dffd8; color: #06301c; }

/* 用户填的内容回显 */
.g-echo { color: #d9ffe8; font-size: .95rem; line-height: 1.7; }
.g-echo b { color: #7dffb4; }
.g-echo small { color: #8fb9a3; }

/* Streamlit 控件在深色底上的配色 */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stSelectbox"] div[data-react-aria-ComboBox] > div,
div.react-aria-ComboBox > div {
  background: rgba(3, 26, 16, .92) !important;
  color: #e6fff0 !important;
  border: 2px solid #2f8a5c !important;
  border-radius: 0 !important;
  font-size: 1rem !important;
}
[data-testid="stTextInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder { color: #6f9b84 !important; }
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] span { color: #b9f0d3 !important; }
[data-testid="stCaptionContainer"] p { color: #8fb9a3 !important; }

button[data-testid^="stBaseButton"] {
  border: 2px solid #8dffb2 !important;
  background: rgba(10, 46, 30, .95) !important;
  color: #b6ffd4 !important;
  border-radius: 0 !important;
  box-shadow: 4px 4px 0 rgba(0, 0, 0, .55) !important;
  font-weight: 700 !important;
}
button[data-testid^="stBaseButton"]:hover {
  background: #8dffb2 !important; color: #042113 !important;
  transform: translate(-1px, -1px) !important;
}
button[data-testid="stBaseButton-primary"] {
  background: linear-gradient(180deg, #8dffb2, #35e2a0) !important;
  color: #042113 !important; border-color: #d9ffe8 !important;
}
button[data-testid="stBaseButton-primary"]:hover { background: #b6ffd4 !important; }

[data-testid="stProgress"] > div {
  background: rgba(6, 34, 21, .9) !important;
  border: 2px solid #2f8a5c !important;
  border-radius: 0 !important; height: 16px !important;
}
[data-testid="stProgress"] div[role="progressbar"] > div {
  background: repeating-linear-gradient(90deg, #8dffb2 0 8px, #35e2a0 8px 16px) !important;
  border-radius: 0 !important;
}

[data-testid="stAlert"] {
  border: 2px solid #2f8a5c !important; border-radius: 0 !important;
  background: rgba(6, 34, 21, .92) !important; color: #e6fff0 !important;
}
[data-testid="stExpander"] details {
  border: 2px solid #2f8a5c !important; border-radius: 0 !important;
  background: rgba(6, 34, 21, .86) !important;
}
[data-testid="stExpander"] summary { background: rgba(14, 60, 38, .95) !important; color: #b6ffd4 !important; }
[data-testid="stExpander"] summary p { color: #b6ffd4 !important; }

[data-testid="stRadioOption"] {
  background: rgba(6, 34, 21, .86) !important;
  border: 2px solid #2f8a5c !important; border-radius: 0 !important;
  box-shadow: 3px 3px 0 rgba(0, 0, 0, .45) !important;
}
[data-testid="stRadioOption"] p { color: #d9ffe8 !important; }
[data-testid="stRadioOption"][data-selected="true"] {
  background: linear-gradient(180deg, #8dffb2, #35e2a0) !important;
}
[data-testid="stRadioOption"][data-selected="true"] p { color: #042113 !important; }
[data-testid="stRadioOption"] > div:first-of-type {
  background: #042113 !important; border-color: #8dffb2 !important;
}
[data-testid="stRadioOption"][data-selected="true"] > div:first-of-type > div { background: #042113 !important; }

/* 导师 / 代笔结果框 */
.g-result {
  border-left: 5px solid #8dffb2; background: rgba(9, 44, 29, .9);
  padding: 12px 16px; margin: 12px 0;
  color: #e6fff0; line-height: 1.75;
}
.g-result .g-result-title {
  font-family: var(--font-px, "Press Start 2P", monospace);
  font-size: .62rem; letter-spacing: .12em; color: #8dffb2; margin-bottom: .4rem;
}
.g-dino { position: fixed; right: 22px; bottom: 78px; z-index: 1; opacity: .9; pointer-events: none;
  animation: gDinoBob 3.4s steps(2, end) infinite; }
@keyframes gDinoBob { 50% { transform: translateY(-6px); } }

@media (max-width: 760px) {
  .forest-ground .tree.t3, .forest-ground .tree.t6 { display: none; }
  .g-dino { display: none; }
  .g-title { font-size: 1.4rem; }
}
[data-testid="stMainBlockContainer"], .block-container{
  animation: guideReveal .72s cubic-bezier(.2,.9,.2,1) both;
}
@keyframes guideReveal{
  0%{opacity:0;transform:scale(.975) translateY(14px);filter:brightness(1.25) saturate(.8);}
  60%{opacity:1;filter:brightness(1.02);}
  100%{opacity:1;transform:none;filter:none;}
}

/* ============ 性能层：GPU 合成 + 渲染隔离（特效全保留） ============ */
.forest-layer, .forest-ground, .g-card, .g-hero, .g-stepbar {
  contain: paint;
  transform: translateZ(0);
  backface-visibility: hidden;
}
.forest-layer .fog, .forest-ground .leaf, .g-dino {
  will-change: transform, opacity;
  transform: translateZ(0);
  backface-visibility: hidden;
  pointer-events: none;
}
@media (prefers-reduced-motion: reduce) {
  html body::before { animation: none !important; }
  .forest-ground .leaf { animation: none !important; }
}
</style>
"""


# ---------------------------------------------------------------------------
# 状态初始化
# ---------------------------------------------------------------------------
def _gi_key(step_id: str, field_key: str) -> str:
    return f"gi_{step_id}_{field_key}"


def _restore_draft() -> None:
    """进入工坊先读 workspace_draft.json，把上次填的内容回填到输入框。

    只填「本轮还没有值」的键，绝不覆盖用户当前正在输入的东西。
    """
    if st.session_state.get("guide_restored"):
        return
    st.session_state["guide_restored"] = True
    loaded = load_workspace_draft()
    if not loaded:
        return
    try:
        if loaded.get("mode") in ("coach", "auto"):
            st.session_state["guide_mode"] = loaded["mode"]
        step_saved = int(loaded.get("step", 0) or 0)
        st.session_state["guide_step"] = max(0, min(step_saved, GUIDE_TOTAL - 1))
        inputs = loaded.get("inputs") or {}
        if isinstance(inputs, dict):
            for key, value in inputs.items():
                if str(key).startswith("gi_") and key not in st.session_state:
                    st.session_state[key] = value
        drafts = loaded.get("drafts") or {}
        if isinstance(drafts, dict):
            for sid, text in drafts.items():
                if sid not in st.session_state.get("guide_drafts", {}):
                    st.session_state.guide_drafts[sid] = text
        reviews = loaded.get("reviews") or {}
        if isinstance(reviews, dict):
            for sid, text in reviews.items():
                if sid not in st.session_state.get("guide_reviews", {}):
                    st.session_state.guide_reviews[sid] = text
    except (TypeError, ValueError):
        return


def _autosave(force: bool = False) -> None:
    """把所有输入框与草稿写进 workspace_draft.json，内容没变就不重复写盘。"""
    try:
        inputs = {}
        for step in GUIDE_STEPS:
            for item in step["fields"]:
                key = _gi_key(step["id"], item["key"])
                value = st.session_state.get(key, "")
                if str(value or "").strip():
                    inputs[key] = str(value)
        payload = {
            "version": 1,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": st.session_state.get("guide_mode", "coach"),
            "step": int(st.session_state.get("guide_step", 0)),
            "inputs": inputs,
            "drafts": dict(st.session_state.get("guide_drafts", {})),
            "reviews": dict(st.session_state.get("guide_reviews", {})),
        }
        blob = repr(sorted(payload["inputs"].items())) + repr(
            {k: len(str(v)) for k, v in payload["drafts"].items()}
        ) + repr({k: len(str(v)) for k, v in payload["reviews"].items()})
        if not force and blob == st.session_state.get("guide_autosave_blob"):
            return
        if save_workspace_draft(payload):
            st.session_state["guide_autosave_blob"] = blob
            st.session_state["guide_autosave_at"] = payload["saved_at"]
        else:
            st.session_state["guide_autosave_error"] = "存档写入失败（可能目录只读）"
    except Exception:  # noqa: BLE001 - 存档失败绝不打断写作
        st.session_state["guide_autosave_error"] = "存档写入失败（已忽略）"


def _init_state() -> None:
    _restore_draft()
    st.session_state.setdefault("guide_mode", "coach")
    st.session_state.setdefault("guide_step", 0)
    st.session_state.setdefault("guide_fields", {})
    st.session_state.setdefault("guide_reviews", {})
    st.session_state.setdefault("guide_drafts", {})
    st.session_state.setdefault("guide_done_step", -1)


def _collect_fields(step: dict) -> dict:
    """把这一步各个输入框的当前值收集成 dict。"""
    values = {}
    for item in step["fields"]:
        values[item["key"]] = str(st.session_state.get(f"gi_{step['id']}_{item['key']}", "") or "").strip()
    return values


def _all_fields() -> dict:
    """合并所有步骤已填内容（用于组装论文与给模型看上下文）。"""
    merged = dict(st.session_state.get("guide_fields", {}))
    for step in GUIDE_STEPS:
        merged.update(_collect_fields(step))
    return merged


def _run_model(step_id: str, mode: str, fields: dict) -> tuple[str | None, str | None]:
    """调用模型：返回 (文本, 错误信息)。任何异常都变成友好提示，不掀翻页面。"""
    from my_agent import build_agent  # 延迟导入，避免 app 顶层循环依赖

    if not has_api_key():
        return None, api_key_error()

    library_context = ""
    if mode == "auto" and step_id == "body":
        try:
            library_context = build_library_context(max_total_chars=6000, per_file_chars=2000)
        except Exception:  # noqa: BLE001 - 文献库异常不该挡住写作
            library_context = ""

    mode_key = "guide_coach" if mode == "coach" else "guide_auto"
    try:
        prompt = build_guide_prompt(
            step_id,
            fields,
            mode,
            library_context=library_context,
            thesis=str(fields.get("thesis", "") or ""),
        )
        materials = list(st.session_state.get("writing_materials") or [])
        picked = st.session_state.get("guide_mat_cite")
        if (
            step_id in ("body", "intro")
            and isinstance(picked, int)
            and 0 <= picked < len(materials)
        ):
            block = build_material_block([materials[picked]])
            if block:
                prompt += (
                    "\n\n【写作大本营素材（来自你自己精读过的文献，可直接引用其结论与数据）】\n"
                    + block
                    + "\n请把这些素材的具体发现融进论证里，不要只说「有研究表明」，要写明是哪篇文献的什么发现。"
                )
        agent = build_agent(mode_key)
        text = str(agent.run(prompt)).strip()
        if not text:
            return None, "模型返回了空内容，请再试一次；如果反复为空，请检查网络或密钥额度。"
        return text, None
    except Exception as exc:  # noqa: BLE001 - 前端兜底
        name = type(exc).__name__
        msg = str(exc)
        if "api" in msg.lower() or "auth" in msg.lower() or "401" in msg:
            return None, f"调用模型失败（{name}）：密钥可能无效或已过期。请在 `.env` 或环境变量里更新 `DEEPSEEK_API_KEY`。"
        if "timeout" in msg.lower() or "connect" in msg.lower():
            return None, f"调用模型超时（{name}）：网络不通或代理问题，请检查网络后重试。"
        return None, f"调用模型失败（{name}）：{msg[:300]}"


def _run_examples(step: dict, item: dict, fields: dict) -> tuple[str | None, str | None]:
    """生成 2-3 个示例（短输出）。失败只返回错误文案，不抛异常。"""
    from my_agent import build_agent

    if not has_api_key():
        return None, api_key_error()
    try:
        prompt = build_example_prompt(
            step_label=step["label"],
            mission=step["mission"],
            field_label=item["label"],
            current=str(fields.get(item["key"], "") or ""),
        )
        agent = build_agent("example")
        text = str(agent.run(prompt)).strip()
        if not text:
            return None, "模型这次没给出例子，再点一次试试。"
        return text[:900], None
    except Exception as exc:  # noqa: BLE001 - 前端兜底
        return None, f"生成示例失败（{type(exc).__name__}）：{str(exc)[:200]}"


def _run_rewrite(text: str, flavour: str) -> tuple[str | None, str | None]:
    """只改写用户粘进来的这一小段。"""
    from my_agent import build_agent

    if not has_api_key():
        return None, api_key_error()
    if not str(text or "").strip():
        return None, "先把要改的那一段粘进来。"
    try:
        prompt = build_paragraph_rewrite_prompt(text, flavour)
        agent = build_agent("polish")
        out = str(agent.run(prompt)).strip()
        if not out:
            return None, "模型返回了空内容，再试一次。"
        return out, None
    except Exception as exc:  # noqa: BLE001
        return None, f"改写失败（{type(exc).__name__}）：{str(exc)[:200]}"


def _render_header() -> None:
    left, right = st.columns([1, 3], vertical_alignment="center")
    with left:
        if st.button("🔙 返回主界面", key="guide_back"):
            st.session_state.page = "main"
            st.rerun()
    with right:
        st.markdown(
            '<div class="g-head">'
            '<div><div class="g-kicker">Guided Forest Workshop</div>'
            '<div class="g-title">🌿 沉浸式论文工坊</div></div></div>',
            unsafe_allow_html=True,
        )
    st.markdown(
        '<div class="g-sub">点对点引导：一步一个小目标，导师只审不写，代笔直接成稿。</div>',
        unsafe_allow_html=True,
    )


def _render_mode_and_progress() -> str:
    mode_label = st.radio(
        "模式",
        ["🧭 导师模式（Coach）", "✍️ 代笔模式（Auto）"],
        horizontal=True,
        key="guide_mode_radio",
        label_visibility="collapsed",
    )
    mode = "coach" if mode_label.startswith("🧭") else "auto"
    st.session_state.guide_mode = mode

    step = int(st.session_state.get("guide_step", 0))
    st.progress((step + 1) / GUIDE_TOTAL)
    st.caption(
        f"第 {step + 1} / {GUIDE_TOTAL} 步 · "
        + ("导师只审不写，改到 PASS 才能进入下一步" if mode == "coach" else "直接生成该部分成稿，可随时回看修改")
    )
    return mode


def _render_step(step_index: int) -> None:
    step = GUIDE_STEPS[step_index]
    step_id = step["id"]
    mode = st.session_state.guide_mode

    note_html = ""
    if step.get("note"):
        note_html = f'<div class="g-check">{step["note"]}</div>'
    st.markdown(
        f'<div class="g-card"><h4>{step["label"]}</h4>'
        f'<div class="g-mission">{step["mission"]}</div>{note_html}</div>',
        unsafe_allow_html=True,
    )

    fields_now = _all_fields()

    if step_id == "body":
        materials = list(st.session_state.get("writing_materials") or [])
        if materials:
            labels = [
                f"{(item.get('title') or '素材')[:36]}（{item.get('level') or '素材'}）"
                for item in materials
            ]
            picked_idx = st.selectbox(
                "📚 从大本营调用素材",
                options=list(range(len(materials))),
                format_func=lambda idx: labels[idx],
                key="guide_mat_pick",
            )
            c_fill, c_cite = st.columns(2)
            with c_fill:
                if st.button("📥 填入表单", key="guide_mat_fill", use_container_width=True):
                    body_text = str(materials[picked_idx].get("body") or "")
                    current = str(st.session_state.get("gi_body_ev1", "") or "")
                    st.session_state["gi_body_ev1"] = (current + "\n\n" + body_text).strip()[:6000]
                    st.success("已填进证据栏，按需要再润色。")
                    st.rerun()
            with c_cite:
                citing = st.session_state.get("guide_mat_cite") == picked_idx
                if st.button(
                    "取消引用" if citing else "✅ 生成时引用这份素材",
                    key="guide_mat_cite_btn",
                    type="primary" if citing else "secondary",
                    use_container_width=True,
                ):
                    st.session_state["guide_mat_cite"] = None if citing else picked_idx
                    st.rerun()
        else:
            st.caption("（大本营还是空的：先在精读工作台点「📥 存入大本营」）")

    for item in step["fields"]:
        key = _gi_key(step_id, item["key"])
        label = item["label"]
        if item.get("options"):
            options = item["options"]
            current = st.session_state.get(key, options[0])
            index = options.index(current) if current in options else 0
            st.selectbox(label, options, index=index, key=key)
        elif item.get("area"):
            st.text_area(label, key=key, height=118, placeholder=item.get("ph", ""))
        else:
            st.text_input(label, key=key, placeholder=item.get("ph", ""))

        # 「没灵感？看个例子」：只展示示例，绝不写回输入框
        example_key = f"guide_example_{step_id}_{item['key']}"
        if st.button(
            "💡 没灵感？看个例子",
            key=f"guide_ex_{step_id}_{item['key']}",
            type="tertiary",
            help="让模型按这一步的任务给 2-3 个示例，参考着改，不会覆盖你写的内容",
        ):
            with st.spinner("🦖 正在想几个像样的例子…"):
                samples, example_error = _run_examples(step, item, fields_now)
            if example_error:
                st.warning(example_error)
            else:
                st.session_state[example_key] = samples
                st.toast("例子已生成，往下看一眼就能照着改", icon="💡")
        saved_examples = st.session_state.get(example_key)
        if saved_examples:
            st.info(saved_examples)
            if st.button("✖️ 收起示例", key=f"guide_ex_hide_{step_id}_{item['key']}", type="tertiary"):
                st.session_state.pop(example_key, None)
                st.rerun()
    filled = _collect_fields(step)
    empty = [item["label"] for item in step["fields"] if not filled.get(item["key"])]
    if empty:
        st.caption("还有没填的：" + "、".join(empty) + "（可以先写一句，导师/代笔都会基于它工作）")

    action_label = "🧭 交给导师审这一步" if mode == "coach" else "✍️ 生成这一部分的草稿"
    error_shown_here = False
    if st.button(action_label, key=f"guide_run_{step_id}", type="primary"):
        started_at = time.time()
        with st.spinner("🦖 大模型正在奋笔疾书…（通常 10-40 秒，请别关页面）"):
            text, error = _run_model(step_id, mode, fields_now)
        if error:
            # 关键：失败时绝不 rerun，否则 st.error 会被立刻刷掉，看起来就是「点了没反应」
            st.session_state["guide_last_error"] = error
            st.session_state["guide_last_error_step"] = step_id
            st.error(error)
            error_shown_here = True
        else:
            st.session_state.pop("guide_last_error", None)
            st.session_state.pop("guide_last_error_step", None)
            if mode == "coach":
                st.session_state.guide_reviews[step_id] = text
                st.session_state[f"guide_verdict_{step_id}"] = parse_guide_verdict(text or "")
            else:
                st.session_state.guide_drafts[step_id] = text
                st.session_state.step_content = text  # 这一步的最终成稿
            st.session_state[f"guide_secs_{step_id}"] = round(time.time() - started_at, 1)
            st.rerun()

    if (
        not error_shown_here
        and st.session_state.get("guide_last_error")
        and st.session_state.get("guide_last_error_step") == step_id
    ):
        st.error(st.session_state["guide_last_error"])

    review = st.session_state.guide_reviews.get(step_id)
    draft = st.session_state.guide_drafts.get(step_id)

    if mode == "coach" and review:
        verdict = st.session_state.get(f"guide_verdict_{step_id}", "REVISE")
        badge = "pass" if verdict == "PASS" else "revise"
        label = "PASS · 可以进入下一步" if verdict == "PASS" else "REVISE · 请按建议修改后重新提交"
        st.markdown(
            f'<div class="g-result"><div class="g-result-title">导师意见 '
            f'<span class="g-badge {badge}">{label}</span></div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(review)
        if verdict != "PASS":
            st.info("先按上面 3 条建议改输入框，再点一次「交给导师审这一步」。改到 PASS，「下一步」才会解锁。")

    if mode == "auto" and draft:
        st.markdown(
            '<div class="g-result"><div class="g-result-title">代笔草稿 '
            '<span class="g-badge auto">可直接使用</span></div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(draft)
        st.download_button(
            "⬇️ 下载这一部分（Markdown）",
            data=draft,
            file_name=f"guide_{step_id}.md",
            mime="text/markdown",
            key=f"guide_dl_{step_id}",
        )

        with st.expander("✂️ 段落精修（选一段，单独改写）", expanded=False):
            st.caption("把草稿里想改的那一段整段粘进来，只改这一小段，不会动其它内容。")
            para = st.text_area(
                "要改的段落",
                key=f"guide_para_{step_id}",
                height=140,
                placeholder="例如：随着人工智能的快速发展，越来越多的研究者开始关注……",
            )
            chosen = None
            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button("更学术严谨", key=f"guide_p_ac_{step_id}", use_container_width=True):
                    chosen = "academic"
            with c2:
                if st.button("更批判性", key=f"guide_p_cr_{step_id}", use_container_width=True):
                    chosen = "critical"
            with c3:
                if st.button("精简字数", key=f"guide_p_co_{step_id}", use_container_width=True):
                    chosen = "concise"

            if chosen:
                with st.spinner("🦖 正在琢磨这一段…"):
                    revised, rewrite_error = _run_rewrite(para, chosen)
                if rewrite_error:
                    st.warning(rewrite_error)
                else:
                    st.session_state[f"guide_para_out_{step_id}"] = {
                        "before": para,
                        "after": revised,
                        "flavour": chosen,
                    }

            pair = st.session_state.get(f"guide_para_out_{step_id}")
            if pair:
                left, right = st.columns(2)
                with left:
                    st.markdown("**修改前**")
                    st.write(pair["before"])
                with right:
                    st.markdown("**修改后**")
                    st.write(pair["after"])
                if st.button("↩️ 替换回原文", key=f"guide_p_apply_{step_id}", type="primary"):
                    current_draft = str(st.session_state.guide_drafts.get(step_id, "") or "")
                    if pair["before"] and pair["before"] in current_draft:
                        st.session_state.guide_drafts[step_id] = current_draft.replace(
                            pair["before"], pair["after"], 1
                        )
                        st.session_state["step_content"] = st.session_state.guide_drafts[step_id]
                        st.session_state.pop(f"guide_para_out_{step_id}", None)
                        st.success("已替换进草稿。")
                        st.rerun()
                    else:
                        st.warning("这段文字没有原样出现在草稿里（可能你手动改过），对比保留着，可以手动复制。")


def _step_unlocked(step_index: int) -> bool:
    """当前步是否达标（决定「下一步」能否点）。"""
    step_id = GUIDE_STEPS[step_index]["id"]
    mode = st.session_state.guide_mode
    if mode == "coach":
        return st.session_state.get(f"guide_verdict_{step_id}") == "PASS"
    return bool(st.session_state.guide_drafts.get(step_id))


def _render_nav(step_index: int) -> None:
    step = GUIDE_STEPS[step_index]
    left, mid, right = st.columns([1, 2, 1], vertical_alignment="center")
    with left:
        if step_index > 0 and st.button("← 上一步", key="guide_prev"):
            st.session_state.guide_step = step_index - 1
            st.rerun()
    with mid:
        unlocked = _step_unlocked(step_index)
        if step_index < GUIDE_TOTAL - 1:
            if st.button(
                "进入下一步 →",
                key="guide_next",
                type="primary",
                disabled=not unlocked,
                help=None if unlocked else "导师给出 PASS（或代笔生成草稿）后才能继续",
            ):
                st.session_state.guide_fields = _all_fields()
                st.session_state.guide_step = step_index + 1
                st.session_state.guide_done_step = max(
                    int(st.session_state.get("guide_done_step", -1)), step_index
                )
                st.rerun()
        else:
            st.caption("已经是最后一步：往下看，组装并导出。")
    with right:
        st.caption(f"{GUIDE_STEPS[step_index]['label']}")


def _render_export() -> None:
    if int(st.session_state.get("guide_step", 0)) < GUIDE_TOTAL - 1:
        return
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown(
        '<div class="g-card"><h4>✨ 组装并导出</h4>'
        '<div class="g-mission">把 1-6 步的最终内容按 引言 → 研究问题 → 方法 → 论证 → 结论 的学术顺序'
        '组装成完整长文，导出 .docx。正文里的 <b>[n]</b> 会全局重排，文末自动生成完全对应的 '
        'APA 参考文献列表；文献综述里的对比表格、以及 [此处建议插入图表：…] 占位符都会原样保留。</div></div>',
        unsafe_allow_html=True,
    )
    fields = _all_fields()
    st.session_state.guide_fields = fields
    drafts = dict(st.session_state.get("guide_drafts", {}))
    missing = [s["label"] for s in GUIDE_STEPS if not drafts.get(s["id"]) and not _step_has_input(s)]
    if missing:
        st.caption("这些步骤还没有内容，导出时会留空：" + "、".join(missing))

    extra_refs_raw = st.text_area(
        "参考文献（可选，一行一条；正文里的 [n] 会自动重排成与文末列表严格对应的编号）",
        key="guide_extra_refs",
        height=110,
        placeholder="例如：Smith, J. (2021). AI writing in EFL classrooms. Journal of Writing Research. https://doi.org/10.xxxx/xxxxx",
    )
    extra_refs = [ln.strip() for ln in str(extra_refs_raw or "").splitlines() if ln.strip()]

    with st.expander("先预览组装后的全文（含对比表格 / 图表占位符 / 参考文献）"):
        try:
            from my_agent import guide_paper_markdown

            _, preview = guide_paper_markdown(fields, drafts, extra_refs)
            st.markdown(preview)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"预览失败：{type(exc).__name__}: {exc}")

    if st.button("✨ 组装并导出完整论文", key="guide_assemble", type="primary"):
        try:
            data = guide_paper_docx(fields, drafts, extra_refs)
        except Exception as exc:  # noqa: BLE001
            st.error(f"组装 Word 失败：{type(exc).__name__}: {exc}")
        else:
            st.success("组装完成：正文数字标号与文末 APA 参考文献一一对应，点下面按钮下载。")
            st.download_button(
                "⬇️ 下载完整论文 .docx",
                data=data,
                file_name="guided_paper.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="guide_download_paper",
            )


def _step_has_input(step: dict) -> bool:
    values = _collect_fields(step)
    return any(str(v).strip() for v in values.values())


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def render_guide_page(dino_svg=None) -> None:
    """渲染整个引导页（主页元素全部隐藏）。"""
    _init_state()
    st.markdown(FOREST_CSS, unsafe_allow_html=True)

    layer = (
        '<div class="forest-layer">'
        '<span class="fog f1"></span><span class="fog f2"></span>'
        '<span class="leaf l1"></span><span class="leaf l2"></span><span class="leaf l3"></span>'
        '<span class="leaf l4"></span><span class="leaf l5"></span><span class="leaf l6"></span>'
        '<span class="leaf l7"></span>'
        "</div>"
        '<div class="forest-ground">'
        '<span class="tree t1"></span><span class="tree t2"></span><span class="tree t3"></span>'
        '<span class="tree t4"></span><span class="tree t5"></span><span class="tree t6"></span>'
        '<span class="grass"></span>'
        "</div>"
    )
    if dino_svg is not None:
        layer += f'<div class="g-dino">{dino_svg(58)}</div>'
    st.markdown(layer, unsafe_allow_html=True)

    if not has_api_key():
        st.error(api_key_error())

    _render_header()
    if st.session_state.get("guide_last_error"):
        st.caption("上次调用没有成功，错误已显示在对应步骤下方；改完可以直接重试。")
    mode = _render_mode_and_progress()
    st.caption("当前模式：" + ("🧭 导师模式（Coach）· 只审不写" if mode == "coach" else "✍️ 代笔模式（Auto）· 直接成稿"))

    step_index = int(st.session_state.get("guide_step", 0))
    step_index = max(0, min(step_index, GUIDE_TOTAL - 1))
    st.session_state.guide_step = step_index

    _render_step(step_index)
    _render_nav(step_index)
    _render_export()

    # 实时存档：任何输入变化都会落到 workspace_draft.json（内容没变就不写盘）
    _autosave()
    if st.session_state.get("guide_autosave_error"):
        st.caption("⚠️ " + st.session_state["guide_autosave_error"])
    elif st.session_state.get("guide_autosave_at"):
        st.caption(
            f"💾 已自动存档 · {st.session_state['guide_autosave_at']} · "
            f"{Path(DRAFT_FILE_HINT).name}（刷新或重开网页会自动读回）"
        )
