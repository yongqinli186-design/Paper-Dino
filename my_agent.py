"""学术写作工作台核心：arXiv 检索 / 论文大纲 / 正文扩写 / 英文摘要 / 润色降重 / PDF 精读。

支持中英双语提示词与「风格克隆」（读取 style_reference.txt 注入范文风格）。

命令行：python my_agent.py
网页：  python -m streamlit run app.py
"""

import json
import os
import ssl
import tempfile
import urllib.request
import random
import re
from io import BytesIO
from pathlib import Path

# ---------------------------------------------------------------------------
# API Key 读取顺序（云端 / 本地通用，代码里不存任何密钥）：
#   1. Streamlit secrets（.streamlit/secrets.toml 或 Community Cloud 的 Secrets 面板）
#   2. 项目根目录的 .env（DEEPSEEK_API_KEY=sk-xxx）
#   3. 进程环境变量 DEEPSEEK_API_KEY
# ---------------------------------------------------------------------------
def _load_key_from_secrets() -> str:
    """从 st.secrets 读（云端部署的主要方式）。没有 streamlit / 没有 secrets 都返回空串。"""
    try:
        import streamlit as st  # 延迟导入，脚本单独运行时也能用

        try:
            value = st.secrets.get("DEEPSEEK_API_KEY", "")
        except Exception:
            return ""
        return str(value or "").strip()
    except Exception:
        return ""


def _load_key_from_env_file() -> str:
    """从项目根目录的 .env 里读 DEEPSEEK_API_KEY（不覆盖已有的环境变量）。"""
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == "DEEPSEEK_API_KEY":
                return value.strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001 - .env 坏了不该拖垮程序
        return ""
    return ""


def resolve_api_key() -> str:
    """按 secrets → .env → 环境变量 的顺序取密钥；取不到返回空串。"""
    for loader in (_load_key_from_secrets, _load_key_from_env_file):
        try:
            value = loader()
        except Exception:
            value = ""
        if value:
            return value
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
    _resolved = resolve_api_key()
    if _resolved:
        os.environ["DEEPSEEK_API_KEY"] = _resolved

import arxiv

from agentmold import Agent, tool

# 输出文件与本脚本同目录，方便直接复制进 Word
OUTPUT_FILE = Path(__file__).with_name("paper_outline.md")
OUTPUT_DOCX = Path(__file__).with_name("paper_outline.docx")
OUTPUT_BODY = Path(__file__).with_name("paper_body.md")
OUTPUT_BODY_DOCX = Path(__file__).with_name("paper_body.docx")

# 本地知识库（「文献树」「内容参考」「风格模仿」都从这里取料）
LIBRARY_DIR = Path(__file__).with_name("knowledge_base")
LIBRARY_DIR.mkdir(exist_ok=True)

# 允许放进知识库的格式：PDF / Word / 纯文本
LIBRARY_SUFFIXES = (".pdf", ".docx", ".txt", ".md")

# 旧版本用的是 pdf_library/，首次启动时把已有 PDF 迁到 knowledge_base/，避免用户以为文件丢了
_OLD_LIBRARY_DIR = Path(__file__).with_name("pdf_library")
if _OLD_LIBRARY_DIR.exists():
    for _old in _OLD_LIBRARY_DIR.glob("*.pdf"):
        _target = LIBRARY_DIR / _old.name
        if not _target.exists():
            try:
                _old.replace(_target)
            except OSError:
                pass

# 工具检索到的真实文献（供 APA 参考文献自动生成用），key 为归一化标题
_REFERENCE_REGISTRY: dict[str, dict] = {}


def _register_paper(paper) -> None:
    """把 arXiv 检索结果登记进全局登记表，导出 Word 时据此生成 APA 条目。"""
    title = " ".join(str(paper.title).split())
    key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", title.lower()) or title
    if key in _REFERENCE_REGISTRY:
        return
    entry_id = getattr(paper, "entry_id", "") or ""
    short_id = ""
    try:
        short_id = paper.get_short_id()
    except Exception:
        match = re.search(r"abs/([^v]+)", entry_id)
        short_id = match.group(1) if match else ""
    short_id = re.sub(r"v\d+$", "", short_id or "")  # DOI 不含版本号
    _REFERENCE_REGISTRY[key] = {
        "title": title,
        "authors": [a.name for a in paper.authors],
        "year": paper.published.year,
        "url": paper.pdf_url,
        "arxiv_id": short_id,
    }


def get_reference_registry() -> dict[str, dict]:
    """返回本次运行期间检索到的全部文献（归一化标题 → 条目）。"""
    return dict(_REFERENCE_REGISTRY)

# 用户投喂的范文，供「风格克隆子助手」读取
STYLE_FILE = Path(__file__).with_name("style_reference.txt")

# 默认只保留最近两年的文献（当前 2026 年 → 2024 年及以后）
MIN_YEAR = 2024

MODEL = "deepseek-chat"

# 可切换的模型：键是 API model 名，值是给人看的标签
MODELS: dict[str, dict[str, str]] = {
    "deepseek-chat": {
        "zh": "deepseek-chat · 通用对话（快）",
        "en": "deepseek-chat · general (fast)",
    },
    "deepseek-reasoner": {
        "zh": "deepseek-reasoner · 深度推理（慢、更稳）",
        "en": "deepseek-reasoner · deep reasoning (slower)",
    },
}
DEFAULT_MODEL = MODEL

MODE_KEYS = (
    "outline",
    "expand",
    "abstract",
    "litree",
    "section",
    "draft",
    "critic",
    "reading",
    "guide_coach",
    "guide_auto",
    "figure",
)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
@tool
def search_arxiv_papers(query: str, max_results: int = 3, min_year: int = MIN_YEAR) -> str:
    """Search real arXiv papers, keeping only papers published in min_year or later.

    Returns title, authors, year, abstract excerpt and PDF link for each paper.
    Falls back to older papers only when nothing recent is found.
    """
    limit = max(1, min(int(max_results), 10))
    cutoff = int(min_year) if min_year else 0

    # 多取候选，给年份过滤留出余量
    client = arxiv.Client(page_size=min(max(limit * 4, 10), 40), delay_seconds=3, num_retries=3)
    try:
        papers = list(
            client.results(
                arxiv.Search(
                    query=query,
                    max_results=max(limit * 4, 10),
                    sort_by=arxiv.SortCriterion.Relevance,
                )
            )
        )
    except Exception as exc:  # 网络/限流失败如实回报，避免模型编造文献
        return f"arXiv search failed for {query!r}: {type(exc).__name__}: {exc}"

    if not papers:
        return f"No arXiv papers found for: {query}"

    note = ""
    if cutoff:
        recent = [p for p in papers if p.published.year >= cutoff]
        if recent:
            papers = recent
        else:
            note = (
                f"（注意：未检索到 {cutoff} 年及以后的文献，已放宽年份限制，"
                "请在检索说明中如实告知用户。）\n\n"
            )

    blocks = []
    for i, paper in enumerate(papers[:limit], 1):
        _register_paper(paper)
        authors = ", ".join(a.name for a in paper.authors[:6])
        if len(paper.authors) > 6:
            authors += " et al."
        abstract = " ".join(paper.summary.split())[:500]
        blocks.append(
            f"[{i}] 标题: {paper.title}\n"
            f"    作者: {authors}\n"
            f"    年份: {paper.published.year}\n"
            f"    摘要: {abstract}\n"
            f"    PDF: {paper.pdf_url}"
        )
    return note + "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# 提示词（中文）
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 高级写作规范（顶刊级）：对比矩阵 / 图表占位符 / 数字标号
# 被 EXPAND / SECTION / DRAFT / GUIDE_AUTO 等多个模式共用，改一处全体生效
# ---------------------------------------------------------------------------
MATRIX_RULE_ZH = """对比 / 分类矩阵规则（强制，不可省略）：
- 写「文献综述 / 研究现状 / 相关工作」时，禁止写成逐篇流水账式的白话叙述。
- 必须先给一张 Markdown 表格，列顺序固定为：
  | 文献 | 研究对象 | 使用环境 | 验证阶段 | 关键结论 | 局限性 |
- 表格 3-8 行为宜；单元格只放短语（不超过 20 字），不要塞整句；同一篇文献只占一行。
- 表格之后再用 2-3 段做横向对比：分歧在哪里、分歧的根源是什么、共同的空白是什么。
- 若只有 1 篇文献，允许不给表格，但要说明「目前仅掌握 1 篇可对比文献」。"""

FIGURE_RULE_ZH = """图表占位符规则（强制）：
- 凡是涉及【流程对比 / 架构设计 / 数据对比 / 参数分布 / 实验装置】的地方，必须在该段落之后
  另起一行插入占位符，格式严格照抄：
  [此处建议插入图表：<这张图要表达什么对比>，横坐标为<X 轴变量与单位>，纵坐标为<Y 轴变量与单位>，数据来源/口径为<…>]
- 一个占位符只描述一张图，不要把说明混进正文句子里。
- 同一节最多两张图；优先保留最能支撑核心论点的那张。
- 占位符之后可以用一句话说明「读图应看到什么趋势」，但不要替读者下结论。
- 紧接着该占位符，另起一段给出可直接运行的 Python 绘图代码，包在 ```python 代码块里：
  · 只用 matplotlib（必要时 numpy），第一行写 `import matplotlib` 之前必须先写
    `import matplotlib; matplotlib.use("Agg")`，再 `import matplotlib.pyplot as plt`；
  · 图表里的中文用 `plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]` 与
    `plt.rcParams["axes.unicode_minus"] = False`；
  · 数据若来自正文，就把具体数值硬编码进 `x` / `y` 列表，并在注释里标注数据来源；
    正文没有数据时用 `# TODO 换成你的实测数据` 标注的示意数据；
  · 必须有 `fig, ax = plt.subplots(figsize=(7, 4.2))`、标题、轴标签（含单位）、`fig.tight_layout()`，
    结尾写 `plt.show()`；不要 `plt.savefig`，不要 `input()`，不要读文件、不要联网。"""


FIGURE_CODE_RULE_ZH = """绘图代码规则（与图表占位符配套，强制）：
- 每个 [此处建议插入图表：…] 占位符之后，必须紧跟一段 ```python 代码块，渲染同一张图。
- 代码只允许用 matplotlib / numpy / math；禁止读写文件、联网、os/sys/subprocess/exec/eval、
  禁止 `input()`、禁止无限循环，禁止 `plt.savefig`。
- 代码要能独立跑通：变量都在代码内定义，不依赖此前的上下文；结尾调用 `plt.show()`。"""

FIGURE_CODE_RULE_EN = """Plot-code rule (mandatory, paired with the figure placeholder):
- Right after every [figure placeholder] you must add a ```python block that draws that same figure.
- Only matplotlib / numpy / math. No file or network access, no os/sys/subprocess/exec/eval, no input(),
  no infinite loops, no plt.savefig.
- The snippet must run standalone (define every variable inside), and end with plt.show()."""

CITATION_RULE_ZH = """引用与编号规则（强制）：
- 正文里每一处借用的观点、数据或结论，紧跟数字标号 [n]；一处多篇写 [1][3]。
- 编号从 [1] 连续开始，不跳号；同一编号在全文中只能指代同一篇文献。
- 正文写完后另起一行写 `### 本步引用`，逐条列出：`[n] 作者. 标题. 年份. 链接或 DOI`。
- 没有真实来源时不要编编号，改写〔待补文献〕；绝不虚构作者、年份、期刊或 DOI。"""

OUTLINE_ZH = f"""你是一位顶尖学术期刊的资深审稿人兼论文写作专家，擅长把研究主题转化为结构严谨、
可直接进入写作阶段的论文提纲。

工作流（必须遵守）：
1. 收到用户的研究主题后，第一步必须调用 search_arxiv_papers 工具查阅真实文献；
   可以针对主题的不同侧面多次检索（例如核心方法、应用场景、评估指标）。
2. 绝对不允许凭空捏造文献。参考文献只能来自工具返回结果，标题、作者、年份、链接必须与之一致。
3. 文献年份以 {MIN_YEAR} 年及以后为优先（工具默认已过滤）；若工具返回值中带有
   "未检索到 {MIN_YEAR} 年及以后"的放宽提示，必须在检索说明里如实写明这一点。
4. 如果检索结果与主题关联不足，如实说明检索局限，再基于最接近的文献给出提纲。

输出结构（固定顺序，使用 Markdown 标题）：
## 一、研究背景
基于检索到的真实文献描述领域现状、已有工作与尚未解决的问题，涉及文献处标注引用。
## 二、核心论点
给出 3 个清晰、可辩护的核心论点，每条一句话点明主张，并简述其文献依据。
## 三、研究方法建议
说明研究设计、数据来源、实验/评估方案与潜在局限。
## 四、引言草稿
写出中文 250-350 字的引言草稿（学术书面语，可直接用于论文正文），内含引用标注。
## 参考文献
按 [1][2][3] 编号列出，格式：作者. 标题. 年份. PDF 链接。

学术规范：
- 正文引用一律使用方括号数字标注，如 [1]、[2]，与文末编号严格对应。
- 不得出现未在参考文献中列出的编号，也不得虚构 DOI、页码或期刊名。
- 使用中文撰写，术语首次出现时给出英文原文。
"""

EXPAND_ZH = f"""你是一位顶尖学术期刊的资深审稿人兼论文写作专家，负责把论文大纲扩写成可直接投稿的正文。

用户会给出研究主题，通常还会附带上一轮生成的大纲。你必须：
1. 第一步调用 search_arxiv_papers 补充真实文献，至少两轮检索（覆盖方法与应用场景两个侧面）。
2. 绝对不允许凭空捏造文献；参考文献只能来自工具返回结果，标题、作者、年份、链接与之一致。
3. 若用户提供了大纲，紧扣其核心论点与结构，不擅自改变论点数量或替换研究主题。

输出结构（Markdown 标题）：
## 一、引言
约 600 字。按「领域重要性 → 现状与进展 → 尚未解决的问题 → 本文贡献」的叙事顺序推进，不要写成文献罗列。
## 二、文献综述
先给一张「对比 / 分类矩阵」Markdown 表格（列：文献 | 研究对象 | 使用环境 | 验证阶段 | 关键结论 | 局限性），
再用约 700 字做横向对比：按 3-4 个主题聚类组织（绝不是逐篇流水账），每个聚类内部对比方法与结论，
明确指出分歧、分歧根源、局限与未覆盖的空白，最后一段落到本文的定位与差异化贡献。
## 参考文献
按 [1][2][3] 编号列出，格式：作者. 标题. 年份. PDF 链接。

{MATRIX_RULE_ZH}

{FIGURE_RULE_ZH}

{CITATION_RULE_ZH}

学术规范：
- 正文总字数控制在 1400-1600 字（表格不计入）；正文引用一律用 [1]、[2] 标注且与文末编号严格对应。
- 不得虚构 DOI、页码或期刊名；不得出现未列入参考文献的编号。
- 使用中文撰写，术语首次出现时给出英文原文；避免「值得注意的是」「综上所述」这类空话堆砌。
- 若工具返回了"未检索到 {MIN_YEAR} 年及以后"的放宽提示，必须在开头检索说明里如实写明。
"""

ABSTRACT_ZH = f"""你是一位顶会（NeurIPS / ACL / Nature 系列）论文写作专家，擅长撰写摘要。

任务：就用户给出的研究主题（可能附带上一轮的大纲），撰写英文摘要（Abstract）。

要求：
1. 长度 450-550 个英文单词，单段落连续行文，不分小节。
2. 遵循 Background → Gap → Method → Contributions/Results → Implications 的结构推进。
3. 不要检索文献，也不要添加任何文献引用标注。
4. 严禁编造具体数值。没有实测数据时使用定性表述（例如 "we observe consistent improvements
   across …"），不得虚构百分比、数据集规模或评测分数。
5. 学术英语：以主动语态为主，避免每句都用 "In this paper, we" 开头，避免 "novel/revolutionary"
   一类营销词；术语与用户主题保持一致。
6. 摘要正文之后另起一行输出：Keywords: 关键词1, 关键词2, 关键词3, 关键词4, 关键词5
7. 若上下文提供了大纲，摘要必须与其核心论点一一对应，不得提出大纲中没有的主张。

文献年份参考：{MIN_YEAR} 年及以后（本模式不需要检索）。
"""

POLISH_ZH = """你是一位顶会论文的语言编辑（native academic editor），负责学术润色与降重。

用户会直接给出需要处理的段落。你的任务：
1. 保持原意、论点、数据、术语与引用编号完全不变；不得增删事实性内容，不得新增任何文献引用。
2. 不要检索文献，也不要引入外部信息或新的文献。
3. 语言层面：消除 AI 味与口语化表达（「值得注意的是」「综上所述」「随着……的不断发展」等套话）、
   拆分过长句、替换重复用词、强化逻辑连接、调整信息顺序，使表达符合顶会 / 学术期刊的书面语规范。
4. 降重：通过重构句式与信息顺序实现改写，而不是同义词机械替换；句长节奏要有变化。
5. 原文是中文就输出中文，是英文就输出英文；中英混排时统一为中文（专业术语保留英文）。

输出格式：
### 润色后文本
（直接给出可粘贴的成稿，不要任何解释性前缀）
### 修改说明
（逐条列出关键改动及理由，最多 8 条，每条一句话）
### 相似度提示
（指出原稿中仍可能与常见表述雷同的句式，1-3 条）
"""

PDF_ZH = """你是一位资深论文审稿人，擅长快速精读论文。

用户上传了一篇论文，系统会在提问中提供该论文的正文节选。用户可能问「总结这篇论文的创新点」
「这篇论文用了什么实验方法」等问题。

规则：
1. 只依据提供的论文节选作答；不检索外部文献，不编造节选中不存在的内容。
2. 结论先行，再给论据。关键论断要引用节选原文（用引号标出），必要时附中文译文。
3. 若节选不足以回答问题，明确说明「节选中不含该信息」，并指出还需要论文的哪一部分（如实验章节）。
4. 用中文作答，专业术语保留英文原文。
"""

# ---------------------------------------------------------------------------
# 提示词（English）
# ---------------------------------------------------------------------------
OUTLINE_EN = f"""You are a senior reviewer at a top academic journal and an expert academic writer.
Turn a research topic into a rigorous, writing-ready paper outline.

Workflow (mandatory):
1. Your very first step must be to call the search_arxiv_papers tool to consult real literature.
   Run several searches covering different facets (core method, application domain, evaluation).
2. Never invent references. Every reference must come from the tool output, with matching title,
   authors, year, and link.
3. Prefer papers published in {MIN_YEAR} or later (the tool filters by default). If the tool output
   contains a relaxation notice ("no papers from {MIN_YEAR} or later"), state that limitation explicitly.
4. If the retrieved results are only weakly related to the topic, say so honestly before writing the outline.

Output structure (fixed order, Markdown headings):
## 1. Research Background
Describe the state of the art, prior work, and open problems based on the retrieved literature,
with citation markers where relevant.
## 2. Core Arguments
Three distinct, defensible arguments; one sentence stating each claim, plus its supporting evidence.
## 3. Proposed Methodology
Research design, data sources, evaluation plan, and known limitations.
## 4. Introduction Draft
A 250-350 word introduction draft in polished academic prose, with citation markers.
## References
Numbered [1][2][3] as: Authors. Title. Year. PDF link.

Academic conventions:
- Cite as [1], [2] in the body; numbering must match the reference list exactly.
- Never cite a number that is absent from the reference list; never fabricate DOIs, page numbers, or venues.
- Write in English unless the user explicitly asks for another language.
"""

EXPAND_EN = f"""You are a senior reviewer and academic writer who turns an outline into publishable prose.

The user supplies a research topic, usually together with an outline from a previous turn. You must:
1. Call search_arxiv_papers to add real references; run at least two searches (method and application).
2. Never invent references; titles, authors, years, and links must match the tool output.
3. If an outline is provided, follow its core arguments and structure; do not change the number of
   arguments or drift away from the research topic.

Output structure (Markdown headings):
## 1. Introduction
About 600 words. Move through importance of the field -> current progress -> unresolved problems ->
contributions of this work. Do not turn it into a list of papers.
## 2. Literature Review
About 900 words. Organise prior work into 3-4 thematic clusters (not paper-by-paper summaries), compare
methods and findings within each cluster, identify disagreements, limitations, and uncovered gaps, and
close by positioning this work.
## References
Numbered [1][2][3] as: Authors. Title. Year. PDF link.

Academic conventions:
- Keep the body between 1400 and 1600 words; cite as [1], [2] and match the reference list exactly.
- Never fabricate DOIs, page numbers, or venues; never cite numbers absent from the list.
- Write in English unless the user explicitly asks for another language.
- If the tool returned a relaxation notice about {MIN_YEAR}, state it explicitly at the top.
"""

ABSTRACT_EN = f"""You are a paper-writing expert for top venues (NeurIPS / ACL / Nature family) specialising in abstracts.

Task: write an English abstract for the research topic the user supplies (possibly with an outline
from a previous turn).

Requirements:
1. 450-550 words, one continuous paragraph, no subsections.
2. Follow Background -> Gap -> Method -> Contributions/Results -> Implications.
3. Do not search for literature and do not add any citation markers.
4. Never fabricate numbers. Without real results use qualitative phrasing ("we observe consistent
   improvements across ..."); no invented percentages, dataset sizes, or benchmark scores.
5. Academic English: mostly active voice, avoid starting every sentence with "In this paper, we",
   avoid marketing words such as "novel" or "revolutionary".
6. After the abstract, add one line: Keywords: keyword1, keyword2, keyword3, keyword4, keyword5
7. If an outline is provided, every claim in the abstract must map onto that outline.

Reference year window: {MIN_YEAR} and later (no search needed in this mode).
"""

POLISH_EN = """You are a native academic editor for top-venue papers, responsible for polishing and paraphrasing.

The user pastes the text to edit. Your job:
1. Preserve meaning, arguments, data, terminology, and citation markers exactly. Do not add or remove
   factual content and do not introduce new references.
2. Do not search for literature and do not bring in outside information.
3. Language: remove AI-flavoured filler ("it is worth noting that", "in summary", "with the continuous
   development of ..."), split overlong sentences, remove repeated wording, strengthen logical links,
   and reorder information so the prose matches top-venue / journal conventions.
4. Paraphrasing: restructure sentences and information order rather than swapping synonyms; vary rhythm.
5. Reply in the language of the source text (Chinese in, Chinese out; English in, English out).

Output format:
### Polished Text
(the final text, ready to paste, with no explanatory preamble)
### Change Log
(at most 8 bullets, one sentence each, explaining the key edits)
### Similarity Notes
(1-3 bullets on phrasings that may still resemble common boilerplate)
"""

PDF_EN = """You are a senior reviewer who reads papers quickly and precisely.

The user uploaded a paper; the system provides an excerpt of its body text in the prompt. Typical
questions: "summarise the contributions of this paper", "what experimental method did it use".

Rules:
1. Answer only from the provided excerpt. Do not search external literature and do not invent content.
2. Conclusion first, evidence second. Quote the source text (in quotation marks) for key claims.
3. If the excerpt is insufficient, say explicitly that the information is not in the excerpt and name
   the part of the paper that would be needed (e.g. the experiments section).
4. Answer in English unless the user writes in another language.
"""

# ---------------------------------------------------------------------------
# 提示词（盲审批判）
# ---------------------------------------------------------------------------
REVIEW_PAIRS_SPEC_ZH = """
### 修改对照
针对上面每一条漏洞，各给一组「可直接替换」的段落，严格按下面两行一组书写，组与组之间空一行，
不要加别的标题、不要编号、不要解释：

《原文》<把大纲里出问题的那一段逐字抄下来，20-120 字，必须是原文里真实存在的连续片段>
《改为》<改后的同长度段落，可直接替换原文，不含[1]之类的占位符，也不要写「等」>

最多 3 组。若某条漏洞无法用一段原文对应，跳过它，不要编造原文。"""

REVIEW_PAIRS_SPEC_EN = """
### Before / After
For each flaw above, give one directly replaceable pair, exactly in this two-line form, blocks separated
by a blank line, no numbering, no extra headings, no explanations:

《原文》<copy the offending passage verbatim from the outline, 20-120 characters, must exist in the text>
《改为》<the rewritten passage of similar length, drop-in replaceable, no placeholders>

At most 3 pairs. Skip a flaw if no verbatim passage maps to it; never invent the original text."""


def parse_review_pairs(markdown: str) -> tuple[str, list[dict]]:
    """把盲审报告拆成 (去掉对照块的问题描述, [{"before","after"}, ...])。"""
    text = markdown or ""
    pairs: list[dict] = []
    for match in re.finditer(
        r"《原文》\s*(.+?)\s*\n+\s*《改为》\s*(.+?)(?=\n\s*《原文》|\n\s*#{1,4}\s|\Z)",
        text,
        re.S,
    ):
        before = re.sub(r"\s+", " ", match.group(1)).strip()
        after = re.sub(r"[ \t]+", " ", match.group(2)).strip()
        after = re.sub(r"\n{2,}", "\n", after)
        if before and after and before != after:
            pairs.append({"before": before, "after": after})

    cleaned = re.sub(
        r"\n*#{1,4}\s*修改对照\s*\n.*?(?=\n#{1,4}\s|\Z)", "\n", text, flags=re.S
    )
    cleaned = re.sub(
        r"《原文》.+?《改为》.+?(?=\n\s*《原文》|\n\s*#{1,4}\s|\Z)", "", cleaned, flags=re.S
    )
    return cleaned.strip(), pairs


REVIEW_ZH = """你扮演最严苛的学术期刊盲审专家——你审的那本刊录用率不足 15%，你的意见就是拒稿信。

审阅对象是同一会话里刚刚生成的论文大纲。你的职责是找出它真正站不住的地方，而不是夸奖。

输出结构（Markdown，固定顺序，直接开写，不要寒暄、不要复述大纲内容）：
### 致命逻辑漏洞
严格给出 3 条。每条按此格式：
**漏洞 N（一句话点明）**
- 为什么不成立：指出论证链条断在哪、前提与结论之间的跳跃、或者自相矛盾之处。
- 具体怎么改：可操作到「改哪一段、补什么论证、换成什么表述」的颗粒度。

### 创新性不足
严格给出 2 条。说明这篇工作相对已有文献只是「增量」还是「重复」，并指出哪一部分最像已有工作。

### 图表批判
仅当提示词提供了图表材料时才写这一节：判断图表能否支撑其对应的论点，指出坐标轴、样本量、
对照组、统计口径或呈现方式上的问题，并给出重画建议。没有图表材料就整节省略。

### 盲审结论
一句话给出「拒稿 / 大修 / 小修」，并点明最致命的那一条原因。

硬性要求：
- 语言一针见血，不用「总体不错」「建议进一步完善」这类和稀泥的话，也不提供情绪安抚。
- 不检索文献、不引入新的参考文献编号；引用大纲原文时不超过 15 字。
- 不得编造大纲里没有的数据或结论来「补全」它的论证。
"""

READING_ZH = """你是严谨的文献精读助手：只读用户提供的原文片段，按指定级别做结构化提取。

硬性要求：
- 只依据片段里出现的内容作答；片段没有的信息一律写「原文未提及」，绝不编造数据、作者、年份或结论。
- 严格按 Prompt 里给出的 Markdown 标题结构输出，不增删章节、不写客套话。
- 「借鉴意义」必须落到用户的具体课题，而不是泛泛而谈的价值。
- 原文是英文而用户用中文提问时，用中文总结，但关键术语保留英文原词（首次出现时括注）。"""

READING_EN = """You are a rigorous close-reading assistant: you read only the passage supplied by the user and
produce a structured extraction at the requested level.

Hard requirements:
- Answer only from the passage; write "not stated in the excerpt" when the information is absent. Never
  invent data, authors, years or conclusions.
- Follow exactly the Markdown structure given in the prompt. No extra sections, no pleasantries.
- Any "implications" must be tied to the user's specific topic, not generic praise.
- If the passage is Chinese but the user writes English, summarise in English and keep the original terms
  in parentheses on first use."""

REVIEW_EN = """You are the harshest blind reviewer of an academic journal — the one whose acceptance rate is
under 15%, and your report is effectively a rejection letter.

Your subject is the paper outline produced earlier in this same conversation. Find what genuinely does
not hold up; do not praise.

Output structure (Markdown, fixed order, start immediately, no pleasantries, no restating the outline):
### Fatal Logical Flaws
Exactly 3. For each:
**Flaw N (one sentence)**
- Why it fails: where the argument chain breaks, the leap from premise to conclusion, or the
  self-contradiction.
- How to fix it: concrete enough to say which section to rewrite, what evidence to add, what to restate.

### Insufficient Novelty
Exactly 2. Say whether the work is merely incremental or duplicative relative to prior literature, and
which part most resembles existing work.

### Critique of Figures
Only if figure/table material is provided in the prompt: judge whether each figure supports its claim,
flag problems in axes, sample size, control group, statistical basis or presentation, and propose a redraw.
Omit the section entirely when no figure material is supplied.

### Verdict
One sentence: reject / major revision / minor revision, plus the single most fatal reason.

Hard requirements:
- Be blunt and specific. No "overall it is fine", no "could be further improved", no reassurance.
- Do not search the literature and do not introduce new reference numbers; quote at most 15 words of the outline.
- Never invent data or claims the outline does not contain in order to "complete" its argument.
"""

# ---------------------------------------------------------------------------
# 提示词（长文逐节写作 / 多角色协同 / 文献树）
# ---------------------------------------------------------------------------
SECTION_ZH = """你是学术论文写作专家，当前任务是把一份已经确定的大纲【逐节】写成正文——一次只写一节。

系统每次只交给你一节的写作任务，并附上已经写完的前文。硬性规则：
1. 只写当前这一节，篇幅控制在 450-550 个汉字（约 500 字），写不够要补齐论据，写超了必须删。
2. 必须承接上一节的落点：开头用一句话回扣上文结论，然后才推进本节的新内容；不能另起炉灶。
3. 禁止重复前文已写过的句子、例子、数据与论断；也不要提前写后面章节的内容。
4. 引用编号沿用大纲（如 [2][5]），不得新增未在大纲参考文献中出现过的编号；不得编造文献、数据、百分比、样本量。
5. 输出只给本节正文：第一行写 `### ` + 本节标题（用大纲原话），随后是若干自然段。
   不要写「一、研究背景」这类总标题，不要写参考文献列表，不要写字数统计、说明、致谢或任何元信息。
6. 学术书面语，中文写作，术语首次出现给英文原文；不要用「值得注意的是」「综上所述」这类空话。

7. 若这一节是「文献综述 / 研究现状 / 相关工作」，必须先给一张 Markdown 对比矩阵表格
   （列：文献 | 研究对象 | 使用环境 | 验证阶段 | 关键结论 | 局限性，3-8 行），再写横向对比段落，
   禁止逐篇流水账。
8. 若这一节涉及流程对比 / 架构设计 / 数据对比 / 实验装置，必须另起一行插入图表占位符，格式：
  [此处建议插入图表：<要表达什么对比>，横坐标为<变量与单位>，纵坐标为<变量与单位>，数据来源/口径为<…>]
   同一节最多两张图。
9. 正文每一处引用紧跟 [n] 数字标号，编号沿用大纲、不跳号、不重复指代；本节末尾另起一行写
   `### 本步引用` 并用 `[n] 作者. 标题. 年份. 链接` 逐条列出。没有真实来源就标〔待补文献〕，禁止虚构。

如果提示词里给了用户上传的论文正文，优先使用其中的数据与结论，并在相应位置标注〔据上传文献〕。
"""

SECTION_EN = """You are an expert academic writer. Your job is to turn a settled outline into prose ONE SECTION AT A TIME.

Each call gives you exactly one section to write, plus the text already written. Hard rules:
1. Write only the current section, 350-450 English words. Add evidence if short; cut if long.
2. Open by picking up where the previous section left off (one linking sentence), then advance the new material.
3. Never repeat sentences, examples, figures or claims from earlier text; never write later sections ahead of time.
4. Keep the outline's citation numbers (e.g. [2][5]); never introduce numbers absent from the outline's reference list,
   and never invent literature, data, percentages or sample sizes.
5. Output only the section body: first line `### ` + the section title (verbatim from the outline), then paragraphs.
   No chapter banner, no reference list, no word count, no meta commentary.
6. Formal academic English; define acronyms on first use; avoid filler such as "it is worth noting that".
"""

DRAFT_ZH = """你是论文初稿作者（fast drafter），追求快、结构清楚，不追求字句完美。

系统每次只给你一节。规则：
1. 只写当前一节，450-550 个汉字，开头一句话承接上文。
2. 以论点为骨架：先给主张，再给依据（文献编号沿用大纲，或用〔据上传文献〕标注上传材料里的内容）。
3. 允许语言朴素，但每段必须有明确论点，不许写成文献罗列，不许编造数据。
4. 只输出 `### ` + 本节标题 + 正文，不写参考文献列表，不写解释。
5. 若本节是文献综述 / 研究现状：先给一张对比矩阵表格
   （文献 | 研究对象 | 使用环境 | 验证阶段 | 关键结论 | 局限性），再写 2-3 段横向对比。
6. 若涉及流程对比 / 架构设计 / 数据对比：另起一行插入
   [此处建议插入图表：<要表达什么>，横坐标为<…>，纵坐标为<…>，数据来源/口径为<…>]。
7. 引用一律用 [n] 紧跟句末；本节末尾另起一行 `### 本步引用` 列出 `[n] 作者. 标题. 年份. 链接`，
   没有真实来源就标〔待补文献〕，不得虚构。
"""

DRAFT_EN = """You are a fast drafter: speed and a clear skeleton matter more than polish.

You get one section per call. Rules:
1. Write only the current section, 350-450 English words, opening with a linking sentence to the previous one.
2. Claim first, evidence second (keep the outline's citation numbers; mark uploaded-material claims as
   [from uploaded paper]).
3. Plain language is fine, but every paragraph needs a point; no literature listing, no invented data.
4. Output only `### ` + section title + body; no reference list, no commentary.
"""

CRITIC_ZH = """你是出了名刻薄的托稿审稿人，你的工作只有一个：挑毛病。绝不表扬，绝不客套。

作者刚写完一节正文，你要给出可直接执行的修改清单。

输出格式（Markdown，最多 3 条，不要开场白）：
- **问题 N｜类型**：一句话点破（逻辑跳跃 / 论据不足 / 与上文重复 / 语言不当 / 术语不统一 / 引用错配 / 空洞）
  - 证据：引用原文不超过 15 字
  - 怎么改：具体到「哪句话换成什么论证」

硬性要求：总字数不超过 200 字；不引入新文献、不重写全文；没有真问题时只写一条最关键的，不许凑数。
"""

CRITIC_EN = """You are a notoriously harsh reviewer. Your only job is to find faults. No praise, no politeness.

The author has just written one section. Give an actionable fix list.

Format (Markdown, at most 3 items, no preamble):
- **Issue N | type**: one sentence naming it (logical leap / thin evidence / repeats earlier text / awkward wording /
  inconsistent terminology / citation mismatch / empty claim)
  - Evidence: quote at most 15 words
  - Fix: which sentence becomes what argument

Hard limits: under 200 words total; no new literature; do not rewrite the whole section; if only one real problem
exists, report only that one.
"""

REWRITE_TASK_ZH = """上面是一位作者写的初稿，下面是审稿人的意见。请以作者身份重写这一节，逐条落实意见：

【审稿意见】
{critique}

重写要求：
1. 仍然是同一节，450-550 个汉字，标题保持不变。
2. 直接输出这一节的正文：第一行是 `### ` + 本节标题，随后是自然段。
   不要任何标签、前言或标题（例如「重写后的正文」），也不要解释你改了什么、不要复述审稿意见、
   不要出现「根据审稿意见」这类字样。
3. 审稿人指出的证据不足处，用大纲里的论点或〔据上传文献〕的材料补实；不得编造数据。
"""

REWRITE_TASK_EN = """Above is the author's first draft; below is the reviewer's report. Rewrite the section as the author,
addressing every point:

【Reviewer report】
{critique}

Requirements:
1. Same section, 350-450 English words, same title.
2. Output the section body directly: first line `### ` + the section title, then paragraphs. No labels, no preamble,
   no separate heading (such as "Rewritten text"), no explanation of your changes, no quoting the review, and never
   write "as the reviewer suggested".
3. Where evidence was called thin, shore it up with the outline's claims or [from uploaded paper] material; never
   invent data.
"""

LITREE_ZH = """你是文献脉络分析师，负责把研究方向画成一棵「文献树」：从源头工作到最新进展。

工作流（必须按顺序执行，不得跳步）：
1. 先调用 search_pdf_library：在用户本地的 PDF 文献库里检索与主题相关的段落，并提取这些 PDF 引用的核心参考文献。
2. 把第 1 步提取到的核心文献（作者 + 年份 + 标题关键词）逐条拿到 search_arxiv_papers 上核实、补全（可分多轮检索）。
3. 只使用工具真实返回的文献。工具没返回的，一律不许写进树里；查不到就写「未在 arXiv 检索到」。

输出结构（Markdown）：
## 检索说明
本地 PDF 命中：N 篇（列出文件名）；arXiv 核实：M 条；检索轮次：K 轮。
## 文献树
用 Markdown 缩进表示层级，年份与作者必须来自工具结果，叶节点给出 arXiv 链接：
- **研究方向：<主题>**
  - **源头工作（2021 及以前）**
    - 作者 (年份). 标题. 链接 —— 一句话说明它奠定了什么
  - **方法演进（2022-2023）**
    - …
  - **最新进展（{min_year} 及以后）**
    - …
  - **争议与空白**
    - 一句话说明哪里还有分歧、哪里没人做，并注明依据的是哪几条文献
## 脉络总结
3 条，说明这条线「从哪里来 → 现在卡在哪 → 接下来会往哪走」。
## 你还可以补的空白
2 条可操作的研究切口。

规范：不要罗列无关文献；同一篇文献只在树里出现一次；叶子节点最多 12 条。
"""

LITREE_EN = """You are a literature-evolution analyst. Draw the research field as a "literature tree", from seminal work
to the newest papers.

Workflow (mandatory order):
1. Call search_pdf_library to search the user's local PDF library for relevant passages and to extract the core
   references those PDFs cite.
2. Take each extracted core reference (author + year + title keywords) to search_arxiv_papers, one by one, to verify
   and complete it (multiple rounds allowed).
3. Use only what the tools actually returned. Anything the tools did not return must stay out of the tree; if a
   search fails, say "not found on arXiv".

Output (Markdown):
## Search log
Local PDF hits: N (list file names); arXiv verified: M; rounds: K.
## Literature tree
Markdown indentation shows the hierarchy; years and authors must come from tool output; leaves carry arXiv links:
- **Field: <topic>**
  - **Foundations (2021 and earlier)**
    - Author (Year). Title. Link — one line on what it established
  - **Method evolution (2022-2023)**
    - …
  - **Frontier ({min_year}+)**
    - …
  - **Debates and gaps**
    - one line each, naming the papers that support the claim
## Lineage summary
3 bullets: where the line came from → where it is stuck → where it goes next.
## Gaps you can fill
2 actionable research openings.

Rules: no irrelevant papers; each paper appears once; at most 12 leaves.
"""

# ---------------------------------------------------------------------------
# 点对点引导模式（Guide）：导师模式只审不写；代笔模式直接成稿
# ---------------------------------------------------------------------------
GUIDE_COACH_ZH = """
你是「论论文」工坊的**导师（Coach）**，不是代笔。用户正在逐步搭一篇学术论文，你负责严格审查他这一步填的内容。

铁律：
1. 绝对不替用户写正文、不重写他的论点段落、不输出可直接粘贴的成稿。只能点评、提问、给修改方向。
2. 先复述你读到的这一步要点（1-2 句），确认没有误解。
3. 然后指出问题，按严重程度排序：论点是否成立、证据是否支撑主张、逻辑跳跃、概念含混、可证伪性缺失、与下一步的衔接。
4. 必须给出**恰好 3 条**具体、可操作的修改建议，每条都要说清「改什么 → 怎么改 → 改完会变成什么样」。
5. 语气可以严厉，但不许空泛夸奖，不许说「很好」「不错」。
6. 最后一行只写一个结论标记：`VERDICT: PASS`（这一步达标，允许进入下一步）或 `VERDICT: REVISE`（必须重写后再提交）。
7. **双向逻辑闭合审查（Double-Chain Logic Check）——本工坊最硬的一条**：
   当用户提交的是「研究背景 / 引言 / 研究意义」或「结论 / 讨论」时，必须双向对账：
   - A 链（正向）：背景提出的问题 → 方法能否回答 → 结论是否只来自这套方法能给出的证据。
   - B 链（反向）：把结论反推回背景——结论成立需要哪些边界条件？这些条件用户在背景里说明了吗？
     如果删掉结论，背景是否依然“成立”（若成立，说明论证没有闭合）。
   - **强制追问被忽略的现实制约**：用户是不是只顾其一不顾其二？只讲收益不讲代价？
     至少点名 2 个该领域最容易被跳过的落地约束并逐条质问，例如：
     运输与部署成本、能耗与散热、数据获取与标注成本、隐私与合规、失败模式的附带损伤、
     可维护性与规模化、时间与人力预算、与既有系统的兼容成本。
   - 判据：只讲技术不讲落地、只讲优点不讲代价、结论强度超过证据 → 直接 `VERDICT: REVISE`。

输出结构（严格遵守）：
### 我看到的内容
### 双向逻辑闭合检查（A 链 / B 链）
### 被忽略的现实制约（至少 2 条，逐条质问）
### 致命问题（最多 3 条，按严重度）
### 三条修改建议
1. …（改什么 → 怎么改 → 改完的样子）
2. …
3. …
### 过关判断
一句话结论
VERDICT: PASS 或 VERDICT: REVISE
"""

GUIDE_AUTO_ZH = """
你是「论论文」工坊的**学术代笔（Ghostwriter）**。用户已经给出了这一步的关键信息，你要直接写出**可直接放进论文的成稿**。

铁律：
1. 只输出正文本身：不要解释、不要复述任务、不要写「好的，下面是……」，不要加代码块围栏。
2. 学术书面语，客观、克制，避免绝对化断言；不确定的地方用「可能／倾向于／在一定程度上」。
3. 结构要能直接嵌进论文：如果指的是某个章节，先给一行 `### 章节标题`，再写正文。
4. 长度：本步内容写 400-700 字（英文 300-450 words），宁可具体不要空话。
5. 不得编造具体数据、作者、年份、DOI。需要数据但用户没给时，写「〔此处需补充数据〕」占位，并在该句说明需要什么数据。
6. 引用必须带数字标号：正文每处借用紧跟 [n]；本步末尾另起一行写 `### 本步引用`，
   用 `[n] 作者. 标题. 年份. 链接或 DOI` 逐条列出；没有真实来源就标〔待补文献〕，绝不虚构条目。
7. **对比 / 分类矩阵（强制）**：写「文献综述 / 研究现状 / 相关工作」时，必须先给一张 Markdown 表格，
   列固定为：文献 | 研究对象 | 使用环境 | 验证阶段 | 关键结论 | 局限性（3-8 行，单元格只放短语），
   表格之后再用 2-3 段做横向对比（分歧在哪、根源是什么、共同空白是什么）。禁止逐篇流水账。
8. **图表占位符（强制）**：涉及流程对比 / 架构设计 / 数据对比 / 实验装置时，必须在该段之后另起一行写
   [此处建议插入图表：<这张图表达什么对比>，横坐标为<变量与单位>，纵坐标为<变量与单位>，数据来源/口径为<…>]
   一节最多两张图；占位符后可用一句话说明「读图应看到什么趋势」。
9. **写「引言 / Introduction」时切换成论辩模式（强制）**：
   采用冲突式漏斗——①共识/美好愿景 → ②转折指出一个具体、可被证据刺穿的 Gap → ③抛出可被反驳的核心 Thesis；
   至少实打实用上 2 句论辩句式（While it is widely assumed that [X], this paper argues that [Y]. /
   Existing literature focuses heavily on [A], but systematically overlooks the critical bottleneck of [B]. /
   The significance of [X] lies not merely in [表层理由], but fundamentally in [深层结构性理由]. /
   It is a misconception that [X]; rather, the real challenge is [Y].），句中 [X]/[A]/[B] 一律换成本文的具体对象；
   严禁把引言写成文献综述或背景说明书，严禁用「随着……的快速发展」开头。
   语感参照（省赛论文核心观点，只学转折力度，不抄内容）：
   "The central engineering challenge is not merely growing compound semiconductors in orbit,
    but closing both the growth chain and the return chain."
"""

# ---------------------------------------------------------------------------
# 论辩性引言（Strongly Argumentative Introduction）
#   冲突式漏斗 + 论辩句式 + 范文语感，供引言写作与导师审查共用
# ---------------------------------------------------------------------------
INTRO_EXEMPLAR = (
    "The central engineering challenge is not merely growing compound semiconductors "
    "in orbit, but closing both the growth chain and the return chain."
)

INTRO_FUNNEL_ZH = """【结构硬约束：冲突式漏斗（Conflict Funnel）——引言必须这样写，不是风格建议】
(1) 共识区（1-2 句）：确立本领域的普遍共识或"美好愿景"——大家都以为已经/即将发生什么好事。
(2) 转折 Pivot：立刻用"然而 / 但是 / 事实上"翻面，指出现有方案或大众认知忽略的**致命缺陷（Gap）**。
    这个 Gap 必须具体到可以被证据刺穿，例如"缺少闭环"、"只在理想条件下成立"、"把两个耦合环节当成独立问题"；
    **禁止**用"研究还不够多 / 还不够深入 / 缺少系统性研究"这类空话充当 Gap。
(3) 核心 Thesis（1-2 句）：用极锐利的句式抛出本文主张，句式形如
    "真正的挑战不在于 A，而在于 B。"（The central challenge is not merely A, but B.）
    Thesis 必须可被反驳：能想象出反方站起来说"我不同意，因为……"。反驳不了的句子不是 Thesis，是口号。
禁止清单：按时间顺序罗列文献；把引言写成文献综述；用"随着……的快速发展"开头；
只描述现象不给主张；结尾只写"本文将……"而没有承诺一个可检验的结论。"""

INTRO_TRIGGERS_ZH = """【论辩句式弹药（引言里至少实打实用掉 2 句；必须替换成本文的具体研究对象，禁止原样贴模板）】
1. While it is widely assumed that [X], this paper argues that [Y].
   （尽管人们普遍认为 [X]，本文主张 [Y]。）
2. Existing literature focuses heavily on [A], but systematically overlooks the critical bottleneck of [B].
   （既有研究大量聚焦 [A]，却系统性地忽略了 [B] 这一关键瓶颈。）
3. The significance of [X] lies not merely in [表层理由], but fundamentally in [深层结构性理由].
   （[X] 的意义不仅在于 [表层理由]，更根本地在于 [深层结构性理由]。）
4. It is a misconception that [X]; rather, the real challenge is [Y].
   （认为 [X] 是一种误解；真正的挑战是 [Y]。）

【语感参照（省赛论文的核心观点；学它的转折力度与句法，不要照抄内容）】
__EXEMPLAR__
为什么这句有力：①先复述一个大家都接受的"任务 A"；②用 not merely … but … 把战场瞬间换到 B；
③B 同时包含"上去"与"回来"两条链，逼出一个别人从未闭合的结构性缺口——这就是 Gap 与 Thesis 合体。"""

INTRO_FUNNEL_EN = """【Structural constraint: the Conflict Funnel. The Introduction MUST be written this way.】
(1) Shared ground (1-2 sentences): state the consensus or promising picture everyone accepts.
(2) Pivot: turn immediately with "however / yet / in fact" and name the fatal, specific GAP that the
    consensus overlooks. The gap must be falsifiable ("no closed loop", "holds only under ideal conditions",
    "treats two coupled stages as independent"), never "more research is needed".
(3) Thesis (1-2 sentences): a sharp, refutable claim of the form
    "The central challenge is not merely A, but B." It must be possible for an opponent to say
    "I disagree, because ...". If nobody could disagree, it is a slogan, not a thesis.
Forbidden: chronological literature listing; introduction-as-literature-review; "with the rapid development of ...";
describing phenomena without a claim; ending with "this paper will ..." and no testable conclusion."""

INTRO_TRIGGERS_EN = """【Argumentative triggers (use at least TWO, rewritten for this paper; never paste the templates)】
1. While it is widely assumed that [X], this paper argues that [Y].
2. Existing literature focuses heavily on [A], but systematically overlooks the critical bottleneck of [B].
3. The significance of [X] lies not merely in [surface reason], but fundamentally in [deep structural reason].
4. It is a misconception that [X]; rather, the real challenge is [Y].

【Tone reference (core claim of an award-winning paper; copy the rhetorical force, not the content)】
__EXEMPLAR__
Why it works: it restates an accepted task A, then swaps the battlefield to B with "not merely ... but ...",
and B couples two chains that nobody has closed together — gap and thesis in one sentence."""

INTRO_COACH_RULES_ZH = """⚠️ 本轮审查对象是【引言】，必须执行「论辩性体检」，四条里**任何一条不过就不许 PASS**：
1. **可被反驳的 Thesis**：用户有没有给出一个明确的、可以被质疑的主张？
   自测：能想象反方说"我不同意，因为……"吗？说不出反方立场 = 没有 Thesis = 不过。
2. **前人盲区（Gap）**：有没有点名前人研究忽略了什么？而且这个 Gap 是**具体缺口**
   （如"只在理想条件下成立""把两条耦合链当成独立问题"），不是"研究还不够多/不深入"？
3. **压迫感（Inevitability）**：读完之后，是否感到"这个问题不解决，整个领域就卡在这"？
   如果读起来像"这个话题挺有意思，我们也来研究一下" → 不过。
4. **不是文献综述**：通篇如果只是"某某做了 A，某某做了 B，因此……"的背景罗列 → 直接 REVISE，不许 PASS。

不过的时候不许只说「不够尖锐」，**必须给出可直接粘贴的替换句**，写成【原句 → 改后句】对照：
- 先用引号原样照抄用户那一句（必须是他真的写过的话，不许编造他没说过的句子）；
- 再另起一行写「改成：<新句>」；
- **新句里禁止出现 [X]/[A]/[B] 这类占位符**，必须已经替换成用户的具体研究对象、具体场景、具体缺口，
  是能直接拷进论文的完整中文（或中英混排）句子，不是模板。
至少给 2 组这样的【原句 → 改后句】对照，让用户照着就能改。
若用户某句话根本不存在（例如 Thesis 栏是空的），就写「原先没有 thesis，请在此补一句：<完整句子>」。

输出里必须单独有这一节：
### 论辩性体检（四条逐条写「过 / 不过」+ 一句理由）
"""


def argumentative_brief(lang: str = "zh") -> str:
    """引言用的「冲突式漏斗 + 论辩句式 + 语感参照」捆绑块。"""
    if lang == "en":
        return (INTRO_FUNNEL_EN + "\n\n" + INTRO_TRIGGERS_EN).replace("__EXEMPLAR__", INTRO_EXEMPLAR)
    return (INTRO_FUNNEL_ZH + "\n\n" + INTRO_TRIGGERS_ZH).replace("__EXEMPLAR__", INTRO_EXEMPLAR)


_INTRO_TITLE_RE = re.compile(r"引\s*言|前\s*言|绪\s*论|introduction|intro\b", flags=re.I)


def is_intro_title(title: str) -> bool:
    """判断某个章节标题是不是引言（中英都认）。"""
    return bool(_INTRO_TITLE_RE.search(str(title or "")))


# ---------------------------------------------------------------------------
# 学术图表：模型只产出 JSON 规格，由 chart_engine 确定性渲染（不执行代码）
# ---------------------------------------------------------------------------
FIGURE_ZH = """
你是「论论文」工坊的**学术图表规格生成器**。用户会给你一段数据或描述，你只输出一份 JSON 规格，
由系统用 matplotlib / graphviz 渲染。**只输出 JSON，不要解释、不要 Markdown 围栏、不要多余文字。**

JSON 字段（按需给，不需要的省略）：
{
  "kind": "bar|line|scatter|box|heatmap|pie|flow",
  "title": "图标题（简洁，学术风格，不要句号）",
  "xlabel": "X 轴名称（含单位）",
  "ylabel": "Y 轴名称（含单位）",
  "caption": "图注：一句话说清这张图证明了什么",
  "source": "数据来源/口径",
  "categories": ["类别1", "类别2"],
  "series": [{"name": "组名", "values": [1.0, 2.0]}],
  "x": [1, 2, 3],
  "points": [[x, y], [x, y]],
  "matrix": [[1, 2], [3, 4]],
  "matrix_x": ["列1", "列2"],
  "matrix_y": ["行1", "行2"],
  "nodes": [{"id": "a", "label": "模块名", "group": "层"}],
  "edges": [["a", "b", "条件或数据流"]],
  "data_table": {"headers": ["列1", "列2"], "rows": [[1, 2]]}
}

选型规则：
- 多组数值比大小 → bar；随时间/序列变化 → line；两变量相关性 → scatter；
  分布比较 → box；二维矩阵/混淆矩阵/热力 → heatmap；占比 → pie；
  系统架构、流程、数据流、实验步骤 → flow（必须给 nodes + edges）。
- 用户只给文字描述没有具体数字时：**不要编造具体数值**。改用 flow 画结构/流程，
  或给出 bar/line 但把 values 全部留空并在 caption 里写「示意图，数值待补」——宁可画结构也不虚构数据。
- 中文标签直接写中文；单位写进 xlabel/ylabel（如「准确率 (%)」「时间 (ms)」）。
- 用户给了表格或 CSV 文本时，务必原样转成 categories + series（或 data_table），不要改数字。
"""

INSTRUCTIONS = {
    "zh": {
        "outline": OUTLINE_ZH,
        "expand": EXPAND_ZH,
        "abstract": ABSTRACT_ZH,
        "litree": LITREE_ZH,
        "section": SECTION_ZH,
        "draft": DRAFT_ZH,
        "critic": CRITIC_ZH,
        "polish": POLISH_ZH,
        "pdf": PDF_ZH,
        "review": REVIEW_ZH,
        "reading": READING_ZH,
        "guide_coach": GUIDE_COACH_ZH,
        "guide_auto": GUIDE_AUTO_ZH,
        "figure": FIGURE_ZH,
    },
    "en": {
        "outline": OUTLINE_EN,
        "expand": EXPAND_EN,
        "abstract": ABSTRACT_EN,
        "litree": LITREE_EN,
        "section": SECTION_EN,
        "draft": DRAFT_EN,
        "critic": CRITIC_EN,
        "polish": POLISH_EN,
        "pdf": PDF_EN,
        "review": REVIEW_EN,
        "reading": READING_EN,
        "guide_coach": GUIDE_COACH_ZH,
        "guide_auto": GUIDE_AUTO_ZH,
        "figure": FIGURE_ZH,
    },
}

# 「风格克隆」注入模板（中文/英文）
# ---------------------------------------------------------------------------
# 模块二：灵感示例 / 段落精修 / 风格强度 / 工坊自动存档
# ---------------------------------------------------------------------------
EXAMPLE_ZH = """你是学术写作导师，负责给正在填表的用户「举几个例子」。

规则：
1. 结合当前步骤的任务与用户已填的少量内容，给出 2-3 个**可以直接照抄着改**的示例。
2. 每个示例写成一到两句话，具体、带研究对象与技术名词，不要空话。
3. 不要替用户下最终结论，示例只是「像这样的写法」，允许用户改动。
4. 只输出示例正文，用「1. 」「2. 」「3. 」编号；不要前言、不要总结、不要解释自己。
"""

EXAMPLE_EN = """You are an academic writing tutor generating example inputs for a form.

Rules:
1. Based on the current step's task and whatever the user has typed, give 2-3 examples they can adapt.
2. Each example is one or two sentences: concrete, with a real research object and technical terms.
3. Do not decide the user's final claim; examples only show "a way of saying it".
4. Output only the examples, numbered "1. " "2. " "3. "; no preface, no summary, no self-explanation.
"""


# INSTRUCTIONS 在上面已经建好，这里补注册「灵感示例」模式（常量定义在下方，所以不能内联写）
INSTRUCTIONS["zh"]["example"] = EXAMPLE_ZH
INSTRUCTIONS["en"]["example"] = EXAMPLE_EN


def build_example_prompt(step_label: str, mission: str, field_label: str, current: str = "", lang: str = "zh") -> str:
    """为「没灵感？看个例子」拼一个短提示：只给上下文，不让模型改动输入框。"""
    current = (current or "").strip()
    if lang == "en":
        return (
            f"Current step: {step_label}\nTask: {mission}\n"
            f"Field to fill: {field_label}\n"
            + (f"What the user typed so far: {current}\n" if current else "")
            + "Give 2-3 example entries for this field."
        )
    return (
        f"当前步骤：{step_label}\n这一步的任务：{mission}\n"
        f"要填的输入框：{field_label}\n"
        + (f"用户目前写了：{current}\n" if current else "")
        + "请为这个输入框给 2-3 个示例。"
    )


# 段落级精修的三种口味
REWRITE_PARA_TEMPLATE = {
    "zh": {
        "academic": (
            "把下面这段文字改写成更学术严谨的版本：术语准确、句子结构完整、"
            "去掉口语与夸张表达、结论有分寸（该 hedging 的地方 hedge）。保持原意与信息量。\n\n"
            "【原文】\n{text}\n\n只输出改写后的段落，不要任何说明或标签。"
        ),
        "critical": (
            "把下面这段文字改写成更有批判性的版本：明确对立面（Strong Argument），"
            "点出既有做法的局限与代价，用「不是……而是……」的对比结构把主张压出来，"
            "但不得编造数据或文献。保持原意与篇幅。\n\n"
            "【原文】\n{text}\n\n只输出改写后的段落，不要任何说明或标签。"
        ),
        "concise": (
            "把下面这段文字压缩成更短的版本：删掉重复、套话、软性修饰，"
            "保留全部论点与关键数据，目标减少 30%-45% 字数。\n\n"
            "【原文】\n{text}\n\n只输出改写后的段落，不要任何说明或标签。"
        ),
    },
    "en": {
        "academic": (
            "Rewrite the paragraph below in stricter academic register: precise terminology, "
            "complete sentences, no colloquialism or hype, appropriately hedged claims. "
            "Keep the meaning and information.\n\n[Text]\n{text}\n\n"
            "Output only the rewritten paragraph."
        ),
        "critical": (
            "Rewrite the paragraph below with a stronger critical argument: name the opposing view, "
            "point out the limits and costs of existing practice, and sharpen the claim with a "
            "\"not merely ... but ...\" contrast. Never invent data or citations.\n\n[Text]\n{text}\n\n"
            "Output only the rewritten paragraph."
        ),
        "concise": (
            "Condense the paragraph below: remove repetition, filler and soft modifiers, "
            "keep every claim and number, cut 30-45% of the length.\n\n[Text]\n{text}\n\n"
            "Output only the rewritten paragraph."
        ),
    },
}


def build_paragraph_rewrite_prompt(text: str, flavour: str = "academic", lang: str = "zh") -> str:
    """段落精修：只改这一小段。flavour ∈ academic / critical / concise。"""
    table = REWRITE_PARA_TEMPLATE.get(lang, REWRITE_PARA_TEMPLATE["zh"])
    template = table.get(flavour, table["academic"])
    return template.format(text=(text or "").strip())


# 风格克隆强度：1-3 轻微 / 4-7 明显 / 8-10 严格
STYLE_INTENSITY = {
    "zh": {
        "light": (
            "系统指令：这次写作只需轻微参考以下范文的行文气质（语速、语气），"
            "句式与用词保持你自己的写法，不要照搬：\n{reference}"
        ),
        "medium": (
            "系统指令：请明显模仿以下范文的文风（语气、常用词汇、句子节奏），"
            "让读者能看出是同一种笔调，但不要整句照抄：\n{reference}"
        ),
        "strong": (
            "系统指令：请严格复刻以下范文的文风，包括句式结构、常用词汇与过渡方式，"
            "尽量让文字读起来像同一位作者写的：\n{reference}"
        ),
    },
    "en": {
        "light": (
            "System instruction: only lightly borrow the feel of the reference below "
            "(pace, tone); keep your own sentence patterns and wording:\n{reference}"
        ),
        "medium": (
            "System instruction: visibly imitate the style of the reference below "
            "(tone, vocabulary, rhythm) so it reads as the same voice, without copying whole "
            "sentences:\n{reference}"
        ),
        "strong": (
            "System instruction: closely replicate the style of the reference below, including "
            "sentence structure, vocabulary and transitions, so it could pass as the same "
            "author:\n{reference}"
        ),
    },
}


def style_tier(intensity: int = 5) -> str:
    """把 1-10 的滑杆值映射成三档：light / medium / strong。异常值一律当 5。"""
    try:
        value = int(intensity)
    except (TypeError, ValueError):
        value = 5
    value = max(1, min(10, value))
    if value <= 3:
        return "light"
    if value <= 7:
        return "medium"
    return "strong"


def inject_style(prompt: str, reference: str, lang: str = "zh", intensity: int = 5) -> str:
    """把范文风格指令前置到用户提问前；范文为空则原样返回。

    intensity（1-10）决定是「轻微参考 / 明显模仿 / 严格复刻」。
    """
    reference = (reference or "").strip()
    if not reference:
        return prompt
    table = STYLE_INTENSITY.get(lang, STYLE_INTENSITY["zh"])
    template = table[style_tier(intensity)]
    return f"{template.format(reference=reference)}\n\n---\n\n{prompt}"


# ---------------------------------------------------------------------------
# 工坊自动存档：读写本地 JSON，任何权限问题都只返回空 / 失败，不抛异常
# ---------------------------------------------------------------------------
WORKSPACE_DRAFT_FILE = Path(__file__).with_name("workspace_draft.json")


def load_workspace_draft(path: Path | None = None) -> dict:
    """读取上次的工坊存档；文件缺失或损坏都返回 {}。"""
    target = Path(path) if path else WORKSPACE_DRAFT_FILE
    try:
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_workspace_draft(payload: dict, path: Path | None = None) -> bool:
    """写存档：先写临时文件再替换，失败（只读目录 / 权限不足）返回 False。"""
    target = Path(path) if path else WORKSPACE_DRAFT_FILE
    tmp = target.with_suffix(".json.tmp")
    try:
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(target)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        try:
            # 兜底：直接覆盖写（某些文件系统不允许 replace）
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError:
            return False


STYLE_TEMPLATE = {
    "zh": (
        "系统指令：请在生成论文时，严格模仿以下用户提供的范文风格"
        "（包括语气、常用词汇、句式结构）：\n{reference}"
    ),
    "en": (
        "System instruction: when writing, strictly imitate the style of the user-provided "
        "reference text below (tone, vocabulary, sentence structure):\n{reference}"
    ),
}

MODE_LABELS = {
    "zh": {
        "outline": "A · 生成论文大纲",
        "expand": "B · 扩写引言+文献综述（约 1500 字）",
        "abstract": "C · 英文摘要 Abstract（约 500 词）",
        "litree": "D · 文献树（本地 PDF → arXiv 溯源）",
    },
    "en": {
        "outline": "A · Paper outline",
        "expand": "B · Expand to intro + related work (~1500 words)",
        "abstract": "C · English abstract (~500 words)",
        "litree": "D · Literature tree (local PDFs → arXiv)",
    },
}


# ---------------------------------------------------------------------------
# 模式装配
# ---------------------------------------------------------------------------
CRITICAL_THINKING_EN = """## CRITICAL THINKING MODE — highest priority, overrides anything above

You are a top-tier academic writing coach and a native English journal editor.
When generating or revising the paper, you must strictly follow these rules.

Rule 1 — Problem-Driven Logic (the L3 blind box).
Never deliver a "Topic without Problem". Transform the user's topic into a
specific research question, make the Gap explicit ("the literature has not yet
established …"), and take a defensible stance. Every piece of writing must
contain, legibly: a Topic, a Problem, the Knowledge it builds on, Insights, a
Claim, and Evidence.

Rule 2 — Justification is mandatory (the "why").
Never merely describe what was done. Every choice — why this population (e.g.
why Chinese students?), why this theory, why this method, why this sample size —
needs an explicit rationale and a logical justification. Do not assume the reader
can read your mind; state the reason in the sentence itself.

Rule 3 — Bridge the evidence and the claim.
Prevent the "data–conclusion disconnect". Whenever you present data or a finding,
explicitly explain how that evidence supports the topic sentence / claim, and
build the bridge for the reader ("This gap of 0.07 is consistent with …, which
suggests …"). Do not ignore counter-evidence, null results, or limitations.

Rule 4 — Cohesive argumentation chain.
Keep logical links between sentences and between paragraphs across
Introduction → Method → Results → Discussion. Use a strong argumentation
structure: Premise → Grounds/Evidence → Conclusion/Claim, with signposting
("Yet", "Therefore", "By contrast", "Taken together").

Rule 5 — Precise hedging language.
Calibrate certainty to the strength of the evidence. Avoid absolute claims such
as "proves" or "clearly shows" unless the evidence is overwhelming.
- Prefer academic hedging verbs: suggest, indicate, argue, claim, appear, seem,
  tend to.
- Use modal verbs and adverbs appropriately: may, might, could, potentially,
  probably, strongly suggest.
- Use that-clauses for caution: "It has been suggested/argued that …".

Output language: English, regardless of the interface language. Keep the section
structure and headings required by the current mode, but write every sentence in
polished, idiomatic academic English, and make the reasoning chain visible.
"""


_NEEDS_SEARCH = {
    "outline": True,
    "expand": True,
    "abstract": False,
    "litree": True,   # 本地文献库 + arXiv 双工具
    "section": False,  # 逐节写作：靠大纲与上文，不联网
    "draft": False,
    "critic": False,
    "review": False,
    "reading": False,
    "guide_coach": False,
    "guide_auto": False,
    "figure": False,
    "example": False,
}


def build_agent(
    mode: str = "outline",
    *,
    polish: bool = False,
    pdf: bool = False,
    lang: str = "zh",
    critical: bool = False,
    model: str | None = None,
) -> Agent:
    """按任务模式与语言构造 Agent。润色 / PDF / 盲审模式都不检索文献。

    critical=True 时启用「英文学术思辨模式」：基座指令换成英文版，再叠加五条
    英文思辨规则（问题驱动 / 必答 Why / 证据—主张搭桥 / 论证链 / 精确 hedging），
    并要求输出英文——与当前 UI 语言无关。
    model 为空时用默认的 deepseek-chat；传 MODELS 里的键即可切换。
    """
    lang = lang if lang in INSTRUCTIONS else "zh"
    model_name = model if model in MODELS else DEFAULT_MODEL
    if polish or mode == "polish":
        key, use_search, name = "polish", False, "Academic Polisher"
    elif pdf or mode == "pdf":
        key, use_search, name = "pdf", False, "Paper Reader"
    elif mode == "review":
        key, use_search, name = "review", False, "Blind Reviewer"
    elif mode == "litree":
        key, use_search, name = "litree", True, "Literature Cartographer"
    elif mode == "section":
        key, use_search, name = "section", False, "Section Writer"
    elif mode == "draft":
        key, use_search, name = "draft", False, "Fast Drafter"
    elif mode == "critic":
        key, use_search, name = "critic", False, "Harsh Reviewer"
    elif mode == "reading":
        key, use_search, name = "reading", False, "Close Reader"
    elif mode == "guide_coach":
        key, use_search, name = "guide_coach", False, "Paper Coach"
    elif mode == "guide_auto":
        key, use_search, name = "guide_auto", False, "Paper Ghostwriter"
    elif mode == "figure":
        key, use_search, name = "figure", False, "Figure Architect"
    elif mode == "example":
        key, use_search, name = "example", False, "Idea Prompter"
    else:
        key = mode if mode in MODE_KEYS else "outline"
        use_search = _NEEDS_SEARCH[key]
        name = "Academic Writing Expert"

    if key == "litree":
        tools = [search_pdf_library, search_arxiv_papers]
    else:
        tools = [search_arxiv_papers] if use_search else []

    instructions = INSTRUCTIONS[lang][key]
    if critical:
        instructions = INSTRUCTIONS["en"][key] + "\n\n" + CRITICAL_THINKING_EN
        name = f"{name} · Critical Thinking"

    return Agent(
        name=name,
        instructions=instructions,
        tools=tools,
        llm={"provider": "deepseek", "model": model_name},
    )


# ---------------------------------------------------------------------------
# 风格克隆
# ---------------------------------------------------------------------------
def save_style_reference(text: str, path: Path = STYLE_FILE) -> Path:
    """把用户投喂的范文写到本地，供下次启动时自动载入。"""
    path.write_text(str(text).strip() + "\n", encoding="utf-8")
    return path


def load_style_reference(path: Path = STYLE_FILE) -> str:
    """读取本地范文；文件不存在时返回空串。"""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


# 素材使用模式：内容参考 vs 语言风格模仿
CONTENT_TEMPLATE = {
    "zh": (
        "系统指令：请基于以下参考材料中的事实和数据来撰写论文，不要编造。"
        "凡是直接取自材料的判断，请在该句末尾标注〔据参考文献〕；"
        "材料没有覆盖的地方可以写成待验证的假设，但不得杜撰数据、作者或年份。\n\n"
        "【参考材料】\n{context}"
    ),
    "en": (
        "System instruction: base the paper on the facts and data in the reference material below; "
        "do not invent. Mark sentences taken from it with [from references]. "
        "Where the material is silent you may state open hypotheses, but never fabricate data, "
        "authors or years.\n\n"
        "[Reference material]\n{context}"
    ),
}


def build_content_prompt(prompt: str, lang: str = "zh", max_total_chars: int = 9000) -> str:
    """「内容参考模式」：把知识库内容分块读进来，前置事实约束；库空则原样返回。"""
    context = build_library_context(max_total_chars=max_total_chars)
    if not context:
        return prompt
    template = CONTENT_TEMPLATE.get(lang, CONTENT_TEMPLATE["zh"])
    return f"{template.format(context=context)}\n\n---\n\n{prompt}"


def build_style_context(lang: str = "zh", paragraphs: int = 2) -> str:
    """「风格模仿模式」：随机抽 1-2 段做范文；返回成品 Prompt 片段（不含用户提问）。

    库空返回 ""，调用方据此判定要不要走风格注入。
    """
    snippet, _src = sample_library_snippet(paragraphs=paragraphs)
    if not snippet:
        return ""
    template = STYLE_TEMPLATE.get(lang, STYLE_TEMPLATE["zh"])
    return f"{template.format(reference=snippet)}\n\n---\n\n"


# ---------------------------------------------------------------------------
# 盲审批判
# ---------------------------------------------------------------------------
# 用户原话，作为动态追加的任务描述
REVIEW_TASK = {
    "zh": (
        "请你扮演最严苛的学术期刊盲审专家，指出上面这篇大纲的 3 个致命逻辑漏洞、"
        "2 个创新性不足的地方，并给出具体的修改建议。语言要一针见血。"
    ),
    "en": (
        "Act as the harshest blind reviewer of an academic journal: point out 3 fatal logical flaws and "
        "2 areas where novelty falls short in the outline above, and give concrete revision advice. "
        "Be blunt."
    ),
}

# 图表识别用的正则
_RE_TABLE = re.compile(r"(?:^[ \t]*\|.*\|[ \t]*\n){2,}", re.M)
_RE_MERMAID = re.compile(r"```(?:mermaid|plantuml|graphviz|dot)\b(.*?)```", re.S | re.I)
_RE_IMG_MD = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_RE_IMG_HTML = re.compile(r"<img\b[^>]*>", re.I)
_RE_CAPTION = re.compile(r"^[ \t]*(?:图|表|Figure|Table)\s*\d+[^\n]{0,80}$", re.M)


def detect_figures(text: str) -> list[dict]:
    """找出文本里已经生成的图表材料（Markdown 表格 / mermaid / 图片引用 / 图表题注）。

    返回 [{"kind": "表格"/"流程图"/"图片"/"题注", "snippet": "..."}]，同一种材料只保留一份，
    供盲审时把它们一并交给模型批判。
    """
    text = text or ""
    found: list[dict] = []

    for match in _RE_TABLE.finditer(text):
        found.append({"kind": "表格", "snippet": match.group(0).strip()})

    for match in _RE_MERMAID.finditer(text):
        fence = match.group(0).strip()
        found.append({"kind": "流程图", "snippet": fence[:800]})

    for match in _RE_IMG_MD.finditer(text):
        alt, url = match.group(1), match.group(2)
        found.append({"kind": "图片", "snippet": f"![{alt}]({url})"})

    for match in _RE_IMG_HTML.finditer(text):
        found.append({"kind": "图片", "snippet": match.group(0)[:400]})

    for match in _RE_CAPTION.finditer(text):
        caption = match.group(0).strip()
        if not any(caption in item["snippet"] for item in found):
            found.append({"kind": "题注", "snippet": caption})

    return found


def build_review_prompt(outline: str, lang: str = "zh", max_figure_chars: int = 2000) -> str:
    """拼装盲审请求：大纲 + 盲审任务；若大纲里带图表，把图表材料一并附上要求结合批判。"""
    lang = lang if lang in REVIEW_TASK else "zh"
    outline = (outline or "").strip()
    task = REVIEW_TASK[lang]
    figures = detect_figures(outline)

    if figures:
        chunks = []
        budget = max_figure_chars
        for item in figures:
            snippet = item["snippet"]
            if budget <= 0:
                break
            snippet = snippet[:budget]
            budget -= len(snippet)
            chunks.append(f"[{item['kind']}]\n{snippet}")
        material = "\n\n".join(chunks)
        if lang == "en":
            extra = (
                "\n\n【Figure material produced in the previous step】\n"
                f"{material}\n\n"
                "In addition, critique these figures: whether each one actually supports the claim it is "
                "attached to, and what is wrong with the axes, sample size, control group, statistical "
                "basis or the way it is drawn — then say how to redraw it."
            )
        else:
            extra = (
                "\n\n【上一步生成的图表材料】\n"
                f"{material}\n\n"
                "另外，请结合上面这些图表进行批判：每张图表是否真正支撑它所对应的论点，"
                "坐标轴、样本量、对照组、统计口径或呈现方式有没有问题，以及应该怎么重画。"
            )
    else:
        extra = ""

    if lang == "en":
        return f"【Outline under review】\n{outline}\n\n【Review request】\n{task}{extra}{REVIEW_PAIRS_SPEC_EN}"
    return f"【待审阅的论文大纲】\n{outline}\n\n【盲审任务】\n{task}{extra}{REVIEW_PAIRS_SPEC_ZH}"


# ---------------------------------------------------------------------------
# 上传 PDF 作为写作素材
# ---------------------------------------------------------------------------
SOURCE_TEMPLATE = {
    "zh": (
        "【优先参考的文献｜{name}】\n{text}\n\n"
        "以上是用户上传的论文正文节选（可能已截断）。写提纲时必须优先依据它的研究问题、方法、"
        "结果与局限展开；arXiv 检索只用来补充这篇文献没有覆盖到的相关工作。"
        "凡直接来自这篇上传文献的判断，在正文里标注〔据上传文献〕，不要把它当作你自己检索到的"
        "文献来编号或编造其作者与年份。\n\n---\n\n{request}"
    ),
    "en": (
        "【Uploaded paper to prioritise | {name}】\n{text}\n\n"
        "The excerpt above is a paper the user uploaded (possibly truncated). Build the outline primarily "
        "on its research question, method, results and limitations; use arXiv search only to add related "
        "work the paper does not cover. Mark claims taken directly from this upload as "
        "[from uploaded paper], and do not turn it into a fabricated numbered citation.\n\n---\n\n{request}"
    ),
}


def build_source_prompt(request: str, name: str, text: str, lang: str = "zh", max_chars: int = 12000) -> str:
    """把上传 PDF 的正文前置到请求前，要求写作时优先参考它。"""
    template = SOURCE_TEMPLATE.get(lang, SOURCE_TEMPLATE["zh"])
    excerpt = (text or "").strip()[:max_chars]
    return template.format(name=name or "PDF", text=excerpt, request=request)


# ---------------------------------------------------------------------------
# APA 参考文献 + Word 导出
# ---------------------------------------------------------------------------
_REF_HEADING = re.compile(r"^#{1,4}[ \t]*(?:参考文献|References)[ \t]*$", re.M | re.I)


def _norm_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (value or "").lower())


def split_reference_section(markdown: str) -> tuple[str, list[str]]:
    """把正文与模型自己写的参考文献分开，返回 (正文, 原始条目列表)。"""
    markdown = markdown or ""
    last = None
    for last in _REF_HEADING.finditer(markdown):
        pass
    if last is None:
        return markdown.strip(), []

    body = markdown[: last.start()].rstrip()
    entries: list[str] = []
    current: list[str] = []
    for line in markdown[last.end():].splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                entries.append(" ".join(current))
                current = []
            continue
        if re.match(r"^\[?\d+[\].、）)]", stripped) and current:
            entries.append(" ".join(current))
            current = [stripped]
        else:
            current.append(stripped)
    if current:
        entries.append(" ".join(current))
    return body, entries


def _sentence_case(title: str) -> str:
    """APA 标题用句首大写；缩写、专名与含数字的词保持原样。"""
    out: list[str] = []
    force_cap = True
    for word in (title or "").split():
        camel = bool(re.search(r"[a-z][A-Z]", word))          # MedAide / HealthAdminBench
        acronym = len(word) > 1 and word.isupper()            # LLM / AI / RAG
        keep = (
            not re.fullmatch(r"[A-Za-z][A-Za-z'\-]*", word)   # 含数字或符号，多半是专名
            or acronym
            or camel
            or len(word) <= 3
        )
        out.append(word if (keep or force_cap) else word.lower())
        force_cap = word.endswith((":", ".", "?", "!"))
    return " ".join(out)


def _apa_author(name: str) -> str:
    parts = (name or "").split()
    if len(parts) <= 1:
        return parts[0] if parts else "Anon."
    surname = parts[-1]
    initials = " ".join(_initials(p) for p in parts[:-1] if p)
    return f"{surname}, {initials}"


def _initials(part: str) -> str:
    """Han-Chin → H.-C.；Yuan → Y.（APA 的连字名首字母规则）。"""
    if "-" in part:
        return "-".join(f"{seg[0]}." for seg in part.split("-") if seg)
    return f"{part[0]}."


def _apa_authors(names: list[str]) -> str:
    formatted = [_apa_author(n) for n in names] or ["Anon."]
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]}, & {formatted[1]}"
    return ", ".join(formatted[:-1]) + ", & " + formatted[-1]


def apa_reference(entry: dict) -> str:
    """按 APA 7（arXiv 预印本）格式输出一条参考文献。"""
    authors = _apa_authors(entry.get("authors") or [])
    year = entry.get("year") or "n.d."
    title = _sentence_case(entry.get("title") or "")
    arxiv_id = entry.get("arxiv_id") or ""
    if arxiv_id:
        source = f"arXiv. https://doi.org/10.48550/arXiv.{arxiv_id}"
    else:
        source = entry.get("url", "")
    return f"{authors} ({year}). {title}. {source}".strip()


def _match_registry_entry(raw: str) -> dict | None:
    """把模型写的一条参考文献对回工具检索到的真实条目（标题包含 或 arXiv ID）。"""
    norm = _norm_text(raw)
    for entry in _REFERENCE_REGISTRY.values():
        key = _norm_text(entry["title"])
        if key and key in norm:
            return entry
    match = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", raw)
    if match:
        for entry in _REFERENCE_REGISTRY.values():
            if (entry.get("arxiv_id") or "").split("v")[0] == match.group(1):
                return entry
    return None


def _surname_key(entry: dict) -> str:
    authors = entry.get("authors") or []
    return authors[0].split()[-1].lower() if authors else "zzz"


def _cited_pairs(markdown: str) -> list[tuple[int, dict]]:
    """返回 [(原编号, 文献条目)]，已去重并按 APA 要求按首作者姓氏排序。"""
    _, raw_entries = split_reference_section(markdown)
    pairs: list[tuple[int, dict]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        match = re.match(r"^\[?\s*(\d{1,2})\s*[\].、）)]", raw)
        number = int(match.group(1)) if match else len(pairs) + 1
        entry = _match_registry_entry(raw)
        if not entry or entry["title"] in seen:
            continue
        seen.add(entry["title"])
        pairs.append((number, entry))
    pairs.sort(key=lambda item: (_surname_key(item[1]), str(item[1].get("year", ""))))
    return pairs


def apa_numbered_references(markdown: str) -> tuple[list[str], dict[int, int]]:
    """生成 APA 条目（按首作者姓氏字母序，带 [n] 编号），并返回原编号 → 新编号映射。"""
    lines: list[str] = []
    mapping: dict[int, int] = {}
    for new_number, (old_number, entry) in enumerate(_cited_pairs(markdown), 1):
        lines.append(f"[{new_number}] {apa_reference(entry)}")
        mapping[old_number] = new_number
    return lines, mapping


def apa_reference_list(markdown: str) -> list[str]:
    """返回可直接写进 Word 的 APA 参考文献文本列表。"""
    lines, _ = apa_numbered_references(markdown)
    if lines:
        return lines
    # 兜底：没对回登记表（例如服务重启后导出旧消息）时保留模型原文条目
    _, raw_entries = split_reference_section(markdown)
    return raw_entries


def renumber_citations(body: str, mapping: dict[int, int]) -> str:
    """参考文献重排后，把正文里的 [n] 同步成新编号，避免引用错位。"""
    if not mapping:
        return body
    return re.sub(
        r"\[(\d{1,2})\]", lambda m: f"[{mapping.get(int(m.group(1)), int(m.group(1)))}]", body
    )


def _add_rich_runs(paragraph, text: str) -> None:
    """把 **加粗** 拆成 run，避免 Word 里出现星号。"""
    for part in re.split(r"(\*\*[^*]+\*\*)", text):
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            run = paragraph.add_run(part[2:-2])
            run.bold = True
        elif part:
            paragraph.add_run(part)


# ---------------------------------------------------------------------------
# 模块四：引用校对 / 图表代码 / Word 插图
# ---------------------------------------------------------------------------
def check_citations(markdown: str) -> dict:
    """校对正文标号与文末参考文献条数：返回 {max_cite, ref_count, cites, ok, message}。"""
    body, refs = split_reference_section(markdown or "")
    cites = sorted({int(n) for n in re.findall(r"\[(\d{1,3})\]", body or "")})
    max_cite = max(cites) if cites else 0
    ref_count = len([r for r in refs if str(r).strip()])
    missing = [n for n in cites if n > ref_count]
    if not cites and not ref_count:
        return {
            "max_cite": 0,
            "ref_count": 0,
            "cites": cites,
            "ok": True,
            "message": "正文没有出现 [n] 标号，文末也没有参考文献列表（按纯文本处理）。",
        }
    if missing:
        return {
            "max_cite": max_cite,
            "ref_count": ref_count,
            "cites": cites,
            "ok": False,
            "message": (
                f"⚠️ 引用标号不匹配！正文最高引用至 [{max_cite}]，但参考文献只有 {ref_count} 条，"
                f"请检查是否遗漏（缺第 {', '.join(str(n) for n in missing)} 条）。"
            ),
        }
    return {
        "max_cite": max_cite,
        "ref_count": ref_count,
        "cites": cites,
        "ok": True,
        "message": f"✅ 引用校对通过：正文最高 [{max_cite}]，参考文献 {ref_count} 条，一一对应。",
    }


FIGURE_CODE_RE = re.compile(r"```(?:python|py)[ \t]*\r?\n(.*?)```", re.S)
_FIGURE_CODE_HINT = re.compile(r"matplotlib|plt\.|pyplot", re.I)


def extract_plot_blocks(markdown: str) -> list[str]:
    """把正文里的 ```python 代码块里，看起来是 matplotlib 的那些取出来。"""
    blocks: list[str] = []
    for match in FIGURE_CODE_RE.finditer(markdown or ""):
        code = match.group(1).strip()
        if code and _FIGURE_CODE_HINT.search(code):
            blocks.append(code)
    return blocks


_BLOCKED_CALLS = (
    "open(",
    "exec(",
    "eval(",
    "__import__",
    "os.",
    "sys.",
    "subprocess",
    "socket",
    "requests",
    "urllib",
    "shutil",
    "pathlib",
    "savefig",
    "input(",
    "compile(",
)


def figure_code_is_safe(code: str) -> tuple[bool, str]:
    """粗筛危险调用（不是沙箱，只是挡住明显的越界操作）。"""
    lowered = (code or "").lower()
    for token in _BLOCKED_CALLS:
        if token in lowered:
            return False, f"代码里出现了不允许的调用：`{token.strip('(')}`"
    return True, ""


def render_figure_code(code: str) -> tuple[bytes | None, str]:
    """执行绘图代码（matplotlib Agg），返回 (PNG 字节, 错误信息)。"""
    safe, reason = figure_code_is_safe(code)
    if not safe:
        return None, reason

    import io

    import matplotlib

    matplotlib.use("Agg")
    matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    matplotlib.rcParams["axes.unicode_minus"] = False
    import matplotlib.pyplot as plt

    namespace: dict = {"__name__": "__figure__"}
    plt.close("all")
    try:
        exec(compile(code, "<figure>", "exec"), namespace)  # noqa: S102 - 已做关键字拦截
        fig = plt.gcf()
        buffer = io.BytesIO()
        fig.savefig(buffer, format="png", dpi=200, bbox_inches="tight")
        plt.close(fig)
        data = buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 - 用户代码，必须兜底
        plt.close("all")
        return None, f"{type(exc).__name__}: {exc}"
    if not data:
        return None, "代码执行完没有产出图片（确认调用了绘图函数）。"
    return data, ""


def save_figure_png(data: bytes, index: int) -> Path:
    figures_dir = Path(__file__).with_name("figures")
    figures_dir.mkdir(exist_ok=True)
    path = figures_dir / f"figure_{index:02d}.png"
    path.write_bytes(data)
    return path


def append_figures_to_docx(data: bytes, images: list[dict]) -> bytes:
    """在已生成的 docx 末尾按顺序插入图片（每张下面一行题注）。"""
    if not images:
        return data
    import io as _io

    from docx import Document
    from docx.shared import Cm

    doc = Document(_io.BytesIO(data))
    doc.add_page_break()
    doc.add_heading("附录：图表", level=1)
    for i, item in enumerate(images, 1):
        path = item.get("path")
        if not path:
            continue
        try:
            doc.add_picture(str(path), width=Cm(15.5))
        except Exception:  # noqa: BLE001 - 单张失败不拖垮导出
            continue
        caption = str(item.get("caption") or f"图 {i}")
        doc.add_paragraph(f"图 {i}　{caption}")
    buffer = _io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def docx_bytes(markdown: str) -> bytes:
    """把 Markdown 转成 Word：正文照排，文末自动补 APA 参考文献（悬挂缩进）。"""
    from io import BytesIO

    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    for style_name in ("Heading 1", "Heading 2", "Heading 3"):
        style = document.styles[style_name]
        style.font.name = "Times New Roman"
        style.font.color.rgb = None if style.font.color.rgb is None else style.font.color.rgb
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")

    refs, mapping = apa_numbered_references(markdown or "")
    if not refs:
        refs = apa_reference_list(markdown or "")   # 兜底：直接用原文条目
    body, _ = split_reference_section(markdown or "")
    lines = renumber_citations(body, mapping).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        # 表格
        if stripped.startswith("|") and i + 1 < len(lines) and set(lines[i + 1].strip()) <= set("|-: "):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not set("".join(cells)) <= set("-: "):
                    rows.append(cells)
                i += 1
            if rows:
                table = document.add_table(rows=0, cols=len(rows[0]))
                table.style = "Table Grid"
                for r_idx, cells in enumerate(rows):
                    cells_row = table.add_row().cells
                    for c_idx, value in enumerate(cells[: len(cells_row)]):
                        cells_row[c_idx].text = ""
                        _add_rich_runs(cells_row[c_idx].paragraphs[0], value)
                        if r_idx == 0:
                            for run in cells_row[c_idx].paragraphs[0].runs:
                                run.bold = True
            continue

        if not stripped:
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)), 3)
            document.add_heading(heading.group(2).strip(), level=level)
            i += 1
            continue

        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        numbered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if bullet or numbered:
            text = bullet.group(1) if bullet else numbered.group(0)
            paragraph = document.add_paragraph(style="List Bullet" if bullet else "List Number")
            _add_rich_runs(paragraph, text if bullet else numbered.group(2))
            i += 1
            continue

        paragraph = document.add_paragraph()
        _add_rich_runs(paragraph, stripped)
        i += 1

    refs, _ = apa_numbered_references(markdown or "")
    if not refs:
        refs = apa_reference_list(markdown or "")
    if refs:
        document.add_heading("参考文献（APA）", level=1)
        for text in refs:
            paragraph = document.add_paragraph()
            fmt = paragraph.paragraph_format
            fmt.left_indent = Cm(0.85)          # 悬挂缩进：首行顶格，回行缩进
            fmt.first_line_indent = Cm(-0.85)
            fmt.space_after = Pt(6)
            _add_rich_runs(paragraph, text)

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def export_docx(markdown: str, path: Path = OUTPUT_DOCX) -> Path:
    """写出 .docx 文件并返回路径。"""
    path.write_bytes(docx_bytes(markdown))
    return path


# ---------------------------------------------------------------------------
# PDF 文本提取
# ---------------------------------------------------------------------------
def extract_pdf_text(data: bytes, max_chars: int = 20000) -> dict:
    """从 PDF 字节流提取纯文本。

    返回 {"pages", "chars", "text", "truncated"}；text 最多 max_chars 个字符，
    避免超长论文把上下文撑爆（DeepSeek 上下文有限）。达到字数上限即停止解析，
    所以几百页的专著也不会卡住。
    """
    from pypdf import PdfReader  # 延迟导入，未上传 PDF 时不付这个开销

    reader = PdfReader(BytesIO(data))
    total_pages = len(reader.pages)
    parts: list[str] = []
    collected = 0
    scanned = 0
    for page in reader.pages:
        if collected >= max_chars:
            break
        scanned += 1
        chunk = (page.extract_text() or "").strip()
        if chunk:
            parts.append(chunk)
            collected += len(chunk) + 2

    full = "\n\n".join(parts)
    return {
        "pages": total_pages,
        "chars": len(full),
        "text": full[:max_chars],
        "truncated": len(full) > max_chars or scanned < total_pages,
    }


# ---------------------------------------------------------------------------
# 本地 PDF 文献库（文献树的「源头」来源）
# ---------------------------------------------------------------------------
_PDF_CACHE: dict[str, str] = {}

_RE_REF_HEADING = re.compile(r"(?im)^\s*(?:\d+\.?\s*)?(参考文献|references|bibliography|works cited)\s*$")
_RE_ARXIV_ID = re.compile(r"arXiv[:\s]*(\d{4}\.\d{4,5})", re.I)
_RE_YEAR_PAREN = re.compile(r"\((?:19|20)\d{2}[a-z]?\)")


def _decode_plain_text(data: bytes) -> str:
    """纯文本解码：中文用户多半是 UTF-8 或 GBK，实在不行再退到替换模式。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_docx_text(data: bytes, max_chars: int = 20000) -> dict:
    """从 .docx 提取纯文本：按段落顺序，表格按行拼成「a | b | c」。"""
    from docx import Document  # 延迟导入

    doc = Document(BytesIO(data))
    parts: list[str] = []
    for para in doc.paragraphs:
        line = re.sub(r"\s+", " ", (para.text or "")).strip()
        if line:
            parts.append(line)
    for table in doc.tables:
        for row in table.rows:
            cells = [re.sub(r"\s+", " ", (c.text or "")).strip() for c in row.cells]
            line = " | ".join(c for c in cells if c)
            if line:
                parts.append(line)

    full = "\n\n".join(parts)
    return {
        "kind": "docx",
        "pages": 0,
        "blocks": len(parts),
        "chars": len(full),
        "text": full[:max_chars],
        "truncated": len(full) > max_chars,
    }


def extract_text(data: bytes, name: str = "", max_chars: int = 20000) -> dict:
    """通用文本提取：不管用户传 PDF / Word / TXT，都返回同样结构的纯文本。

    返回 {"kind", "pages", "chars", "text", "truncated"}；pages 对非 PDF 为 0，
    额外带 "blocks"（段落数）方便界面显示。
    """
    suffix = Path(name or "").suffix.lower()
    if suffix == ".pdf":
        info = extract_pdf_text(data, max_chars=max_chars)
        return {**info, "kind": "pdf", "blocks": info.get("pages", 0)}
    if suffix == ".docx":
        return extract_docx_text(data, max_chars=max_chars)
    if suffix in (".txt", ".md"):
        text = _decode_plain_text(data)
        return {
            "kind": "text",
            "pages": 0,
            "blocks": len([b for b in re.split(r"\n{2,}", text) if b.strip()]),
            "chars": len(text),
            "text": text[:max_chars],
            "truncated": len(text) > max_chars,
        }

    # 后缀缺失或陌生：先当 PDF 试，失败再当纯文本
    try:
        info = extract_pdf_text(data, max_chars=max_chars)
        return {**info, "kind": "pdf", "blocks": info.get("pages", 0)}
    except Exception:
        text = _decode_plain_text(data)
        return {
            "kind": "text",
            "pages": 0,
            "blocks": len([b for b in re.split(r"\n{2,}", text) if b.strip()]),
            "chars": len(text),
            "text": text[:max_chars],
            "truncated": len(text) > max_chars,
        }


# ---------------------------------------------------------------------------
# 本地知识库（PDF / DOCX / TXT 混放，供文献树、内容参考、风格模仿共用）
# ---------------------------------------------------------------------------
_LIB_CACHE: dict[str, str] = {}
_PDF_CACHE = _LIB_CACHE  # 兼容旧名字


def list_library() -> list[Path]:
    """返回知识库里所有支持的文档（按文件名排序）。"""
    files = [p for p in LIBRARY_DIR.iterdir() if p.is_file() and p.suffix.lower() in LIBRARY_SUFFIXES]
    return sorted(files, key=lambda p: p.name.lower())


def library_count() -> int:
    return len(list_library())


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^\w\u4e00-\u9fff .\-]+", "_", Path(name).stem).strip()
    return stem or "doc"


def add_document_to_library(name: str, data: bytes) -> Path:
    """把任意支持格式的文档存进 knowledge_base/（同名覆盖），并清掉它的文本缓存。"""
    suffix = Path(name).suffix.lower()
    if suffix not in LIBRARY_SUFFIXES:
        suffix = ".txt"
    path = LIBRARY_DIR / f"{_safe_stem(name)}{suffix}"
    path.write_bytes(data)
    for old in LIBRARY_DIR.glob(f"{_safe_stem(name)}.*"):  # 同名不同后缀不共存
        if old != path and old.suffix.lower() in LIBRARY_SUFFIXES:
            try:
                old.unlink()
            except OSError:
                pass
    _LIB_CACHE.pop(str(path), None)
    return path


def add_pdf_to_library(name: str, data: bytes) -> Path:  # 旧名字保留，内部已通用
    return add_document_to_library(name, data)


def delete_library_file(name: str) -> bool:
    """删除知识库里的一个文件；成功返回 True。"""
    target = LIBRARY_DIR / Path(name).name
    if not target.is_file():
        return False
    try:
        os.remove(target)
    except OSError:
        return False
    _LIB_CACHE.pop(str(target), None)
    return True


def _library_text(path: Path, max_chars: int = 80000) -> str:
    key = str(path)
    if key not in _LIB_CACHE:
        try:
            _LIB_CACHE[key] = extract_text(path.read_bytes(), path.name, max_chars=max_chars)["text"]
        except Exception:
            _LIB_CACHE[key] = ""
    return _LIB_CACHE[key]


def library_paragraphs(path: Path, min_len: int = 80) -> list[str]:
    """把一个文档拆成可用的段落列表（空行/过短的行会被丢掉）。

    PDF 抽出来的正文经常没有空行，所以再退化一层：按单换行切；仍是整块就按句子
    聚成 ~420 字的片段，保证「随机抽一段」真的是一小段而不是整页。
    """
    text = _library_text(path)
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n{2,}", text)]
    paras = [p for p in paras if len(p) >= min_len]
    if len(paras) >= 2:
        return paras

    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if len(ln) >= min_len]
    if len(lines) >= 2:
        return lines

    flat = re.sub(r"\s+", " ", text).strip()
    if not flat:
        return []
    sentences = re.split(r"(?<=[。！？.!?])\s*", flat)
    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        if not sent:
            continue
        if buf and len(buf) + len(sent) > 420:
            chunks.append(buf.strip())
            buf = sent
        else:
            buf += sent
    if buf.strip():
        chunks.append(buf.strip())
    return [c for c in chunks if len(c) >= min_len]


def sample_library_snippet(
    name: str = "",
    paragraphs: int = 2,
    min_len: int = 120,
    max_len: int = 600,
) -> tuple[str, str]:
    """从知识库随机抽 1-2 段文字当范文，返回 (片段, 文件名)；库空返回 ("", "")。"""
    files = list_library()
    if name:
        picked = [p for p in files if p.name == name]
        files = picked or files
    if not files:
        return "", ""
    chosen = random.choice(files)
    paras = [p for p in library_paragraphs(chosen, min_len=min_len) if len(p) >= min_len]
    if not paras:
        paras = [p for p in library_paragraphs(chosen, min_len=40) if len(p) >= 40]
    if not paras:
        return "", ""

    def clip(text: str) -> str:
        """截到句子边界，避免半句结尾。"""
        if len(text) <= max_len:
            return text
        cut = text[:max_len]
        pos = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"), cut.rfind(". "))
        return (cut[: pos + 1] if pos >= max_len // 2 else cut).strip()

    take = max(1, min(int(paragraphs), 2, len(paras)))
    snippet = "\n\n".join(clip(p) for p in random.sample(paras, take))
    return snippet.strip(), chosen.name


def build_library_context(max_total_chars: int = 9000, per_file_chars: int = 3000) -> str:
    """把知识库内容分块拼成参考资料（总量封顶，防止 Prompt 超长）。

    每个文件取开头若干段（论文的摘要/引言通常就在这里），按 max_total_chars 截断。
    """
    files = list_library()
    if not files:
        return ""
    blocks: list[str] = []
    budget = max_total_chars
    for path in files:
        if budget <= 0:
            break
        text = _library_text(path)
        if not text:
            continue
        chunk = text[: min(per_file_chars, budget)].strip()
        if not chunk:
            continue
        budget -= len(chunk)
        blocks.append(f"--- 参考材料：{path.name} ---\n{chunk}")
    return "\n\n".join(blocks)


def _keywords(query: str) -> list[str]:
    """粗粒度关键词：英文按词切，中文按 2-gram 切，够用且无需向量库。"""
    words = [w for w in re.split(r"[^\w\u4e00-\u9fff]+", query or "") if len(w) >= 2]
    out: list[str] = []
    for w in words:
        out.append(w)
        if re.search(r"[\u4e00-\u9fff]", w) and len(w) >= 4:
            out.extend(w[i : i + 2] for i in range(len(w) - 1))
    seen: set[str] = set()
    uniq = [w for w in out if not (w in seen or seen.add(w))]
    return uniq[:24]


def extract_references_from_text(text: str, max_refs: int = 12) -> list[str]:
    """从论文正文里粗略抽出参考文献条目（优先取 References 段落）。"""
    blocks = text or ""
    heads = list(_RE_REF_HEADING.finditer(blocks))
    cands: list[str] = []
    if heads:
        start = heads[-1].end()
        for line in blocks[start:].splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if not line:
                continue
            if _RE_ARXIV_ID.search(line) or _RE_YEAR_PAREN.search(line) or re.search(r"\bet al\.", line):
                cands.append(line[:280])
            if len(cands) >= max_refs:
                break
    if not cands:  # 没有参考文献段落：退化成找带 arXiv 编号的行
        for line in blocks.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if _RE_ARXIV_ID.search(line) and len(line) > 30:
                cands.append(line[:280])
            if len(cands) >= max_refs:
                break
    seen: set[str] = set()
    uniq: list[str] = []
    for c in cands:
        key = re.sub(r"\W+", "", c.lower())[:60]
        if key and key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


@tool
def search_pdf_library(query: str, top_k: int = 3, max_chars: int = 6000) -> str:
    """Search the user's local PDF library for passages relevant to the query.

    Returns the best matching passages (with file names) and the core references the
    cited PDFs themselves cite, so those references can then be verified on arXiv.
    Returns an explicit note when the local library is empty.
    """
    files = list_library()
    if not files:
        return (
            "【本地文献库】为空：用户还没有上传 PDF。"
            "请直接调用 search_arxiv_papers 检索，并在「检索说明」里写明本地库为空。"
        )

    words = _keywords(query)
    scored: list[tuple[int, Path, list[str]]] = []
    for path in files:
        text = _library_text(path)
        if not text:
            continue
        paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n{2,}", text) if len(p.strip()) >= 80]
        top: list[tuple[int, str]] = []
        for para in paras:
            low = para.lower()
            score = sum(low.count(w.lower()) for w in words)
            if score:
                top.append((score, para[:900]))
        top.sort(key=lambda x: -x[0])
        score_all = sum(s for s, _ in top)
        scored.append((score_all, path, [p for _, p in top[:2]]))

    scored.sort(key=lambda x: -x[0])
    hits = [s for s in scored if s[0] > 0][: max(1, min(int(top_k), 6))]

    if not hits:
        return (
            f"【本地文献库】共 {len(files)} 篇，但没有命中与「{query}」相关的段落。"
            "请改用 search_arxiv_papers 检索，并在检索说明中说明本地未命中。"
        )

    lines = [f"【本地文献库】共 {len(files)} 篇；命中 {len(hits)} 篇。"]
    budget = max_chars
    refs: list[str] = []
    for i, (_, path, paras) in enumerate(hits, 1):
        text = _library_text(path)
        lines.append(f"\n--- 本地文献 [{i}] {path.name} ---")
        for para in paras:
            if budget <= 0:
                break
            chunk = para[:budget]
            budget -= len(chunk)
            lines.append(chunk)
        for ref in extract_references_from_text(text, max_refs=5):
            if ref not in refs:
                refs.append(ref)

    lines.append("\n【这些 PDF 原文里引用的核心文献（原文摘录，未经核实）】")
    if refs:
        lines.extend(f"- {r}" for r in refs[:15])
        lines.append("\n下一步：把上面每一条（作者 + 年份 + 标题关键词）拿到 search_arxiv_papers 上核实、补全。")
    else:
        lines.append("- 未在这些 PDF 里找到可解析的参考文献条目，请改用研究主题直接检索 arXiv。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 长篇正文：大纲拆节 → 逐节提示词 → 组装
# ---------------------------------------------------------------------------
_RE_SECTION_HEAD = re.compile(r"^(#{2,3})\s+(.*\S)\s*$")
_RE_NUMBERED_HEAD = re.compile(r"^(?:\d+[.、)]|[一二三四五六七八九十]+[、.)])\s*(\S.*)$")
_REF_TITLES = {"参考文献", "references", "bibliography", "文献", "引用文献"}


def split_outline_sections(markdown: str) -> list[dict]:
    """把大纲拆成 [{"title", "body"}]。优先二级/三级标题，退化为「一、/1.」式小标题。"""
    body, _ = split_reference_section(markdown or "")
    sections: list[dict] = []
    cur: dict | None = None
    for line in body.splitlines():
        m = _RE_SECTION_HEAD.match(line)
        if m:
            if cur and cur["title"]:
                sections.append(cur)
            title = m.group(2).strip().strip("*")
            cur = {"title": title, "body": ""}
            continue
        if cur is None:
            continue
        cur["body"] += line + "\n"
    if cur and cur["title"]:
        sections.append(cur)

    if not sections:  # 退路：按「一、研究背景」这类编号行切
        for line in body.splitlines():
            m = _RE_NUMBERED_HEAD.match(line)
            if m:
                if cur and cur["title"]:
                    sections.append(cur)
                cur = {"title": m.group(1).strip().strip("*"), "body": ""}
            elif cur is not None:
                cur["body"] += line + "\n"
        if cur and cur["title"]:
            sections.append(cur)

    cleaned: list[dict] = []
    for sec in sections:
        title = sec["title"].strip()
        if not title or title.lower().strip("# ") in _REF_TITLES:
            continue
        text = sec["body"].strip()
        # 空小节也保留：只有标题时模型可据标题展开，比丢掉一节安全
        cleaned.append({"title": title, "body": text})
    return cleaned


def outline_title(markdown: str) -> str:
    """取大纲的一级标题（或第一行非空文本）作文档名。"""
    for line in (markdown or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^#{1,2}\s+(.*\S)\s*$", line)
        if m:
            return m.group(1).strip().strip("*")
        return line.strip("* ")[:60]
    return "论文正文"


def build_section_prompt(
    outline: str,
    title: str,
    body: str,
    previous_text: str = "",
    lang: str = "zh",
    uploaded: str = "",
    target_chars: int = 500,
) -> str:
    """单节写作提示词：大纲要点 + 前文落点 + 字数与禁重复约束。"""
    is_zh = lang != "en"
    outline_excerpt = (outline or "").strip()[:4000]
    prev = (previous_text or "").strip()
    prev = prev[-1200:] if prev else ""
    parts = []
    if outline_excerpt:
        parts.append(("【论文大纲（节选）】" if is_zh else "【Paper outline (excerpt)】") + "\n" + outline_excerpt)
    if uploaded:
        parts.append(("【用户上传的文献材料（优先作为依据）】" if is_zh else "【Uploaded source material】") + "\n" + uploaded[:6000])
    if prev:
        parts.append(
            ("【上一节已经写完的正文（只用于承接，禁止重复其句子与例子）】\n" if is_zh
             else "【Text already written (for continuity only; never repeat its sentences or examples)】\n")
            + "…" + prev
        )
    parts.append(
        ("【本次只需要写的这一节】\n标题：" + title + "\n大纲要点：\n" + (body or "（无细目，请按标题合理展开）"))
        if is_zh
        else ("【The single section to write now】\nTitle: " + title + "\nOutline points:\n" + (body or "(none; expand from the title)"))
    )
    parts.append(
        (f"【字数】本节 {target_chars - 50}-{target_chars + 50} 字左右，只写这一节。" if is_zh
         else f"【Length】About {target_chars} characters (±10%), this section only.")
    )
    # 引言单独加码：冲突式漏斗 + 论辩句式（其他章节不受影响）
    if is_intro_title(title):
        parts.append(argumentative_brief(lang))
        parts.append(
            "【引言的交付要求】按「共识/美好愿景 → 转折指出具体 Gap → 锐利 Thesis」三段推进；"
            "至少用上 2 句上面的论辩句式（替换成本文对象）；不许罗列文献、不许用「随着……的发展」开头。"
            if is_zh
            else "【Introduction delivery】Advance in three moves: shared ground → pivot naming a concrete gap → "
                 "sharp refutable thesis. Use at least two argumentative triggers, rewritten for this paper. "
                 "No literature listing, no 'with the rapid development of ...' opener."
        )

    base = "\n\n".join(parts)
    rule = FIGURE_CODE_RULE_ZH if lang == "zh" else FIGURE_CODE_RULE_EN
    return base + "\n\n" + rule

def build_critic_prompt(title: str, draft: str, lang: str = "zh") -> str:
    """把初稿交给毒舌审稿人。"""
    if lang == "en":
        return f"【Section title】{title}\n\n【Draft just written】\n{draft}\n\nNow tear it apart."
    return f"【本节标题】{title}\n\n【作者刚写完的初稿】\n{draft}\n\n请开始挑错。"


def build_rewrite_prompt(title: str, draft: str, critique: str, lang: str = "zh") -> str:
    """初稿 + 审稿意见 → 作者重写。"""
    template = REWRITE_TASK_EN if lang == "en" else REWRITE_TASK_ZH
    head = f"【Section title】{title}\n\n【Draft】\n{draft}" if lang == "en" else f"【本节标题】{title}\n\n【初稿】\n{draft}"
    return head + "\n\n" + template.format(critique=critique.strip())


def build_compress_prompt(title: str, text: str, lang: str = "zh", target_chars: int = 500) -> str:
    """字数超预算时，让模型把这一节压回 500 字（保留引用编号与论点）。"""
    if lang == "en":
        return (
            f"【Section】{title}\n\n【Current text (too long)】\n{text}\n\n"
            f"Compress it to {target_chars - 50}-{target_chars + 50} words: drop filler and repeated explanation, "
            "keep every claim, every citation number and the `### ` title line. Output the compressed section only, "
            "no commentary."
        )
    return (
        f"【本节标题】{title}\n\n【当前正文（超出字数）】\n{text}\n\n"
        f"请把这一节压缩到 {target_chars - 50}-{target_chars + 50} 个汉字：删掉铺垫、重复解释与套话，"
        "保留全部论点、全部引用编号，以及开头的 `### ` 标题行。只输出压缩后的本节正文，不要任何说明。"
    )


def assemble_body(title: str, parts: list[dict], references_md: str = "", lang: str = "zh") -> str:
    """把逐节生成的正文拼成一篇完整 Markdown（带标题与 APA 参考文献段）。"""
    chunks = [f"# {title}".strip()]
    for part in parts:
        text = (part.get("text") or "").strip()
        if not text:
            continue
        sec_title = (part.get("title") or "").strip()
        if not text.startswith("#") and sec_title:
            text = f"### {sec_title}\n\n{text}"
        chunks.append(text)
    if references_md.strip():
        heading = "## References" if lang == "en" else "## 参考文献"
        chunks.append(f"{heading}\n\n{references_md.strip()}")
    return "\n\n".join(chunks) + "\n"


# ---------------------------------------------------------------------------
# 落盘 + 命令行入口
# ---------------------------------------------------------------------------
def save_outline(text: str, path: Path = OUTPUT_FILE) -> Path:
    """把结果写成 UTF-8 Markdown，返回实际路径。"""
    path.write_text(str(text).rstrip() + "\n", encoding="utf-8")
    return path


def save_body(text: str, path: Path = OUTPUT_BODY) -> Path:
    """把长篇正文写成 UTF-8 Markdown。"""
    path.write_text(str(text).rstrip() + "\n", encoding="utf-8")
    return path


def export_body(markdown: str, path: Path = OUTPUT_BODY_DOCX) -> Path:
    """把长篇正文导出成 .docx（与大纲同一套 APA 排版）。"""
    path.write_bytes(docx_bytes(markdown))
    return path


# ---------------------------------------------------------------------------
# 文献精读工作台：五级相关度 + 自定义叠加 + 双格式导出
# ---------------------------------------------------------------------------
# 每级只读「该读的那部分」，并带自己的一套提取要求
READING_LEVELS: dict[str, dict] = {
    "S": {
        "label": "🌟 S级 (核心基石)",
        "label_en": "🌟 S — core foundation",
        "alias": "S",
        "desc": "通读全文",
        "desc_en": "read the full text",
        "sections": ("all",),
        "max_chars": 14000,
        "bullets": (
            "研究问题（research question）",
            "理论框架（theory / framework）",
            "核心论点（claims）",
            "研究方法（methods）",
            "创新点（novelty）",
            "对你课题的借鉴意义（implications）",
        ),
    },
    "A": {
        "label": "⭐ A级 (方法参考)",
        "label_en": "⭐ A — method reference",
        "alias": "A",
        "desc": "读摘要 / 方法 / 结论",
        "desc_en": "abstract / methods / conclusion",
        "sections": ("abstract", "method", "conclusion"),
        "max_chars": 6500,
        "bullets": (
            "具体模型 / 算法（model, algorithm）",
            "数据与结果（dataset, metrics, numbers）",
            "方法论启示（what you can reuse）",
        ),
    },
    "B": {
        "label": "🟢 B级 (背景综述)",
        "label_en": "🟢 B — background",
        "alias": "B",
        "desc": "读引言 / 结论",
        "desc_en": "introduction / conclusion",
        "sections": ("abstract", "intro", "conclusion"),
        "max_chars": 4500,
        "bullets": (
            "宏观背景（context）",
            "前人局限 / Gap（limitations of prior work）",
            "学术脉络定位（where it sits in the field）",
        ),
    },
    "C": {
        "label": "🟡 C级 (边缘扫读)",
        "label_en": "🟡 C — quick scan",
        "alias": "C",
        "desc": "仅读标题 / 摘要",
        "desc_en": "title / abstract only",
        "sections": ("abstract",),
        "max_chars": 1800,
        "bullets": ("1-2 句核心结论（one or two sentences）",),
    },
    "D": {
        "label": "🔴 D级 (低质过滤)",
        "label_en": "🔴 D — filter out",
        "alias": "D",
        "desc": "仅比对标题",
        "desc_en": "title comparison only",
        "sections": ("title",),
        "max_chars": 400,
        "bullets": (),
    },
}

# 章节识别：中文/英文论文常见标题
_RE_PAPER_HEADING = re.compile(
    r"^[ \t]*(?:"
    r"abstract|摘\s*要|"
    r"introduction|引\s*言|前\s*言|"
    r"(?:related\s+work|literature\s+review|相关工作|文献综述)|"
    r"(?:method(?:s|ology)?|approach|methods?\s+and\s+materials|方\s*法|研究方法|模型与算法)|"
    r"(?:experiment(?:s)?|evaluation|results?|实\s*验|结果与分析|实验结果)|"
    r"(?:discussion|讨\s*论)|"
    r"(?:conclusion(?:s)?|concluding\s+remarks|结\s*论|总结与展望)|"
    r"(?:references?|bibliography|参考文献)"
    r")[ \t]*(?:[:：]?[ \t]*\d*[ \t]*)$",
    re.M | re.I,
)

_HEADING_KIND = (
    ("abstract", ("abstract", "摘要")),
    ("intro", ("introduction", "引言", "前言")),
    ("related", ("related work", "literature review", "相关工作", "文献综述")),
    ("method", ("method", "approach", "方法")),
    ("experiment", ("experiment", "evaluation", "results", "实验", "结果")),
    ("discussion", ("discussion", "讨论")),
    ("conclusion", ("conclusion", "concluding", "结论", "总结")),
    ("references", ("reference", "bibliography", "参考文献")),
)


def _heading_kind(name: str) -> str:
    low = name.strip().lower()
    for kind, keys in _HEADING_KIND:
        for key in keys:
            if key in low:
                return kind
    return "other"


def split_paper_sections(text: str) -> list[dict]:
    """把论文正文按常见章节标题切开，返回 [{"kind","title","body"}]（按出现顺序）。"""
    text = text or ""
    marks = list(_RE_PAPER_HEADING.finditer(text))
    if not marks:
        return [{"kind": "all", "title": "", "body": text}]

    blocks: list[dict] = []
    head = text[: marks[0].start()].strip()
    if head:
        blocks.append({"kind": "head", "title": "开头", "body": head})
    for idx, mark in enumerate(marks):
        end = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
        body = text[mark.end() : end].strip()
        title = re.sub(r"\s+", " ", mark.group(0)).strip()
        blocks.append({"kind": _heading_kind(title), "title": title, "body": body})
    return blocks


def paper_excerpt(text: str, level: str = "S", max_chars: int | None = None) -> str:
    """按精读级别挑出「该读的那部分」原文，返回带小标题的节选。

    D 级只回开头几行当标题比对素材；其余级别按 sections 白名单抽取，抽不到就退回开头。
    """
    spec = READING_LEVELS.get(level.upper(), READING_LEVELS["S"])
    budget = max_chars or spec["max_chars"]
    text = (text or "").strip()
    if not text:
        return ""
    if level.upper() == "D":
        first = text.splitlines()[0] if text.splitlines() else text[:200]
        return first[:budget]

    wanted = spec["sections"]
    blocks = split_paper_sections(text)
    picked: list[dict] = []
    if "all" in wanted:
        picked = [b for b in blocks if b["kind"] != "references"]
    else:
        # 开头（含标题/作者/摘要前的内容）永远保留一小段，便于识别文献
        for blk in blocks:
            if blk["kind"] in ("head", "abstract"):
                picked.append(blk)
        for blk in blocks:
            if blk["kind"] in wanted and blk["kind"] != "abstract":
                picked.append(blk)

    if not picked:
        picked = [{"kind": "head", "title": "原文", "body": text}]

    chunks: list[str] = []
    used = 0
    for blk in picked:
        body = re.sub(r"\n{3,}", "\n\n", blk["body"]).strip()
        if not body:
            continue
        room = budget - used
        if room <= 0:
            break
        piece = body[:room]
        label = f"【{blk['title']}】\n" if blk["title"] and blk["kind"] != "head" else ""
        chunks.append(label + piece)
        used += len(piece)
    return "\n\n".join(chunks).strip()


RELEVANCE_TEMPLATE = {
    "zh": (
        "你是文献筛选助手。请只做一件事：判断下面这篇文献与我的课题的相关度。\n"
        "【我的课题】{topic}\n"
        "【文献标题】{title}\n"
        "【文献开头片段】\n{head}\n\n"
        "只输出两行，不要任何多余文字：\n"
        "相关度：高 / 中 / 低\n"
        "理由：一句话（不超过 40 字）"
    ),
    "en": (
        "You are a screening assistant. Do one thing only: judge how relevant the paper below is to my topic.\n"
        "[Topic] {topic}\n[Title] {title}\n[Opening excerpt]\n{head}\n\n"
        "Output exactly two lines, nothing else:\n"
        "Relevance: high / medium / low\n"
        "Reason: one sentence (<= 20 words)"
    ),
}

READING_FOCUS_BLOCK = {
    "zh": (
        "\n【本轮读取重点：语言风格，不是内容综述】\n"
        "除了上面要求的字段，请额外给出：\n"
        "## 语言风格卡片\n"
        "- **人称与主语**：作者习惯用哪种主语（我们 / 本文 / 研究者）。\n"
        "- **句子长度与节奏**：长句为主还是短句为主，一句话里平均几个分句。\n"
        "- **论证动词**：作者用来表态的动词或短语（例如「表明」「暗示」「难以排除」）。\n"
        "- **转折与让步句式**：照抄 2 句原文里的典型句式作为模板。\n"
        "- **可复用的句式骨架**：给 3 条把具体内容挖空的骨架句（用 ____ 占位，不要写 [X]）。\n"
        "不要概述这篇文献的研究内容，只交付风格层面的东西。\n"
    ),
    "en": (
        "\n[Focus of this pass: writing style, not a content summary]\n"
        "In addition to the fields above, output:\n"
        "## Style card\n"
        "- **Person and subjects**: which subjects the author prefers.\n"
        "- **Sentence length and rhythm**: long vs short, clauses per sentence.\n"
        "- **Stance verbs**: the verbs used to hedge or claim.\n"
        "- **Contrast / concession patterns**: copy 2 typical sentences verbatim as templates.\n"
        "- **Reusable skeletons**: 3 sentence skeletons with blanks (use ____, never [X]).\n"
        "Do not summarise the paper's content; only deliver style-level findings.\n"
    ),
}

READING_TEMPLATE = {
    "zh": (
        "你是严谨的文献精读助手。课题：{topic}\n"
        "文献：{title}\n"
        "精读级别：{level_label}（{desc}）\n\n"
        "只依据下面的原文片段作答，原文没有的信息写「原文未提及」，禁止编造。\n"
        "输出 Markdown，严格按这个结构：\n"
        "## 一句话定位\n（20 字以内）\n"
        "## 相关度\n（高 / 中 / 低 + 一句理由）\n"
        "{bullet_block}"
        "{custom_block}"
        "【原文片段】\n{excerpt}"
    ),
    "en": (
        "You are a rigorous close-reading assistant. Topic: {topic}\n"
        "Paper: {title}\nReading level: {level_label} ({desc})\n\n"
        "Answer only from the passage below; write 'not stated in the excerpt' when absent; never fabricate.\n"
        "Output Markdown with exactly this structure:\n"
        "## One-line summary\n(<= 20 words)\n"
        "## Relevance\n(high / medium / low + one reason)\n"
        "{bullet_block}"
        "{custom_block}"
        "[Excerpt]\n{excerpt}"
    ),
}

# ---------------------------------------------------------------------------
# 模块三：arXiv 在线检索 / 下载 / 自动分级 / 写作大本营素材
# ---------------------------------------------------------------------------
ARXIV_DIR = Path(tempfile.gettempdir()) / "paper-dino-arxiv"


def search_arxiv_entries(query: str, max_results: int = 5) -> list[dict]:
    """检索 arXiv，返回结构化结果（给网页列表用，不经过模型）。"""
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, min(int(max_results or 5), 10))
    client = arxiv.Client(page_size=limit, delay_seconds=3, num_retries=2)
    try:
        papers = list(
            client.results(
                arxiv.Search(
                    query=query,
                    max_results=limit,
                    sort_by=arxiv.SortCriterion.Relevance,
                )
            )
        )
    except Exception:  # noqa: BLE001 - 网络失败当成空结果
        return []

    entries: list[dict] = []
    for paper in papers[:limit]:
        try:
            _register_paper(paper)
        except Exception:  # noqa: BLE001
            pass
        short_id = str(paper.get_short_id() or "")
        arxiv_id = re.sub(r"v\d+$", "", short_id)
        entries.append(
            {
                "title": (paper.title or "").strip().replace("\n", " "),
                "authors": ", ".join(a.name for a in paper.authors[:3])
                + (" et al." if len(paper.authors) > 3 else ""),
                "year": paper.published.year if paper.published else "",
                "summary": re.sub(r"\s+", " ", (paper.summary or "")).strip(),
                "pdf_url": str(paper.pdf_url or ""),
                "arxiv_id": arxiv_id,
                "page_url": str(paper.entry_id or ""),
            }
        )
    return entries


def _http_get_bytes(url: str) -> bytes:
    """下载二进制：先正常校验证书，失败再退到不校验（本机有代理/自签环境）。"""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; PaperDino/1.0)",
            "Accept": "application/pdf,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return resp.read()
    except Exception:  # noqa: BLE001 - 再试一次不校验证书
        context = ssl._create_unverified_context()  # noqa: SLF001
        with urllib.request.urlopen(request, timeout=60, context=context) as resp:
            return resp.read()


def download_arxiv_pdf(url: str, filename: str = "") -> Path:
    """把 arXiv PDF 存到本地临时目录，返回路径；失败抛异常由调用方提示。"""
    ARXIV_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-.一-鿿]+", "_", filename or "") or "arxiv"
    if not safe.lower().endswith(".pdf"):
        safe += ".pdf"
    target = ARXIV_DIR / safe
    data = _http_get_bytes(url)
    if not data.startswith(b"%PDF"):
        raise ValueError("下载到的不是 PDF（可能被代理拦截）")
    target.write_bytes(data)
    return target


AUTO_LEVEL_TEMPLATE = (
    "你只做一件事：判断下面这篇论文对「{topic}」这个课题的相关度级别。\n"
    "级别定义：S=核心基石（必须通读）、A=方法参考、B=背景综述、C=边缘扫描。\n"
    "只输出两行：第一行一个字母（S/A/B/C），第二行不超过 30 字的理由。\n"
    "不要输出任何其它内容。\n\n"
    "标题：{title}\n"
    "正文开头：\n{head}"
)


def build_auto_level_prompt(topic: str, title: str, head_text: str, max_chars: int = 1000) -> str:
    return AUTO_LEVEL_TEMPLATE.format(
        topic=topic or "（未填写课题）",
        title=title,
        head=re.sub(r"\s+", " ", head_text or "")[:max_chars],
    )


def parse_auto_level(reply: str) -> tuple[str, str]:
    """从模型回复里抠出级别字母和理由；抠不到就当 C。"""
    text = (reply or "").strip()
    match = re.search(r"\b([SABCD])\b", text.upper())
    level = match.group(1) if match else "C"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    reason = ""
    for line in lines:
        if line.upper().strip("。.:：") == level:
            continue
        if re.fullmatch(r"[SABCD]", line.upper()):
            continue
        reason = line
        break
    return level, reason[:60]


MATERIAL_TEMPLATE = (
    "【写作大本营素材 · {title}】\n{body}\n"
)


def build_material_block(materials: list[dict], max_chars: int = 2400) -> str:
    """把大本营里的素材拼成一段可注入 prompt 的文本。"""
    chunks: list[str] = []
    used = 0
    for item in materials or []:
        body = str(item.get("body") or "").strip()
        if not body:
            continue
        piece = MATERIAL_TEMPLATE.format(title=item.get("title") or "", body=body)
        if used + len(piece) > max_chars:
            piece = piece[: max(0, max_chars - used)]
        if not piece.strip():
            break
        chunks.append(piece)
        used += len(piece)
        if used >= max_chars:
            break
    return "\n".join(chunks).strip()


def build_relevance_prompt(topic: str, title: str, text: str, lang: str = "zh") -> str:
    head = re.sub(r"\s+", " ", (text or "").strip())[:600]
    template = RELEVANCE_TEMPLATE.get(lang, RELEVANCE_TEMPLATE["zh"])
    return template.format(topic=topic.strip() or "（未填写课题）", title=title, head=head)


def build_reading_prompt(
    topic: str,
    title: str,
    text: str,
    level: str = "S",
    lang: str = "zh",
    custom: str = "",
    focus: str = "content",
    rag_block: str = "",
) -> tuple[str, str]:
    """按级别拼精读 Prompt，返回 (prompt, 用于界面的节选原文)。

    rag_block 非空时：把 FAISS 检索出来的高相关片段（带 [来源：文件名 - 第X页]）
    顶到正文前面，并要求模型优先引用这些带页码的片段。
    """
    level_key = level.upper()
    spec = READING_LEVELS.get(level_key, READING_LEVELS["S"])
    excerpt = paper_excerpt(text, level_key)
    if level_key == "D":
        # 标题比对级：不读正文，只让模型判断值不值得读（自定义要求照样叠加）
        custom_line = ""
        if custom.strip():
            custom_line = f"\n额外要求（必须一并回答）：{custom.strip()}\n"
        prompt = (
            f"课题：{topic.strip()}\n文献标题：{title}\n"
            f"文献开头：{excerpt[:300]}\n"
            f"{custom_line}\n"
            "只输出两行：\n相关度：高 / 中 / 低\n建议：如果相关度低，写「相关度极低，建议放弃阅读」；"
            "否则写一句「建议扫读：<理由>」。"
        )
        if focus == "style":
            prompt += "\n" + READING_FOCUS_BLOCK.get(lang, READING_FOCUS_BLOCK["zh"])
        return prompt, excerpt

    bullets = spec["bullets"]
    if lang == "en":
        bullet_block = "## Extracted content\n" + "\n".join(f"- **{b}**: " for b in bullets) + "\n"
        custom_block = ""
        if custom.strip():
            custom_block = (
                "\n## Additional request (must answer as well)\n"
                f"{custom.strip()}\n"
            )
    else:
        bullet_block = "## 提取内容\n" + "\n".join(f"- **{b}**：" for b in bullets) + "\n"
        custom_block = ""
        if custom.strip():
            custom_block = f"\n## 额外要求（必须一并回答）\n{custom.strip()}\n"

    rag_section = ""
    if rag_block.strip():
        if lang == "en":
            rag_section = (
                "\n## Retrieved passages (each carries [source: file - page])\n"
                "Prefer these passages when extracting; if you use one, keep its page marker.\n"
                f"{rag_block.strip()}\n"
            )
        else:
            rag_section = (
                "\n## 检索到的原文片段（每段都带 [来源：文件名 - 第X页]）\n"
                "提取时优先依据这些片段；引用时保留页码标记，不要丢掉来源。\n"
                f"{rag_block.strip()}\n"
            )

    template = READING_TEMPLATE.get(lang, READING_TEMPLATE["zh"])
    prompt = template.format(
        topic=topic.strip() or "（未填写课题）",
        title=title,
        level_label=spec["label"],
        desc=spec["desc"],
        bullet_block=bullet_block,
        custom_block=custom_block,
        excerpt=excerpt + rag_section,
    )
    if focus == "style":
        prompt += "\n" + READING_FOCUS_BLOCK.get(lang, READING_FOCUS_BLOCK["zh"])
    return prompt, excerpt


def parse_relevance(reply: str) -> tuple[str, str]:
    """从模型回复里抠出（相关度, 理由），抠不到就原样返回。"""
    text = (reply or "").strip()
    level = ""
    reason = ""
    for line in text.splitlines():
        low = line.strip()
        if not low:
            continue
        if re.match(r"^(相关度|relevance)\s*[:：]", low, re.I):
            value = re.split(r"[:：]", low, maxsplit=1)[1].strip()
            if re.search(r"高|high", value, re.I):
                level = "高"
            elif re.search(r"中|medium|mid", value, re.I):
                level = "中"
            elif re.search(r"低|low", value, re.I):
                level = "低"
            else:
                level = value[:12]
        elif re.match(r"^(理由|reason)\s*[:：]", low, re.I):
            reason = re.split(r"[:：]", low, maxsplit=1)[1].strip()
    return level or "未知", reason or text.replace("\n", " ")[:60]


def reading_plain_text(topic: str, reports: list[dict]) -> str:
    """把多篇报告拼成纯文本（供复制到剪贴板 / 粘进笔记）。"""
    lines = [f"{topic or '文献精读'}｜文献精读报告", ""]
    for idx, rep in enumerate(reports, 1):
        lines.append(f"[{idx}] {rep.get('title', '未命名文献')}")
        lines.append(
            f"级别：{rep.get('level_label', '')}｜相关度：{rep.get('relevance', '未知')}"
            f"{('｜' + rep['reason']) if rep.get('reason') else ''}"
        )
        if rep.get("custom"):
            lines.append(f"自定义要求：{rep['custom']}")
        lines.append("")
        lines.append((rep.get("markdown") or "").strip())
        lines.append("")
        lines.append("-" * 60)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def reading_notes_docx(topic: str, reports: list[dict]) -> bytes:
    """按 H1（课题）/ H2（文献名）/ 正文（提取结果）的层级生成 Word 笔记。"""
    from io import BytesIO

    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(11)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    for style_name in ("Heading 1", "Heading 2", "Heading 3"):
        style = document.styles[style_name]
        style.font.name = "Times New Roman"
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")

    document.add_heading(topic.strip() or "文献精读报告", level=1)
    for rep in reports:
        document.add_heading(rep.get("title", "未命名文献"), level=2)
        meta = document.add_paragraph()
        meta_run = meta.add_run(
            f"级别：{rep.get('level_label', '')}｜相关度：{rep.get('relevance', '未知')}"
            + (f"｜{rep['reason']}" if rep.get("reason") else "")
        )
        meta_run.italic = True
        if rep.get("custom"):
            note = document.add_paragraph()
            note.add_run(f"自定义要求：{rep['custom']}").bold = True

        for line in (rep.get("markdown") or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if heading:
                document.add_heading(heading.group(2).strip(), level=min(len(heading.group(1)) + 1, 3))
                continue
            bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
            if bullet:
                document.add_paragraph(style="List Bullet").add_run(bullet.group(1))
                continue
            numbered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
            if numbered:
                document.add_paragraph(style="List Number").add_run(numbered.group(1))
                continue
            paragraph = document.add_paragraph()
            _add_rich_runs(paragraph, stripped)
        document.add_paragraph()

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def main() -> None:
    response = build_agent("outline").run(
        "帮我写一篇关于大模型智能体（LLM Agents）在医疗领域应用的论文大纲"
    )
    print(response)
    saved = save_outline(response)
    print(f"\n[saved] {saved}  ({len(str(response))} 字符)")


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------------
# 点对点引导模式（Guide）：六步结构、Prompt 组装、成稿组装与导出
# ---------------------------------------------------------------------------
GUIDE_STEPS: list[dict] = [
    {
        "id": "thesis",
        "label": "Step 1 · 确立核心论题",
        "en": "Step 1 · Thesis",
        "mission": "从大话题缩小到具体的学术 Gap：大话题 → 你已经看到的空白/矛盾 → 一句话论点。",
        "fields": [
            {"key": "big_topic", "label": "1. 大话题", "ph": "例如：生成式 AI 在高校学术写作中的应用", "area": False},
            {"key": "angle", "label": "2. 切入点（你打算从哪个缝隙钻进去）", "ph": "例如：EFL 学习者在人机协作写作中的作者身份", "area": False},
            {"key": "thesis", "label": "3. 拟定的一句话核心论点", "ph": "例如：AI 协作写作并未削弱 EFL 作者身份，而是把它从'独立执笔'重构为'协商编辑'。", "area": True},
        ],
    },
    {
        "id": "rqs",
        "label": "Step 2 · 拆解论题与提出研究问题",
        "en": "Step 2 · Research questions",
        "mission": "把核心论点拆成 3 个支撑性问题，每个都要有 Why（为什么必须答）和 How（打算怎么答）。",
        "fields": [
            {"key": "rq1", "label": "RQ1（含 Why 与 How）", "ph": "RQ1：学习者在人机协作中如何分配决策权？Why：这决定作者身份是否被让渡。How：写作过程日志 + 回溯访谈。", "area": True},
            {"key": "rq2", "label": "RQ2（含 Why 与 How）", "ph": "RQ2：…", "area": True},
            {"key": "rq3", "label": "RQ3（含 Why 与 How）", "ph": "RQ3：…", "area": True},
        ],
    },
    {
        "id": "design",
        "label": "Step 3 · 研究导向与设计",
        "en": "Step 3 · Research design",
        "mission": "选择研究导向（案例研究 / 文献综述 / 实验研究），并写清数据来源或实验设计。",
        "fields": [
            {"key": "approach", "label": "1. 导向类型", "ph": "", "area": False,
             "options": ["案例研究（Case study）", "文献综述（Literature review）", "实验 / 准实验（Experiment）", "混合方法（Mixed methods）"]},
            {"key": "detail", "label": "2. 具体数据来源 / 实验方法设计", "ph": "例如：某高校 EFL 写作课 2 个班 68 人，12 周；实验组用 AI 协作工作台，对照组传统写作；采集草稿版本链、屏幕录制、期末文本与学生访谈。", "area": True},
        ],
    },
    {
        "id": "body",
        "label": "Step 4 · 主体论证与证据",
        "en": "Step 4 · Body & evidence",
        "mission": "按 Topic Sentence + Evidence + Analysis 组织：针对 Step 2 的三个问题，分别填入你找到的证据/数据。",
        "fields": [
            {"key": "ev1", "label": "针对 RQ1 的证据 / 数据", "ph": "论点句 → 证据（数据、引文、观察）→ 分析（这说明什么，为何支撑论点）", "area": True},
            {"key": "ev2", "label": "针对 RQ2 的证据 / 数据", "ph": "…", "area": True},
            {"key": "ev3", "label": "针对 RQ3 的证据 / 数据", "ph": "…", "area": True},
        ],
    },
    {
        "id": "conclusion",
        "label": "Step 5 · 结论与讨论",
        "en": "Step 5 · Conclusion",
        "mission": "总结成果，写清局限性（Limitations）与未来方向。",
        "fields": [
            {"key": "conclusion", "label": "1. 核心结论", "ph": "例如：作者身份由'执笔'转向'协商编辑'，且高度依赖提示素养。", "area": True},
            {"key": "limits", "label": "2. 局限性", "ph": "例如：单校两班、12 周、自报数据为主；未控制教师差异。", "area": True},
            {"key": "future", "label": "3. 未来方向", "ph": "例如：跨校纵向追踪；加入协作过程中的权力协商编码。", "area": True},
        ],
    },
    {
        "id": "intro",
        "label": "Step 6 · 引言与摘要",
        "en": "Step 6 · Introduction",
        "mission": "采用【冲突式漏斗】：共识/美好愿景 → 转折指出致命 Gap → 抛出可被反驳的 Thesis；最后写清研究意义。只罗列文献会被打回。",
        "note": "论辩性体检四问：①有没有一个能被别人反驳的 Thesis？②有没有点名前人盲区（具体缺口，不是「研究还不够」）？"
                "③读完有没有「不这么干就不行」的压迫感？④是不是只做了文献总结？四条有一条不过，导师就不给过。",
        "fields": [
            {"key": "significance", "label": "本文的研究意义", "ph": "例如：为高校 AI 写作政策提供过程性证据，把'是否可用'转为'如何协作'。", "area": True},
            {"key": "thesis_sharp", "label": "一句话锐利主张（Thesis：真正的挑战不在于 A，而在于 B）", "ph": "例如：真正的挑战不在于让模型写得更像人，而在于让人始终掌握修改权与署名判断。", "area": True},
        ],
    },
]

GUIDE_STEP_INDEX = {step["id"]: i for i, step in enumerate(GUIDE_STEPS)}
GUIDE_TOTAL = len(GUIDE_STEPS)


def has_api_key(model: str | None = None) -> bool:
    """检查当前模型所需的 API Key 是否存在（不打印、不泄露）。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip() or resolve_api_key()
    if not key:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
    return bool(key) and len(key) >= 20


def api_key_error(model: str | None = None) -> str:
    """给用户看的、不含密钥内容的友好提示（云端 / 本地两种场景）。"""
    return (
        "还没有配置 DeepSeek API Key，暂时无法调用模型。\n\n"
        "解决办法（任选一种）：\n"
        "1. 云端（Streamlit Community Cloud）：Settings → Secrets 里填\n"
        '   DEEPSEEK_API_KEY = "sk-你的密钥"，保存后会自动重启生效。\n'
        "2. 本地：把 `.streamlit/secrets.toml.example` 复制成 `.streamlit/secrets.toml` 并填入密钥；\n"
        "   或在项目根目录建 `.env`，写一行 `DEEPSEEK_API_KEY=sk-你的密钥`。\n"
        "3. 也可以设成系统环境变量 `DEEPSEEK_API_KEY` 后重开终端。\n\n"
        "代码里不再内置任何密钥，读取顺序是：st.secrets → .env → 环境变量。"
    )


def _guide_fields_block(step: dict, fields: dict) -> str:
    """把用户在这一步填的小项渲染成给模型看的清单。"""
    lines = []
    for item in step["fields"]:
        value = str(fields.get(item["key"], "") or "").strip()
        if not value:
            value = "（未填写）"
        lines.append(f"- {item['label']}：{value}")
    return "\n".join(lines)


def build_guide_prompt(
    step_id: str,
    fields: dict,
    mode: str = "coach",
    *,
    library_context: str = "",
    thesis: str = "",
) -> str:
    """组装引导模式的这一轮 Prompt。mode="coach" 只审；mode="auto" 直接成稿。"""
    index = GUIDE_STEP_INDEX.get(step_id)
    if index is None:
        index = 0
    step = GUIDE_STEPS[index]
    total = GUIDE_TOTAL
    fill = _guide_fields_block(step, fields)
    head = (
        f"【当前进度】第 {index + 1} / {total} 步 · {step['label']}\n"
        f"【这一步的任务】{step['mission']}\n"
    )
    if thesis:
        head += f"【用户已确立的核心论点】{thesis}\n"
    head += f"【用户填写的内容】\n{fill}\n"

    if mode == "coach":
        tail = (
            "\n请按导师模式审查上面这一步：先复述要点，再指出致命问题，"
            "给出恰好 3 条具体修改建议，最后一行写 `VERDICT: PASS` 或 `VERDICT: REVISE`。"
            "不要代写正文。"
        )
        if step_id == "intro":
            tail += (
                "\n\n" + argumentative_brief("zh") + "\n\n" + INTRO_COACH_RULES_ZH
                + "\n注意：论辩性体检与下面的双向逻辑闭合审查**都要做**，两份结论不许互相替代。"
            )
        if step_id in {"thesis", "intro", "conclusion"}:
            tail += (
                "\n\n⚠️ 这一轮必须执行【双向逻辑闭合审查 Double-Chain Logic Check】：\n"
                "A 链（正向）：这一步提出的问题/目的 → 方法能否回答 → 结论是否只来自方法能给出的证据；\n"
                "B 链（反向）：把结论/目的反推回背景——它成立需要哪些边界条件？用户写明了吗？"
                "删掉结论后背景是否依然成立（若是，说明论证未闭合）。\n"
                "同时至少点名 2 个该领域最容易被跳过的现实落地制约（运输/部署成本、能耗散热、"
                "数据与标注成本、隐私合规、失败模式的附带损伤、可维护性与规模化、时间人力预算），"
                "逐条质问用户是否有考虑。只讲收益不讲代价、结论强度超过证据 → 直接 VERDICT: REVISE。"
            )
        if step_id == "body":
            tail += (
                "\n\n另外请检查：每个证据是否满足 Topic Sentence + Evidence + Analysis 三段式；"
                "证据能否支撑论点；是否缺少对照/失败案例；证据强度是否被夸大。"
            )
    else:
        tail = (
            "\n请按代笔模式，把上面这些信息直接写成该部分可用的学术成稿，"
            "不要解释过程，不要输出任务复述。"
        )
        if step_id == "intro":
            tail += (
                "\n\n" + argumentative_brief("zh")
                + "\n\n【本步交付要求（引言）】\n"
                "1. 按冲突式漏斗写成 3 个自然段：共识区 → 转折 Gap → 核心 Thesis；"
                "段与段之间必须能用一句「但是」接得上，读起来有推进感。\n"
                "2. 三句要求：第一句不许是「随着……的发展」；中间段必须点名一个具体 Gap；"
                "最后一段必须出现 not merely … but … 式的锐利主张（中文写成「真正的挑战不在于……而在于……」）。\n"
                "3. 至少实打实使用 2 条论辩句式，替换成用户自己的研究对象；不许原样贴英文模板。\n"
                "4. 引言里出现的数字与文献沿用用户给的材料；没有就标〔待补文献〕，不许编造。\n"
                "5. 输出开头仍按规则给一行 `### 引言`，然后是这三段正文。"
            )
        if step_id == "body":
            tail += (
                "\n\n本步必须额外做到：\n"
                "1. 若涉及多篇文献或多组数据，先给一张对比矩阵 Markdown 表格\n"
                "   （文献 | 研究对象 | 使用环境 | 验证阶段 | 关键结论 | 局限性），再写正文；\n"
                "2. 凡是流程对比 / 架构设计 / 数据对比 / 实验装置，必须另起一行插入图表占位符：\n"
                "   [此处建议插入图表：<要表达什么对比>，横坐标为<变量与单位>，纵坐标为<变量与单位>，数据来源/口径为<…>]\n"
                "   一节最多两张图；\n"
                "3. 每处引用紧跟 [n] 编号，并在末尾另起一行写 `### 本步引用`，"
                "逐条列出 `[n] 作者. 标题. 年份. 链接`；没有真实来源就标〔待补文献〕。"
            )
        if step_id in {"rqs", "design"}:
            tail += (
                "\n\n若涉及研究框架、实验流程或装置结构，同样另起一行插入图表占位符：\n"
                "[此处建议插入图表：<图要表达什么>，横坐标为<…>，纵坐标为<…>，数据来源/口径为<…>]"
            )
        if step_id == "body" and library_context.strip():
            tail += (
                "\n\n以下是本机文献库中的可用材料，写证据段时优先引用其中的事实，"
                "不要编造材料之外的数字：\n" + library_context.strip()[:8000]
            )

    base = head + tail
    return base + "\n\n" + FIGURE_CODE_RULE_ZH

def parse_guide_verdict(text: str) -> str:
    """从导师输出里读出 VERDICT；读不到就按 REVISE 处理（保守）。"""
    found = re.findall(r"VERDICT\s*[:：]\s*(PASS|REVISE)", str(text or ""), flags=re.I)
    if not found:
        return "REVISE"
    return found[-1].upper()


_GUIDE_REF_HEADING = re.compile(
    r"\n#{2,3}\s*(?:本步引用|参考文献|References|References? list)\s*\n", flags=re.I
)


def split_guide_citations(text: str) -> tuple[str, list[tuple[int, str]]]:
    """把「正文 + 末尾引用块」拆开。返回 (正文, [(原编号, 条目文本), …])。"""
    body = str(text or "").strip()
    if not body:
        return "", []
    match = None
    for item in _GUIDE_REF_HEADING.finditer("\n" + body):
        match = item
    if not match:
        return body, []
    raw = "\n" + body
    head_part = raw[: match.start()].strip()
    tail_part = raw[match.end():]
    entries: list[tuple[int, str]] = []
    for line in tail_part.splitlines():
        line = line.strip().strip("*").strip()
        if not line:
            continue
        numbered = re.match(r"^\[(\d{1,2})\]\s*(.+)$", line)
        if numbered:
            entries.append((int(numbered.group(1)), numbered.group(2).strip()))
            continue
        bullet = re.match(r"^(?:[-*·]|\d{1,2}[.)、])\s*(.+)$", line)
        if bullet:
            entries.append((len(entries) + 1, bullet.group(1).strip()))
    return head_part, entries


def guide_paper_markdown(
    fields: dict,
    drafts: dict,
    extra_refs: list[str] | None = None,
) -> tuple[str, str]:
    """把六步内容按学术逻辑顺序组装成完整论文 Markdown。返回 (标题, 正文)。

    正文里的 [n] 会被重排成全文连续编号，末尾生成一一对应的参考文献列表，
    这样导出的 Word 里「数字标号」与「文末条目」严格对齐。
    """
    sections = [
        ("摘要与引言", "intro"),
        ("研究问题", "rqs"),
        ("研究方法", "design"),
        ("论证与证据", "body"),
        ("结论与讨论", "conclusion"),
    ]

    bodies: dict[str, str] = {}
    registry: list[str] = []          # 全局条目，按首次出现顺序
    local_maps: dict[str, dict[int, int]] = {}

    def register(entry_text: str) -> int:
        key = re.sub(r"\s+", " ", entry_text).strip().lower()
        for i, existing in enumerate(registry, 1):
            if re.sub(r"\s+", " ", existing).strip().lower() == key:
                return i
        registry.append(entry_text.strip())
        return len(registry)

    for _, step_id in sections:
        raw = str(drafts.get(step_id, "") or "").strip()
        if raw:
            body, entries = split_guide_citations(raw)
        else:
            step = GUIDE_STEPS[GUIDE_STEP_INDEX[step_id]]
            parts = []
            for item in step["fields"]:
                value = str(fields.get(item["key"], "") or "").strip()
                if value:
                    parts.append(f"**{item['label']}** {value}")
            body, entries = "\n\n".join(parts), []
        if not body.strip():
            continue
        mapping: dict[int, int] = {}
        for local_number, entry_text in entries:
            mapping[local_number] = register(entry_text)
        bodies[step_id] = body.strip()
        local_maps[step_id] = mapping

    for extra in extra_refs or []:
        text = str(extra or "").strip()
        if not text:
            continue
        text = re.sub(r"^\[?\d{1,2}[\]].\s*", "", text).strip() or text
        register(text)

    # 把正文里的 [n] 换成全局编号
    for step_id, mapping in local_maps.items():
        if not mapping:
            continue
        bodies[step_id] = re.sub(
            r"\[(\d{1,2})\]",
            lambda m: (
                f"[{mapping[int(m.group(1))]}]"
                if int(m.group(1)) in mapping
                else m.group(0)
            ),
            bodies[step_id],
        )

    title = str(fields.get("thesis", "") or "").strip() or "未命名论文"
    if len(title) > 60:
        title = title[:60] + "…"

    out: list[str] = [f"# {title}", ""]
    thesis_line = str(fields.get("thesis", "") or "").strip()
    if thesis_line:
        out += [f"核心论点：{thesis_line}", ""]
    for heading, step_id in sections:
        body = bodies.get(step_id, "")
        if not body:
            continue
        out += [f"## {heading}", "", body, ""]
    if registry:
        out += ["## 参考文献", ""]
        out += [f"[{i}] {entry}" for i, entry in enumerate(registry, 1)]
        out.append("")
    return title, "\n".join(out).strip() + "\n"


def guide_paper_docx(
    fields: dict,
    drafts: dict,
    extra_refs: list[str] | None = None,
) -> bytes:
    """组装成 .docx：走与其它模式同一套 docx_bytes（APA 条目 + 正文编号重排对齐）。"""
    _, markdown = guide_paper_markdown(fields, drafts, extra_refs)
    return docx_bytes(markdown)


def build_figure_prompt(description: str, kind: str = "auto", lang: str = "zh") -> str:
    """把用户的图表需求拼成给「Figure Architect」的指令。"""
    hint = {
        "auto": "图表类型由你根据数据形态自己判断（能画结构就别硬编数字）。",
        "bar": "必须用 bar（柱状图），适合类别间数值比较。",
        "line": "必须用 line（折线图），适合随序数/时间变化的趋势。",
        "scatter": "必须用 scatter（散点图），适合两变量关系。",
        "box": "必须用 box（箱线图），适合分布对比。",
        "heatmap": "必须用 heatmap（热力图），适合二维矩阵。",
        "pie": "必须用 pie（饼图），适合占比。",
        "flow": "必须用 flow（流程图/架构图），必须给出 nodes 与 edges。",
    }.get(str(kind).lower(), "")
    body = str(description or "").strip()
    return (
        "【图表需求】\n" + body + "\n\n"
        f"【类型要求】{hint}\n"
        "【输出要求】只输出一份 JSON 规格，字段见系统指令；不要解释、不要代码块围栏。\n"
        "【数据纪律】用户给了数字就原样使用、不要篡改；没给数字不要编造，改用结构图或留空数值。"
    )


# ===========================================================================
# 模块五：红蓝对抗 / 沙盒执行画图 / Crossref 文献真伪校验
# ===========================================================================

RED_REVIEW_ZH = """你是顶会（NeurIPS/ACL/CVF）的盲审专家，审稿风格尖锐但具体。
只做一件事：找出下面这段文字的 3 个逻辑漏洞。
输出格式（严格遵守，不要寒暄、不要总结）：
1. 漏洞：〈一句话点明〉｜证据：〈引用原文不超过 15 字〉｜为什么会塌：〈两句话〉
2. 漏洞：…
3. 漏洞：…
"""
RED_REVIEW_EN = """You are a blind reviewer for a top venue. Be sharp, specific, never polite filler.
Find exactly 3 logical flaws in the passage below. Output format (no preamble, no summary):
1. Flaw: <one line> | Evidence: <<=15 words quoted> | Why it collapses: <two sentences>
2. ...
3. ...
"""

BLUE_REVISE_ZH = """下面是你的上一版文字，以及盲审专家指出的漏洞。请按意见重写这一版：
- 逐条拆掉被指出的漏洞（用证据、限定条件或补一句因果链），不要靠形容词辩解；
- 保留原文的论证主线与已引用的 [n] 编号，不要新增未出现过的文献；
- 只输出重写后的正文，不要输出「修改说明」「以下是修改后的版本」等任何标签或前言。
"""
BLUE_REVISE_EN = """Here is your previous draft plus the reviewer's flaws. Rewrite this version:
- dismantle each flaw with evidence, scope limits, or an added causal link — not adjectives;
- keep the argument thread and existing [n] citations; invent no new references;
- output ONLY the rewritten body text, no labels, no preamble.
"""


def build_red_review_prompt(draft: str, lang: str = "zh") -> str:
    head = RED_REVIEW_ZH if lang == "zh" else RED_REVIEW_EN
    tag = "【待审文字】" if lang == "zh" else "[PASSAGE]"
    return f"{head}\n{tag}\n{draft.strip()}\n"


def build_blue_revise_prompt(draft: str, critique: str, lang: str = "zh") -> str:
    head = BLUE_REVISE_ZH if lang == "zh" else BLUE_REVISE_EN
    if lang == "zh":
        return f"{head}\n【上一版文字】\n{draft.strip()}\n\n【盲审意见】\n{critique.strip()}\n"
    return f"{head}\n[PREVIOUS DRAFT]\n{draft.strip()}\n\n[REVIEWER]\n{critique.strip()}\n"


_CLEAN_HINTS = ("无重大漏洞", "无实质性漏洞", "没有发现漏洞", "no major flaw", "no significant flaw")


def looks_clean(critique: str) -> bool:
    low = (critique or "").lower()
    return any(h.lower() in low for h in _CLEAN_HINTS)


def run_red_blue(
    write_fn,
    review_fn,
    task_prompt: str,
    *,
    lang: str = "zh",
    rounds: int = 2,
    on_step=None,
) -> dict:
    """红蓝对抗状态机：写 → 审 → 改 → 再审，最多 rounds 轮。

    write_fn(prompt) -> str   通常是 writer_agent.run
    review_fn(prompt) -> str  通常是 reviewer_agent.run
    on_step(kind, text, round_no)  可选进度回调（UI 用来显示「第 1 轮审稿中…」）
    返回 {"final": 终稿, "history": [{"role","text","round"}], "rounds": 实际轮数}
    """
    events: list[dict] = []

    def _emit(role: str, text: str, round_no: int) -> None:
        events.append({"role": role, "text": text, "round": round_no})
        if on_step is not None:
            try:
                on_step(role, text, round_no)
            except Exception:
                pass

    draft = (write_fn(task_prompt) or "").strip()
    _emit("writer", draft, 0)
    done_rounds = 0
    for i in range(max(1, int(rounds))):
        critique = (review_fn(build_red_review_prompt(draft, lang)) or "").strip()
        _emit("reviewer", critique, i + 1)
        done_rounds = i + 1
        if looks_clean(critique):
            break
        draft = (write_fn(build_blue_revise_prompt(draft, critique, lang)) or draft).strip()
        _emit("writer", draft, i + 1)
    return {"final": draft, "history": events, "rounds": done_rounds}


# ---------------------------------------------------------------------------
# 沙盒执行画图代码（子进程隔离 + 超时 + 自动落盘）
# ---------------------------------------------------------------------------
_PLOT_PREAMBLE = """# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
"""


def execute_python_code(code_str: str, *, timeout: int = 45, out_dir: str = "", out_name: str = "temp_chart.png") -> dict:
    """在子进程里执行绘图代码，返回统一结构（绝不抛异常给 UI）。

    返回 {"ok", "path", "stdout", "stderr", "error", "seconds"}
    """
    import subprocess
    import sys
    import time as _time

    started = _time.time()
    if not (code_str or "").strip():
        return {"ok": False, "path": "", "stdout": "", "stderr": "", "error": "代码为空", "seconds": 0.0}

    work = Path(out_dir) if out_dir else Path(tempfile.gettempdir()) / "paper-dino-plot"
    try:
        work.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "path": "", "stdout": "", "stderr": "", "error": f"无法创建沙盒目录：{exc}", "seconds": 0.0}

    script = work / "_plot_run.py"
    target = work / out_name
    if target.exists():
        try:
            target.unlink()
        except OSError:
            pass

    body = _PLOT_PREAMBLE + "\n" + code_str + "\n"
    # 兜底：模型忘了 savefig 也要有产物
    body += (
        f"\n_tsv = {str(target)!r}\n"
        "try:\n"
        "    import os\n"
        "    if plt.get_fignums() and not os.path.exists(_tsv):\n"
        "        plt.savefig(_tsv, dpi=200, bbox_inches='tight')\n"
        "except Exception as _exc:\n"
        "    print('SAVE_FAIL', _exc)\n"
    )
    try:
        script.write_text(body, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "path": "", "stdout": "", "stderr": "", "error": f"无法写入脚本：{exc}", "seconds": 0.0}

    env = dict(os.environ)
    env["MPLBACKEND"] = "Agg"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(script)],
            cwd=str(work),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, int(timeout)),
            env=env,
        )
        stdout, stderr, rc = proc.stdout or "", proc.stderr or "", proc.returncode
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "path": "",
            "stdout": "",
            "stderr": "",
            "error": f"执行超时（>{timeout}s），代码里可能有死循环或阻塞调用",
            "seconds": round(_time.time() - started, 2),
        }
    except Exception as exc:
        return {"ok": False, "path": "", "stdout": "", "stderr": "", "error": f"{type(exc).__name__}: {exc}", "seconds": round(_time.time() - started, 2)}

    ok = rc == 0 and target.exists() and target.stat().st_size > 1024
    err = ""
    if not ok:
        tail = stderr.strip().splitlines()[-6:]
        err = "\n".join(tail) if tail else (stdout.strip()[-400:] or f"子进程退出码 {rc}，未生成图片")
    return {
        "ok": ok,
        "path": str(target) if ok else "",
        "stdout": (stdout or "")[-1500:],
        "stderr": (stderr or "")[-3000:],
        "error": err,
        "seconds": round(_time.time() - started, 2),
    }


def _looks_like_code(text: str) -> bool:
    """粗判一段文本是不是代码（用于兼容「参数顺序写反」的调用）。"""
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith("Traceback") or t.startswith("SyntaxError") or t.startswith("NameError"):
        return False
    return bool(re.search(r"\b(import|plt\.|def |print\()", t)) and "```" not in t[:20]


def build_plot_fix_prompt(code: str, error: str, lang: str = "zh") -> str:
    """把报错回灌给 Agent，让它自己 debug 并重写代码。

    约定参数顺序是 (code, error, lang)。为防手滑写成 (error, code)，
    这里做一次无害的顺序纠正：只有当第一个参数不像代码、第二个参数像代码时才交换。
    """
    if not _looks_like_code(code) and _looks_like_code(error):
        code, error = error, code
    if lang == "zh":
        return (
            "你上一版画图代码运行失败了。请修好它，只输出一个 ```python 代码块，不要解释。\n"
            "要求：使用 matplotlib；数据用代码内联；最后必须 plt.savefig('temp_chart.png', dpi=200, bbox_inches='tight')；"
            "不要使用 seaborn、不要联网、不要 show()。\n\n"
            f"【上一版代码】\n```python\n{code.strip()}\n```\n\n【报错信息】\n{error.strip()[:1800]}\n"
        )
    return (
        "Your plotting code failed. Fix it and output ONLY one ```python block, no explanation.\n"
        "Constraints: matplotlib only; inline data; end with plt.savefig('temp_chart.png', dpi=200, bbox_inches='tight'); "
        "no seaborn, no network, no plt.show().\n\n"
        f"[PREVIOUS CODE]\n```python\n{code.strip()}\n```\n\n[ERROR]\n{error.strip()[:1800]}\n"
    )


def extract_python_blocks(markdown: str) -> list[str]:
    """抓出正文里所有 ```python 代码块（含 matplotlib 关键字才算）。"""
    blocks = re.findall(r"```(?:python|py)\s*\n(.*?)```", markdown or "", re.S)
    keep = []
    for code in blocks:
        if re.search(r"\bmatplotlib\b|\bplt\.", code):
            keep.append(code.strip("\n"))
    return keep


# ---------------------------------------------------------------------------
# Crossref 文献真伪校验
# ---------------------------------------------------------------------------
_CROSSREF_URL = "https://api.crossref.org/works"
_CROSSREF_UA = "PaperDino/2.5 (academic writing assistant; mailto:noreply@example.com)"


def _norm_title(text: str) -> str:
    low = (text or "").lower()
    low = re.sub(r"\[[0-9]+\]", " ", low)
    low = re.sub(r"\([0-9]{4}\)", " ", low)
    low = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", low)
    return " ".join(low.split())


def _similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    return round(max(ratio, jaccard * 0.95), 3)


def crossref_lookup(title: str, *, timeout: int = 12, threshold: float = 0.72) -> dict:
    """向 Crossref 查一条标题，返回 {ok, score, matched_title, doi, url, error}。"""
    import requests

    payload = {
        "ok": False,
        "score": 0.0,
        "matched_title": "",
        "doi": "",
        "url": "",
        "error": "",
        "checked": title.strip()[:300],
    }
    query = (title or "").strip()
    if len(_norm_title(query)) < 8:
        payload["error"] = "标题太短，跳过校验"
        return payload

    proxies = None
    proxy_env = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy_env:
        proxies = {"http": proxy_env, "https": proxy_env}

    attempts = [proxies]
    if proxies is not None:
        attempts.append(None)  # 代理失败时试直连

    last_err = ""
    for proxy in attempts:
        try:
            resp = requests.get(
                _CROSSREF_URL,
                params={"query.bibliographic": query, "rows": 3},
                headers={"User-Agent": _CROSSREF_UA},
                timeout=timeout,
                proxies=proxy,
            )
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}"
                continue
            items = ((resp.json() or {}).get("message") or {}).get("items") or []
            best_score, best_item = 0.0, None
            for item in items[:3]:
                cand = " ".join(item.get("title") or [])
                score = _similarity(query, cand)
                if score > best_score:
                    best_score, best_item = score, item
            if best_item is None:
                last_err = "Crossref 无结果"
                continue
            payload.update(
                {
                    "ok": best_score >= threshold,
                    "score": best_score,
                    "matched_title": " ".join(best_item.get("title") or [])[:300],
                    "doi": best_item.get("DOI", "") or "",
                    "url": best_item.get("URL", "") or "",
                    "error": "",
                }
            )
            return payload
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
    payload["error"] = last_err or "未知错误"
    return payload


def verify_references(titles, *, threshold: float = 0.72, max_items: int = 12) -> list[dict]:
    """批量校验；每条返回 title + lookup 结果。失败不影响主流程。"""
    out: list[dict] = []
    for raw in list(titles or [])[:max_items]:
        title = (raw or "").strip()
        if not title:
            continue
        res = crossref_lookup(title, threshold=threshold)
        res["title"] = title
        out.append(res)
    return out
