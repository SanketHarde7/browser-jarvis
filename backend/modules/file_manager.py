# Path: backend/modules/file_manager.py
# Use: Smart PC File Search, Fuzzy Path Resolution, and File Operations Engine for MAX Assistant.

import os
import sys
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("MAX.FILE_MANAGER")

class SmartFileManager:
    """
    Smart PC File Management Engine for MAX Assistant.
    Indexes user folders (OneDrive Desktop, Downloads, Documents, Pictures),
    performs fuzzy file searching, file copying/moving, and step verification.
    """
    _instance = None

    def __init__(self):
        self.user_profile = Path(os.environ.get("USERPROFILE", r"C:\Users\sanke"))
        
        # Exact User Path Definitions
        self.folder_map = {
            "desktop": self.user_profile / "OneDrive" / "Desktop",
            "documents": self.user_profile / "OneDrive" / "Documents",
            "pictures": self.user_profile / "OneDrive" / "Pictures",
            "downloads": self.user_profile / "Downloads",
            "workspace": self.user_profile / "OneDrive" / "Desktop" / "Jarvis"
        }
        
        # Verify and create fallback paths if needed
        for name, path in self.folder_map.items():
            if not path.exists():
                logger.warning(f"Folder path '{path}' does not exist. Using fallback.")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = SmartFileManager()
        return cls._instance

    def resolve_folder_alias(self, alias: str) -> Path:
        """Map alias like 'downloads', 'desktop', 'documents' to exact Path with Path Traversal Sandboxing."""
        alias_clean = str(alias).lower().strip()
        if alias_clean in self.folder_map:
            return self.folder_map[alias_clean]
        
        # Direct path check with Sandbox Guard (Must be inside user profile)
        try:
            p = Path(alias).resolve()
            user_profile_resolved = self.user_profile.resolve()
            if p.exists() and p.is_dir() and (user_profile_resolved in p.parents or p == user_profile_resolved):
                return p
        except Exception:
            pass
            
        return self.folder_map["downloads"]

    def find_file(self, query: str, folder_hint: str = None) -> Tuple[bool, str, Optional[Path]]:
        """
        Smart Natural Language & Fuzzy File Search.
        Resolves queries like:
        - "Downloads me photo jo aaj download kiya"
        - "Latest PDF file on Desktop"
        - "Recent document"
        """
        if not query or not query.strip():
            return self.get_latest_file(folder_alias=folder_hint or "downloads")

        query_clean = query.lower().strip()

        # Check for natural language cues (aaj, today, photo, image, latest, recent)
        nl_cues = ["photo", "image", "picture", "screenshot", "pdf", "doc", "latest", "recent", "aaj", "today", "abhi"]
        if any(cue in query_clean for cue in nl_cues):
            res_ok, res_msg, res_path = self.smart_resolve_file(query_clean, folder_hint)
            if res_ok and res_path:
                return res_ok, res_msg, res_path

        search_dirs = []

        if folder_hint:
            target_dir = self.resolve_folder_alias(folder_hint)
            if target_dir.exists():
                search_dirs.append(target_dir)

        # Fallback to searching all key user directories
        if not search_dirs:
            search_dirs = [
                self.folder_map["downloads"],
                self.folder_map["desktop"],
                self.folder_map["documents"],
                self.folder_map["workspace"]
            ]

        matches: List[Tuple[float, Path]] = []

        for s_dir in search_dirs:
            if not s_dir.exists():
                continue
            try:
                for root, _, files in os.walk(s_dir):
                    for f in files:
                        f_lower = f.lower()
                        # Direct or fuzzy substring match
                        if query_clean in f_lower:
                            full_p = Path(root) / f
                            score = len(query_clean) / max(len(f_lower), 1)
                            matches.append((score, full_p))
            except Exception as e:
                logger.error(f"Error scanning dir '{s_dir}': {e}")

        if matches:
            # Sort by highest match score and newest modification time
            matches.sort(key=lambda x: (x[0], x[1].stat().st_mtime if x[1].exists() else 0), reverse=True)
            best_match = matches[0][1]
            logger.info(f"✅ Found file for '{query}': {best_match}")
            return True, f"File found: {best_match.name}", best_match

        return False, f"No file matching '{query}' found in indexed directories.", None

    def get_latest_file(self, folder_alias: str = "downloads") -> Tuple[bool, str, Optional[Path]]:
        """Retrieve the most recently created/modified file in a folder."""
        target_dir = self.resolve_folder_alias(folder_alias)
        if not target_dir.exists():
            return False, f"Folder '{target_dir}' does not exist.", None

        try:
            files = [target_dir / f for f in os.listdir(target_dir) if (target_dir / f).is_file()]
            if not files:
                return False, f"No files found in '{target_dir.name}'.", None

            latest_file = max(files, key=lambda p: p.stat().st_mtime)
            return True, f"Latest file: {latest_file.name}", latest_file
        except Exception as e:
            logger.error(f"Error getting latest file: {e}")
            return False, f"Failed to inspect folder: {e}", None

    def smart_resolve_file(self, query: str, folder_hint: str = None) -> Tuple[bool, str, Optional[Path]]:
        """
        Smart Natural Language File Resolver.
        Resolves queries like:
        - "Downloads me photo jo aaj download kiya"
        - "Latest PDF file on Desktop"
        - "Recent document"
        """
        if not query or not query.strip():
            return self.get_latest_file(folder_alias=folder_hint or "downloads")

        query_clean = query.lower().strip()

        # 1. Detect Category / Extensions
        target_exts = set()
        image_keywords = ["photo", "image", "picture", "screenshot", "pic", "img", "photograph"]
        doc_keywords = ["pdf", "doc", "document", "text", "file", "report", "csv", "excel", "sheet"]
        video_keywords = ["video", "clip", "movie", "mp4"]

        if any(k in query_clean for k in image_keywords):
            target_exts.update([".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"])
        if any(k in query_clean for k in doc_keywords):
            target_exts.update([".pdf", ".docx", ".doc", ".txt", ".xlsx", ".csv"])
        if any(k in query_clean for k in video_keywords):
            target_exts.update([".mp4", ".mkv", ".mov", ".avi"])

        # 2. Detect Time Filter ("aaj", "today", "latest", "recent", "abhi")
        is_today = any(k in query_clean for k in ["aaj", "today", "aaj ki"])
        is_latest = any(k in query_clean for k in ["latest", "recent", "last", "abhi", "new", "nayi", "naya"])

        # Determine target folder
        target_folder = folder_hint
        if not target_folder:
            for alias in ["downloads", "desktop", "documents", "pictures"]:
                if alias in query_clean:
                    target_folder = alias
                    break

        search_dir = self.resolve_folder_alias(target_folder or "downloads")

        if not search_dir.exists():
            return False, f"Directory '{search_dir}' does not exist.", None

        # Scan files in directory
        import time
        now = time.time()
        matching_files = []

        try:
            for root, _, files in os.walk(search_dir):
                for f in files:
                    p = Path(root) / f
                    if not p.is_file():
                        continue

                    ext = p.suffix.lower()
                    mtime = p.stat().st_mtime
                    age_seconds = now - mtime

                    # Match extensions if category was detected
                    if target_exts and ext not in target_exts:
                        continue

                    # Match time if 'aaj/today' requested (within last 24 hours)
                    if is_today and age_seconds > 86400:
                        continue

                    matching_files.append((mtime, p))

        except Exception as e:
            logger.error(f"Error scanning directory '{search_dir}': {e}")

        if matching_files:
            # Sort by newest modification time
            matching_files.sort(key=lambda x: x[0], reverse=True)
            best_file = matching_files[0][1]
            logger.info(f"🎯 Smart resolved file: '{best_file.name}' from query '{query}'")
            return True, f"Resolved file: {best_file.name}", best_file

        return False, f"No matching file found for query '{query}'.", None

    def copy_file(self, source_query: str, dest_folder: str) -> Tuple[bool, str]:
        """Search and copy a file to a destination folder."""
        found, msg, src_path = self.find_file(source_query)
        if not found or not src_path:
            return False, f"Copy failed: {msg}"

        dest_dir = self.resolve_folder_alias(dest_folder)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / src_path.name

        try:
            shutil.copy2(src_path, dest_path)
            if dest_path.exists() and dest_path.stat().st_size == src_path.stat().st_size:
                return True, f"Successfully copied '{src_path.name}' to '{dest_dir.name}'."
            return False, "File copy verification failed: sizes do not match."
        except Exception as e:
            return False, f"Copy error: {e}"

    def list_files(self, folder_or_query: str = "downloads") -> str:
        """List contents of a specified folder or alias."""
        target_dir = self.resolve_folder_alias(folder_or_query)
        if not target_dir.exists():
            return f"Folder '{target_dir}' does not exist."

        try:
            items = os.listdir(target_dir)
            if not items:
                return f"Folder '{target_dir.name}' is empty."
            
            files = [f"• {item}" for item in items[:20]]
            return f"Contents of '{target_dir.name}' ({len(items)} items):\n" + "\n".join(files)
        except Exception as e:
            return f"Failed to list files: {e}"

    def list_files_by_relative_date(self, folder_alias: str = "downloads", days_ago: int = 0) -> Tuple[bool, str, List[Path]]:
        """
        List files created or modified N days ago (e.g. days_ago=2 for '2 din pehle', days_ago=1 for 'kal/yesterday', days_ago=0 for 'aaj/today').
        """
        search_dir = self.resolve_folder_alias(folder_alias)
        if not search_dir.exists():
            return False, f"Directory '{search_dir}' does not exist.", []

        import time
        from datetime import datetime, timedelta
        
        now = datetime.now()
        target_date = (now - timedelta(days=days_ago)).date()

        matching_files = []
        try:
            for root, dirs, files in os.walk(search_dir):
                for item in files + dirs:
                    p = Path(root) / item
                    try:
                        mtime = p.stat().st_mtime
                        item_date = datetime.fromtimestamp(mtime).date()
                        if item_date == target_date:
                            matching_files.append(p)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error scanning directory for date filter: {e}")

        date_label = "today" if days_ago == 0 else ("yesterday" if days_ago == 1 else f"{days_ago} days ago")
        if matching_files:
            file_names = [f"• {p.name}" for p in matching_files[:15]]
            summary = f"Files/folders modified in '{search_dir.name}' {date_label}:\n" + "\n".join(file_names)
            return True, summary, matching_files

        return False, f"No files or folders found in '{search_dir.name}' modified {date_label}.", []

    def move_file(self, source_query: str, dest_folder: str) -> Tuple[bool, str]:
        """Search and move a file to a destination folder."""
        found, msg, src_path = self.find_file(source_query)
        if not found or not src_path:
            return False, f"Move failed: {msg}"

        dest_dir = self.resolve_folder_alias(dest_folder)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / src_path.name

        try:
            shutil.move(str(src_path), str(dest_path))
            if dest_path.exists():
                return True, f"Successfully moved '{src_path.name}' to '{dest_dir.name}'."
            return False, "File move verification failed."
        except Exception as e:
            return False, f"Move error: {e}"

# Singleton accessor
_file_manager: Optional[SmartFileManager] = None

def get_file_manager() -> SmartFileManager:
    global _file_manager
    if _file_manager is None:
        _file_manager = SmartFileManager()
    return _file_manager
