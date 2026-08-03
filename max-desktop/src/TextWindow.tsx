// Path: max-desktop/src/TextWindow.tsx
// Use: Text input and output window component UI.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { LogicalSize, PhysicalPosition } from "@tauri-apps/api/dpi";
import { getCurrentWindow } from "@tauri-apps/api/window";
import "./TextWindow.css";

const TEXT_WINDOW_CONTENT_KEY = "max-text-window-content";
const TEXT_WINDOW_VISIBLE_KEY = "max-text-window-visible";

const textWindow = getCurrentWindow();

function readTextWindowState() {
  return {
    visible: localStorage.getItem(TEXT_WINDOW_VISIBLE_KEY) === "1",
    text: localStorage.getItem(TEXT_WINDOW_CONTENT_KEY) || "",
  };
}

function estimateWindowSize(text: string) {
  // Constants for a professional, dynamically bounded UI
  const MAX_WIDTH = 450;
  const MIN_WIDTH = 280;
  const MAX_HEIGHT = 550;
  const MIN_HEIGHT = 120;

  // Realistic font metrics for 15px sans-serif
  const CHAR_WIDTH = 7.2;
  const LINE_HEIGHT = 24;

  const PADDING_WIDTH = 56; // Left+right padding + scrollbar leeway
  const PADDING_HEIGHT = 74; // Top+bottom padding + title height

  const textLength = text.length;
  if (textLength === 0) {
    return { width: MIN_WIDTH, height: MIN_HEIGHT };
  }

  // Compute width bounded by MIN/MAX
  let width = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.ceil(textLength * CHAR_WIDTH + PADDING_WIDTH)));

  // Compute height based on line wrapping
  const textAreaWidth = width - PADDING_WIDTH;
  const charsPerLine = Math.max(10, Math.floor(textAreaWidth / CHAR_WIDTH));

  const lines = text.split(/\r?\n/);
  let totalWrappedLines = 0;

  for (const line of lines) {
    if (line.length === 0) {
      totalWrappedLines += 1;
    } else {
      totalWrappedLines += Math.ceil(line.length / charsPerLine);
    }
  }

  let height = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.ceil(totalWrappedLines * LINE_HEIGHT + PADDING_HEIGHT)));

  // Add buffer for shadows and smooth resizing
  return { width: width + 10, height: height + 10 };
}

export const TextWindow: React.FC = () => {
  const [{ visible, text }, setWindowState] = useState(readTextWindowState);
  const resizeTimerRef = useRef<number | null>(null);
  const textRef = useRef<HTMLDivElement | null>(null);
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true);
  const [pinned, setPinned] = useState(() => localStorage.getItem("max-text-window-pinned") === "1");
  const [showTopShadow, setShowTopShadow] = useState(false);
  const [showBottomShadow, setShowBottomShadow] = useState(false);
  const [displayedText, setDisplayedText] = useState("");

  const syncWindow = useCallback(async (nextVisible: boolean, nextText: string) => {
    if (nextVisible) {
      const { width, height } = estimateWindowSize(nextText);
      // CRITICAL: Disable ignore-cursor-events so scrollbar & buttons are clickable.
      // The Rust start_listening_animation sets ignore_cursor_events(true) for the gas
      // border overlay, but this same window is reused for the text panel.
      await textWindow.setIgnoreCursorEvents(false).catch(() => { });
      await textWindow.setAlwaysOnTop(true).catch(() => { });
      await textWindow.setResizable(true).catch(() => { });
      await textWindow.setSize(new LogicalSize(width, height)).catch(() => { });
      await textWindow.show().catch(() => { });
      await textWindow.setFocus().catch(() => { });
      return;
    }

    // Restore ignore-cursor-events when hiding so gas border stays click-through
    await textWindow.setIgnoreCursorEvents(true).catch(() => { });
    await textWindow.hide().catch(() => { });
  }, []);

  useEffect(() => {
    document.body.style.background = "transparent";
    document.body.style.overflow = "hidden";
    document.body.style.pointerEvents = "auto";

    const refresh = () => setWindowState(readTextWindowState());
    const interval = window.setInterval(refresh, 120);
    window.addEventListener("storage", refresh);

    refresh();

    return () => {
      window.removeEventListener("storage", refresh);
      clearInterval(interval);
      if (resizeTimerRef.current) clearTimeout(resizeTimerRef.current);
    };
  }, []);

  useEffect(() => {
    if (resizeTimerRef.current) clearTimeout(resizeTimerRef.current);
    resizeTimerRef.current = window.setTimeout(() => {
      void syncWindow(visible, text);
    }, 16);
  }, [text, visible, syncWindow]);

  // Robust Typewriter Effect (bypasses Chromium background throttling)
  useEffect(() => {
    if (!visible) {
      setDisplayedText("");
      return;
    }

    const chars = Array.from(text);
    const totalChars = chars.length;
    let msPerChar = 65; // Slightly slower default for smoothness
    if (totalChars > 600) msPerChar = 40; // Fast enough for very long text but not jarring
    else if (totalChars > 200) msPerChar = 50;
    else if (totalChars > 60) msPerChar = 65;

    let index = 0;
    setDisplayedText((prev) => {
      if (text.startsWith(prev)) {
        index = Array.from(prev).length;
        return prev;
      }
      return "";
    });

    if (index >= totalChars) return;

    let lastTime = performance.now();
    let animationFrameId: number;

    const tick = (now: number) => {
      const delta = now - lastTime;
      if (delta >= msPerChar) {
        const charsToAdd = Math.floor(delta / msPerChar);
        index += charsToAdd;
        lastTime = now - (delta % msPerChar);

        if (index > totalChars) index = totalChars;
        setDisplayedText(chars.slice(0, index).join(""));
      }

      if (index < totalChars) {
        animationFrameId = requestAnimationFrame(tick);
      }
    };

    animationFrameId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animationFrameId);
  }, [text, visible]);

  // Auto-scroll to bottom when content changes and update shadows
  useEffect(() => {
    const el = textRef.current;
    if (!el) return;
    const atTop = el.scrollTop <= 8;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 8;
    setShowTopShadow(!atTop);
    setShowBottomShadow(!atBottom);
    if (autoScrollEnabled) {
      el.scrollTop = el.scrollHeight;
    }
  }, [displayedText, autoScrollEnabled]);

  // Detect user scrolls & pointer interaction to pause auto-scroll
  useEffect(() => {
    const el = textRef.current;
    if (!el) return;
    let isDown = false;
    const handleScroll = () => {
      if (isDown) return;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 10;
      setAutoScrollEnabled(atBottom);
    };
    const onDown = () => (isDown = true);
    const onUp = () => {
      isDown = false;
      handleScroll();
    };
    el.addEventListener("scroll", handleScroll);
    el.addEventListener("pointerdown", onDown);
    window.addEventListener("pointerup", onUp);
    return () => {
      el.removeEventListener("scroll", handleScroll);
      el.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointerup", onUp);
    };
  }, []);

  useEffect(() => {
    if (!visible) return;
    const position = localStorage.getItem("max-text-window-position");
    if (!position) return;
    try {
      const parsed = JSON.parse(position) as { x: number; y: number };
      if (Number.isFinite(parsed.x) && Number.isFinite(parsed.y)) {
        void textWindow.setPosition(new PhysicalPosition(parsed.x, parsed.y)).catch(() => { });
      }
    } catch { }
  }, [visible]);

  if (!visible) {
    return null;
  }

  return (
    <div className="text-window-shell">
      <div className="text-window-panel">
        <div className="text-window-title">
          MAX
          <button
            className={`text-window-pin ${pinned ? 'pinned' : ''}`}
            onClick={() => {
              const next = !pinned;
              setPinned(next);
              localStorage.setItem('max-text-window-pinned', next ? '1' : '0');
            }}
            aria-pressed={pinned}
            title={pinned ? 'Pinned - will not auto-close' : 'Pin window to keep open'}
          >
            📌
          </button>
        </div>

        <div className="text-window-scroll-wrap">
          {showTopShadow && <div className="scroll-shadow top" aria-hidden />}
          <div className="text-window-text" aria-live="polite" ref={textRef}>
            {displayedText}
          </div>
          {showBottomShadow && <div className="scroll-shadow bottom" aria-hidden />}
        </div>
      </div>
    </div>
  );
};

export default TextWindow;