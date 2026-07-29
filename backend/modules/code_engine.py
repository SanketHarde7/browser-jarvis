# Path: backend/modules/code_engine.py
# Use: Safely executes Python and shell code snippets.
"""
code_engine.py — MAX v4.0
Handles: write_code, run_code, code_review, fix_code, project_scaffold
Tone: Friendly, no 'sir' overload.
"""
import asyncio
import os
import re
import sys
import ast
import json
import time
import logging
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple, List

from groq import AsyncGroq
from api_utils import execute_with_retry

logger = logging.getLogger("MAX.CODE_ENGINE")


class CodeEngine:
    """Agent-level code engine for MAX."""

    def __init__(self, config):
        self.config = config
        self.workspace = config.WORKSPACE_DIR
        self.code_dir = config.CODE_SAVE_DIR
        self.languages = config.CODE_LANGUAGES
        self.timeout = config.AGENT_CODE_TIMEOUT
        self.max_file_size_kb = config.MAX_FILE_SIZE_KB

    # ═══════════════════════════════════════════
    # INTERNAL: Code Generation via LLM
    # ═══════════════════════════════════════════

    async def _generate_code_with_llm(self, description: str, language: str, extra_context: str = "") -> str:
        system_prompt = f"""You are MAX Code Generator — an expert programmer.
Rules:
- Write ONLY code, no explanations, no markdown code blocks
- Code must be complete, runnable, and well-commented
- Use best practices and proper error handling
- Keep it concise but fully functional
- Do NOT include ``` or any markdown formatting
- Do NOT write a main() function unless explicitly asked
- Include brief docstring/comments explaining what the code does
"""
        user_prompt = f"Write {language} code for: {description}"
        if extra_context:
            user_prompt += f"\n\nAdditional context: {extra_context}"

        try:
            async def call():
                client = AsyncGroq(api_key=self.config.get_active_api_key())
                return await client.chat.completions.create(
                    model=self.config.LLM_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.3,
                    max_tokens=2000,
                )
            response = await execute_with_retry(call)
            code = response.choices[0].message.content.strip()
            code = self._strip_markdown_code_blocks(code)
            return code
        except Exception as e:
            logger.error(f"Code generation LLM call failed: {e}")
            return f"# Error generating code: {e}"

    def _strip_markdown_code_blocks(self, text: str) -> str:
        text = re.sub(r'^```\w*\n', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n```\s*$', '', text, flags=re.MULTILINE)
        text = text.replace('```', '')
        return text.strip()

    def _detect_language(self, hint: str) -> str:
        hint_lower = hint.lower().strip()
        lang_map = {
            "python": "python", "py": "python",
            "javascript": "javascript", "js": "javascript",
            "typescript": "typescript", "ts": "typescript",
            "java": "java",
            "c++": "cpp", "cpp": "cpp",
            "c": "c",
            "go": "go", "golang": "go",
            "rust": "rust", "rs": "rust",
            "ruby": "ruby", "rb": "ruby",
            "php": "php",
            "swift": "swift",
            "kotlin": "kotlin", "kt": "kotlin",
            "html": "html",
            "css": "css",
            "sql": "sql",
            "bash": "bash", "shell": "bash", "sh": "bash",
            "powershell": "powershell", "ps1": "powershell",
            "react": "javascript",
            "nodejs": "javascript", "node": "javascript",
        }
        for key, lang in lang_map.items():
            if key in hint_lower:
                return lang
        ext_match = re.search(r'\.(\w+)$', hint_lower)
        if ext_match:
            ext = ext_match.group(1)
            for lang, lang_ext in self.languages.items():
                if lang_ext == f".{ext}":
                    return lang
        return "python"

    def _generate_filename(self, description: str, language: str) -> str:
        clean = re.sub(r'[^\w\s-]', '', description.lower().strip())
        clean = re.sub(r'\s+', '_', clean)
        if len(clean) > 50:
            clean = clean[:50]
        ext = self.languages.get(language, ".py")
        return f"{clean}{ext}"

    def _get_unique_filepath(self, filename: str) -> Path:
        filepath = self.code_dir / filename
        if not filepath.exists():
            return filepath
        stem = filepath.stem
        ext = filepath.suffix
        counter = 1
        while True:
            new_name = f"{stem}_{counter}{ext}"
            new_path = self.code_dir / new_name
            if not new_path.exists():
                return new_path
            counter += 1

    def _save_code_file(self, code: str, filepath: Path) -> str:
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(code, encoding='utf-8')
            return str(filepath)
        except Exception as e:
            logger.error(f"Failed to save code file: {e}")
            raise

    # ═══════════════════════════════════════════
    # SKILL: write_code
    # ═══════════════════════════════════════════

    async def write_code(self, *args) -> str:
        try:
            if len(args) >= 2:
                language_hint = args[0]
                description = args[1]
            elif len(args) == 1:
                language_hint = args[0]
                description = args[0]
            else:
                return "Please provide what code to write, boss."

            language = self._detect_language(language_hint)
            filename = self._generate_filename(description, language)
            filepath = self._get_unique_filepath(filename)
            code = await self._generate_code_with_llm(description, language)

            if code.startswith("# Error"):
                return "Code generation failed boss. Please try again with more details."

            saved_path = self._save_code_file(code, filepath)
            rel_path = Path(saved_path).relative_to(self.workspace)

            logger.info(f"Code saved: {saved_path} ({len(code)} chars)")
            return f"Code likh diya boss, {rel_path} mein save ho gayi."

        except Exception as e:
            logger.error(f"write_code error: {e}")
            return f"Code writing mein error aaya boss: {str(e)}"

    # ═══════════════════════════════════════════
    # SKILL: run_code
    # ═══════════════════════════════════════════

    async def run_code(self, *args) -> str:
        if not args:
            return "Please provide the file path to run, boss."

        filepath_str = args[0]
        try:
            filepath = Path(filepath_str).expanduser().resolve()
            if not filepath.is_absolute():
                filepath = self.code_dir / filepath_str
                if not filepath.exists():
                    filepath = self.workspace / filepath_str

            if not filepath.exists():
                return f"File nahi mila boss: {filepath_str}"

            size_kb = filepath.stat().st_size / 1024
            if size_kb > self.max_file_size_kb:
                return f"File bahut badi hai boss ({size_kb:.0f}KB). Max {self.max_file_size_kb}KB allowed."

            language = self._detect_language(filepath.suffix)
            code = filepath.read_text(encoding='utf-8')
            result = await self._execute_code(code, language, filepath)
            return result

        except Exception as e:
            logger.error(f"run_code error: {e}")
            return f"Code run karne mein error aaya boss: {str(e)}"

    async def _execute_code(self, code: str, language: str, filepath: Path) -> str:
        try:
            if language == "python":
                return await self._run_python_code(code, filepath)
            elif language in ("javascript", "typescript"):
                return await self._run_nodejs_code(code, filepath)
            elif language in ("bash", "shell", "powershell"):
                return await self._run_shell_code(code, filepath)
            elif language == "go":
                return await self._run_go_code(code, filepath)
            elif language == "rust":
                return await self._run_rust_code(code, filepath)
            elif language == "java":
                return await self._run_java_code(code, filepath)
            elif language == "c" or language == "cpp":
                return await self._run_c_cpp_code(code, filepath)
            else:
                return f"{language} ke liye runner abhi available nahi hai boss. Sirf execute kar sakta hoon: python, javascript, bash, go, rust, java, c, cpp."
        except Exception as e:
            return f"Execution error: {str(e)[:200]}"

    async def _run_python_code(self, code: str, filepath: Path) -> str:
        try:
            ast.parse(code)
        except SyntaxError as e:
            return f"Syntax Error boss: Line {e.lineno}: {e.msg}"

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(filepath),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            output = stdout.decode('utf-8', errors='replace').strip()
            error = stderr.decode('utf-8', errors='replace').strip()

            if proc.returncode == 0:
                if output:
                    return f"Output boss:\n{output[:500]}"
                return "Code successfully run ho gaya boss. Koi output nahi tha."
            else:
                err_msg = error[:300] if error else "Unknown error"
                return f"Error boss:\n{err_msg}"

        except asyncio.TimeoutError:
            proc.kill()
            return f"Code {self.timeout}s se zyada time le raha tha boss. Timeout kar diya."

    async def _run_nodejs_code(self, code: str, filepath: Path) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "node", str(filepath),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            output = stdout.decode('utf-8', errors='replace').strip()
            error = stderr.decode('utf-8', errors='replace').strip()

            if proc.returncode == 0:
                return f"Output boss:\n{output[:500]}" if output else "Code successfully run ho gaya boss."
            else:
                return f"Error boss:\n{error[:300]}"

        except FileNotFoundError:
            return "Node.js installed nahi hai boss. Please install Node.js first."
        except asyncio.TimeoutError:
            proc.kill()
            return f"Code timeout ho gaya boss ({self.timeout}s)."

    async def _run_shell_code(self, code: str, filepath: Path) -> str:
        try:
            proc = await asyncio.create_subprocess_shell(
                code if code else f"bash {filepath}",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.workspace,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            output = stdout.decode('utf-8', errors='replace').strip()
            error = stderr.decode('utf-8', errors='replace').strip()

            if proc.returncode == 0:
                return f"Output boss:\n{output[:500]}" if output else "Command run ho gayi boss."
            else:
                return f"Error boss:\n{error[:300]}"

        except asyncio.TimeoutError:
            proc.kill()
            return f"Command timeout ho gayi boss ({self.timeout}s)."

    async def _run_go_code(self, code: str, filepath: Path) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "go", "run", str(filepath),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=filepath.parent,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            output = stdout.decode('utf-8', errors='replace').strip()
            error = stderr.decode('utf-8', errors='replace').strip()

            if proc.returncode == 0:
                return f"Output boss:\n{output[:500]}" if output else "Go code run ho gaya boss."
            else:
                return f"Go error boss:\n{error[:300]}"

        except FileNotFoundError:
            return "Go compiler installed nahi hai boss. 'go' command nahi mila."
        except asyncio.TimeoutError:
            proc.kill()
            return f"Go code timeout ho gaya boss ({self.timeout}s)."

    async def _run_rust_code(self, code: str, filepath: Path) -> str:
        try:
            exe_path = filepath.with_suffix('')
            compile_proc = await asyncio.create_subprocess_exec(
                "rustc", str(filepath), "-o", str(exe_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _, compile_err = await asyncio.wait_for(compile_proc.communicate(), timeout=30)

            if compile_proc.returncode != 0:
                return f"Rust compile error boss:\n{compile_err.decode()[:300]}"

            run_proc = await asyncio.create_subprocess_exec(
                str(exe_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                run_proc.communicate(), timeout=self.timeout
            )

            if exe_path.exists():
                exe_path.unlink()

            output = stdout.decode('utf-8', errors='replace').strip()
            if run_proc.returncode == 0:
                return f"Output boss:\n{output[:500]}" if output else "Rust code run ho gaya boss."
            else:
                return f"Runtime error boss:\n{stderr.decode()[:300]}"

        except FileNotFoundError:
            return "Rust compiler (rustc) installed nahi hai boss."
        except asyncio.TimeoutError:
            return f"Rust code timeout ho gaya boss ({self.timeout}s)."

    async def _run_java_code(self, code: str, filepath: Path) -> str:
        try:
            compile_proc = await asyncio.create_subprocess_exec(
                "javac", str(filepath),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=filepath.parent,
            )
            _, compile_err = await asyncio.wait_for(compile_proc.communicate(), timeout=30)

            if compile_proc.returncode != 0:
                return f"Java compile error boss:\n{compile_err.decode()[:300]}"

            class_name = filepath.stem
            run_proc = await asyncio.create_subprocess_exec(
                "java", class_name,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=filepath.parent,
            )
            stdout, stderr = await asyncio.wait_for(
                run_proc.communicate(), timeout=self.timeout
            )

            output = stdout.decode('utf-8', errors='replace').strip()
            if run_proc.returncode == 0:
                return f"Output boss:\n{output[:500]}" if output else "Java code run ho gaya boss."
            else:
                return f"Runtime error boss:\n{stderr.decode()[:300]}"

        except FileNotFoundError:
            return "Java (javac/java) installed nahi hai boss."
        except asyncio.TimeoutError:
            return f"Java code timeout ho gaya boss ({self.timeout}s)."

    async def _run_c_cpp_code(self, code: str, filepath: Path) -> str:
        try:
            compiler = "g++" if filepath.suffix in ('.cpp', '.cxx', '.cc') else "gcc"
            exe_path = filepath.with_suffix('.exe' if sys.platform == 'win32' else '')

            compile_proc = await asyncio.create_subprocess_exec(
                compiler, str(filepath), "-o", str(exe_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _, compile_err = await asyncio.wait_for(compile_proc.communicate(), timeout=30)

            if compile_proc.returncode != 0:
                return f"{compiler.upper()} compile error boss:\n{compile_err.decode()[:300]}"

            run_proc = await asyncio.create_subprocess_exec(
                str(exe_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                run_proc.communicate(), timeout=self.timeout
            )

            if exe_path.exists():
                exe_path.unlink()

            output = stdout.decode('utf-8', errors='replace').strip()
            if run_proc.returncode == 0:
                return f"Output boss:\n{output[:500]}" if output else "Code run ho gaya boss."
            else:
                return f"Runtime error boss:\n{stderr.decode()[:300]}"

        except FileNotFoundError:
            return f"{compiler.upper()} compiler installed nahi hai boss."
        except asyncio.TimeoutError:
            return f"C/C++ code timeout ho gaya boss ({self.timeout}s)."

    # ═══════════════════════════════════════════
    # SKILL: code_review
    # ═══════════════════════════════════════════

    async def code_review(self, *args) -> str:
        if not args:
            return "Please provide the file to review, boss."

        filepath_str = args[0]
        try:
            filepath = Path(filepath_str).expanduser().resolve()
            if not filepath.is_absolute():
                filepath = self.code_dir / filepath_str
                if not filepath.exists():
                    filepath = self.workspace / filepath_str

            if not filepath.exists():
                return f"File nahi mila boss: {filepath_str}"

            code = filepath.read_text(encoding='utf-8')
            if len(code) > self.max_file_size_kb * 1024:
                return f"File bahut badi hai boss. Review ke liye choti file do."

            language = self._detect_language(filepath.suffix)
            issues = self._static_analysis(code, language)
            review = await self._llm_code_review(code, language, filepath.name)

            response = f"Code review for {filepath.name}:\n\n{review}"
            if issues:
                response += f"\n\nStatic analysis issues:\n" + "\n".join(issues[:5])

            return response

        except Exception as e:
            logger.error(f"code_review error: {e}")
            return f"Review mein error aaya boss: {str(e)}"

    def _static_analysis(self, code: str, language: str) -> List[str]:
        issues = []
        if language == "python":
            try:
                ast.parse(code)
            except SyntaxError as e:
                issues.append(f"Syntax error at line {e.lineno}: {e.msg}")

            lines = code.split('\n')
            for i, line in enumerate(lines, 1):
                if 'eval(' in line and not line.strip().startswith('#'):
                    issues.append(f"Line {i}: eval() use — security risk")
                if 'exec(' in line and not line.strip().startswith('#'):
                    issues.append(f"Line {i}: exec() use — security risk")
                if 'password' in line.lower() and '=' in line:
                    issues.append(f"Line {i}: Hardcoded password detected")
                if 'TODO' in line:
                    issues.append(f"Line {i}: TODO found — incomplete implementation")
                if 'FIXME' in line:
                    issues.append(f"Line {i}: FIXME found — known issue")

            if 'if __name__' not in code and len(lines) > 20:
                issues.append("Missing 'if __name__ == main' guard — code runs on import")

            if 'try:' not in code and ('open(' in code or 'requests.' in code):
                issues.append("No error handling for I/O operations")

        return issues

    async def _llm_code_review(self, code: str, language: str, filename: str) -> str:
        prompt = f"""Review this {language} code file '{filename}':

```
{code[:3000]}
```

Provide a brief review covering:
1. Code quality (1-2 lines)
2. Any bugs or issues
3. Suggestions for improvement
Keep it concise — max 5 bullet points."""

        try:
            async def call():
                client = AsyncGroq(api_key=self.config.get_active_api_key())
                return await client.chat.completions.create(
                    model=self.config.LLM_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5,
                    max_tokens=500,
                )
            response = await execute_with_retry(call)
            return response.choices[0].message.content.strip()
        except Exception:
            return "LLM review unavailable boss. Static analysis se kaam chala lo."

    # ═══════════════════════════════════════════
    # SKILL: fix_code
    # ═══════════════════════════════════════════

    async def fix_code(self, *args) -> str:
        if len(args) < 2:
            return "Usage: fix_code:filepath:issue_description boss."

        filepath_str = args[0]
        issue = args[1] if len(args) > 1 else "fix issues"

        try:
            filepath = Path(filepath_str).expanduser().resolve()
            if not filepath.is_absolute():
                filepath = self.code_dir / filepath_str
                if not filepath.exists():
                    filepath = self.workspace / filepath_str

            if not filepath.exists():
                return f"File nahi mila boss: {filepath_str}"

            code = filepath.read_text(encoding='utf-8')
            language = self._detect_language(filepath.suffix)
            fixed_code = await self._generate_fix(code, language, issue)

            if fixed_code.startswith("# Error") or fixed_code == code:
                return "Fix generate nahi ho paya boss. Koi improvement nahi mila."

            backup_path = filepath.with_suffix(filepath.suffix + '.backup')
            filepath.rename(backup_path)
            self._save_code_file(fixed_code, filepath)

            return f"Code fix kar diya boss. Backup: {backup_path.name}"

        except Exception as e:
            logger.error(f"fix_code error: {e}")
            return f"Code fix karne mein error boss: {str(e)}"

    async def _generate_fix(self, code: str, language: str, issue: str) -> str:
        prompt = f"""Fix this {language} code for the issue: {issue}

Original code:
```
{code[:3000]}
```

Return ONLY the fixed code, no explanations, no markdown blocks."""

        try:
            async def call():
                client = AsyncGroq(api_key=self.config.get_active_api_key())
                return await client.chat.completions.create(
                    model=self.config.LLM_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=2000,
                )
            response = await execute_with_retry(call)
            return self._strip_markdown_code_blocks(response.choices[0].message.content.strip())
        except Exception:
            return code

    # ═══════════════════════════════════════════
    # SKILL: project_scaffold
    # ═══════════════════════════════════════════

    async def project_scaffold(self, *args) -> str:
        if len(args) < 1:
            return "Project type aur name dono chahiye boss. Example: 'react todo-app'"

        project_type = args[0].lower().strip()
        project_name = args[1] if len(args) > 1 else f"my-{project_type}-project"
        project_name = re.sub(r'[^\w-]', '', project_name).lower()

        if project_type not in self.config.PROJECT_TEMPLATES:
            available = ", ".join(self.config.PROJECT_TEMPLATES.keys())
            return f"Template nahi mila boss. Available: {available}"

        try:
            template = self.config.PROJECT_TEMPLATES[project_type]
            project_dir = self.code_dir / project_name

            if project_dir.exists():
                return f"{project_name} already exists boss. Doosra naam do."

            for dir_path in template.get("dirs", []):
                (project_dir / dir_path).mkdir(parents=True, exist_ok=True)

            for file_path, content in template.get("files", {}).items():
                full_path = project_dir / file_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                filled_content = content.replace("{{name}}", project_name)
                full_path.write_text(filled_content, encoding='utf-8')

            logger.info(f"Project scaffolded: {project_dir}")
            file_count = sum(1 for _ in project_dir.rglob('*') if _.is_file())
            return f"{project_type.upper()} project '{project_name}' ready hai boss. {file_count} files banayi."

        except Exception as e:
            logger.error(f"project_scaffold error: {e}")
            return f"Project scaffold karne mein error boss: {str(e)}"


# Singleton
import asyncio

_code_engine: Optional[CodeEngine] = None


def get_code_engine(config) -> CodeEngine:
    global _code_engine
    if _code_engine is None:
        _code_engine = CodeEngine(config)
    return _code_engine
