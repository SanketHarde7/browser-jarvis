# Path: backend/config.py
# Use: Loads configuration settings and system environment variables.
"""
config.py — MAX v4.0
Added: Email, SmartHome, Browser, Plugin system configs.
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MAX.CONFIG")


def get_project_root() -> Path:
    current = Path(__file__).resolve()
    if current.parent.name == "backend":
        return current.parent.parent
    return current.parent


PROJECT_ROOT = get_project_root()

env_file = PROJECT_ROOT / "backend" / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=str(env_file), override=True)
    logger.info(f"✅ .env loaded from: {env_file}")
else:
    load_dotenv(override=True)


class Config:

    # ── Server ──
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    SKILLS_ENABLED: bool = True
    COUNTING_PAUSE: float = float(os.getenv("COUNTING_PAUSE", "0.8"))

    # ── Paths ──
    PROJECT_ROOT: Path = PROJECT_ROOT
    BACKEND_DIR: Path = PROJECT_ROOT / "backend"
    DATA_DIR: Path = BACKEND_DIR / "data"
    WORKSPACE_DIR: Path = Path(os.getenv("WORKSPACE_DIR", str(Path.home() / "projects")))
    CODE_SAVE_DIR: Path = Path(os.getenv("CODE_SAVE_DIR", str(Path.home() / "projects" / "jarvis-generated")))
    PLUGINS_DIR: Path = Path(os.getenv("PLUGINS_DIR", str(BACKEND_DIR / "plugins")))

    # ── Search Dirs — used by file_manager ──
    @property
    def SEARCH_DIRS(self) -> list:
        raw = os.getenv("SEARCH_DIRS", "")
        dirs = [Path(p.strip()) for p in raw.split(",") if p.strip()]
        if not dirs:
            dirs = [self.WORKSPACE_DIR, Path.home() / "Desktop", Path.home() / "Documents"]
        return dirs

    # ── Knowledge Base Dirs — used by knowledge_indexer ──
    @property
    def KNOWLEDGE_DIRS(self) -> list:
        raw = os.getenv("KNOWLEDGE_DIRS", "")
        dirs = [Path(p.strip()) for p in raw.split(",") if p.strip()]
        if not dirs:
            dirs = [
                self.PROJECT_ROOT / "knowledge",
                self.PROJECT_ROOT / "knowledge",
                self.BACKEND_DIR / "knowledge",
                self.BACKEND_DIR / "knowledge",
            ]
        return dirs

    # ── Memory ──
    MEMORY_FILE: str = str(DATA_DIR / "memory.json")
    MEMORY_MAX_MESSAGES: int = int(os.getenv("MEMORY_MAX_MESSAGES", "80"))
    MEMORY_SUMMARIZE_THRESHOLD: int = int(os.getenv("MEMORY_SUMMARIZE_THRESHOLD", "80"))

    # ── File Handling ──
    MAX_FILE_SIZE_KB: int = int(os.getenv("MAX_FILE_SIZE_KB", "5000"))
    KNOWLEDGE_MAX_FILE_SIZE_KB: int = int(os.getenv("KNOWLEDGE_MAX_FILE_SIZE_KB", str(MAX_FILE_SIZE_KB)))
    KNOWLEDGE_INDEX_FILE: Path = DATA_DIR / "knowledge_index.json"

    # ── TTS Settings ──
    TTS_VOICE: str = os.getenv("TTS_VOICE", "en-IN-PrabhatNeural")
    TTS_RATE: str = os.getenv("TTS_RATE", "+10%")
    TTS_PITCH: str = os.getenv("TTS_PITCH", "+1Hz")
    TTS_RATE_EN: str = os.getenv("TTS_RATE_EN", TTS_RATE)
    TTS_PITCH_EN: str = os.getenv("TTS_PITCH_EN", TTS_PITCH)

    # ── AI Models ──
    LLM_MODEL: str = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
    WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "whisper-large-v3")
    VISION_MODEL: str = os.getenv("VISION_MODEL", "Qwen/Qwen3.6-27B")

    # ── Groq Rate-Limit Manager ──
    # Free tier ≈ 30 requests/min per key → keep a small safety margin.
    GROQ_RPM_PER_KEY: int = int(os.getenv("GROQ_RPM_PER_KEY", "28"))
    # Max simultaneous Groq calls across the whole app (prevents burst 429s).
    GROQ_MAX_CONCURRENT: int = int(os.getenv("GROQ_MAX_CONCURRENT", "4"))
    # Seconds to cache identical LLM responses (dedupes double STT triggers).
    GROQ_CACHE_TTL: float = float(os.getenv("GROQ_CACHE_TTL", "15"))

    # ── Agent Settings ──
    AGENT_MAX_STEPS: int = int(os.getenv("AGENT_MAX_STEPS", "10"))
    AGENT_CODE_TIMEOUT: int = int(os.getenv("AGENT_CODE_TIMEOUT", "30"))
    AGENT_AUTO_CORRECT: bool = os.getenv("AGENT_AUTO_CORRECT", "true").lower() == "true"
    AGENT_LEARN_PREFERENCES: bool = os.getenv("AGENT_LEARN_PREFERENCES", "true").lower() == "true"

    # ── Email Settings ──
    EMAIL_ADDRESS: str = os.getenv("EMAIL_ADDRESS", "")
    EMAIL_APP_PASSWORD: str = os.getenv("EMAIL_APP_PASSWORD", "")
    IMAP_SERVER: str = os.getenv("IMAP_SERVER", "imap.gmail.com")
    SMTP_SERVER: str = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))

    # ── Smart Home ──
    IR_BLASTER_ENABLED: bool = os.getenv("IR_BLASTER_ENABLED", "false").lower() == "true"
    IR_BLASTER_IP: str = os.getenv("IR_BLASTER_IP", "")
    IR_BLASTER_TYPE: str = os.getenv("IR_BLASTER_TYPE", "broadlink")  # broadlink or tuya
    TUYA_DEVICE_ID: str = os.getenv("TUYA_DEVICE_ID", "")
    TUYA_LOCAL_KEY: str = os.getenv("TUYA_LOCAL_KEY", "")
    TUYA_DEVICE_IP: str = os.getenv("TUYA_DEVICE_IP", "")
    TUYA_DEVICE_VERSION: str = os.getenv("TUYA_DEVICE_VERSION", "3.3")

    # ── Browser ──
    BROWSER_HEADLESS: bool = os.getenv("BROWSER_HEADLESS", "true").lower() == "true"
    BROWSER_TIMEOUT: int = int(os.getenv("BROWSER_TIMEOUT", "15"))

    # ── Code Languages ──
    CODE_LANGUAGES: dict = {
        "python": ".py", "javascript": ".js", "typescript": ".ts",
        "java": ".java", "cpp": ".cpp", "c": ".c", "go": ".go",
        "rust": ".rs", "ruby": ".rb", "php": ".php", "swift": ".swift",
        "html": ".html", "css": ".css", "sql": ".sql", "bash": ".sh",
        "solidity": ".sol", "powershell": ".ps1",
    }

    # ── Project Templates ──
    PROJECT_TEMPLATES: dict = {
        "react": {
            "dirs": ["src/components", "src/hooks", "src/styles", "public"],
            "files": {
                "package.json": '{"name":"{{name}}","version":"0.1.0","dependencies":{"react":"^18","react-dom":"^18","react-scripts":"5.0.1"},"scripts":{"start":"react-scripts start","build":"react-scripts build"}}',
                "src/App.js": 'function App() { return <div><h1>{{name}}</h1></div>; } export default App;',
                "src/index.js": 'import React from "react"; import ReactDOM from "react-dom/client"; import App from "./App"; ReactDOM.createRoot(document.getElementById("root")).render(<App />);',
                "public/index.html": '<!DOCTYPE html><html><head><title>{{name}}</title></head><body><div id="root"></div></body></html>',
            },
        },
        "python": {
            "dirs": ["src", "tests"],
            "files": {
                "src/main.py": "def main():\n    print('Hello from {{name}}!')\n\nif __name__ == '__main__':\n    main()",
                "requirements.txt": "",
                "README.md": "# {{name}}\n\nPython project.",
            },
        },
        "fastapi": {
            "dirs": ["app", "app/api", "app/models", "tests"],
            "files": {
                "app/main.py": "from fastapi import FastAPI\n\napp = FastAPI(title='{{name}}')\n\n@app.get('/')\ndef root():\n    return {'message': 'Welcome to {{name}}'}",
                "requirements.txt": "fastapi>=0.115.0\nuvicorn[standard]>=0.30.0",
                "README.md": "# {{name}}\n\nFastAPI project.\n\n## Run\n```\nuvicorn app.main:app --reload\n```",
            },
        },
        "node": {
            "dirs": ["src"],
            "files": {
                "src/index.js": "console.log('Hello from {{name}}!');",
                "package.json": '{"name":"{{name}}","version":"1.0.0","scripts":{"start":"node src/index.js"}}',
            },
        },
    }

    # ── File Icons ──
    FILE_ICONS: dict = {
        ".py": "🐍", ".js": "📜", ".ts": "🔷", ".jsx": "⚛️", ".tsx": "⚛️",
        ".java": "☕", ".cpp": "🔧", ".c": "🔧", ".go": "🐹", ".rs": "🦀",
        ".html": "🌐", ".css": "🎨", ".json": "📋", ".md": "📝",
        ".txt": "📄", ".sql": "🗄️", ".sh": "🐚", ".env": "🔐",
        "folder": "📁", "default": "📄",
    }

    # ── App Maps ── (for skills.py open_app)
    WINDOWS_APP_MAP: dict = {
        "notepad": "notepad.exe", "calculator": "calc.exe",
        "paint": "mspaint.exe", "cmd": "cmd.exe",
        "terminal": "wt.exe", "powershell": "powershell.exe",
        "explorer": "explorer.exe", "task manager": "taskmgr.exe",
        "chrome": "start chrome", "firefox": "start firefox",
        "edge": "start msedge", "brave": "start brave",
        "vscode": "code", "vs code": "code", "code": "code",
        "word": "start winword", "excel": "start excel",
        "powerpoint": "start powerpnt", "outlook": "start outlook",
        "teams": "start teams", "spotify": "start spotify",
        "whatsapp": "start whatsapp", "discord": "start discord",
        "slack": "start slack", "zoom": "start zoom",
        "pycharm": "pycharm64", "intellij": "idea64",
        "postman": "start postman", "figma": "start figma",
        "obs": "start obs64", "vlc": "start vlc",
    }

    MAC_APP_MAP: dict = {
        "notepad": "TextEdit", "calculator": "Calculator",
        "chrome": "Google Chrome", "firefox": "Firefox",
        "vscode": "Visual Studio Code", "vs code": "Visual Studio Code",
        "code": "Visual Studio Code", "spotify": "Spotify",
        "discord": "Discord", "slack": "Slack", "zoom": "zoom.us",
    }

    WEB_FALLBACK_MAP: dict = {
        "whatsapp": "https://web.whatsapp.com",
        "vscode": "https://vscode.dev", "vs code": "https://vscode.dev",
        "youtube": "https://youtube.com", "github": "https://github.com",
        "instagram": "https://instagram.com", "spotify": "https://open.spotify.com",
        "gmail": "https://mail.google.com", "drive": "https://drive.google.com",
        "claude": "https://claude.ai", "chatgpt": "https://chat.openai.com",
        "notion": "https://notion.so", "figma": "https://figma.com",
        "twitter": "https://twitter.com", "x": "https://x.com",
        "linkedin": "https://linkedin.com", "reddit": "https://reddit.com",
        "netflix": "https://netflix.com", "discord": "https://discord.com/app",
    }

    # ── API Key Rotation ──
    _current_key_idx: int = 0

    @property
    def GROQ_API_KEYS(self) -> list:
        raw = os.getenv("GROQ_API_KEYS", os.getenv("GROQ_API_KEY", ""))
        return [k.strip() for k in raw.split(",") if k.strip()]

    @property
    def GROQ_API_KEY(self) -> str:
        return self.get_active_api_key()

    def get_active_api_key(self) -> str:
        keys = self.GROQ_API_KEYS
        if not keys:
            raise ValueError("No GROQ_API_KEY found. Check your .env file.")
        return keys[self._current_key_idx % len(keys)]

    def rotate_api_key(self) -> bool:
        keys = self.GROQ_API_KEYS
        if len(keys) <= 1:
            return False
        self._current_key_idx = (self._current_key_idx + 1) % len(keys)
        logger.info(f"🔄 API Key rotated → index {self._current_key_idx}")
        return True

    # ── AI Orchestrator ──
    AI_ORCHESTRATOR_ENABLED: bool = True
    AI_RESPONSE_TIMEOUT: int = 120
    AI_PAGE_LOAD_TIMEOUT: int = 15
    AI_INJECT_METHOD: str = "clipboard"
    AI_KEEP_BROWSER_OPEN: bool = True
    AI_DEFAULT_PLATFORM: str = "chatgpt"

    @property
    def OPERA_PROFILE_PATH(self) -> str:
        import platform as plat
        from pathlib import Path
        system = plat.system()
        home = Path.home()
        custom_path = os.getenv("OPERA_PROFILE_PATH")
        if custom_path:
            return custom_path
        if system == "Windows":
            return str(home / "AppData" / "Roaming" / "Opera Software" / "Opera Stable")
        elif system == "Darwin":
            return str(home / "Library" / "Application Support" / "com.operasoftware.Opera")
        else:
            return str(home / ".config" / "opera")

    @property
    def OPERA_BINARY_PATH(self) -> str:
        import platform as plat
        from pathlib import Path
        system = plat.system()
        home = Path.home()
        custom_path = os.getenv("OPERA_BINARY_PATH")
        if custom_path:
            return custom_path
        if system == "Windows":
            return str(home / "AppData" / "Local" / "Programs" / "Opera" / "opera.exe")
        elif system == "Darwin":
            return "/Applications/Opera.app/Contents/MacOS/Opera"
        else:
            return "/usr/bin/opera"



config = Config()

# Ensure directories exist at startup
config.DATA_DIR.mkdir(parents=True, exist_ok=True)
config.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
config.CODE_SAVE_DIR.mkdir(parents=True, exist_ok=True)
config.PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
