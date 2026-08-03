# Path: backend/modules/ai_orchestrator/research_agent.py
# Use: Autonomous Agentic Research with Chunking, Critic Loop & Live Appending
import os
import time
import logging
import urllib.parse
import platform as os_platform
import pyperclip
from pathlib import Path
# codex-changes detail: keep research module importable when browser automation dependency is not installed.
try:
    import undetected_chromedriver as uc
except ImportError:
    uc = None
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from modules.ai_orchestrator.platform_config import PLATFORMS

logger = logging.getLogger("MAX.RESEARCH_AGENT")

class DeepResearchAgent:
    def __init__(self, config):
        self.config = config
        self.driver = None
        
        # Force Absolute Path for Workspace
        base_dir = os.path.abspath(os.path.dirname(__file__)) 
        project_root = os.path.abspath(os.path.join(base_dir, "../../.."))
        
        self.research_dir = Path(project_root) / "Jarvis Generated" / "Research"
        self.research_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"📁 Research Folder initialized at: {self.research_dir}")

    def _init_browser(self):
        # codex-changes detail: surface a clear runtime error only when deep browser research is invoked.
        if uc is None:
            raise RuntimeError("undetected-chromedriver is not installed; deep browser research is unavailable.")
        if not self.driver:
            logger.info("Initializing Stateful Browser for Deep Research...")
            options = uc.ChromeOptions()
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--start-maximized")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.page_load_strategy = 'eager'
            
            self.driver = uc.Chrome(options=options)
            self.driver.set_page_load_timeout(30)
            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''Object.defineProperty(navigator, 'webdriver', {get: () => undefined});'''
            })
    
    def _autonomous_search(self, topic: str) -> list:
        """Finds top URLs with multiple fallbacks and redirects handling."""
        urls = []
        try:
            logger.info(f"Autonomously searching the web for: {topic}...")
            search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(topic)}"
            self.driver.get(search_url)
            time.sleep(3.0) # Thoda extra wait DOM load ke liye
            
            # Multiple fallback selectors (Agar ek class fail ho toh dusri try karega)
            selectors = ["a.result__url", "h2.result__title a", ".result__snippet"]
            
            link_elements = []
            for sel in selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if elements:
                    link_elements = elements
                    break # Agar elements mil gaye toh aage wale selectors check mat karo
                    
            # Links extract karna aur decode karna
            for el in link_elements:
                href = el.get_attribute("href")
                
                # DuckDuckGo ke hidden redirect links ko extract karna
                if href and "duckduckgo.com/l/?uddg=" in href:
                    import urllib.parse
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    if 'uddg' in parsed:
                        href = parsed['uddg'][0]
                        
                # Duplicate aur DuckDuckGo ke internal links filter karna
                if href and "duckduckgo.com" not in href and href not in urls:
                    urls.append(href)
                    
                if len(urls) >= 3: 
                    break
                    
            # ULTIMATE FALLBACK: Agar phir bhi list khali reh gayi, toh Wikipedia ka direct URL add kar do
            if not urls:
                logger.warning("DuckDuckGo selectors failed. Falling back to Wikipedia direct link...")
                safe_wiki = topic.replace(" ", "_").title()
                urls.append(f"https://en.wikipedia.org/wiki/{safe_wiki}")
                
            logger.info(f"Autonomously found URLs: {urls}")
            
        except Exception as e:
            logger.warning(f"Autonomous search failed completely: {e}")
            # Absolute Failsafe
            safe_wiki = topic.replace(" ", "_").title()
            urls.append(f"https://en.wikipedia.org/wiki/{safe_wiki}")
            
        return urls



    def _scrape_single_url(self, url: str) -> str:
        try:
            logger.info(f"Crawling URL: {url}")
            self.driver.get(url)
            time.sleep(3)
            
            paragraphs = self.driver.find_elements(By.TAG_NAME, "p")
            text_blocks = []
            for p in paragraphs[:50]: 
                text = p.text.strip()
                if len(text) > 30:
                    text_blocks.append(text)
            
            return "\n\n".join(text_blocks)
        except Exception as e:
            logger.warning(f"Failed to crawl {url}. Skipping. Error: {e}")
            return ""

    def _inject_and_submit(self, prompt: str, platform_info):
        textarea = None
        for sel in platform_info.textarea_selectors:
            try:
                textarea = WebDriverWait(self.driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, sel)))
                break
            except:
                continue
                
        if not textarea:
            raise Exception("Could not find chat input box.")

        textarea.click()
        time.sleep(0.5)
        control_key = Keys.COMMAND if os_platform.system() == "Darwin" else Keys.CONTROL
        textarea.send_keys(control_key, 'a')
        textarea.send_keys(Keys.BACKSPACE)
        time.sleep(0.5)

        original_clip = pyperclip.paste()
        pyperclip.copy(prompt)
        textarea.send_keys(control_key, 'v')
        time.sleep(1)
        pyperclip.copy(original_clip)

        for sel in platform_info.submit_selectors:
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                if btn.is_enabled():
                    self.driver.execute_script("arguments[0].click();", btn)
                    return
            except:
                continue
        textarea.send_keys(Keys.ENTER)

    def _get_chat_text(self, platform_info) -> str:
        try:
            for sel in platform_info.response_selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if elements:
                    return elements[-1].text.strip()
        except:
            pass
        return ""

    def _smart_harvester(self, platform_info, original_prompt: str) -> str:
        logger.info("Waiting for AI Response...")
        start_time = time.time()
        response_started = False
        
        while time.time() - start_time < 30:
            text = self._get_chat_text(platform_info)
            if len(text) > 5:
                response_started = True
                break
            time.sleep(1)

        if not response_started:
            logger.warning("30s Watchdog Triggered! Refreshing page...")
            self.driver.refresh()
            time.sleep(6) 
            text = self._get_chat_text(platform_info)
            if len(text) < 5:
                self._inject_and_submit(original_prompt, platform_info)
                time.sleep(3)
        
        last_length = 0
        stable_seconds = 0
        final_text = ""

        while True:
            text = self._get_chat_text(platform_info)
            current_length = len(text)

            if current_length > 0 and current_length == last_length:
                stable_seconds += 1
            else:
                stable_seconds = 0 

            last_length = current_length
            final_text = text

            if stable_seconds >= 4:
                break
            time.sleep(1)

        return final_text

    def run_research(self, topic: str, urls_to_crawl: list, ai_platform: str):
        try:
            self._init_browser()
            safe_topic = topic.replace(" ", "_").lower()
            file_path = self.research_dir / f"research_{safe_topic}.txt"
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"--- DEEP RESEARCH REPORT: {topic.upper()} ---\n\n")

            if not urls_to_crawl:
                urls_to_crawl = self._autonomous_search(topic)

            raw_data = ""
            for url in urls_to_crawl:
                raw_data += f"\nSource: {url}\n{self._scrape_single_url(url)}"
                        
            if not raw_data.strip():
                raw_data = "No external raw data found. Use your internal knowledge base."

            platform_info = PLATFORMS.get(ai_platform.lower())
            if not platform_info:
                logger.error(f"Unknown platform: {ai_platform}")
                return
                
            self.driver.get(platform_info.url_new_chat)
            time.sleep(4) 
            
            # STAGE 1: Base Outline
            logger.info("Stage 1: Generating Base Outline...")
            outline_prompt = (
                f"Topic: '{topic}'.\nRaw Data:\n{raw_data[:20000]}\n\n"
                f"Act as a Master Researcher. Based on this data and your knowledge, "
                f"generate a highly detailed, 6-chapter outline for a comprehensive research paper. "
                f"Just output the bulleted outline, no conversational filler."
            )
            self._inject_and_submit(outline_prompt, platform_info)
            base_outline = self._smart_harvester(platform_info, outline_prompt)
            
            # STAGE 2: Agentic Critic Loop (Bounded)
            logger.info("Stage 2: Agentic Critic Review...")
            critic_prompt = (
                f"Review the outline you just created. Identify ONLY the top 2 most critical missing "
                f"advanced concepts or technical details. Merge them into the existing chapters. "
                f"DO NOT exceed a maximum of 8 main chapters total. Output the final, locked-in outline only."
            )
            self._inject_and_submit(critic_prompt, platform_info)
            final_outline = self._smart_harvester(platform_info, critic_prompt)

            with open(file_path, "a", encoding="utf-8") as f:
                f.write("--- APPROVED OUTLINE ---\n" + final_outline + "\n\n--- MAIN CONTENT ---\n\n")

            # STAGE 3: Chunked Generation & Live Appending
            logger.info("Stage 3: Chunked Generation...")
            chunks = ["Chapters 1 and 2", "Chapters 3 and 4", "Chapters 5 and 6", "Chapters 7 and 8 (if applicable)"]
            
            for chunk in chunks:
                logger.info(f"Generating {chunk}...")
                write_prompt = (
                    f"Based EXACTLY on the locked outline above, write ONLY {chunk} in extreme detail. "
                    f"Use professional formatting, deep technical explanations, and clear headings. "
                    f"DO NOT write any other chapters right now. Start directly with the content."
                )
                self._inject_and_submit(write_prompt, platform_info)
                chunk_content = self._smart_harvester(platform_info, write_prompt)
                
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(chunk_content + "\n\n")
                    
                time.sleep(3) 
                
            logger.info(f"SUCCESS! Autonomous Deep Research saved to: {file_path}")
            return "Research completed successfully."

        except Exception as e:
            logger.error(f"Research Agent failed: {e}")
        finally:
            if self.driver:
                self.driver.quit()
                self.driver = None
