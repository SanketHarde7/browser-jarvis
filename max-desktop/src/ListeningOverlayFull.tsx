// Path: max-desktop/src/ListeningOverlayFull.tsx
// Use: Fullscreen voice input overlay for Tauri app.
import React, { useEffect, useState } from "react";
import "./ListeningOverlayFull.css";

// --- Custom Idle Theme Component (TESTING FIXED YELLOW) ---
const IdleTheme: React.FC<{ active: boolean }> = ({ active }) => {
  // COMMENTED OUT FOR TESTING
  /*
  const [layers, setLayers] = useState<{ id: number; hue: number }[]>([]);

  useEffect(() => {
    if (!active) {
      setLayers([]);
      return;
    }
    setLayers([{ id: Date.now(), hue: Math.floor(Math.random() * 360) }]);
    const interval = setInterval(() => {
      setLayers((prev) => {
        const newLayer = { id: Date.now(), hue: Math.floor(Math.random() * 360) };
        return [...prev.slice(-1), newLayer];
      });
    }, 4000);
    return () => clearInterval(interval);
  }, [active]);
  */

  return (
    <div className={`theme-layer ${active ? "active" : ""}`}>
      <div
        className="gas-edge gas-bottom theme-idle-layer"
        style={{ 
          backgroundImage: `linear-gradient(90deg, #ffeb3b, #fbc02d, #ffeb3b, #fbc02d, #ffeb3b)`
        }}
      />
    </div>
  );
};

export const ListeningOverlayFull: React.FC = () => {
  const [overlayState, setOverlayState] = useState(() => {
    return localStorage.getItem("max-overlay-state") || "idle";
  });


  useEffect(() => {
    document.body.style.background = "transparent";
    document.body.style.overflow = "hidden";

    // 1. Storage Listener (fires when App.tsx changes state)
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === "max-overlay-state" && e.newValue) {
        setOverlayState(e.newValue);
      }
    };
    window.addEventListener("storage", handleStorageChange);

    // 2. Fallback Polling (Catches the exact millisecond if storage event is slightly delayed)
    const interval = setInterval(() => {
      const current = localStorage.getItem("max-overlay-state") || "idle";
      setOverlayState((prev) => prev !== current ? current : prev);
    }, 100);

    return () => {
      window.removeEventListener("storage", handleStorageChange);
      clearInterval(interval);
    };
  }, []);

  return (
    <div className="gas-overlay-container" aria-hidden="true">
      
      {/* 0. IDLE THEME (Random Right-to-Left Chemical Mix) */}
      <IdleTheme active={overlayState === "idle"} />

      {/* 1. LISTENING THEME (Blue / Cyan Family) */}
      <div className={`theme-layer ${overlayState === "listening" ? "active" : ""}`}>
        <div className="gas-edge gas-bottom theme-listening" />
      </div>

      {/* 2. PROCESSING THEME (Orange / Warm Amber Family) */}
      <div className={`theme-layer ${overlayState === "processing" ? "active" : ""}`}>
        <div className="gas-edge gas-bottom theme-processing" />
      </div>

      {/* 3. SPEAKING THEME (Deep Purple, Neon Pink, Violet) */}
      <div className={`theme-layer ${overlayState === "speaking" ? "active" : ""}`}>
        <div className="gas-edge gas-bottom theme-speaking" />
      </div>


      <div className="ambient-screen-glow" />
    </div>
  );
};