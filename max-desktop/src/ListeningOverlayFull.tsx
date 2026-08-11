// Path: max-desktop/src/ListeningOverlayFull.tsx
// Use: Fullscreen voice input overlay for Tauri app.
import React, { useEffect, useState } from "react";
import "./ListeningOverlayFull.css";

// --- Custom Idle Theme Component (Dynamic Gradients with Dissolution) ---
const generateRandomGradient = () => {
  const baseHue = Math.floor(Math.random() * 360);
  const spread = 25 + Math.floor(Math.random() * 15); // Hue step between 25-40
  
  const h1 = baseHue;
  const h2 = (baseHue + spread) % 360;
  const h3 = (baseHue + spread * 2) % 360;
  const h4 = (baseHue + spread * 3) % 360;
  
  // Symmetrical gradient (palindrome) ensures perfectly seamless looping 
  // and small hue steps prevent the browser's RGB interpolation from creating gray/dull jump zones.
  return `linear-gradient(90deg, 
    hsl(${h1}, 90%, 65%), 
    hsl(${h2}, 90%, 65%), 
    hsl(${h3}, 90%, 65%), 
    hsl(${h4}, 90%, 65%), 
    hsl(${h3}, 90%, 65%), 
    hsl(${h2}, 90%, 65%), 
    hsl(${h1}, 90%, 65%)
  )`;
};

const IdleTheme: React.FC<{ active: boolean }> = ({ active }) => {
  const [layers, setLayers] = useState<{ id: number; background: string }[]>([]);

  useEffect(() => {
    if (!active) {
      setLayers([]);
      return;
    }
    setLayers([{ id: Date.now(), background: generateRandomGradient() }]);
    
    const interval = setInterval(() => {
      setLayers((prev) => {
        // Only keep the most recent layer and append the new one for crossfading
        const newLayer = { id: Date.now(), background: generateRandomGradient() };
        return prev.length > 0 ? [prev[prev.length - 1], newLayer] : [newLayer];
      });
    }, 6000); // Change gradient every 6 seconds for a slower, breathing feel

    return () => clearInterval(interval);
  }, [active]);

  return (
    <div className={`theme-layer ${active ? "active" : ""}`}>
      {layers.map((layer, index) => {
        const isNew = index === layers.length - 1 && layers.length > 1;
        return (
          <div
            key={layer.id}
            className={`layer-wrapper ${isNew ? "fade-in-breathing" : ""}`}
            style={{ 
              position: "absolute",
              inset: 0,
              zIndex: index
            }}
          >
            <div
              className="gas-edge gas-bottom theme-idle-layer"
              style={{ backgroundImage: layer.background }}
            />
          </div>
        );
      })}
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
      
      {/* 0. IDLE & LISTENING THEME (Dynamic Random Gradients) */}
      <IdleTheme active={overlayState === "idle" || overlayState === "listening"} />

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