"""样式层：像素主题 CSS、双语文案、像素小恐龙、通用小组件（下载 / 字数 / 计费提示 / 自检）。

这个文件只做「外观与小组件」，不含任何业务判断；所有 Agent 调用仍然留在 my_agent.py。
"""

import html
import os
import re
import socket
from pathlib import Path

import json

import streamlit as st
import streamlit.components.v1 as components
from pixel_theme import PIXEL_CSS


APP_VERSION = "2.6.0"

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

def get_agent(
    mode: str,
    polish: bool,
    pdf: bool,
    lang: str,
    critical: bool = False,
    model: str | None = None,
):
    from my_agent import build_agent, resolve_model_name, DEFAULT_MODEL  # 延迟导入：避免循环依赖
    # 非 DeepSeek 提供商时，model 由通行证里的选择决定（build_agent 内部会忽略 deepseek 型号）
    return build_agent(
        mode, polish=polish, pdf=pdf, lang=lang, critical=critical,
        model=model or resolve_model_name(DEFAULT_MODEL),
    )


def extract_reference_titles(markdown: str) -> list[str]:
    """从文末参考文献里抽纯标题（兼容 [n] 前缀 + APA 的 `作者 (年). 标题.`）。"""
    from my_agent import split_reference_section
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
    from my_agent import docx_bytes
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
    from my_agent import append_figures_to_docx, check_citations
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


def count_chars(text: str) -> int:
    """中文按汉字数、英文按词数，取有意义的那个。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    return cjk or len(re.findall(r"[A-Za-z][A-Za-z'\-]*", text))

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
