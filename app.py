"""论论文 · 主控。

这里只做四件事：
1) 页面配置与主题样式注入；
2) 鉴权门禁（auth_utils）；
3) 路由：沉浸式工坊 → guide_page，否则 → chat_page；
4) 把「公共上下文」交给下层模块，业务逻辑一律不进这个文件。
"""

import streamlit as st
import streamlit.components.v1 as components

import ui_styles as U
from auth_utils import require_auth
from chat_page import render_chat_page
from guide_page import render_guide_page
from pixel_theme import CLICK_JS, IRIS_JS, PIXEL_CSS
from sidebar_ui import render_sidebar

st.set_page_config(page_title="论论文 · 论文写作工作台", page_icon="🦖", layout="wide")

# ---- 主题：静态 CSS（无动画、无 backdrop-filter、无 mousemove） --------------
st.markdown(PIXEL_CSS, unsafe_allow_html=True)
if CLICK_JS:
    components.html(CLICK_JS, height=0)
if IRIS_JS:
    components.html(IRIS_JS, height=0)

# ---- 鉴权：没有通行证就停在这里（require_auth 内部会 st.stop） --------------
if "page" not in st.session_state:
    st.session_state.page = "main"

require_auth()

# ---- 路由 -------------------------------------------------------------------
if st.session_state.page == "guide":
    render_guide_page(U.dino_svg)
    st.stop()

# ---- 公共上下文：ui_styles 里的一切（含 ALL 常量与组件） ---------------------
CTX = {k: v for k, v in vars(U).items() if not k.startswith("__")}

# ---- 抽屉 / 侧边栏 → 返回本轮设置 -------------------------------------------
state = {**CTX, **render_sidebar(CTX)}
state.setdefault("dino_svg", U.dino_svg)

# ---- 主聊天页 ---------------------------------------------------------------
render_chat_page(state)
