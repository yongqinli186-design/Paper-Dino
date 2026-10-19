"""主界面层：Banner、任务状态、对话历史、快捷动作、精读工作台、胶囊输入框、页脚。

对外只暴露 render_chat_page(state)：state 由 main 的上下文 + render_sidebar 的返回值合并而成。
"""

import html

import rag_utils
import streamlit as st


def _agent_namespace() -> dict:
    """把 my_agent 的公开名字取过来（延迟导入，绕开导入顺序问题）。

    拆分 app.py 时，下层模块靠 `globals().update(ctx)` 拿常量；而 ctx 只来自
    ui_styles。ui_styles 不再顶层 import my_agent 之后，MODE_LABELS / MODELS /
    READING_LEVELS 这类常量就必须在这里补上，否则运行到那行就 NameError。
    """
    import my_agent as _m

    return {k: v for k, v in vars(_m).items() if not k.startswith("_")}


def render_chat_page(state: dict) -> None:
    """渲染主聊天页面（所有副作用都通过 st.* 与 st.session_state 完成）。"""

    globals().update(_agent_namespace())
    globals().update(state)  # UI 层同名项优先

    # ---------------------------------------------------------------------------
    # 当前任务
    # ---------------------------------------------------------------------------
    style_active = bool(style_snippet)
    pdf_source = pdf_ready and pdf_use == "src"

    if polish:
        task_key = "polish"
        task_label = t["task_polish"]
    elif pdf_ready and not pdf_source:
        task_key = "pdf"
        task_label = t["task_pdf"].format(name=st.session_state["pdf"]["name"])
    else:
        task_key = mode
        task_label = MODE_LABELS[lang][mode]
        if pdf_source:
            task_label = t["task_source"].format(name=st.session_state["pdf"]["name"])
    if style_active:
        task_label += t["task_style_suffix"]
    if critical:
        task_label += t["task_critical_suffix"]

    # 背景层与吉祥物
    st.markdown(
        '<div class="pixel-layer">'
        '<span class="star s1"></span><span class="star s2"></span>'
        '<span class="star s3"></span><span class="star s4"></span>'
        '<span class="star s5"></span><span class="star s6"></span>'
        "</div>"
        f'<div class="pet"><div class="pet-body">{dino_svg(42)}</div>'
        '<span class="pet-tag">dino.exe</span></div>',
        unsafe_allow_html=True,
    )

    # 新手引导 Banner（主页最上方）+ 横向六步流程图
    _flow_html = '<span class="flow-arrow">➔</span>'.join(
        f'<span class="flow-step"><b>{idx}</b>{html.escape(name)}</span>'
        for idx, name in enumerate(t["flow"], 1)
    )
    st.markdown(
        f'<div class="start-banner">'
        f'<div class="start-banner-inner">'
        f'<div class="start-kicker">START HERE</div>'
        f'<h2 class="start-title">{html.escape(t["banner_title"])}</h2>'
        f'<p class="start-sub">{html.escape(t["banner_sub"])}</p>'
        f'<div class="flow-row">{_flow_html}</div>'
        f"</div></div>",
        unsafe_allow_html=True,
    )
    with st.container(key="banner_cta"):
        if st.button(
            t["banner_cta"],
            key="banner_start",
            type="primary",
            use_container_width=True,
            help="进入六步引导：选题 → 检索 → 大纲 → 扩写 → 盲审 → 导出",
        ):
            st.session_state.page = "guide"
            st.session_state.setdefault("guide_step", 0)
            st.rerun()

    # 极简标题区：只留大标题 + 一行副标题
    st.markdown(
        f'<div class="hero-min">'
        f'<div class="hero-dino">{dino_svg(76)}</div>'
        f'<div class="hero-min-text">'
        f'<div class="hero-kicker">{t["hero_kicker"]}</div>'
        f'<h1 class="hero-title">{html.escape(t["title"])}</h1>'
        f'<p class="hero-sub">{html.escape(t["hero_note"])}</p>'
        f"</div></div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div class="task-pill"><span class="pulse"></span>'
        f"<b>{html.escape(task_label)}</b></div>",
        unsafe_allow_html=True,
    )

    # 沉浸式引导模式入口
    with st.container(key="guide_entry"):
        if st.button(
            "🌿 进入沉浸式论文工坊",
            key="enter_guide",
            type="primary",
            use_container_width=True,
            help="点对点六步写作：导师只审不写 / 代笔直接成稿，最后组装成 Word",
        ):
            st.session_state.page = "guide"
            st.session_state.setdefault("guide_step", 0)
            st.rerun()

    if "read_queue" not in st.session_state:
        st.session_state.read_queue = []
    if "writing_materials" not in st.session_state:
        st.session_state.writing_materials = []
    if "figures" not in st.session_state:
        st.session_state.figures = []

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # ---------------------------------------------------------------------------
    # 快捷操作：小图标 + 悬停提示
    # ---------------------------------------------------------------------------
    outline_ready = bool(st.session_state.get("last_outline") or st.session_state.get("last_output"))
    review_hint = t["review_help"] if outline_ready else t["review_need"]
    longform_hint = t["longform_help"] if outline_ready else t["longform_need"]

    with st.container(key="quick_actions"):
        review_clicked = st.button(
            "",
            icon=":material/gavel:",
            key="act_review",
            help=t["tip_review"] + " · " + review_hint,
            disabled=not outline_ready,
            type="tertiary",
        )
        expand_clicked = st.button(
            "",
            icon=":material/notes:",
            key="act_expand",
            help=t["tip_expand"] + " · " + longform_hint,
            disabled=not outline_ready,
            type="tertiary",
        )
        hq_clicked = st.button(
            "",
            icon=":material/auto_awesome:",
            key="act_hq",
            help=t["tip_hq"] + " · " + longform_hint,
            disabled=not outline_ready,
            type="tertiary",
        )
        if st.session_state.get("last_saved"):
            st.caption("💾 " + t["saved_out"].format(name=st.session_state["last_saved"]))

    if not st.session_state.messages:
        hint_key = {
            "polish": "hint_polish",
            "pdf": "hint_pdf",
            "expand": "hint_expand",
            "abstract": "hint_abstract",
            "litree": "hint_litree",
        }
        if pdf_source:
            hint_key[task_key] = "hint_source"
        hint_text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t[hint_key.get(task_key, "hint_outline")])
        st.markdown(
            f'<div class="hint-card"><span class="hint-emoji">👋</span><div>{hint_text}</div></div>',
            unsafe_allow_html=True,
        )

    if st.session_state.messages:
        for idx, message in enumerate(st.session_state.messages):
            role = message["role"]
            avatar = message.get("avatar") or ("🧑‍🎓" if role == "user" else "🦖")
            with st.chat_message(role, avatar=avatar):
                kind = message.get("kind")
                if role == "assistant" and kind == "review":
                    render_review_message(idx, message["content"])
                else:
                    st.markdown(message["content"])
                    if role == "assistant":
                        cite_verify_block(message["content"], str(idx))
                    render_plot_blocks(idx, message["content"])
                if role == "assistant":
                    md_name = (
                        "blind_review.md"
                        if kind == "review"
                        else ("paper_body.md" if kind == "body" else "paper_outline.md")
                    )
                    with st.container(key=f"msg_actions_{idx}"):
                        st.download_button(
                            "",
                            icon=":material/download:",
                            data=message["content"],
                            file_name=md_name,
                            mime="text/markdown",
                            key=f"download-{idx}",
                            help=t["tip_md"],
                            type="tertiary",
                        )
                        docx_download(
                            message["content"],
                            md_name.replace(".md", ".docx"),
                            key=f"download-docx-{idx}",
                            help_text=t["tip_docx"],
                            gate_citations=(kind == "body"),
                            with_figures=(kind in ("body", None)),
                        )


    def _apply_replacement(before: str, after: str) -> int:
        """把「修改后段落」替换回正文（聊天正文 / 大纲 / 分节正文都试一遍），返回替换处数。"""
        before = (before or "").strip()
        after = (after or "").strip()
        if not before or not after:
            return 0
        changed = 0

        def _swap(text: str) -> tuple[str, bool]:
            if before and before in text:
                return text.replace(before, after, 1), True
            return text, False

        for msg in st.session_state.get("messages") or []:
            if msg.get("role") != "assistant":
                continue
            new_text, hit = _swap(str(msg.get("content") or ""))
            if hit:
                msg["content"] = new_text
                changed += 1

        sections = st.session_state.get("body_sections") or {}
        for sid, text in list(sections.items()):
            new_text, hit = _swap(str(text or ""))
            if hit:
                sections[sid] = new_text
                changed += 1

        for key in ("last_output", "last_outline"):
            value = str(st.session_state.get(key) or "")
            if not value:
                continue
            new_value, hit = _swap(value)
            if hit:
                st.session_state[key] = new_value
                changed += 1

        return changed


    def render_review_message(idx: int, content: str) -> None:
        """盲审消息：两个标签页 —— 存在问题 / 修改建议（左右对比 + 一键替换）。"""
        problems, pairs = parse_review_pairs(content)
        tab_problems, tab_fixes = st.tabs([t["rv_tab_problems"], t["rv_tab_fixes"]])
        with tab_problems:
            st.markdown(problems or content)
        with tab_fixes:
            if not pairs:
                st.info(t["rv_no_pairs"])
                st.markdown(content)
                return
            for j, pair in enumerate(pairs):
                left, right = st.columns(2)
                with left:
                    st.markdown(f"**{t['rv_before']}**")
                    st.write(pair["before"])
                with right:
                    st.markdown(f"**{t['rv_after']}**")
                    st.write(pair["after"])
                if st.button(t["rv_apply"], key=f"rv_apply_{idx}_{j}", type="secondary"):
                    changed = _apply_replacement(pair["before"], pair["after"])
                    if changed:
                        st.success(t["rv_applied"].format(n=changed))
                        st.rerun()
                    else:
                        st.warning(t["rv_apply_fail"])


    def run_redblue_turn(writer_agent, reviewer_agent, prompt_text: str, loader_text: str) -> tuple[str, bool]:
        """红蓝对抗：写 → 审 → 改（最多 2 轮），中间过程折叠展示。返回 (终稿, 是否成功)。"""
        transcript: list[dict] = []
        slot = st.empty()

        def on_step(role: str, text: str, round_no: int) -> None:
            transcript.append({"role": role, "text": text, "round": round_no})
            if role == "reviewer":
                slot.markdown(f'<div class="dino-loader">{dino_svg(26)}<span>'
                              f'{t["rb_reviewing"].format(n=round_no)}<span class="dots"></span></span></div>',
                              unsafe_allow_html=True)
            elif round_no:
                slot.markdown(f'<div class="dino-loader">{dino_svg(26)}<span>'
                              f'{t["rb_revising"].format(n=round_no)}<span class="dots"></span></span></div>',
                              unsafe_allow_html=True)

        slot.markdown(
            f'<div class="dino-loader">{dino_svg(26)}<span>{t["rb_writing"]}<span class="dots"></span></span></div>',
            unsafe_allow_html=True,
        )
        try:
            result = run_red_blue(writer_agent.run, reviewer_agent.run, prompt_text, lang=lang, rounds=2, on_step=on_step)
            final = (result.get("final") or "").strip()
            rounds = int(result.get("rounds") or 0)
            ok = bool(final)
        except Exception as exc:  # noqa: BLE001 - 前端兜底
            slot.empty()
            return t["rb_failed"].format(err=f"{type(exc).__name__}: {exc}"), False
        slot.empty()

        with st.expander(t["rb_debate"].format(n=rounds), expanded=False):
            for ev in transcript:
                who = "🔵 Writer" if ev["role"] == "writer" else "🔴 Reviewer"
                head = t["rb_round"].format(n=ev["round"]) if ev["role"] == "writer" else who
                st.markdown(f"**{who} · {head}**")
                body = ev["text"]
                st.markdown(body if len(body) < 2600 else body[:2600] + "…")
                st.divider()
        st.caption(t["rb_final"].format(n=rounds))
        return final, ok


    def rag_context_for_upload(uploaded, query: str, *, k: int = 5, max_chars: int = 3200) -> tuple[str, int]:
        """把上传的 PDF 落到临时目录 → FAISS 检索 → 返回带页码来源的上下文。"""
        if uploaded is None or not (query or "").strip():
            return "", 0
        try:
            tmp_dir = Path(tempfile.gettempdir()) / "paper-dino-rag"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            suffix = Path(uploaded.name).suffix or ".pdf"
            tmp_path = tmp_dir / f"{abs(hash(uploaded.name)) % 10**8}_{Path(uploaded.name).stem[:40]}{suffix}"
            if not tmp_path.exists():
                tmp_path.write_bytes(uploaded.getvalue())
            hits = rag_utils.similarity_search([tmp_path], query, k=k)
            return rag_utils.format_context(hits, max_chars=max_chars), len(hits)
        except Exception:
            return "", 0


    def rag_context_for_library(query: str, *, k: int = 6, max_chars: int = 4200) -> tuple[str, int]:
        """知识库里的文件走 RAG 检索（带 [来源：文件名 - 第X页]）。"""
        try:
            paths = [str(p) for p in sorted(Path(LIBRARY_DIR).glob("*")) if p.is_file()] if "LIBRARY_DIR" in globals() else []
            if not paths:
                import my_agent as _m

                paths = [str(p) for p in sorted(_m.LIBRARY_DIR.glob("*")) if p.is_file()]
            hits = rag_utils.similarity_search(paths, query, k=k)
            return rag_utils.format_context(hits, max_chars=max_chars), len(hits)
        except Exception:
            return "", 0


    def cite_verify_block(content: str, key: str) -> None:
        """点一下才联网：把文末参考文献逐条丢给 Crossref，没命中的标红。"""
        if st.button(t["cite_verify"], key=f"cite_verify_{key}", type="tertiary"):
            titles = extract_reference_titles(content)
            if not titles:
                st.caption(t["cite_none"])
                return
            with st.spinner(t["cite_verifying"]):
                try:
                    results = verify_references(titles)
                except Exception as exc:  # noqa: BLE001
                    st.warning(t["cite_verify_err"].format(err=f"{type(exc).__name__}: {exc}"))
                    return
            bad = [r for r in results if not r.get("ok")]
            if not bad:
                st.success(t["cite_all_ok"].format(n=len(results)))
                return
            st.warning(t["cite_some_bad"].format(bad=len(bad), n=len(results)))
            for row in bad:
                st.markdown(
                    f'<div class="cite-bad">{html.escape(t["cite_bad_line"].format(title=row.get("title", "")[:90], score=row.get("score", 0), note=(row.get("error") or "—")[:80]))}</div>',
                    unsafe_allow_html=True,
                )


    def render_plot_blocks(idx: int, content: str) -> None:
        """正文里的 matplotlib 代码块 → 一个「📊 生成图表」按钮 + 预览图。"""
        blocks = extract_plot_blocks(content)
        if not blocks:
            return
        for j, code in enumerate(blocks):
            state_key = f"fig_png_{idx}_{j}"
            st.caption(f"📈 {t['fig_make']} · matplotlib")
            if st.button(t["fig_make"], key=f"fig_btn_{idx}_{j}", type="secondary"):
                run = execute_python_code(code)
                if not run["ok"]:
                    st.warning(t["plot_fix_try"])
                    try:
                        fixed = extract_python_blocks(
                            str(get_agent("outline", False, False, lang, critical, model).run(
                                build_plot_fix_prompt(code, run["error"], lang)
                            ))
                        )
                        if fixed:
                            retry = execute_python_code(fixed[0])
                            if retry["ok"]:
                                st.success(t["plot_fix_ok"])
                                run = retry
                            else:
                                st.error(t["plot_fix_fail"].format(err=retry["error"][:400]))
                                run = retry
                    except Exception as exc:  # noqa: BLE001 - 自动修复失败不影响主流程
                        st.error(t["plot_fix_fail"].format(err=f"{type(exc).__name__}: {exc}"))
                if run["ok"]:
                    try:
                        png = Path(run["path"]).read_bytes()
                    except OSError as exc:
                        st.error(t["fig_fail"].format(err=f"{type(exc).__name__}: {exc}"))
                        png = None
                    err = ""
                else:
                    png, err = None, (run["error"] or "未知错误")
                st.caption(t["plot_sandbox"].format(sec=run["seconds"]))

                if png is None:
                    st.error(t["fig_fail"].format(err=err))
                else:
                    st.session_state[state_key] = png
                    figures = list(st.session_state.get("figures") or [])
                    try:
                        path = save_figure_png(png, len(figures) + 1)
                    except Exception as exc:  # noqa: BLE001 - 存盘失败只提示
                        st.warning(t["fig_fail"].format(err=f"{type(exc).__name__}: {exc}"))
                    else:
                        figures.append({"path": str(path), "caption": f"图 {len(figures) + 1}"})
                        st.session_state["figures"] = figures
                        st.success(t["fig_done"])
            stored = st.session_state.get(state_key)
            if stored:
                st.image(stored, use_container_width=True)


    def run_turn(agent, prompt_text: str, loader_text: str, *, avatar=None, file_name: str = "paper_outline.md"):
        """跑一轮 Agent：显示恐龙加载动画 → 输出气泡 → 统计 → 小图标下载。返回 (文本, 是否成功)。"""
        started = time.time()
        ok = True
        with st.chat_message("assistant", avatar=avatar or "🦖"):
            slot = st.empty()
            with slot:
                show_loader(loader_text)
            try:
                reply = str(agent.run(prompt_text))
            except Exception as exc:  # 前端不让异常掀翻页面
                reply, ok = t["failed"].format(err=f"{type(exc).__name__}: {exc}"), False
            slot.empty()
            st.markdown(reply)
            render_plot_blocks("latest", reply)

            cjk, words = word_stats(reply)
            st.caption(
                "｜".join(
                    [
                        t["elapsed"].format(sec=f"{time.time() - started:.1f}"),
                        t["cjk"].format(n=cjk),
                        t["words"].format(n=words),
                    ]
                )
            )
            with st.container(key="msg_actions_latest"):
                st.download_button(
                    "",
                    icon=":material/download:",
                    data=reply,
                    file_name=file_name,
                    mime="text/markdown",
                    key="download-latest",
                    help=t["tip_md"],
                    type="tertiary",
                )
                docx_download(
                    reply,
                    file_name.replace(".md", ".docx"),
                    key="download-latest-docx",
                    help_text=t["tip_docx"],
                    gate_citations=True,
                    with_figures=True,
                )
        return reply, ok




    def write_long_form(*, high_quality: bool) -> None:
        """按大纲逐节写作：每节 ~500 字，上一节作为上下文，网页上逐段「爬格子」长出来。"""
        outline = st.session_state.get("last_outline") or st.session_state.get("last_output", "")
        sections = split_outline_sections(outline)
        if not sections:
            st.warning(t["longform_no_sections"])
            return

        doc_title = outline_title(outline)
        uploaded = st.session_state["pdf"]["text"] if pdf_source else ""
        writer = get_agent(
            "draft" if high_quality else "section", False, False, lang, critical, model
        )
        critic = get_agent("critic", False, False, lang, critical, model) if high_quality else None

        hq_flag = ("是" if high_quality else "否") if lang == "zh" else ("yes" if high_quality else "no")
        st.caption(t["longform_start"].format(n=len(sections), hq=hq_flag))
        progress = st.progress(0.0, text=t["longform_progress"].format(n=len(sections)))
        body_box = st.container()

        parts: list[dict] = []
        prev_text = ""
        started = time.time()
        for i, sec in enumerate(sections, 1):
            progress.progress(
                (i - 1) / len(sections),
                text=t["longform_section"].format(i=i, n=len(sections), title=sec["title"]),
            )
            prompt = build_section_prompt(outline, sec["title"], sec["body"], prev_text, lang, uploaded=uploaded)
            prompt = build_content_prompt(prompt, lang)
            if style_active:
                prompt = inject_style(prompt, style_snippet, lang, style_intensity)
            try:
                text = str(writer.run(prompt))
            except Exception as exc:
                st.error(t["longform_failed"].format(i=i, err=f"{type(exc).__name__}: {exc}"))
                break

            critique = ""
            if high_quality:
                try:
                    critique = str(critic.run(build_critic_prompt(sec["title"], text, lang)))
                    text = str(writer.run(build_rewrite_prompt(sec["title"], text, critique, lang)))
                except Exception as exc:
                    st.warning(t["critic_failed"].format(i=i, err=f"{type(exc).__name__}: {exc}"))

            if count_chars(text) > 600:  # 模型常写超预算，压回 500 字左右
                try:
                    text = str(writer.run(build_compress_prompt(sec["title"], text, lang)))
                except Exception:
                    pass

            # 逐段渲染：每节写完立刻出现在页面上
            with body_box:
                st.markdown(text)
                if critique:
                    with st.expander(t["critic_expander"].format(i=i, title=sec["title"])):
                        st.markdown(critique)

            parts.append({"title": sec["title"], "text": text})
            st.session_state["body_sections"] = parts
            prev_text = (prev_text + "\n\n" + text)[-6000:]
            progress.progress(
                i / len(sections),
                text=t["longform_section_done"].format(i=i, n=len(sections), chars=count_chars(text)),
            )

        if not parts:
            return

        _, refs = split_reference_section(outline)
        body_md = assemble_body(doc_title, parts, "\n".join(refs), lang)
        total = count_chars(body_md)
        st.session_state["long_body"] = body_md
        st.session_state.messages.append({"role": "assistant", "content": body_md, "kind": "body"})
        try:
            saved = save_body(body_md, OUTPUT_BODY)
            saved_docx = export_body(body_md, OUTPUT_BODY_DOCX)
        except OSError as exc:
            st.warning(t["save_failed"].format(err=exc))
        else:
            st.success(t["longform_done"].format(n=len(parts), chars=total))
            st.session_state["last_saved"] = (
                f"{saved.name} + {saved_docx.name}（{total} 字 / {time.time() - started:.0f} 秒）"
            )



    class _QueuedDoc:
        """把知识库里的文件包装成和 st.file_uploader 返回对象一样的接口（name / getvalue）。"""

        def __init__(self, path: Path) -> None:
            self.name = path.name
            self._path = path

        def getvalue(self) -> bytes:
            return self._path.read_bytes()


    def queued_docs(names: list[str]) -> tuple[list[_QueuedDoc], list[str]]:
        """按名字取文件：先看 arXiv 临时目录（read_queue_paths），再回落到知识库。"""
        by_name = {doc.name: doc for doc in list_library()}
        extra = st.session_state.get("read_queue_paths") or {}
        found: list[_QueuedDoc] = []
        missing: list[str] = []
        for name in names:
            path = by_name.get(name)
            if path is None and extra.get(name):
                candidate = Path(str(extra[name]))
                path = candidate if candidate.exists() else None
            if path is None:
                missing.append(name)
            else:
                found.append(_QueuedDoc(path))
        return found, missing


    def run_reading_workbench(files, topic: str, level: str, custom: str, focus: str = "content") -> None:
        """逐篇：先判相关度，再按级别抽原文 + 提取，最后把报告写进 session_state。"""
        spec = READING_LEVELS.get(level, READING_LEVELS["S"])
        agent = get_agent("reading", False, False, lang, critical, model)
        reports: list[dict] = []
        total = len(files)
        bar = st.progress(0.0, text=t["read_running"].format(n=total))
        box = st.status(t["read_running"].format(n=total), expanded=True)

        for idx, item in enumerate(files, 1):
            bar.progress(
                (idx - 1) / total,
                text=t["read_progress"].format(i=idx, n=total, name=item.name),
            )
            title = Path(item.name).stem
            try:
                info = extract_text(item.getvalue(), item.name, max_chars=60000)
                body_text = (info.get("text") or "").strip()
                if not body_text:
                    box.write(f"⚠️ {t['read_skip'].format(name=item.name)}")
                    continue

                rel_reply = str(agent.run(build_relevance_prompt(topic, title, body_text, lang)))
                relevance, reason = parse_relevance(rel_reply)
                rag_block, rag_hits = rag_context_for_upload(item, topic or title, k=5)
                if rag_hits:
                    box.write(t["rag_note"].format(name=item.name, k=rag_hits))
                else:
                    box.write(t["rag_bad"])
                reading_prompt, excerpt = build_reading_prompt(
                    topic, title, body_text, level, lang, custom, focus, rag_block
                )
                markdown = str(agent.run(reading_prompt))
            except Exception as exc:  # noqa: BLE001 - 单篇失败不拖垮整批
                box.write(f"⚠️ {t['read_failed'].format(name=item.name, err=f'{type(exc).__name__}: {exc}')}")
                continue

            reports.append(
                {
                    "title": title,
                    "file": item.name,
                    "level": level,
                    "level_label": spec["label"] if lang == "zh" else spec["label_en"],
                    "relevance": relevance,
                    "reason": reason,
                    "custom": custom if custom else "",
                    "focus": focus,
                    "chars": len(excerpt),
                    "markdown": markdown,
                }
            )
            box.write(f"✅ {title}｜{t['read_relevance'].format(level=relevance)}")

        bar.progress(1.0, text=t["read_done"].format(n=len(reports), level=spec["label"]))
        box.update(label=t["read_done"].format(n=len(reports), level=spec["label"]), state="complete")
        st.session_state["reading_reports"] = reports
        st.session_state["reading_topic"] = topic
        st.session_state["reading_level"] = level


    def render_reading_reports() -> None:
        """展示已完成的精读报告 + 复制 / Word 两个导出按钮。"""
        reports = st.session_state.get("reading_reports") or []
        if not reports:
            return
        topic = st.session_state.get("reading_topic", "")
        st.markdown(f'<div class="card-title">{t["read_report_title"]}</div>', unsafe_allow_html=True)
        st.caption(f"{topic or t['read_topic']}｜{len(reports)} {t['read_relevance'].format(level='')}".strip(" ｜"))

        for rep in reports:
            badge = f"｜{t['read_custom_badge']}" if rep.get("custom") else ""
            label = (
                f"{rep['title']}｜{rep['level_label']}｜"
                f"{t['read_relevance'].format(level=rep['relevance'])}{badge}"
            )
            with st.expander(label, expanded=len(reports) == 1):
                if rep.get("reason"):
                    st.caption(f"🔎 {rep['reason']}")
                st.markdown(rep["markdown"])

        plain = reading_plain_text(topic, reports)
        col_copy, col_word = st.columns(2)
        with col_copy:
            copy_to_clipboard(plain, t["read_copy"], t["read_copied"])
        with col_word:
            try:
                notes = reading_notes_docx(topic, reports)
            except Exception as exc:  # noqa: BLE001 - 导出失败只提示
                st.warning(t["docx_failed"].format(err=f"{type(exc).__name__}: {exc}"))
            else:
                st.download_button(
                    t["read_word"],
                    data=notes,
                    file_name=t["read_docx_name"],
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="read_docx_download",
                    use_container_width=True,
                )
        st.markdown("---")
        if st.button(t["hq_save"], key="hq_save", type="secondary", use_container_width=True):
            body = "\n\n".join(str(rep.get("markdown") or "") for rep in reports).strip()
            if not body:
                st.info(t["hq_empty"])
            else:
                items = list(st.session_state.get("writing_materials") or [])
                items.append(
                    {
                        "title": topic or (reports[0].get("title") if reports else "reading"),
                        "level": reports[0].get("relevance", "") if reports else "",
                        "body": body[:6000],
                    }
                )
                st.session_state["writing_materials"] = items
                st.success(t["hq_saved"].format(n=len(items)))

        if st.button(t["read_clear"], key="read_clear", type="tertiary"):
            st.session_state.pop("reading_reports", None)
            st.rerun()


    if read_clicked:
        queued, missing = queued_docs(list(st.session_state.get("read_queue") or []))
        if missing:
            st.caption(t["queue_missing"].format(names="、".join(missing)))
        batch = list(reading_files or []) + queued
        if not batch:
            st.warning(t["read_need_files"])
        else:
            if not (reading_topic or "").strip():
                st.caption(t["read_need_topic"])
            run_reading_workbench(
                batch,
                (reading_topic or "").strip(),
                reading_level,
                (reading_custom or "").strip(),
                material_mode,
            )

    render_reading_reports()


    # ---------------------------------------------------------------------------
    # 悬浮胶囊输入框
    # ---------------------------------------------------------------------------
    placeholder = {
        "polish": t["ph_polish"],
        "pdf": t["ph_pdf"],
        "litree": t["ph_litree"],
        "outline": t["ph_source"] if pdf_source else t["ph_outline"],
    }.get(task_key, t["ph_other"])

    prompt = st.chat_input(placeholder)

    # 老式游戏状态栏：通过 CSS 变量注入到输入框下方（stBottomBlockContainer::after）
    _status_text = "▌ >> " + t["status_ready"] + "   v" + APP_VERSION + " · 🦖"
    st.markdown(
        "<style>[data-testid=\"stBottomBlockContainer\"]{--status-text:"
        + json.dumps(_status_text, ensure_ascii=False)
        + ";}</style>",
        unsafe_allow_html=True,
    )

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        last_output = st.session_state.get("last_output", "")

        if task_key == "pdf":
            task_prompt = (
                f"【论文正文节选｜{st.session_state['pdf']['name']}】\n"
                f"{st.session_state['pdf']['text']}\n\n"
                f"【我的问题】\n{prompt}"
            )
        elif task_key == "polish":
            task_prompt = prompt
        else:
            if task_key in ("expand", "abstract") and last_output:
                task_prompt = f"研究主题：{prompt}\n\n【上一轮生成的大纲】\n{last_output}"
            else:
                task_prompt = prompt
            if pdf_source:
                # 用户选择「写作素材」：把上传 PDF 的正文顶到最前面，让大纲优先参考它
                pdf_info = st.session_state["pdf"]
                task_prompt = build_source_prompt(task_prompt, pdf_info["name"], pdf_info["text"], lang)
            # 知识库节选与「不要编造」约束一起前置（与精读读取方式无关）
            with st.spinner(t["rag_warm"]):
                rag_lib, rag_lib_n = rag_context_for_library(prompt, k=6)
            if rag_lib_n:
                task_prompt = (
                    "【RAG 检索片段（每段带 [来源：文件名 - 第X页]，引用时请保留页码）】\n"
                    f"{rag_lib}\n\n"
                    + task_prompt
                )
            task_prompt = build_content_prompt(task_prompt, lang)

        if style_active:
            task_prompt = inject_style(task_prompt, style_snippet, lang, style_intensity)

        loader_key = {"polish": "load_polish", "pdf": "load_pdf"}.get(
            task_key,
            "load_search" if task_key in ("outline", "expand", "litree") else "load_write",
        )

        if redblue:
            with st.chat_message("assistant", avatar="🦖"):
                reply, ok = run_redblue_turn(
                    get_agent(task_key, polish, pdf_ready, lang, critical, model),
                    get_agent("critic", False, False, lang, critical, model),
                    task_prompt,
                    t[loader_key],
                )
                st.markdown(reply)
        else:
            reply, ok = run_turn(
                get_agent(task_key, polish, pdf_ready, lang, critical, model),
                task_prompt,
                t[loader_key],
            )

        st.session_state.messages.append({"role": "assistant", "content": reply})
        if ok:
            st.session_state["last_output"] = reply
            if task_key == "outline":
                st.session_state["last_outline"] = reply
                try:
                    saved = save_outline(reply, OUTPUT_FILE)
                    saved_docx = export_docx(reply, OUTPUT_DOCX)
                    st.session_state["last_saved"] = (
                        f"{saved.name} + {saved_docx.name}（{len(reply)} 字符）"
                    )
                except OSError as exc:
                    st.warning(t["save_failed"].format(err=exc))

    elif review_clicked:
        # 盲审：读取大纲，单独调用盲审 Agent，结果只作为新气泡追加，绝不覆盖原大纲
        outline_text = st.session_state.get("last_outline") or st.session_state.get("last_output", "")
        review_prompt = build_review_prompt(outline_text, lang)
        reply, ok = run_turn(
            get_agent("review", False, False, lang, critical, model),
            review_prompt,
            t["load_review"],
            avatar="👨‍⚖️",
            file_name="blind_review.md",
        )
        if ok:
            st.session_state.messages.append(
                {"role": "assistant", "content": reply, "avatar": "👨‍⚖️", "kind": "review"}
            )

    elif expand_clicked or hq_clicked:
        # 长篇正文：逐节生成（可选 作者 → 毒舌审稿 → 重写），每段落在页面上立即出现
        write_long_form(high_quality=hq_clicked)

    # ---------------------------------------------------------------------------
    # 页脚：模型与版本
    # ---------------------------------------------------------------------------
    st.markdown(
        f"""
    <div class="app-footer">
      🦖 <b>{html.escape(t['title'])}</b>
      <span class="dot">·</span> {t['footer_model'].format(model=model)}
      <span class="dot">·</span> {t['footer_built'].format(ver=APP_VERSION, stv=st.__version__)}
      <div class="made">{t['footer_note']}</div>
    </div>
    """,
        unsafe_allow_html=True,
    )
