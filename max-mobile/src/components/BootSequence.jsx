/**
 * 🚀 Boot Sequence Component
 * Cinematic startup animation with glitch text and system initialization messages
 */
import { useState, useEffect } from 'react'

const BOOT_LINES = [
  { text: '> Initializing neural interface...', delay: 200 },
  { text: '> Loading Groq LLM module [llama-3.3-70b]', delay: 400 },
  { text: '> Whisper STT engine: READY', delay: 600 },
  { text: '> Edge-TTS voice synthesis: ONLINE', delay: 800 },
  { text: '> Memory manager: LOADED', delay: 1000 },
  { text: '> Skills engine: 13 skills registered', delay: 1200 },
  { text: '> WebSocket real-time link: CONNECTED', delay: 1400 },
  { text: '> All systems nominal.', delay: 1600 },
]

export default function BootSequence({ onComplete }) {
  const [visibleLines, setVisibleLines] = useState(0)
  const [showTitle, setShowTitle] = useState(false)
  const [dismissing, setDismissing] = useState(false)

  useEffect(() => {
    // Show title after 300ms
    const titleTimer = setTimeout(() => setShowTitle(true), 300)

    // Show boot lines progressively
    const lineTimers = BOOT_LINES.map((line, i) =>
      setTimeout(() => setVisibleLines(i + 1), line.delay + 800)
    )

    // Auto dismiss after all lines shown
    const dismissTimer = setTimeout(() => {
      setDismissing(true)
    }, BOOT_LINES.length * 200 + 2400)

    const completeTimer = setTimeout(() => {
      onComplete?.()
    }, BOOT_LINES.length * 200 + 3000)

    return () => {
      clearTimeout(titleTimer)
      lineTimers.forEach(clearTimeout)
      clearTimeout(dismissTimer)
      clearTimeout(completeTimer)
    }
  }, [onComplete])

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 9999,
        background: 'var(--bg-void)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        animation: dismissing ? 'bootDismiss 0.6s ease forwards' : 'none',
      }}
    >
      {/* Background grid effect */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          backgroundImage: `
            linear-gradient(rgba(0, 212, 255, 0.03) 1px, transparent 1px),
            linear-gradient(90deg, rgba(0, 212, 255, 0.03) 1px, transparent 1px)
          `,
          backgroundSize: '40px 40px',
          opacity: 0.5,
        }}
      />

      {/* Scan line */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: '2px',
          background: 'linear-gradient(90deg, transparent, var(--accent-cyan), transparent)',
          animation: 'gridScan 3s linear infinite',
          opacity: 0.5,
        }}
      />

      {/* MAX Title */}
      {showTitle && (
        <h1
          style={{
            fontFamily: 'var(--font-display)',
            fontSize: 'clamp(3rem, 8vw, 6rem)',
            fontWeight: 900,
            letterSpacing: '12px',
            background: 'linear-gradient(135deg, #00d4ff 0%, #00ff88 50%, #00d4ff 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            animation: 'bootGlitch 0.5s ease, bootFlicker 2s ease',
            textShadow: 'none',
            marginBottom: '3rem',
            position: 'relative',
            zIndex: 2,
          }}
        >
          MAX
        </h1>
      )}

      {/* Horizontal line under title */}
      {showTitle && (
        <div
          style={{
            width: '300px',
            height: '1px',
            background: 'linear-gradient(90deg, transparent, var(--accent-cyan), transparent)',
            animation: 'bootLineExpand 1s ease forwards',
            marginBottom: '2.5rem',
          }}
        />
      )}

      {/* Boot Lines */}
      <div
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: '0.85rem',
          color: 'var(--text-dim)',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.4rem',
          maxWidth: '500px',
          width: '90%',
          position: 'relative',
          zIndex: 2,
        }}
      >
        {BOOT_LINES.slice(0, visibleLines).map((line, i) => (
          <div
            key={i}
            style={{
              animation: 'bootFadeIn 0.3s ease forwards',
              color: i === visibleLines - 1 ? 'var(--accent-cyan)' : 'var(--text-dim)',
              transition: 'color 0.3s ease',
            }}
          >
            {line.text}
            {i === visibleLines - 1 && (
              <span style={{ animation: 'bootFlicker 1s infinite' }}>▊</span>
            )}
          </div>
        ))}
      </div>

      {/* Bottom status */}
      {visibleLines >= BOOT_LINES.length && (
        <div
          style={{
            position: 'absolute',
            bottom: '3rem',
            fontFamily: 'var(--font-display)',
            fontSize: '0.9rem',
            color: 'var(--accent-green)',
            letterSpacing: '4px',
            animation: 'bootFadeIn 0.5s ease, textGlow 2s ease infinite',
          }}
        >
          SYSTEMS ONLINE
        </div>
      )}
    </div>
  )
}
