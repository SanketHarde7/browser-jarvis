/**
 * 🚀 Boot Sequence — Premium minimal startup
 * Clean fade-in with system init lines, no sci-fi clutter
 */
import { useState, useEffect } from 'react'

const BOOT_LINES = [
  { text: '> Initializing neural interface...', delay: 200 },
  { text: '> Loading LLM module [llama-3.3-70b]', delay: 400 },
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
    const titleTimer = setTimeout(() => setShowTitle(true), 300)

    const lineTimers = BOOT_LINES.map((line, i) =>
      setTimeout(() => setVisibleLines(i + 1), line.delay + 800)
    )

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
    <div className={`boot-screen ${dismissing ? 'dismissing' : ''}`}>
      {/* Title */}
      {showTitle && (
        <>
          <h1 className="boot-title">MAX</h1>
          <div className="boot-divider" />
        </>
      )}

      {/* Boot Lines */}
      <div className="boot-lines">
        {BOOT_LINES.slice(0, visibleLines).map((line, i) => (
          <div
            key={i}
            className={`boot-line ${i === visibleLines - 1 ? 'active' : ''}`}
          >
            {line.text}
            {i === visibleLines - 1 && <span className="cursor">▊</span>}
          </div>
        ))}
      </div>

      {/* Status */}
      {visibleLines >= BOOT_LINES.length && (
        <div className="boot-status">SYSTEMS ONLINE</div>
      )}
    </div>
  )
}
