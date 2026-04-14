"""Screen Vision - AI Slime's eyes.

Takes periodic screenshots, sends to multimodal LLM for understanding,
then immediately deletes the image. Sensitive content (passwords, API keys,
credit cards, etc.) is automatically detected and ignored.

All processing is local → API → delete. No screenshots are ever kept.
"""
import time
import random
import logging
import tempfile
import re
from pathlib import Path

log = logging.getLogger("rimuru.screen")

# Minimum seconds between screenshots (don't spam)
MIN_INTERVAL = 120   # 2 minutes
MAX_INTERVAL = 600   # 10 minutes

# Sensitive patterns - if the LLM mentions these, discard the learning
SENSITIVE_PATTERNS = [
    r"(?i)(api[_\s-]?key|api[_\s-]?secret)",
    r"(?i)(password|passwd|密碼|pwd)",
    r"(?i)(secret[_\s-]?key|access[_\s-]?token|bearer)",
    r"(?i)(sk-[a-zA-Z0-9]{20,})",           # OpenAI-style keys
    r"(?i)(AIza[a-zA-Z0-9_-]{30,})",         # Google API keys
    r"(?i)(ghp_[a-zA-Z0-9]{30,})",           # GitHub tokens
    r"(?i)(credit\s*card|信用卡|cvv|card\s*number)",
    r"(?i)(private[_\s-]?key|-----BEGIN)",
    r"(?i)(ssh-rsa|ssh-ed25519)",
    r"(?i)(\.env|credentials|auth[_\s-]?token)",
    r"(?i)(bank|銀行|帳號|account\s*number)",
]

# Window titles that should never be screenshotted
BLOCKED_WINDOWS = [
    "password", "密碼", "credential", "keychain",
    "1password", "bitwarden", "lastpass", "keepass",
    "bank", "銀行", "payment", "付款",
]

VISION_PROMPT = """你是 AI Slime，正在透過「千里眼」技能觀察使用者的螢幕。

請分析這張截圖，用中文簡短描述：
1. 使用者正在做什麼（哪個程式、什麼內容）
2. 可以學到什麼（使用者的工作模式、興趣、正在研究的主題）
3. 任何值得注意的狀態（錯誤訊息、卡住的程式、需要注意的事）

⚠️ 重要安全規則：
- 如果螢幕上有任何 API Key、密碼、Token、金鑰、信用卡號、個人帳號等敏感資訊，
  你必須在開頭回覆「[SENSITIVE]」，然後不要描述任何敏感內容。
- 如果畫面是登入頁面或密碼管理器，回覆「[SENSITIVE]」。
- 只描述非敏感的工作內容。

用 2-3 句話簡短回覆。"""


class ScreenWatcher:
    def __init__(self):
        self._last_capture = 0.0
        self._next_interval = MIN_INTERVAL
        self._observations: list[dict] = []  # Recent screen observations
        self._enabled = True

    def should_capture(self) -> bool:
        """Check if it's time for a random screenshot."""
        if not self._enabled:
            return False
        now = time.time()
        if now - self._last_capture >= self._next_interval:
            return True
        return False

    def capture_and_learn(self) -> dict | None:
        """Take screenshot → analyze with vision → delete → return learning.

        Returns None if:
        - Screenshot failed
        - Content is sensitive
        - API call failed
        """
        self._last_capture = time.time()
        # Randomize next interval so it's not predictable
        self._next_interval = random.randint(MIN_INTERVAL, MAX_INTERVAL)

        # Check if current window is blocked
        if self._is_blocked_window():
            log.debug("Blocked window detected, skipping screenshot")
            return None

        screenshot_path = None
        try:
            # 1. Take screenshot
            screenshot_path = self._take_screenshot()
            if not screenshot_path:
                return None

            # 2. Send to vision LLM
            analysis = self._analyze_screenshot(screenshot_path)
            if not analysis:
                return None

            # 3. Check for sensitive content
            if self._is_sensitive(analysis):
                log.info("Sensitive content detected in screenshot, discarding")
                return None

            # 4. Store observation
            obs = {
                "time": time.time(),
                "analysis": analysis,
                "type": "screen_capture",
            }
            self._observations.append(obs)
            # Keep last 50
            if len(self._observations) > 50:
                self._observations = self._observations[-50:]

            log.info(f"Screen observation: {analysis[:80]}...")
            return obs

        except Exception as e:
            log.error(f"Screen capture error: {e}")
            return None
        finally:
            # 5. ALWAYS delete screenshot
            if screenshot_path:
                try:
                    Path(screenshot_path).unlink(missing_ok=True)
                    log.debug("Screenshot deleted")
                except Exception:
                    pass

    def _take_screenshot(self) -> str | None:
        """Take a screenshot and save to temp file."""
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()

            # Save to temp file (will be deleted after analysis)
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                              prefix="rimuru_eye_")
            img.save(tmp.name, "PNG")
            tmp.close()

            # Resize if too large (save API bandwidth)
            file_size = Path(tmp.name).stat().st_size
            if file_size > 2_000_000:  # > 2MB
                img = img.resize((img.width // 2, img.height // 2))
                img.save(tmp.name, "PNG")

            return tmp.name
        except Exception as e:
            log.error(f"Screenshot failed: {e}")
            return None

    def _analyze_screenshot(self, image_path: str) -> str | None:
        """Send screenshot to multimodal LLM for analysis."""
        try:
            from sentinel import config

            # Try all vision-capable providers in order, fallback on failure
            for provider in config.LLM_PROVIDERS:
                if not provider.get("enabled") or not provider.get("api_key"):
                    continue

                result = None
                if provider["type"] == "gemini":
                    result = self._analyze_with_gemini(provider, image_path)
                elif provider["type"] == "anthropic":
                    result = self._analyze_with_anthropic(provider, image_path)
                else:
                    continue

                if result:
                    return result
                log.info(f"{provider.get('name', '?')} vision 失敗，嘗試下一個...")

            log.warning("No vision-capable LLM available")
            return None

        except Exception as e:
            log.error(f"Vision analysis failed: {e}")
            return None

    def _analyze_with_gemini(self, provider: dict, image_path: str) -> str | None:
        """Use Gemini's vision capability."""
        import google.genai as genai
        from google.genai import types

        client = genai.Client(api_key=provider["api_key"])

        with open(image_path, "rb") as f:
            image_data = f.read()

        image_part = types.Part.from_bytes(data=image_data, mime_type="image/png")

        for model in provider["models"]:
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[image_part, VISION_PROMPT],
                    config=types.GenerateContentConfig(
                        temperature=0.3,
                        max_output_tokens=300,
                    ),
                )
                return response.text.strip()
            except Exception as e:
                error_str = str(e)
                if any(k in error_str.lower() for k in ["429", "503", "rate", "quota", "exhausted"]):
                    log.warning(f"Gemini/{model} vision rate limited, trying next...")
                    continue
                log.error(f"Gemini/{model} vision error: {e}")
                continue
        return None

    def _analyze_with_anthropic(self, provider: dict, image_path: str) -> str | None:
        """Use Claude's vision capability."""
        import anthropic
        import base64

        client = anthropic.Anthropic(api_key=provider["api_key"])

        with open(image_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        for model in provider["models"]:
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=300,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": image_data,
                                },
                            },
                            {
                                "type": "text",
                                "text": VISION_PROMPT,
                            },
                        ],
                    }],
                )
                return response.content[0].text.strip()
            except Exception as e:
                error_str = str(e)
                if any(k in error_str.lower() for k in ["429", "503", "rate", "quota"]):
                    continue
                log.error(f"Anthropic/{model} vision error: {e}")
                continue
        return None

    def _is_sensitive(self, text: str) -> bool:
        """Check if the analysis mentions sensitive content."""
        if "[SENSITIVE]" in text.upper():
            return True
        for pattern in SENSITIVE_PATTERNS:
            if re.search(pattern, text):
                return True
        return False

    def _is_blocked_window(self) -> bool:
        """Check if current window title suggests sensitive content."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.lower()

            for blocked in BLOCKED_WINDOWS:
                if blocked in title:
                    return True
        except Exception:
            pass
        return False

    def get_recent_observations(self, n: int = 10) -> list[dict]:
        """Get recent screen observations for distillation."""
        return self._observations[-n:]

    def get_observation_summary(self) -> str:
        """Get a text summary of recent screen observations."""
        recent = self.get_recent_observations(5)
        if not recent:
            return ""

        lines = ["=== 螢幕觀察（千里眼）==="]
        for obs in recent:
            lines.append(f"  {obs['analysis']}")
        return "\n".join(lines)
