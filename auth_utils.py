"""鉴权层：API Key / 邀请码校验、凭据落盘与恢复、未登录时的通行证页面。"""

import hashlib
import json
import os
import time
from pathlib import Path

import streamlit as st


# ---------------------------------------------------------------------------
# 访问鉴权：自带 API Key / 邀请码，二选一
#   通过后 → auth_status=True；用户 Key 存 session_state.user_api_key（my_agent 会优先读它）
#   邀请码 → 把系统 Key 注入环境变量，走内置额度
#   刷新页面掉线 → 凭据落盘到 auth_store.json（云端只读磁盘时退回 Session State）
# ---------------------------------------------------------------------------
AUTH_FILE = Path(__file__).with_name("auth_store.json")

# 支持的模型提供商：label 给界面看，key 给后端配置用
PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek",
        "placeholder": "sk-...",
        "help": "在 platform.deepseek.com 的 API keys 页面创建，形如 sk-xxxxxxxx。",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI",
        "placeholder": "sk-proj-...",
        "help": "在 platform.openai.com/api-keys 创建，通常以 sk-proj- 开头。",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "anthropic": {
        "label": "Anthropic (Claude)",
        "placeholder": "sk-ant-...",
        "help": "在 console.anthropic.com 创建，形如 sk-ant-xxxx。",
        "base_url": "",  # 留空 = 用 SDK 官方默认端点
        "model": "claude-3-5-sonnet-latest",
    },
    "custom": {
        "label": "自定义 (OpenAI 兼容)",
        "placeholder": "任意 Key（本地 Ollama 可填 ollama）",
        "help": "中转 API / vLLM / Ollama / LM Studio 等，只要能走 OpenAI 兼容接口即可。",
        "base_url": "",
        "model": "",
    },
}

# 兜底：任何情况下都退回 DeepSeek 官方地址与模型，绝不让页面因为缺配置崩溃
FALLBACK_PROVIDER = "deepseek"
FALLBACK_BASE_URL = "https://api.deepseek.com/v1"
FALLBACK_MODEL = "deepseek-chat"

PROVIDER_LABELS = [spec["label"] for spec in PROVIDERS.values()]
LABEL_TO_KEY = {spec["label"]: key for key, spec in PROVIDERS.items()}


def _digest(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _invite_code() -> str:
    """邀请码：st.secrets 优先，其次环境变量 INVITE_CODE。"""
    code = ""
    try:
        code = str(st.secrets.get("INVITE_CODE", "") or "").strip()
    except Exception:  # noqa: BLE001 - 本地没有 secrets.toml 很正常
        code = ""
    return code or os.environ.get("INVITE_CODE", "").strip()


def _secret(name: str) -> str:
    """读一条配置：st.secrets 优先，其次环境变量；本地没有 secrets.toml 不算错。"""
    try:
        value = str(st.secrets.get(name, "") or "").strip()
    except Exception:  # noqa: BLE001
        value = ""
    return value or os.environ.get(name, "").strip()


def _system_key() -> str:
    """系统内置 Key：只给邀请码用户使用。

    优先级：SYSTEM_API_KEY（可配合 SYSTEM_PROVIDER 用任意平台）
            → DEEPSEEK_API_KEY → OPENAI_API_KEY（后者仅当 SYSTEM_PROVIDER=openai）
    """
    explicit = _secret("SYSTEM_API_KEY")
    if explicit:
        return explicit
    key = _secret("DEEPSEEK_API_KEY")
    if key:
        return key
    if _system_provider() == "openai":
        return _secret("OPENAI_API_KEY")
    return ""


def _system_provider() -> str:
    provider = (_secret("SYSTEM_PROVIDER") or "deepseek").lower()
    return provider if provider in PROVIDERS else "deepseek"


def _provider_spec(provider: str | None = None) -> dict:
    """取某个提供商的配置；未知 provider 或结构缺字段时都退回默认值。"""
    name = provider or _system_provider()
    spec = PROVIDERS.get(name)
    if not isinstance(spec, dict):
        spec = PROVIDERS.get(FALLBACK_PROVIDER, {})
    return spec if isinstance(spec, dict) else {}


def _system_base_url() -> str:
    """系统 Base URL：优先读配置，读不到就回退官方地址（永不上抛 KeyError）。"""
    try:
        explicit = _secret("SYSTEM_BASE_URL")
    except Exception:  # noqa: BLE001 - 配置写坏了也不能崩
        explicit = ""
    if explicit:
        return explicit
    try:
        return str(_provider_spec().get("base_url") or FALLBACK_BASE_URL).strip() or FALLBACK_BASE_URL
    except Exception:  # noqa: BLE001
        return FALLBACK_BASE_URL


def _system_model() -> str:
    """系统模型名：同上，取不到就给 DeepSeek 默认模型。"""
    try:
        explicit = _secret("SYSTEM_MODEL")
    except Exception:  # noqa: BLE001
        explicit = ""
    if explicit:
        return explicit
    try:
        return str(_provider_spec().get("model") or FALLBACK_MODEL).strip() or FALLBACK_MODEL
    except Exception:  # noqa: BLE001
        return FALLBACK_MODEL


def _auth_read_store() -> dict:
    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 - 文件不存在 / 坏了都当作未登录
        return {}
    return data if isinstance(data, dict) else {}


def _auth_write_store(payload: dict) -> None:
    try:
        AUTH_FILE.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 - 云端只读磁盘时忽略，靠 Session State 撑着
        pass


def _auth_clear_store() -> None:
    try:
        AUTH_FILE.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _apply_system_key() -> None:
    """把站点内置的 Key 装进当前会话，让模型客户端按正确的平台路由。

    - 仍然写一份 DEEPSEEK_API_KEY 环境变量（兼容只认这个变量的旧链路）；
    - 同时写 Session State，这样 OpenAI / 自定义 Base URL 也能直接用上系统 Key。
    """
    key = _system_key()
    if not key:
        return
    os.environ["DEEPSEEK_API_KEY"] = key
    st.session_state.user_api_key = key
    st.session_state.user_provider = _system_provider()
    st.session_state.user_base_url = _system_base_url()
    st.session_state.user_model = _system_model()


def _auth_restore() -> None:
    """页面刷新后自动恢复登录（Session State 不跨刷新，读本地凭据文件）。"""
    if st.session_state.get("auth_status"):
        return
    data = _auth_read_store()
    mode = str(data.get("mode", ""))
    if mode == "key":
        key = str(data.get("key", "")).strip()
        provider = str(data.get("provider", "deepseek")).strip() or "deepseek"
        if provider not in PROVIDERS:
            provider = "deepseek"
        ok = (
            key.startswith("sk-") and len(key) >= 20
            if provider == "deepseek"
            else len(key) >= 6
        )
        if ok:
            st.session_state.user_api_key = key
            st.session_state.user_provider = provider
            st.session_state.user_base_url = str(data.get("base_url", "") or "").strip()
            st.session_state.user_model = str(data.get("model", "") or "").strip()
            st.session_state.auth_mode = "key"
            st.session_state.auth_status = True
    elif mode == "invite":
        code = _invite_code()
        if code and str(data.get("code_digest", "")) == _digest(code):
            st.session_state.auth_mode = "invite"
            st.session_state.auth_status = True
            _apply_system_key()


def _render_auth_gate() -> None:
    invite_ready = bool(_invite_code())
    st.markdown(
        '<div class="pixel-layer">'
        '<span class="star s1"></span><span class="star s2"></span>'
        '<span class="star s3"></span><span class="star s4"></span>'
        '<span class="star s5"></span><span class="star s6"></span>'
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="auth-card">'
        '<div class="auth-kicker">ACCESS CONTROL</div>'
        '<h2 class="auth-title">🦖 请出示通行证</h2>'
        '<p class="auth-sub">这里的每个回答都会消耗 Token。'
        "请填入你自己的 API Key，或使用管理员发放的邀请码。</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    tab_key, tab_code = st.tabs(["🔑 使用我的 API Key", "🎟️ 输入邀请码"])
    with tab_key:
        label_in = st.selectbox(
            "API 提供商",
            PROVIDER_LABELS,
            key="gate_provider",
            help="选好后，下面输入框的提示与校验会跟着变。",
        )
        provider_in = LABEL_TO_KEY.get(label_in, "deepseek")
        spec = _provider_spec(provider_in)
        st.text_input(
            "API Key（只存在你自己的浏览器会话里）",
            type="password",
            key="gate_key",
            placeholder=spec["placeholder"],
            help=spec["help"],
        )
        if provider_in == "anthropic":
            import importlib.util

            if importlib.util.find_spec("anthropic") is None:
                st.warning(
                    "本机还没装 Anthropic SDK，Claude 的 Key 会调用失败："
                    "先运行 pip install anthropic 再试。"
                )
        if provider_in == "custom":
            st.text_input(
                "Base URL（必填，OpenAI 兼容）",
                key="gate_base",
                placeholder="例如 http://localhost:11434/v1",
                help="中转 API 就填它给你的地址；本地 Ollama 一般是 http://localhost:11434/v1。",
            )
            st.text_input(
                "模型名（可选）",
                key="gate_model",
                placeholder="例如 gpt-4o-mini / qwen2.5:7b / llama3.1",
                help="留空则用默认 gpt-4o-mini；Ollama 请填你本地拉下来的模型名。",
            )
    with tab_code:
        st.text_input(
            "邀请码",
            type="password",
            key="gate_code",
            placeholder="例如 888888",
            help="邀请码用户会使用站点内置的 Key 额度。",
        )
        if invite_ready:
            st.caption("邀请码已配置，可直接激活。")
        else:
            st.caption("本站尚未配置邀请码（管理员需在 Secrets 里设置 INVITE_CODE）。")

    if st.button("🚀 激活系统", key="gate_submit", type="primary", use_container_width=True):
        key_in = str(st.session_state.get("gate_key", "") or "").strip()
        code_in = str(st.session_state.get("gate_code", "") or "").strip()
        if key_in:
            provider = LABEL_TO_KEY.get(label_in, "deepseek")
            base_in = str(st.session_state.get("gate_base", "") or "").strip()
            model_in = str(st.session_state.get("gate_model", "") or "").strip()
            bad = (
                (not key_in.startswith("sk-")) or len(key_in) < 20
                if provider == "deepseek"
                else len(key_in) < 6
            )
            if bad:
                st.error(
                    "API Key 格式不对：DeepSeek 需以 sk- 开头且不少于 20 位；"
                    "其它提供商不少于 6 位即可。"
                )
            elif provider == "custom" and not base_in:
                st.error("选了「自定义」就一定要填 Base URL，否则请求不知道该发到哪。")
            else:
                st.session_state.user_api_key = key_in
                st.session_state.user_provider = provider
                st.session_state.user_base_url = base_in
                st.session_state.user_model = model_in
                st.session_state.auth_mode = "key"
                st.session_state.auth_status = True
                _auth_write_store(
                    {
                        "mode": "key",
                        "key": key_in,
                        "provider": provider,
                        "base_url": base_in,
                        "model": model_in,
                        "ts": time.time(),
                    }
                )
                st.rerun()
        elif code_in:
            code = _invite_code()
            if not code:
                st.error("本站还没有配置邀请码，请联系管理员。")
            elif code_in != code:
                st.error("邀请码不正确，请检查后重试。")
            elif not _system_key():
                st.error(
                    "站点还没有配置内置 Key（Secrets 里的 DEEPSEEK_API_KEY），"
                    "邀请码暂时不可用，请改用你自己的 API Key。"
                )
            else:
                st.session_state.auth_mode = "invite"
                st.session_state.auth_status = True
                _apply_system_key()
                _auth_write_store(
                    {"mode": "invite", "code_digest": _digest(code), "ts": time.time()}
                )
                st.rerun()
        else:
            st.error("请提供有效的 API Key 或邀请码！")

    st.caption("提示：凭据只存在本机，刷新或重开网页都不用重新输入；点抽屉里的『退出登录』可清除。")


def require_auth() -> None:
    """恢复登录态；没有通行证就渲染门禁并终止本轮渲染。"""
    _auth_restore()
    if not st.session_state.get("auth_status"):
        _render_auth_gate()
        st.stop()
