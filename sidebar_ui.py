"""侧边栏层：抽屉里的全部控件（模式、模型、音效、文献库、精读、自检、退出登录）。

对外只暴露 render_sidebar(ctx)：吃一个上下文字典，吐一个「本轮设置」字典。
"""

import html

import chart_engine as charts
from auth_utils import _auth_clear_store  # 退出登录时清本地凭据
from my_agent import DEFAULT_MODEL, MODELS, MODE_LABELS  # noqa: F401  (供模式/模型下拉框)
import streamlit as st
from sound_engine import (  # noqa: F401  (BGM_TRACKS 供下拉框使用)
    BGM_TRACKS,
    load_sound_settings,
    render_sound,
    save_sound_settings,
)


def _agent_namespace() -> dict:
    """把 my_agent 的公开名字取过来（延迟导入，绕开导入顺序问题）。

    拆分 app.py 时，下层模块靠 `globals().update(ctx)` 拿常量；而 ctx 只来自
    ui_styles。ui_styles 不再顶层 import my_agent 之后，MODE_LABELS / MODELS /
    READING_LEVELS 这类常量就必须在这里补上，否则运行到那行就 NameError。
    """
    import my_agent as _m

    return {k: v for k, v in vars(_m).items() if not k.startswith("_")}


def _auto_level_queue(queue_names: list[str], topic: str, t: dict, lang: str, critical: bool, model: str) -> None:
    """逐篇读开头 ~2000 字，让模型按课题判 S/A/B/C，结果写进 read_levels 并回填级别。"""
    from concurrent.futures import ThreadPoolExecutor

    paths = dict(st.session_state.get("read_queue_paths") or {})
    docs: list[tuple[str, Path]] = []
    for name in queue_names:
        raw = paths.get(name)
        path = Path(raw) if raw else None
        if path is None or not path.exists():
            continue
        docs.append((name, path))
    if not docs:
        st.info(t["autolevel_empty"])
        return

    agent = get_agent("reading", False, False, lang, critical, model)

    def _grade(item: tuple[str, Path]) -> tuple[str, str, str]:
        name, path = item
        try:
            info = extract_text(path.read_bytes(), path.name, max_chars=2600)
            head = (info.get("text") or "")[:2000]
            reply = str(agent.run(build_auto_level_prompt(topic, Path(name).stem, head, 1000)))
            level, reason = parse_auto_level(reply)
        except Exception as exc:  # noqa: BLE001 - 单篇失败不影响其它
            return name, "", f"{type(exc).__name__}: {exc}"
        return name, level, reason

    graded: dict[str, dict] = dict(st.session_state.get("read_levels") or {})
    order = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, level, reason in pool.map(_grade, docs):
            if level:
                graded[name] = {"level": level, "reason": reason}

    st.session_state["read_levels"] = graded
    if graded:
        best = min(graded.values(), key=lambda it: order.get(it.get("level", "C"), 3))
        top = best.get("level", "C")
        st.session_state["read_level_target"] = top
        label = READING_LEVELS.get(top, READING_LEVELS["C"])["label"]
        st.success(t["autolevel_done"].format(n=len(graded), top=label if lang == "zh" else top))


def render_sidebar(ctx: dict) -> dict:
    """渲染抽屉与右下角静音球，返回本轮生效的设置（lang / t / mode / pdf / style / sound…）。"""

    globals().update(_agent_namespace())
    globals().update(ctx)  # UI 层同名项优先

    # ---------------------------------------------------------------------------

    # ---------------------------------------------------------------------------
    # 抽屉面板状态（默认收起）
    # ---------------------------------------------------------------------------
    sound_defaults = load_sound_settings()
    # 本次会话第一次进来：用户要求声音默认全关，别被上次的选择带跑
    if not st.session_state.get("sound_session_init"):
        st.session_state["sound_session_init"] = True
        sound_defaults = {"bgm": "none", "sfx": False}

    if "drawer_open" not in st.session_state:
        st.session_state["drawer_open"] = False
    drawer_open = st.session_state["drawer_open"]

    st.markdown(
        "<style>div[class~='st-key-drawer']{transform:translateX("
        + ("0" if drawer_open else "-108%")
        + ")}</style>",
        unsafe_allow_html=True,
    )

    # 左上角像素图标：点它开 / 合面板
    with st.container(key="drawer_btn"):
        if st.button("🦖", key="drawer_toggle", help="设置 / Settings", type="tertiary"):
            # 先翻转状态再重跑：这样下面对抽屉注入的 transform 才是新值
            st.session_state["drawer_open"] = not drawer_open
            st.rerun()

    # ---------------------------------------------------------------------------
    # 抽屉面板：语言、模式、润色、思辨、PDF、文献库、高级设置
    # ---------------------------------------------------------------------------
    with st.container(key="drawer"):
        head_l, head_r = st.columns([5, 1], vertical_alignment="center")
        with head_l:
            st.markdown('<div class="drawer-title">DINO · SETUP</div>', unsafe_allow_html=True)
        with head_r:
            if st.button("✕", key="drawer_close", help="收起面板 / Close", type="tertiary"):
                st.session_state["drawer_open"] = False
                st.rerun()

        lang_label = st.radio(
            "Language / 语言",
            ["中文", "English"],
            horizontal=True,
            label_visibility="collapsed",
            key="lang_radio",
        )
        lang = "zh" if lang_label == "中文" else "en"
        t = L[lang]

        st.markdown(f'<div class="drawer-label">{t["s_mode"]}</div>', unsafe_allow_html=True)
        mode = st.radio(
            "mode",
            options=list(MODE_LABELS[lang]),
            format_func=lambda key: MODE_LABELS[lang][key],
            label_visibility="collapsed",
        )

        st.markdown(f'<div class="drawer-label">{t["s_model"]}</div>', unsafe_allow_html=True)
        model = st.selectbox(
            t["model_label"],
            options=list(MODELS.keys()),
            format_func=lambda key: MODELS[key][lang],
            index=list(MODELS.keys()).index(DEFAULT_MODEL),
            key="model_select",
            label_visibility="collapsed",
        )
        _prov = session_provider()
        if _prov == "deepseek":
            st.caption(_billing_hint(model, lang))
        else:
            from auth_utils import PROVIDERS as _PROVIDERS

            _label = _PROVIDERS.get(_prov, {}).get("label", _prov)
            model = resolve_model_name()
            st.caption(
                f"当前接入：{_label}｜模型 {model}（上面这个下拉只在 DeepSeek 下生效）"
            )

        polish = st.checkbox(t["polish_label"], help=t["polish_help"])
        critical = st.toggle(t["critical_label"], help=t["critical_help"], key="critical_toggle")
        redblue = st.toggle(t["redblue_label"], help=t["redblue_help"], key="redblue_toggle")
        if redblue:
            st.caption(t["redblue_on"])

        st.markdown(f'<div class="drawer-label">{t["s_sound"]}</div>', unsafe_allow_html=True)
        bgm_key = st.selectbox(
            t["bgm_label"],
            options=list(BGM_TRACKS.keys()),
            format_func=lambda key: t["bgm_" + key],
            index=list(BGM_TRACKS.keys()).index(sound_defaults["bgm"]),
            key="bgm_select",
        )
        sfx_on = st.toggle(
            t["sfx_label"],
            value=sound_defaults["sfx"],
            help=t["sfx_help"],
            key="sfx_toggle",
        )
        save_sound_settings(bgm_key, sfx_on)

        pdf_ready = False
        pdf_use = "qa"
        with st.expander(t["s_pdf"], expanded=False):
            uploaded = st.file_uploader(
                t["pdf_label"], type=["pdf", "docx", "txt", "md"], key="pdf_uploader"
            )
            if uploaded is not None:
                signature = (uploaded.name, uploaded.size)
                if st.session_state.get("pdf_signature") != signature:
                    with st.spinner(t["pdf_parsing"]):
                        try:
                            info = extract_text(uploaded.getvalue(), uploaded.name)
                            st.session_state["pdf"] = {**info, "name": uploaded.name}
                        except Exception as exc:
                            st.session_state["pdf"] = None
                            st.error(t["pdf_failed"].format(err=f"{type(exc).__name__}: {exc}"))
                    st.session_state["pdf_signature"] = signature

                info = st.session_state.get("pdf")
                if info:
                    pdf_ready = True
                    suffix = t["pdf_truncated"] if info["truncated"] else ""
                    template = t["pdf_loaded"] if info.get("kind") == "pdf" else t["pdf_loaded_doc"]
                    st.caption(
                        template.format(
                            name=info["name"],
                            pages=info.get("pages", 0),
                            blocks=info.get("blocks", 0),
                            chars=info["chars"],
                            suffix=suffix,
                        )
                    )
                    pdf_use = st.radio(
                        t["pdf_use"],
                        options=["qa", "src"],
                        format_func=lambda key: t["pdf_use_qa"] if key == "qa" else t["pdf_use_src"],
                    )
            else:
                st.session_state.pop("pdf_signature", None)
                st.session_state.pop("pdf", None)

        with st.expander(t["s_library"], expanded=True):
            lib_files = st.file_uploader(
                t["lib_upload"],
                type=["pdf", "docx", "txt", "md"],
                accept_multiple_files=True,
                key="lib_uploader",
            )
            if lib_files:
                seen = st.session_state.get("lib_seen") or set()
                st.session_state["lib_seen"] = seen
                for item in lib_files:
                    sig = f"{item.name}:{item.size}"
                    if sig in seen:
                        continue
                    try:
                        path = add_document_to_library(item.name, item.getvalue())
                    except OSError as exc:
                        st.warning(f"{item.name}: {exc}")
                    else:
                        seen.add(sig)
                        st.caption(t["lib_added"].format(name=path.name))
            count = library_count()
            st.caption(t["lib_count"].format(n=count))
            if count == 0:
                st.caption(t["lib_empty"])
            else:
                st.caption(t["lib_files"])
                with st.container(key="lib_list"):
                    for doc in list_library():
                        col_name, col_add, col_del = st.columns([5, 1, 1])
                        with col_name:
                            st.markdown(
                                f'<div class="lib-row" title="{html.escape(doc.name)}">📄 {html.escape(doc.name)}</div>',
                                unsafe_allow_html=True,
                            )
                        with col_add:
                            if st.button(
                                "➕",
                                key=f"addread_{doc.name}",
                                help=t["lib_to_read_help"],
                                type="tertiary",
                            ):
                                queue = list(st.session_state.get("read_queue") or [])
                                if doc.name not in queue:
                                    queue.append(doc.name)
                                st.session_state["read_queue"] = queue
                                st.session_state["lib_queued"] = t["lib_to_read_added"].format(name=doc.name)
                                st.rerun()
                        with col_del:
                            if st.button(
                                "🗑️",
                                key=f"del_{doc.name}",
                                help=t["lib_delete_help"],
                                type="tertiary",
                            ):
                                if delete_library_file(doc.name):
                                    st.session_state["lib_deleted"] = t["lib_deleted"].format(name=doc.name)
                                    st.session_state.pop("lib_seen", None)
                                st.rerun()
            if st.session_state.get("lib_deleted"):
                st.caption(st.session_state["lib_deleted"])
            if st.session_state.get("lib_queued"):
                st.caption(st.session_state["lib_queued"])

        # 写作侧的素材来源与「精读读取方式」彻底解耦：
        #   知识库始终作为全局上下文（内容参考）参与写作；风格只来自用户手写/上传的范文。
        #   侧边栏原来的 radio 现在被移进精读工作台，只决定精读怎么读文件。
        style_text = (st.session_state.get("style_box") or "").strip()
        style_snippet = style_text
        style_source_name = ""

        lib_context_preview = build_library_context()
        material_hint = (
            t["material_hint_content"].format(chars=len(lib_context_preview))
            if lib_context_preview
            else t["material_hint_content_empty"]
        )
        st.caption(material_hint)

        with st.expander(t["advanced"], expanded=False):
            st.caption(t["s_style"])
            if "style_box" not in st.session_state:
                st.session_state["style_box"] = load_style_reference()

            style_file = st.file_uploader(
                t["style_upload"], type=["txt", "md", "docx"], key="style_uploader"
            )
            if style_file is not None:
                try:
                    if style_file.name.lower().endswith(".docx"):
                        text = extract_text(style_file.getvalue(), style_file.name)["text"]
                    else:
                        text = style_file.getvalue().decode("utf-8")
                except Exception:  # noqa: BLE001 - 解析失败就提示，不让页面崩
                    st.error(t["style_decode_failed"])
                    text = None
                if text is not None:
                    if st.session_state.get("style_file_name") != style_file.name + str(style_file.size):
                        save_style_reference(text)
                        st.session_state["style_box"] = text.strip()
                        st.session_state["style_file_name"] = style_file.name + str(style_file.size)
                        st.caption(t["style_loaded"].format(chars=len(text.strip())))

            style_text = st.text_area(t["style_ta"], key="style_box", height=140)
            st.session_state.setdefault("style_intensity_value", 5)
            style_intensity = st.slider(
                t["style_intensity"],
                min_value=1,
                max_value=10,
                step=1,
                key="style_intensity_value",
                help=t["style_intensity_help"],
            )
            st.caption(t["style_tier_hint"].format(tier=t["style_tier_" + style_tier(style_intensity)]))
            if st.button(t["style_save"], use_container_width=True):
                save_style_reference(style_text)
                st.caption(t["style_saved"].format(chars=len(style_text.strip())))

        if st.button(t["clear"], use_container_width=True, key="clear_chat"):
            st.session_state.messages = []
            st.session_state.pop("last_output", None)
            st.session_state.pop("last_outline", None)
            st.rerun()

        # 退出登录：清掉用户 Key / 凭据文件，回到通行证页面
        _who = "🎟️ 邀请码用户" if st.session_state.get("auth_mode") == "invite" else "🔑 自带 Key"
        st.caption(f"当前身份：{_who}")
        if st.button("🚪 退出登录", use_container_width=True, key="logout_btn", type="tertiary"):
            st.session_state.pop("user_api_key", None)
            st.session_state.pop("user_provider", None)
            st.session_state.pop("user_base_url", None)
            st.session_state.pop("user_model", None)
            st.session_state.pop("auth_mode", None)
            st.session_state["auth_status"] = False
            st.session_state.pop("gate_key", None)
            st.session_state.pop("gate_code", None)
            _auth_clear_store()
            st.rerun()

        with st.expander("📊 学术图表（PNG / SVG / Excel）", expanded=False):
            st.caption("描述你想要的图，或直接粘数据；Agent 只产出 JSON 规格，由本地确定性渲染，不执行生成的代码。")
            fig_desc = st.text_area(
                "图表需求 / 数据",
                key="fig_desc",
                height=110,
                placeholder="例如：比较三种方法在四个数据集上的准确率\n基线 71/74/69/73\nOurs 79/83/78/81\nSOTA 77/80/76/79（%）",
            )
            fig_kind = st.selectbox(
                "图表类型",
                options=list(charts.KIND_LABELS.keys()),
                format_func=lambda key: charts.KIND_LABELS[key],
                index=0,
                key="fig_kind",
            )
            fig_clicked = st.button("📊 生成图表", key="fig_run", use_container_width=True, type="primary")

            if fig_clicked:
                if not has_api_key():
                    st.error(api_key_error())
                elif not (fig_desc or "").strip():
                    st.error("先写一句图表需求，或者把数据粘进来。")
                else:
                    with st.spinner("正在设计图表…"):
                        try:
                            painter = get_agent("figure", False, False, lang)
                            raw = painter.run(build_figure_prompt(fig_desc, fig_kind, lang))
                            text = raw if isinstance(raw, str) else getattr(raw, "text", str(raw))
                            spec = charts.parse_figure_spec(text)
                            if not spec:
                                st.session_state["fig_pack"] = None
                                st.error("模型没有返回可解析的图表规格，请把数据或结构写得更具体。")
                            else:
                                png, backend = charts.render_figure(spec, "png")
                                svg, _ = charts.render_figure(spec, "svg")
                                xlsx = charts.figure_to_xlsx(spec, png)
                                st.session_state["fig_pack"] = {
                                    "spec": spec,
                                    "png": png,
                                    "svg": svg,
                                    "xlsx": xlsx,
                                    "backend": backend,
                                }
                        except Exception as exc:  # noqa: BLE001 - 出错只提示，不崩页面
                            st.session_state["fig_pack"] = None
                            st.error(f"图表生成失败：{type(exc).__name__}: {exc}")

            fig_pack = st.session_state.get("fig_pack")
            if fig_pack:
                stem = _figure_stem(fig_pack["spec"])
                st.caption(charts.spec_summary(fig_pack["spec"]))
                st.image(fig_pack["png"], use_container_width=True)
                if fig_pack["spec"].get("caption"):
                    st.caption(f"图注：{fig_pack['spec']['caption']}")
                if fig_pack["spec"].get("source"):
                    st.caption(f"数据来源：{fig_pack['spec']['source']}")
                col_png, col_svg, col_xlsx = st.columns(3)
                with col_png:
                    st.download_button(
                        "PNG",
                        data=fig_pack["png"],
                        file_name=f"{stem}.png",
                        mime="image/png",
                        key="fig_dl_png",
                        use_container_width=True,
                    )
                with col_svg:
                    st.download_button(
                        "SVG",
                        data=fig_pack["svg"],
                        file_name=f"{stem}.svg",
                        mime="image/svg+xml",
                        key="fig_dl_svg",
                        use_container_width=True,
                    )
                with col_xlsx:
                    st.download_button(
                        "Excel",
                        data=fig_pack["xlsx"],
                        file_name=f"{stem}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="fig_dl_xlsx",
                        use_container_width=True,
                    )
                if fig_pack["spec"].get("kind") == "flow":
                    if fig_pack.get("backend") == "matplotlib":
                        st.caption("本机没有 Graphviz，已用内置渲染器画好；也可以下载 DOT 源码，在 Graphviz / draw.io 里继续改。")
                    st.download_button(
                        "下载 DOT 源码",
                        data=charts.flow_dot_source(fig_pack["spec"]),
                        file_name=f"{stem}.dot",
                        mime="text/vnd.graphviz",
                        key="fig_dl_dot",
                        use_container_width=True,
                    )

        with st.expander(t["s_reading"], expanded=False):
            material_mode = st.radio(
                t["material_mode_rd"],
                options=["content", "style"],
                format_func=lambda key: t["material_content_rd"] if key == "content" else t["material_style_rd"],
                key="material_mode",
                horizontal=True,
            )
            st.caption(t["material_scope_hint"])
            reading_topic = st.text_input(
                t["read_topic"], key="read_topic_input", placeholder=t["read_topic_ph"]
            )
            with st.container(key="arxiv_search"):
                st.caption(t["arxiv_prev"])
                arxiv_cols = st.columns([4, 1])
                with arxiv_cols[0]:
                    arxiv_query = st.text_input(
                        "arXiv", key="arxiv_query", placeholder=t["arxiv_ph"], label_visibility="collapsed"
                    )
                with arxiv_cols[1]:
                    arxiv_go = st.button(t["arxiv_btn"], key="arxiv_search_btn", use_container_width=True)
                if arxiv_go:
                    with st.spinner("🔍 正在 arXiv 上翻检索结果…"):
                        try:
                            st.session_state["arxiv_results"] = search_arxiv_entries(arxiv_query, 5)
                        except Exception as exc:  # noqa: BLE001 - 搜索失败只提示
                            st.session_state["arxiv_results"] = []
                            st.warning(t["arxiv_fail"].format(err=f"{type(exc).__name__}: {exc}"))
                results = st.session_state.get("arxiv_results") or []
                if arxiv_go and not results:
                    st.info(t["arxiv_empty"])
                for hit in results:
                    with st.container(key=f"arxiv_row_{hit.get('arxiv_id') or abs(hash(hit.get('title', '')))}"):
                        st.markdown(
                            f"**{html.escape(hit.get('title', ''))}**\n\n"
                            f"<span style='opacity:.7'>{html.escape(hit.get('authors', ''))} · {hit.get('year', '')}</span>",
                            unsafe_allow_html=True,
                        )
                        st.caption((hit.get("summary") or "")[:100] + ("…" if len(hit.get("summary") or "") > 100 else ""))
                        if st.button(t["arxiv_add"], key=f"arxiv_add_{hit.get('arxiv_id')}", type="tertiary"):
                            with st.spinner("⬇️ 正在把 PDF 存到本地临时目录…"):
                                try:
                                    saved = download_arxiv_pdf(
                                        hit.get("pdf_url", ""),
                                        filename=f"{hit.get('arxiv_id') or 'arxiv'} {hit.get('title', '')[:40]}.pdf",
                                    )
                                except Exception as exc:  # noqa: BLE001 - 下载失败不崩
                                    st.warning(t["arxiv_fail"].format(err=f"{type(exc).__name__}: {exc}"))
                                else:
                                    paths = dict(st.session_state.get("read_queue_paths") or {})
                                    paths[saved.name] = str(saved)
                                    st.session_state["read_queue_paths"] = paths
                                    queue_now = list(st.session_state.get("read_queue") or [])
                                    if saved.name not in queue_now:
                                        queue_now.append(saved.name)
                                    st.session_state["read_queue"] = queue_now
                                    st.success(t["arxiv_added"].format(name=saved.name))
                                    st.rerun()

            reading_files = st.file_uploader(
                t["read_upload"],
                type=["pdf", "docx"],
                accept_multiple_files=True,
                key="read_uploader",
            )
            _target_level = st.session_state.pop("read_level_target", None)
            if _target_level in READING_LEVELS:
                st.session_state["read_level_radio"] = _target_level
            reading_level = st.radio(
                t["read_level"],
                options=list(READING_LEVELS),
                format_func=lambda key: (
                    f"{READING_LEVELS[key]['label']} — {READING_LEVELS[key]['desc']}"
                    if lang == "zh"
                    else f"{READING_LEVELS[key]['label_en']} — {READING_LEVELS[key]['desc_en']}"
                ),
                key="read_level_radio",
            )
            st.caption(
                (READING_LEVELS[reading_level]["desc"] + " · "
                 + "、".join(READING_LEVELS[reading_level]["bullets"]))
                if lang == "zh" and READING_LEVELS[reading_level]["bullets"]
                else (
                    "Only compares the title and says whether the paper is worth reading."
                    if lang == "en" and reading_level == "D"
                    else (
                        READING_LEVELS[reading_level]["desc_en"]
                        if lang == "en"
                        else "只比对标题，输出「相关度极低，建议放弃阅读」。"
                    )
                )
            )
            queue_names = list(st.session_state.get("read_queue") or [])
            st.caption(t["queue_title"].format(n=len(queue_names)) if queue_names else t["queue_empty"])
            if queue_names:
                levels = st.session_state.get("read_levels") or {}
                with st.container(key="read_queue_list"):
                    for qname in queue_names:
                        tag = levels.get(qname) or {}
                        badge = f'<span class="rq-badge">🤖 {tag["level"]}</span>' if tag.get("level") else ""
                        c_name, c_del = st.columns([6, 1])
                        with c_name:
                            st.markdown(
                                    f'<div class="lib-row" title="{html.escape(qname)}">📄 {html.escape(qname)}'
                                    f' {badge}</div>',
                                    unsafe_allow_html=True,
                                )
                        with c_del:
                            if st.button(
                                "🗑️",
                                key=f"rq_del_{qname}",
                                help=t["queue_remove"],
                                type="tertiary",
                            ):
                                st.session_state["read_queue"] = [x for x in queue_names if x != qname]
                                st.rerun()
                if len(queue_names) >= 2:
                    if st.button(t["autolevel_btn"], key="read_autolevel", type="secondary", use_container_width=True):
                        with st.spinner("🤖 正在逐篇读开头、按课题判级…"):
                            _auto_level_queue(queue_names, reading_topic, t, lang, critical, model)
                        st.rerun()
                if st.button(t["queue_clear"], key="read_queue_clear", type="tertiary"):
                    st.session_state["read_queue"] = []
                    st.session_state["read_queue_paths"] = {}
                    st.session_state["read_levels"] = {}
                    st.rerun()

            read_custom_on = st.checkbox(t["read_custom_toggle"], key="read_custom_on")
            reading_custom = ""
            if read_custom_on:
                reading_custom = st.text_input(
                    t["read_custom_label"],
                    key="read_custom_text",
                    placeholder=t["read_custom_ph"],
                )
            read_clicked = st.button(
                t["read_run"], key="read_run", type="primary", use_container_width=True
            )

        # --- 系统自检（抽屉最底部）--------------------------------------------
        with st.expander(t["selfcheck"], expanded=False):
            if st.button(t["selfcheck_run"], key="selfcheck_run", type="tertiary", use_container_width=True):
                # 只有点了按钮才发请求，平时不发；异常一律留在这一块里
                try:
                    st.session_state["selfcheck_rows"] = _run_self_check()
                except Exception as exc:
                    st.session_state["selfcheck_rows"] = []
                    st.error(f"{type(exc).__name__}: {exc}")
            rows = st.session_state.get("selfcheck_rows") or []
            if not rows:
                st.caption(t["selfcheck_idle"])
            else:
                try:
                    st.markdown(
                        "".join(_self_check_line(row, t) for row in rows),
                        unsafe_allow_html=True,
                    )
                except Exception as exc:
                    st.error(f"{type(exc).__name__}: {exc}")

    # ---------------------------------------------------------------------------
    # 右下角：静音 / 勿扰（默认开启，压住所有声音）
    # ---------------------------------------------------------------------------
    with st.container(key="mute_ball"):
        muted = st.toggle(
            t["mute_on"] if st.session_state.get("mute_toggle", True) else t["mute_off"],
            value=True,
            key="mute_toggle",
            help=t["mute_help"],
        )
    if muted:
        bgm_key, sfx_on = "none", False

    render_sound(bgm_key, sfx_on)

    return {k: v for k, v in list(locals().items()) if k != "ctx"}

