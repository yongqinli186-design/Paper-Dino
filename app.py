"""Streamlit 前端：论论文（Paper Dino）——绿色清爽 + 像素恐龙 + 中英双语 + 风格克隆 + 盲审。

python -m streamlit run app.py
"""

import html
import os
import re
import socket
import tempfile
import time
from pathlib import Path

import json

import streamlit as st
import streamlit.components.v1 as components

import chart_engine as charts
import rag_utils
from pixel_theme import CLICK_JS, IRIS_JS, PIXEL_CSS
from sound_engine import BGM_TRACKS, load_sound_settings, render_sound, save_sound_settings

from my_agent import (
    build_plot_fix_prompt,
    execute_python_code,
    extract_python_blocks,
    run_red_blue,
    verify_references,
    append_figures_to_docx,
    check_citations,
    extract_plot_blocks,
    parse_review_pairs,
    render_figure_code,
    save_figure_png,
    build_auto_level_prompt,
    build_material_block,
    download_arxiv_pdf,
    parse_auto_level,
    search_arxiv_entries,
    MODE_LABELS,
    MODEL,
    MODELS,
    DEFAULT_MODEL,
    OUTPUT_BODY,
    OUTPUT_BODY_DOCX,
    OUTPUT_DOCX,
    OUTPUT_FILE,
    READING_LEVELS,
    STYLE_FILE,
    add_document_to_library,
    assemble_body,
    build_agent,
    build_compress_prompt,
    build_critic_prompt,
    api_key_error,
    build_content_prompt,
    build_figure_prompt,
    has_api_key,
    build_library_context,
    build_reading_prompt,
    build_relevance_prompt,
    build_rewrite_prompt,
    build_review_prompt,
    build_section_prompt,
    build_source_prompt,
    delete_library_file,
    docx_bytes,
    export_body,
    export_docx,
    extract_pdf_text,
    extract_text,
    inject_style,
    library_count,
    list_library,
    load_style_reference,
    outline_title,
    parse_relevance,
    reading_notes_docx,
    reading_plain_text,
    sample_library_snippet,
    save_body,
    save_outline,
    save_style_reference,
    split_outline_sections,
    split_reference_section,
    style_tier,
)

st.set_page_config(page_title="论论文 · 论文写作工作台", page_icon="🦖", layout="wide")

APP_VERSION = "2.5.1"

# ---------------------------------------------------------------------------
# 计费参考 + 系统自检（都是「提示」，出错也只影响自己那一行）
# ---------------------------------------------------------------------------
MODEL_BILLING = {
    "deepseek-chat": {
        "zh": "💰 极高性价比 · 约 ￥1 / 百万 token（长文首选）",
        "en": "💰 Very cheap · ≈ ￥1 / 1M tokens (best for long drafts)",
    },
    "deepseek-reasoner": {
        "zh": "🧠 推理模型 · 约 ￥3 / 百万 token（思考更久，输出更慢）",
        "en": "🧠 Reasoning model · ≈ ￥3 / 1M tokens (thinks longer, slower)",
    },
}

BILLING_FALLBACK = {
    "zh": "💰 计费参考：以所选服务商的实际单价为准",
    "en": "💰 Billing: charged at your provider's actual rate",
}


def _billing_hint(model_key: str, lang: str) -> str:
    """按所选模型给一句计费参考；未知模型按关键字粗分，任何异常都退回通用文案。"""
    try:
        item = MODEL_BILLING.get(model_key)
        if item:
            return item.get(lang) or item["zh"]
        low = str(model_key).lower()
        if "ollama" in low or "local" in low or "qwen" in low:
            return (
                "🆓 本地免费运行（只吃显存 / 内存）"
                if lang == "zh"
                else "🆓 Local & free (GPU / RAM only)"
            )
        if "opus" in low:
            return (
                "💸 昂贵模型 · 约 ￥150 / 百万 token，请谨慎长文输出"
                if lang == "zh"
                else "💸 Expensive · ≈ ￥150 / 1M tokens — keep outputs short"
            )
        if "claude" in low or "gpt" in low or "gemini" in low:
            return (
                "💸 商用模型 · 约 ￥30–150 / 百万 token"
                if lang == "zh"
                else "💸 Commercial · ≈ ￥30–150 / 1M tokens"
            )
        return BILLING_FALLBACK[lang]
    except Exception:
        return BILLING_FALLBACK.get(lang, "")


def _probe_api(env_name: str, host: str) -> dict:
    """只看两件事：Key 在不在、域名通不通。异常一律吞掉。"""
    key_ok = bool((os.environ.get(env_name) or "").strip())
    net_ok = False
    if host:
        try:
            with socket.create_connection((host, 443), timeout=2.5):
                net_ok = True
        except Exception:
            net_ok = False
    return {"key_ok": key_ok, "net_ok": net_ok}


def _probe_library() -> dict:
    """往知识库里写一个探针文件再删掉，验证真实读写权限。"""
    try:
        from my_agent import LIBRARY_DIR

        folder = Path(LIBRARY_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".selfcheck.tmp"
        probe.write_text("ok", encoding="utf-8")
        got = probe.read_text(encoding="utf-8")
        probe.unlink()
        return {"ok": got == "ok", "detail": str(folder)}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _run_self_check() -> list[dict]:
    """逐项自检，单项失败不影响其它项，也绝不冒泡到页面。"""
    rows: list[dict] = []
    for name, env_name, host in (
        ("DeepSeek API", "DEEPSEEK_API_KEY", "api.deepseek.com"),
        ("Claude API", "ANTHROPIC_API_KEY", "api.anthropic.com"),
    ):
        try:
            rows.append({"name": name, "kind": "api", **_probe_api(env_name, host)})
        except Exception as exc:
            rows.append(
                {"name": name, "kind": "api", "key_ok": False, "net_ok": False,
                 "detail": f"{type(exc).__name__}: {exc}"}
            )
    try:
        rows.append({"name": "Local library", "kind": "fs", **_probe_library()})
    except Exception as exc:
        rows.append({"name": "Local library", "kind": "fs", "ok": False,
                     "detail": f"{type(exc).__name__}: {exc}"})
    return rows


def _self_check_line(row: dict, t: dict) -> str:
    """把一行自检结果渲染成像素风小卡片。"""
    try:
        if row["kind"] == "fs":
            mark = t["selfcheck_rw_ok"] if row.get("ok") else t["selfcheck_rw_bad"]
            note = html.escape(str(row.get("detail", "")))[:60]
        else:
            if row.get("key_ok") and row.get("net_ok"):
                mark = t["selfcheck_ok"]
                note = ""
            elif not row.get("key_ok"):
                mark = t["selfcheck_key_missing"]
                note = ""
            else:
                mark = t["selfcheck_net_bad"]
                note = ""
        return (
            f'<div class="check-row"><b>{html.escape(row["name"])}</b>'
            f"<span>{mark}</span>"
            + (f'<i>{note}</i>' if note else "")
            + "</div>"
        )
    except Exception:
        return ""



def _figure_stem(spec: dict) -> str:
    """把图标题变成安全的文件名（中文保留）。"""
    raw = str((spec or {}).get("title") or "figure")
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", raw).strip("_")
    return (cleaned or "figure")[:40]

# ---------------------------------------------------------------------------
# 文案（中 / 英）
# ---------------------------------------------------------------------------
L = {
    "zh": {
        "title": "论论文",
        "subtitle": "填好选题，让 dino 帮你查文献、搭大纲、写正文、导 Word｜arXiv 真实检索 + DeepSeek 写作",
        "hero_kicker": "Academic Writing Studio",
        "hero_chip1": "arXiv 真实检索",
        "hero_chip2": "DeepSeek 逐节写作",
        "hero_chip3": "APA · Word 导出",
        "task_line": "当前任务",
        "chat_title": "对话记录",
        "hero_note": "查文献 · 搭大纲 · 写正文 · 导 Word",
        "advanced": "⚙️ 高级设置",
        "drawer_title": "DINO · SETUP",
        "tip_review": "盲审大纲",
        "tip_expand": "逐节扩写正文",
        "tip_hq": "高质量正文（作者 → 审稿 → 重写）",
        "tip_md": "下载 Markdown",
        "tip_docx": "下载 Word（文末 APA 参考文献）",
        "s_sound": "🔊 声音",
        "bgm_label": "背景音乐",
        "sfx_label": "启用交互音效",
        "sfx_help": "点击按钮、开关或输入框回车时播放 8-bit 音效（第一次点击页面后才会有声音）。",
        "bgm_none": "安静（无）",
        "bgm_forest": "森林治愈（钢琴）",
        "bgm_pixel": "像素回忆（8-bit）",
        "footer_model": "模型 {model}",
        "footer_note": "本地运行 · 论文与范文只留在你的电脑上",
        "footer_built": "版本 v{ver} · Streamlit {stv}",
        "s_lang": "语言",
        "banner_title": "🚀 一键开始标准论文写作",
        "banner_sub": "六步走完一篇论文，每一步都有 AI 陪跑：选题 → 检索 → 大纲 → 扩写 → 盲审 → 导出。",
        "banner_cta": "🚀 一键开始 · 进入沉浸式工坊",
        "flow": ["选题破冰", "搜文献", "写大纲", "扩写正文", "盲审修改", "导出 Word"],
        "mute_on": "🔇 静音",
        "mute_off": "🎵 声音",
        "mute_help": "勿扰模式：关掉背景音乐与全部点击音效（默认开启）。",
        "selfcheck": "🔧 系统自检",
        "selfcheck_run": "重新检测",
        "selfcheck_idle": "点「重新检测」才会真正发请求，平时不打扰。",
        "selfcheck_ok": "✅ 正常",
        "selfcheck_key_missing": "❌ 缺失 Key",
        "selfcheck_net_bad": "❌ 无法连接",
        "selfcheck_rw_ok": "✅ 可读写",
        "selfcheck_rw_bad": "❌ 权限不足",
        "s_mode": "① 输出模式",
    "s_model": "🧠 模型切换",
    "model_label": "模型",
        "s_polish": "② 润色模式",
        "polish_label": "启用润色模式",
        "polish_help": "勾选后不再检索文献，直接对你输入的段落做学术润色 + 降重，保持原意。",
        "s_critical": "③ 英文学术思辨",
        "critical_label": "🎓 英文学术思辨模式",
        "critical_help": "开启后系统提示词换成英文思辨规则：问题驱动（先找 Gap）、每个选择都交代 Why、证据与主张之间搭桥、论证链连贯、精确 hedging（suggest / indicate / may …），输出为英文学术文本。",
        "critical_on": "已开启：所有模式改用英文思辨规则，输出 English。",
        "task_critical_suffix": " · 🎓 英文学术思辨",
        "status_ready": "SYSTEM READY. AGENT 已苏醒，正在文献库里翻找论文…",
        "s_pdf": "④ 论文 / 文献精读",
        "pdf_label": "上传论文（PDF · DOCX · TXT，基于原文提问）",
        "pdf_parsing": "正在解析文件…",
        "pdf_loaded": "已载入 {name}｜{pages} 页｜提取 {chars} 字符{suffix}",
        "pdf_loaded_doc": "已载入 {name}｜{blocks} 段｜提取 {chars} 字符{suffix}",
        "pdf_truncated": "，超出部分已截断",
        "pdf_hint": "现在提问会基于这篇论文的原文作答，例如「总结这篇论文的创新点」。",
        "pdf_use": "这份文件用来做什么",
        "pdf_use_qa": "📄 精读问答（基于原文提问）",
        "pdf_use_src": "📝 写作素材（写大纲时优先参考）",
        "pdf_hint_src": "写大纲时会优先参考这篇文献的正文与结论，arXiv 检索只用来补充相关工作。",
        "pdf_failed": "文件解析失败：{err}",
        "s_style": "🎭 风格克隆范文（可选）",
        "style_intensity": "模仿强度",
        "style_intensity_help": "1-3 只借一点气质，4-7 明显模仿，8-10 严格复刻句式与用词。",
        "style_tier_hint": "当前强度：{tier}",
        "style_tier_light": "轻微参考该文风",
        "style_tier_medium": "明显模仿该文风",
        "style_tier_strong": "严格复刻该文风",
        "style_ta": "粘贴你的范文（中英文均可）",
        "style_upload": "或上传 .txt / .md / .docx 文件",
        "style_save": "💾 保存范文",
        "style_saved": "范文已保存到 style_reference.txt（{chars} 字符）",
        "style_loaded": "已从 style_reference.txt 载入 {chars} 字符范文",
        "style_toggle": "开启子助手风格克隆",
        "style_toggle_help": "开启后，每次提问都会在 Prompt 中注入范文风格指令（对所有模式生效）。",
        "style_missing": "已开启风格克隆，但还没有范文——请在上面粘贴或上传。",
        "style_decode_failed": "文件读取失败，请确认编码（推荐 UTF-8）。",
        "clear": "🗑 清空对话",
        "saved_out": "已保存：{name}",
        "task": "当前任务：**{label}**",
        "task_polish": "✍️ 润色降重（不检索文献）",
        "task_pdf": "📄 PDF 精读问答（{name}）",
        "task_source": "📝 大纲写作 · 优先参考 {name}",
        "task_style_suffix": " · 风格克隆已开启",
        "hint_polish": "粘贴一段你的论文段落，我会做学术润色 + 降重（保持原意）。",
        "hint_pdf": "就这篇 PDF 提问，例如「总结这篇论文的创新点」「这篇论文用了什么实验方法」。",
        "hint_source": "输入你要写的论文主题，我会先按你上传的这篇 PDF 的研究问题与方法搭大纲，再用 arXiv 补充相关工作。",
        "hint_outline": "输入一个研究主题，例如：**大模型智能体（LLM Agents）在医疗领域应用的论文大纲**。",
        "hint_expand": "输入研究主题（可先跑一次模式 A 生成大纲，我会基于它扩写 1500 字正文）。",
        "hint_abstract": "输入研究主题（若已有大纲，我会严格对齐其中的主张写出 450-550 词英文摘要）。",
        "ph_polish": "粘贴需要润色的段落…",
        "ph_pdf": "向这篇 PDF 提问，例如：这篇论文用了什么实验方法？",
        "ph_outline": "输入论文主题，例如：LLM Agents 在医疗领域的应用",
        "ph_source": "输入论文主题，我会优先按上传的 PDF 搭大纲",
        "ph_other": "输入研究主题（附大纲更佳）",
        "load_search": "🦖 恐龙正在努力查阅文献中…",
        "load_write": "🦖 恐龙正在努力写作中…",
        "load_polish": "🦖 恐龙正在逐句打磨中…",
        "load_pdf": "🦖 恐龙正在精读论文中…",
        "review_btn": "👨‍⚖️ 让盲审专家批阅大纲",
        "review_help": "把最近一次生成的大纲交给最严苛的盲审专家，原大纲保持不变，批判结果在新气泡里。",
        "review_need": "先生成一份大纲（模式 A），这个按钮才能用。",
        "load_review": "👨‍⚖️ 盲审专家正在挑刺中…",
        "s_library": "📚 我的文献库",
        "lib_upload": "添加到知识库（PDF / DOCX / TXT，可多选）",
        "lib_count": "知识库已有 {n} 个文件",
        "lib_added": "已加入知识库：{name}",
        "lib_empty": "知识库还是空的——「文献树」会退化成只搜 arXiv。",
        "lib_help": "放进来的文件会参与「文献树」的本地检索、内容参考与风格模仿，写大纲时也会被优先参考。",
        "lib_files": "📚 文献库文件",
        "lib_delete_help": "把这个文件从知识库删除（不可恢复）",
        "lib_deleted": "已删除：{name}",
        "material_mode": "素材使用模式",
        "material_content": "📖 内容参考模式",
        "material_style": "🎭 语言风格模仿模式",
        "material_mode_rd": "精读读取方式（只作用于精读工作台）",
        "material_content_rd": "📖 内容参考（读原文节选）",
        "material_style_rd": "🎭 语言风格（只提炼句式与语气）",
        "material_scope_hint": "这个开关只决定「文献精读工作台」怎么读你选中的文件；写作时读取知识库不受它影响。",
        "lib_to_read": "➕ 添加到精读",
        "lib_to_read_help": "把这篇放进精读队列，稍后在工作台里批量精读",
        "lib_to_read_added": "已加入精读队列：{name}",
        "queue_title": "待精读队列（{n} 篇）",
        "queue_empty": "队列是空的：点文献库里的「➕ 添加到精读」，或直接在上面上传。",
        "queue_remove": "从队列移除",
        "queue_clear": "🧹 清空队列",
        "queue_missing": "队列里有文件已经不在知识库了，已自动跳过：{names}",
        "material_hint_content": "本次会读取知识库全文节选（约 {chars} 字），注入「基于材料事实、不要编造」。",
        "material_hint_content_empty": "知识库为空：内容参考模式暂时不注入材料，请先上传文献或改用风格模式。",
        "material_hint_style": "本次不读全文，只随机抽取范文片段：{name}",
        "material_hint_style_text": "本次使用你手写的范文（{chars} 字符）做风格克隆。",
        "material_hint_style_empty": "知识库和范文都是空的：风格模式暂时不注入，请先上传文献或粘贴范文。",
        "style_source_library": "已从知识库随机抽取范文：{name}",
        "hint_litree": "输入你的研究方向，我会先在你上传的 PDF 里找源头文献，再去 arXiv 追最新进展，最后画出一棵文献树。",
        "ph_litree": "输入研究方向，例如：LLM agents 在医疗领域的多智能体协作",
        "expand_btn": "📝 一键扩写正文（按大纲逐节 500 字）",
        "hq_btn": "🏆 生成高质量正文（作者 → 毒舌审稿 → 重写）",
        "longform_help": "按最近一次生成的大纲逐节写作：每节约 500 字，承接上一节、禁止重复，网页上逐段长出来。",
        "longform_need": "先生成一份大纲（模式 A），这两个按钮才能用。",
        "longform_ready": "大纲已就绪，可拆成 {n} 节",
        "longform_start": "开始逐节写作：共 {n} 节｜高质量模式：{hq}",
        "longform_progress": "准备中…（共 {n} 节）",
        "longform_section": "正在写第 {i}/{n} 节：{title}",
        "longform_section_done": "第 {i}/{n} 节 ✓ 约 {chars} 字",
        "longform_done": "正文完成：{n} 节｜约 {chars} 字｜已写入 paper_body.md + paper_body.docx",
        "longform_no_sections": "这份大纲里没找到可拆的章节标题，请先跑模式 A 生成带 ## 小标题的大纲。",
        "longform_failed": "第 {i} 节写作失败：{err}",
        "critic_expander": "🔥 毒舌审稿人意见（第 {i} 节：{title}）",
        "critic_failed": "第 {i} 节审稿失败，已保留初稿：{err}",
        "elapsed": "耗时 {sec} 秒",
        "cjk": "中文 {n} 字",
        "words": "英文 {n} 词",
        "download": "⬇️ 下载 Markdown",
        "download_docx": "⬇️ 下载 Word（文末 APA 参考文献）",
        "docx_failed": "Word 生成失败：{err}",
        "failed": "运行失败：{err}",
        "save_failed": "写入失败：{err}",
        "s_reading": "🔬 文献精读工作台",
        "read_topic": "课题 / 论文题目",
        "read_topic_ph": "例如：大模型智能体在医疗分诊中的应用",
        "read_upload": "待读文献（PDF / DOCX，可多选）",
        "read_level": "相关度级别（五选一）",
        "read_custom_toggle": "叠加自定义阅读要求",
        "read_custom_label": "自定义要求（会追加到每篇的提取里）",
        "read_custom_ph": "例如：找出文献里用到的数据集与标注规模",
        "read_run": "🔬 开始精读",
        "read_need_files": "先上传至少一篇文献。",
        "read_need_topic": "建议先填课题，相关度判断会更准。",
        "read_running": "正在精读 {n} 篇文献…",
        "read_progress": "第 {i}/{n} 篇：{name}",
        "read_done": "精读完成：{n} 篇｜级别 {level}",
        "read_skip": "{name} 没抽出正文，已跳过。",
        "read_failed": "{name} 处理失败：{err}",
        "read_report_title": "📚 精读报告",
        "read_copy": "📋 复制纯文本",
        "read_copied": "✓ 已复制",
        "read_word": "📥 导出 Word 笔记",
        "read_docx_name": "文献精读笔记.docx",
        "rv_tab_problems": "❌ 存在问题",
    "rv_tab_fixes": "✅ 修改建议",
    "rv_before": "原段落",
    "rv_after": "修改后段落",
    "rv_apply": "🔄 一键替换回正文",
    "rv_applied": "已替换 {n} 处正文内容。",
    "rv_apply_fail": "没在正文里找到这段原文（可能已被改过），可以手动复制。",
    "rv_no_pairs": "这一轮审稿没有给出可直接替换的段落对照，下面是完整意见。",
    "fig_make": "📊 生成图表",
    "fig_fail": "绘图失败（{err}）",
    "fig_done": "已生成图表并保存到 figures/（导出 Word 时会附在文末）。",
    "cite_ok": "✅ 引用校对通过：正文最高 [{max_cite}]，参考文献 {ref_count} 条。",
    "cite_fail": "⚠️ 引用标号不匹配！正文最高引用至 [{max_cite}]，但参考文献只有 {ref_count} 条，请检查是否遗漏。",
    "cite_none": "正文没有 [n] 标号，按纯文本导出。",
    "read_clear": "清空报告",
    "arxiv_ph": "输入关键词，例如：LLM agents in medical triage",
    "arxiv_btn": "🔍 搜索 arXiv",
    "arxiv_empty": "没有搜到结果，换个关键词试试。",
    "arxiv_add": "➕ 加入精读队列",
    "arxiv_added": "已加入队列：{name}",
    "arxiv_fail": "下载失败（{err}），可以再点一次。",
    "arxiv_prev": "已加入的 arXiv 文献会先存到本地临时目录再读，不需要你手动下载。",
    "autolevel_btn": "🤖 自动分级（按课题判 S/A/B/C）",
    "autolevel_done": "自动分级完成：{n} 篇已打标签，相关度最高的是 {top}（已把级别切到 {top}）。",
    "autolevel_empty": "队列里还没有文献，先从知识库或 arXiv 加几篇。",
    "hq_save": "📥 存入大本营",
    "hq_saved": "已存入写作大本营（当前共 {n} 份素材）。",
    "hq_empty": "这份报告还没生成内容，先跑一次精读。",
    "material_pick": "📚 从大本营调用素材",
    "material_fill": "📥 填入表单",
    "material_use": "✅ 生成时引用这份素材",
    "material_unuse": "取消引用",
    "material_none": "（大本营还是空的：先在精读工作台点「📥 存入大本营」）",
    "material_filled": "已填进证据栏，可以按需要再润色。",
        "read_relevance": "相关度 {level}",
        "read_custom_badge": "含自定义要求",
        "read_d_level": "相关度极低，建议放弃阅读",
    },
    "en": {
        "title": "Lun Lun Wen · Paper Dino",
        "subtitle": "Give dino a topic — it searches arXiv, builds the outline, writes the draft, exports Word｜DeepSeek underneath",
        "hero_kicker": "Academic Writing Studio",
        "hero_chip1": "Real arXiv search",
        "hero_chip2": "Section-by-section drafting",
        "hero_chip3": "APA · Word export",
        "task_line": "Current task",
        "chat_title": "Conversation",
        "hero_note": "search · outline · draft · export",
        "advanced": "⚙️ Advanced",
        "drawer_title": "DINO · SETUP",
        "tip_review": "Blind review the outline",
        "tip_expand": "Draft the paper section by section",
        "tip_hq": "High-quality draft (author → critic → rewrite)",
        "tip_md": "Download Markdown",
        "tip_docx": "Download Word (APA references)",
        "s_sound": "🔊 Sound",
        "bgm_label": "Background music",
        "sfx_label": "Interaction sounds",
        "sfx_help": "Plays a short 8-bit blip on clicks, toggles and Enter (audio unlocks on your first click).",
        "bgm_none": "Silence (off)",
        "bgm_forest": "Forest healing (piano)",
        "bgm_pixel": "Pixel memories (8-bit)",
        "footer_model": "Model {model}",
        "footer_note": "Runs locally · papers and samples never leave your machine",
        "footer_built": "Version v{ver} · Streamlit {stv}",
        "s_lang": "Language",
        "banner_title": "🚀 One-click standard paper workflow",
        "banner_sub": "Six steps from topic to Word: topic → search → outline → draft → peer review → export.",
        "banner_cta": "🚀 Start now · open the immersive studio",
        "flow": ["Pick topic", "Search", "Outline", "Draft", "Blind review", "Export Word"],
        "mute_on": "🔇 Muted",
        "mute_off": "🎵 Sound",
        "mute_help": "Do-not-disturb: turns off background music and every click sound (on by default).",
        "selfcheck": "🔧 System check",
        "selfcheck_run": "Run check",
        "selfcheck_idle": "Nothing is requested until you press “Run check”.",
        "selfcheck_ok": "✅ OK",
        "selfcheck_key_missing": "❌ Missing key",
        "selfcheck_net_bad": "❌ Unreachable",
        "selfcheck_rw_ok": "✅ Read / write",
        "selfcheck_rw_bad": "❌ No permission",
        "s_mode": "① Output mode",
    "s_model": "🧠 Model",
    "model_label": "Model",
        "s_polish": "② Polish mode",
        "polish_label": "Enable polish mode",
        "polish_help": "Skips literature search and polishes / paraphrases your pasted paragraph while keeping its meaning.",
        "s_critical": "③ English critical thinking",
        "critical_label": "🎓 English academic critical-thinking mode",
        "critical_help": "Replaces the system prompt with English critical-thinking rules: problem-driven (find the Gap), a Why for every choice, an explicit bridge from evidence to claim, a cohesive argument chain, and precise hedging (suggest / indicate / may …). Output is academic English.",
        "critical_on": "On: every mode now writes in English with the critical-thinking rules.",
        "task_critical_suffix": " · 🎓 English critical thinking",
        "status_ready": "SYSTEM READY. AGENT IS AWAKE AND LOOKING FOR PAPERS...",
        "s_pdf": "④ Paper reading",
        "pdf_label": "Upload a paper (PDF · DOCX · TXT)",
        "pdf_parsing": "Parsing the file…",
        "pdf_loaded": "Loaded {name}｜{pages} pages｜{chars} characters extracted{suffix}",
        "pdf_loaded_doc": "Loaded {name}｜{blocks} blocks｜{chars} characters extracted{suffix}",
        "pdf_truncated": ", truncated",
        "pdf_hint": "Questions are now answered from this paper, e.g. “summarise its contributions”.",
        "pdf_use": "How should this file be used",
        "pdf_use_qa": "📄 Reading Q&A (ask about the paper)",
        "pdf_use_src": "📝 Writing source (outline leans on it)",
        "pdf_hint_src": "Outlines will lean on this paper's body and findings; arXiv search only fills in related work.",
        "pdf_failed": "File parsing failed: {err}",
        "s_style": "🎭 Style reference (optional)",
        "style_intensity": "Imitation strength",
        "style_intensity_help": "1-3 borrows the feel, 4-7 visibly imitates, 8-10 replicates sentence patterns and wording.",
        "style_tier_hint": "Current strength: {tier}",
        "style_tier_light": "lightly borrow the tone",
        "style_tier_medium": "visibly imitate the style",
        "style_tier_strong": "closely replicate the style",
        "style_ta": "Paste sample writing (Chinese or English)",
        "style_upload": "or upload a .txt / .md / .docx file",
        "style_save": "💾 Save reference",
        "style_saved": "Saved to style_reference.txt ({chars} characters)",
        "style_loaded": "Loaded {chars} characters from style_reference.txt",
        "style_toggle": "Enable style-clone sub-assistant",
        "style_toggle_help": "When on, every prompt carries the style instruction built from your reference text.",
        "style_missing": "Style clone is on but no reference text yet — paste or upload one above.",
        "style_decode_failed": "Could not read the file; please use UTF-8.",
        "clear": "🗑 Clear chat",
        "saved_out": "Saved: {name}",
        "task": "Current task: **{label}**",
        "task_polish": "✍️ Polish & paraphrase (no search)",
        "task_pdf": "📄 PDF reading Q&A ({name})",
        "task_source": "📝 Outline · leaning on {name}",
        "task_style_suffix": " · style clone on",
        "hint_polish": "Paste a paragraph; I will polish and paraphrase it while keeping the meaning.",
        "hint_pdf": "Ask about this PDF, e.g. “summarise the contributions” or “what experiments were run?”.",
        "hint_source": "Enter your topic; the outline will follow this uploaded paper's questions and method first, with arXiv search only filling gaps.",
        "hint_outline": "Enter a research topic, e.g. **LLM agents in healthcare: paper outline**.",
        "hint_expand": "Enter a topic (run mode A first and I will expand its outline into 1500 words).",
        "hint_abstract": "Enter a topic (with an outline, the abstract will mirror its claims).",
        "ph_polish": "Paste the text to polish…",
        "ph_pdf": "Ask this PDF a question, e.g. what experimental method did it use?",
        "ph_outline": "Enter a research topic, e.g. LLM agents in healthcare",
        "ph_source": "Enter a topic — the outline will follow your uploaded PDF first",
        "ph_other": "Enter a research topic (outline attached is better)",
        "load_search": "🦖 Dino is digging through the literature…",
        "load_write": "🦖 Dino is writing away…",
        "load_polish": "🦖 Dino is polishing sentence by sentence…",
        "load_pdf": "🦖 Dino is reading the paper…",
        "review_btn": "👨‍⚖️ Have a blind reviewer critique the outline",
        "review_help": "Hand the latest outline to the harshest blind reviewer. The outline stays untouched; the critique appears in a new bubble.",
        "review_need": "Generate an outline first (mode A) to enable this button.",
        "load_review": "👨‍⚖️ The blind reviewer is tearing it apart…",
        "s_library": "📚 My library",
        "lib_upload": "Add to the knowledge base (PDF / DOCX / TXT, multiple allowed)",
        "lib_count": "{n} file(s) in the knowledge base",
        "lib_added": "Added to the knowledge base: {name}",
        "lib_empty": "The knowledge base is empty — literature-tree mode will fall back to arXiv only.",
        "lib_help": "Files stored here feed literature-tree retrieval, content reference and style imitation, and are preferred when outlining.",
        "lib_files": "📚 Library files",
        "lib_delete_help": "Delete this file from the knowledge base (cannot be undone)",
        "lib_deleted": "Deleted: {name}",
        "material_mode": "Material mode",
        "material_content": "📖 Content reference",
        "material_style": "🎭 Style imitation",
        "material_mode_rd": "Reading focus (reading workbench only)",
        "material_content_rd": "📖 Content (read the excerpt)",
        "material_style_rd": "🎭 Style (extract phrasing only)",
        "material_scope_hint": "This switch only decides how the reading workbench reads your selected files; it never changes how the writing agents read the library.",
        "lib_to_read": "➕ To reading",
        "lib_to_read_help": "Queue this paper for the reading workbench",
        "lib_to_read_added": "Queued for reading: {name}",
        "queue_title": "Reading queue ({n})",
        "queue_empty": "The queue is empty: use ➕ in the library, or upload above.",
        "queue_remove": "Remove from queue",
        "queue_clear": "🧹 Clear queue",
        "queue_missing": "Some queued files are no longer in the library and were skipped: {names}",
        "material_hint_content": "This turn reads an excerpt of the knowledge base (~{chars} chars) and injects “use the facts, do not invent”.",
        "material_hint_content_empty": "Knowledge base is empty: content mode injects nothing — upload files or switch to style mode.",
        "material_hint_style": "This turn reads no full text, only a random sample: {name}",
        "material_hint_style_text": "This turn clones the style of your pasted reference ({chars} chars).",
        "material_hint_style_empty": "Library and reference text are both empty: style mode injects nothing — upload a file or paste a paragraph.",
        "style_source_library": "Random sample taken from the knowledge base: {name}",
        "hint_litree": "Enter your research direction: I search your PDFs for the source papers, verify them on arXiv, then draw a literature tree.",
        "ph_litree": "Enter a research direction, e.g. multi-agent collaboration of LLM agents in healthcare",
        "expand_btn": "📝 Expand into full text (500 chars per section)",
        "hq_btn": "🏆 High-quality draft (author → harsh reviewer → rewrite)",
        "longform_help": "Writes section by section from the latest outline: ~500 chars each, continuing from the previous one, no repetition, growing live on the page.",
        "longform_need": "Generate an outline first (mode A) to enable these buttons.",
        "longform_ready": "Outline ready, splittable into {n} sections",
        "longform_start": "Writing section by section: {n} sections | high quality: {hq}",
        "longform_progress": "Preparing… ({n} sections)",
        "longform_section": "Writing section {i}/{n}: {title}",
        "longform_section_done": "Section {i}/{n} ✓ ~{chars} chars",
        "longform_done": "Draft done: {n} sections | ~{chars} chars | saved to paper_body.md + paper_body.docx",
        "longform_no_sections": "No section headings found in this outline — run mode A to produce one with ## headings.",
        "longform_failed": "Section {i} failed: {err}",
        "critic_expander": "🔥 Reviewer's report (section {i}: {title})",
        "critic_failed": "Review failed for section {i}; keeping the draft: {err}",
        "elapsed": "{sec}s elapsed",
        "cjk": "{n} Chinese chars",
        "words": "{n} English words",
        "download": "⬇️ Download Markdown",
        "download_docx": "⬇️ Download Word (APA references)",
        "docx_failed": "Word export failed: {err}",
        "failed": "Failed: {err}",
        "save_failed": "Write failed: {err}",
        "s_reading": "🔬 Reading workspace",
        "read_topic": "My topic / working title",
        "read_topic_ph": "e.g. LLM agents for hospital triage",
        "read_upload": "Papers to read (PDF / DOCX, multiple)",
        "read_level": "Relevance level (pick one)",
        "read_custom_toggle": "Add a custom reading request",
        "read_custom_label": "Custom request (appended to every extraction)",
        "read_custom_ph": "e.g. list the datasets and annotation sizes used",
        "read_run": "🔬 Start reading",
        "read_need_files": "Upload at least one paper first.",
        "read_need_topic": "Fill in your topic — relevance judgements will be sharper.",
        "read_running": "Reading {n} paper(s)…",
        "read_progress": "Paper {i}/{n}: {name}",
        "read_done": "Done: {n} paper(s) | level {level}",
        "read_skip": "{name} had no extractable body text and was skipped.",
        "read_failed": "{name} failed: {err}",
        "read_report_title": "📚 Reading reports",
        "read_copy": "📋 Copy plain text",
        "read_copied": "✓ Copied",
        "read_word": "📥 Export Word notes",
        "read_docx_name": "reading-notes.docx",
        "rv_tab_problems": "❌ Problems",
    "rv_tab_fixes": "✅ Suggested fixes",
    "rv_before": "Original",
    "rv_after": "Rewritten",
    "rv_apply": "🔄 Replace in draft",
    "rv_applied": "Replaced {n} passage(s) in the draft.",
    "rv_apply_fail": "That passage is no longer in the draft (it may have changed) — copy it manually.",
    "rv_no_pairs": "This review did not return replaceable passage pairs; the full report is below.",
    "fig_make": "📊 Generate chart",
    "fig_fail": "Plot failed ({err})",
    "fig_done": "Chart generated and saved under figures/ (appended when you export Word).",
    "cite_ok": "✅ Citation check passed: highest [{max_cite}], {ref_count} references.",
    "cite_fail": "⚠️ Citation mismatch: the text cites up to [{max_cite}] but only {ref_count} references exist.",
    "cite_none": "No [n] markers found — exporting as plain text.",
    "read_clear": "Clear reports",
    "arxiv_ph": "Keywords, e.g. LLM agents in medical triage",
    "arxiv_btn": "🔍 Search arXiv",
    "arxiv_empty": "Nothing found — try different keywords.",
    "arxiv_add": "➕ Add to reading queue",
    "arxiv_added": "Added to queue: {name}",
    "arxiv_fail": "Download failed ({err}) — try again.",
    "arxiv_prev": "arXiv papers are downloaded into a local temp folder before reading.",
    "autolevel_btn": "🤖 Auto-grade (S/A/B/C by topic)",
    "autolevel_done": "Auto-graded {n} papers; the closest is {top} (level switched to {top}).",
    "autolevel_empty": "The queue is empty — add papers from the library or arXiv first.",
    "hq_save": "📥 Save to base camp",
    "hq_saved": "Saved to the writing base camp ({n} items).",
    "hq_empty": "This report has no content yet — run a reading first.",
    "material_pick": "📚 Pull material from base camp",
    "material_fill": "📥 Fill the form",
    "material_use": "✅ Cite this material when generating",
    "material_unuse": "Stop citing",
    "material_none": "(Base camp is empty: use “📥 Save to base camp” in the reading workbench.)",
    "material_filled": "Filled into the evidence field — polish it as you like.",
        "read_relevance": "relevance {level}",
        "read_custom_badge": "custom request included",
        "read_d_level": "Very low relevance — skip this paper",
    },
}

# ---------------------------------------------------------------------------
# 模块五新增文案（只追加，不改原有键）
# ---------------------------------------------------------------------------
L["zh"].update(
    {
        "redblue_label": "⚔️ 红蓝对抗模式",
        "redblue_help": "Writer 先写，盲审专家找 3 个逻辑漏洞，Writer 再改，最多 2 轮；中间过程收进折叠面板。",
        "redblue_on": "已开启：每轮回答都会先写、再审、再改。",
        "rb_writing": "🖋️ Writer 正在写第一版…",
        "rb_reviewing": "🔴 盲审专家正在挑漏洞（第 {n} 轮）…",
        "rb_revising": "🔵 Writer 正在按意见重写（第 {n} 轮）…",
        "rb_debate": "⚔️ 查看红蓝双方的交锋过程（{n} 轮）",
        "rb_round": "第 {n} 轮",
        "rb_final": "✅ 终稿（{n} 轮对抗后）",
        "rb_failed": "红蓝对抗中断：{err}",
        "rag_note": "RAG 检索：{name}（{k} 段，含页码来源）",
        "rag_bad": "RAG 未命中，退回全文节选。",
        "cite_verify": "🔍 校验文献真伪（Crossref）",
        "cite_verifying": "正在向 Crossref 逐条核对…",
        "cite_all_ok": "✅ {n} 条文献全部在 Crossref 命中。",
        "cite_some_bad": "⚠️ 文献校验失败：疑似 AI 幻觉生成，请核实（{bad}/{n} 条未命中）",
        "cite_bad_line": "未命中：{title}｜最高相似度 {score}｜{note}",
        "cite_verify_err": "核对失败：{err}",
        "plot_fix_try": "⚠️ 运行失败，正在让 Agent 自动 Debug 并重跑…",
        "plot_fix_ok": "✅ 自动修好了，第 2 版代码运行成功。",
        "plot_fix_fail": "❌ 自动 Debug 也没成功：{err}",
        "plot_sandbox": "沙盒执行 {sec}s",
        "rag_warm": "首次检索要加载本地嵌入模型（约 20 秒），之后都是秒回…",
    }
)
L["en"].update(
    {
        "redblue_label": "⚔️ Red-blue adversarial mode",
        "redblue_help": "Writer drafts, a blind reviewer hunts 3 logical flaws, writer rewrites. Max 2 rounds; the debate folds into an expander.",
        "redblue_on": "On: every answer is drafted, reviewed, then rewritten.",
        "rb_writing": "🖋️ Writer is drafting…",
        "rb_reviewing": "🔴 Blind reviewer is hunting flaws (round {n})…",
        "rb_revising": "🔵 Writer is rewriting (round {n})…",
        "rb_debate": "⚔️ Open the debate transcript ({n} rounds)",
        "rb_round": "Round {n}",
        "rb_final": "✅ Final draft (after {n} rounds)",
        "rb_failed": "Red-blue loop aborted: {err}",
        "rag_note": "RAG: {name} ({k} chunks with page sources)",
        "rag_bad": "RAG returned nothing; falling back to the excerpt.",
        "cite_verify": "🔍 Verify references (Crossref)",
        "cite_verifying": "Checking each reference against Crossref…",
        "cite_all_ok": "✅ All {n} references found in Crossref.",
        "cite_some_bad": "⚠️ Reference check failed: possibly hallucinated — please verify ({bad}/{n} unmatched)",
        "cite_bad_line": "Unmatched: {title} | best score {score} | {note}",
        "cite_verify_err": "Verification failed: {err}",
        "plot_fix_try": "⚠️ Run failed; asking the agent to debug and retry…",
        "plot_fix_ok": "✅ Auto-fixed: second version ran fine.",
        "plot_fix_fail": "❌ Auto-debug also failed: {err}",
        "plot_sandbox": "Sandbox run {sec}s",
        "rag_warm": "First retrieval loads the local embedding model (~20s); later calls are instant…",
    }
)

# ---------------------------------------------------------------------------
# 像素小恐龙（内联 SVG，原创像素画，致敬 Chrome 离线恐龙，无外部资源依赖）
# ---------------------------------------------------------------------------
_DINO_BODY = [
    (10, 2, 4, 1), (9, 3, 6, 1), (9, 4, 6, 1), (9, 5, 4, 1),
    (6, 6, 7, 1), (5, 7, 7, 1), (4, 8, 7, 1), (3, 9, 7, 1),
    (2, 10, 9, 1), (2, 11, 8, 1), (3, 12, 6, 1),
    (2, 6, 4, 1), (1, 7, 4, 1), (0, 8, 4, 1),
    (3, 13, 2, 1), (3, 14, 2, 1), (2, 15, 3, 1),
    (7, 13, 2, 1), (7, 14, 2, 1), (7, 15, 3, 1),
    (10, 10, 2, 1),
]
_DINO_BELLY = [(4, 11, 5, 1), (4, 12, 4, 1), (5, 13, 2, 1)]


def dino_svg(size: int = 48, css: str = "") -> str:
    body = "".join(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}"/>' for x, y, w, h in _DINO_BODY
    )
    belly = "".join(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}"/>' for x, y, w, h in _DINO_BELLY
    )
    return (
        f'<svg class="{css}" width="{size}" height="{size}" viewBox="0 0 16 16" '
        f'xmlns="http://www.w3.org/2000/svg" shape-rendering="crispEdges" '
        f'style="vertical-align:middle">'
        f'<g fill="#4e9a5f">{body}</g>'
        f'<g fill="#a7e0b0">{belly}</g>'
        f'<rect x="12" y="3" width="1" height="1" fill="#ffffff"/>'
        f'<rect x="12" y="4" width="1" height="1" fill="#ffffff"/>'
        "</svg>"
    )


# ---------------------------------------------------------------------------
# 主题 CSS（像素风主题集中在 pixel_theme.py，纯前端）
# ---------------------------------------------------------------------------
CSS = PIXEL_CSS

st.markdown(CSS, unsafe_allow_html=True)
components.html(CLICK_JS, height=0)
components.html(IRIS_JS, height=0)

# ---------------------------------------------------------------------------
# 页面路由：guide = 完全独立的沉浸式论文工坊（主页元素一个都不渲染）
# ---------------------------------------------------------------------------
if "page" not in st.session_state:
    st.session_state.page = "main"

if st.session_state.page == "guide":
    from guide_page import render_guide_page

    render_guide_page(dino_svg)
    st.stop()


@st.cache_resource(show_spinner=False)
def get_agent(
    mode: str,
    polish: bool,
    pdf: bool,
    lang: str,
    critical: bool = False,
    model: str = DEFAULT_MODEL,
):
    return build_agent(
        mode, polish=polish, pdf=pdf, lang=lang, critical=critical, model=model
    )


def extract_reference_titles(markdown: str) -> list[str]:
    """从文末参考文献里抽纯标题（兼容 [n] 前缀 + APA 的 `作者 (年). 标题.`）。"""
    if not markdown:
        return []
    try:
        _body, entries = split_reference_section(markdown)
    except Exception:
        entries = []
    if not entries:
        entries = [ln for ln in (markdown or "").splitlines() if re.match(r"^\s*(\[\d+\]|\d+\.)", ln)]
    titles: list[str] = []
    for raw in entries:
        line = re.sub(r"^\s*(\[\d+\]|\d+\.)\s*", "", raw or "").strip()
        if not line:
            continue
        m = re.search(r"\((\d{4})\)\.?\s*(.+?)(?:\.\s|$)", line)
        if m:
            cand = m.group(2).strip(" .")
        else:
            cand = re.sub(r"^(?:[A-Z][^.]{0,40}\.\s*)+", "", line).strip(" .")
        cand = re.sub(r"\s*arXiv:.*$", "", cand).strip(" .")
        if len(cand) >= 12:
            titles.append(cand[:220])
    return titles[:10]


def word_stats(text: str) -> tuple[int, int]:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text))
    return cjk, words


def show_loader(text: str) -> None:
    st.markdown(
        f'<div class="dino-loader">{dino_svg(26)}<span>{text}<span class="dots"></span></span></div>',
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False, max_entries=64)
def to_docx_bytes(markdown: str) -> bytes:
    """Markdown → Word（文末自动补 APA 参考文献）。同样的文本只生成一次。"""
    return docx_bytes(markdown)


def docx_download(
    content: str,
    file_name: str,
    key: str,
    help_text: str = "",
    icon: str = ":material/description:",
    gate_citations: bool = False,
    with_figures: bool = False,
) -> None:
    """把 Markdown 导出成 Word（文末 APA 参考文献）。

    gate_citations=True 时先校对正文 [n] 与文末参考文献条数，不匹配只报错、不给下载按钮；
    with_figures=True 时把「📊 生成图表」存下来的 PNG 按顺序附到文末。
    """
    if gate_citations:
        info = check_citations(content)
        if not info["cites"] and not info["ref_count"]:
            st.caption(t["cite_none"])
        elif not info["ok"]:
            st.error(
                t["cite_fail"].format(max_cite=info["max_cite"], ref_count=info["ref_count"])
            )
            return
        else:
            st.success(t["cite_ok"].format(max_cite=info["max_cite"], ref_count=info["ref_count"]))

    try:
        data = to_docx_bytes(content)
        if with_figures:
            figures = list(st.session_state.get("figures") or [])
            if figures:
                data = append_figures_to_docx(data, figures)
    except Exception as exc:  # noqa: BLE001 - 前端兜底
        st.warning(f"{file_name}: {type(exc).__name__}: {exc}")
        return
    st.download_button(
        "",
        icon=icon,
        data=data,
        file_name=file_name,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key=key,
        help=help_text or None,
        type="tertiary",
    )


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
    st.caption(_billing_hint(model, lang))

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
                        _auto_level_queue(queue_names, reading_topic)
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


def count_chars(text: str) -> int:
    """中文按汉字数、英文按词数，取有意义的那个。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return cjk or len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text))


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


# ---------------------------------------------------------------------------
# 文献精读工作台：执行 + 报告展示 + 双格式导出
# ---------------------------------------------------------------------------
def copy_to_clipboard(text: str, label: str, copied_label: str, height: int = 54) -> None:
    """在组件 iframe 里放一个按钮，点击后把整段文本写进剪贴板。

    浏览器要求剪贴板写入必须由用户手势触发，所以不能由 Streamlit 按钮代劳；
    这里把按钮放进 iframe，优先用 clipboard API，失败回退 execCommand。
    """
    safe_text = json.dumps(text, ensure_ascii=False).replace("<", "\\u003c")
    safe_label = json.dumps(label, ensure_ascii=False)
    safe_copied = json.dumps(copied_label, ensure_ascii=False)
    components.html(
        f"""
        <style>
          html, body {{ margin:0; padding:0; background:transparent; overflow:hidden; }}
          #px-copy {{
            width:100%; height:44px; cursor:pointer;
            font-family:"Press Start 2P", "VT323", "Microsoft YaHei", monospace;
            font-size:11px; letter-spacing:.04em; color:#14392a;
            background:linear-gradient(135deg,#c9ffd8,#7de49b);
            border:3px solid #0c2a1c; border-radius:0;
            box-shadow:4px 4px 0 rgba(12,42,28,.55);
            transition:transform .12s ease, filter .12s ease;
          }}
          #px-copy:hover {{ transform:translate(-1px,-1px); filter:saturate(1.15) brightness(1.04); }}
          #px-copy:active {{ transform:translate(4px,4px); box-shadow:0 0 0 rgba(0,0,0,0); }}
        </style>
        <button id="px-copy">{html.escape(label)}</button>
        <script>
          const btn = document.getElementById('px-copy');
          const payload = {safe_text};
          const original = {safe_label};
          const copied = {safe_copied};
          async function doCopy() {{
            try {{
              await navigator.clipboard.writeText(payload);
            }} catch (err) {{
              const ta = document.createElement('textarea');
              ta.value = payload;
              ta.style.position = 'fixed';
              ta.style.opacity = '0';
              document.body.appendChild(ta);
              ta.focus();
              ta.select();
              try {{ document.execCommand('copy'); }} catch (e) {{}}
              document.body.removeChild(ta);
            }}
            btn.textContent = copied;
            setTimeout(() => {{ btn.textContent = original; }}, 2200);
          }}
          btn.addEventListener('click', doCopy);
        </script>
        """,
        height=height,
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


def _auto_level_queue(queue_names: list[str], topic: str) -> None:
    """并发读每篇开头 ~1000 字，让模型判 S/A/B/C，结果打标签并回填单选框。"""
    from concurrent.futures import ThreadPoolExecutor

    docs, _missing = queued_docs(list(queue_names))
    if not docs:
        st.info(t["autolevel_empty"])
        return

    agent = get_agent("reading", False, False, lang, critical, model)

    def _grade(doc: _QueuedDoc) -> tuple[str, str, str]:
        title = Path(doc.name).stem
        try:
            info = extract_text(doc.getvalue(), doc.name, max_chars=2600)
            head = (info.get("text") or "")[:2000]
            reply = str(agent.run(build_auto_level_prompt(topic, title, head, 1000)))
            level, reason = parse_auto_level(reply)
        except Exception as exc:  # noqa: BLE001 - 单篇失败不影响其它
            return doc.name, "", f"{type(exc).__name__}: {exc}"
        return doc.name, level, reason

    graded: dict[str, dict] = dict(st.session_state.get("read_levels") or {})
    order = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, level, reason in pool.map(_grade, docs):
            if level:
                graded[name] = {"level": level, "reason": reason}

    st.session_state["read_levels"] = graded
    if graded:
        best = min(graded.values(), key=lambda item: order.get(item.get("level", "C"), 3))
        top = best.get("level", "C")
        st.session_state["read_level_target"] = top
        st.success(
            t["autolevel_done"].format(
                n=len(graded), top=READING_LEVELS.get(top, READING_LEVELS["C"])["label"] if lang == "zh" else top
            )
        )


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
