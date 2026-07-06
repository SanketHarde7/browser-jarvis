/**
 * 📊 HUD Status Bar — Top Header
 * Shows MAX branding, connection status, current state, and time
 */
import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'

const STATE_LABELS = {
  idle: { text: 'SYSTEM READY', color: '#00d4ff' },
  listening: { text: 'LISTENING...', color: '#00ff88' },
  thinking: { text: 'PROCESSING...', color: '#ffd700' },
  speaking: { text: 'RESPONDING...', color: '#ff3a8a' },
}

export default function StatusBar({
  state = 'idle',
  connected = false,
  error = null,
  onToggleChat,
  chatOpen = false,
  continuousListening = false,
}) {
  const [time, setTime] = useState('')
  const cfg = STATE_LABELS[state] || STATE_LABELS.idle

  useEffect(() => {
    const update = () => {
      setTime(
        new Date().toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })
      )
    }
    update()
    const interval = setInterval(update, 1000)
    return () => clearInterval(interval)
  }, [])

  return (
    <header
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        zIndex: 50,
        padding: '0.8rem 1.5rem',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        background: 'rgba(5, 10, 15, 0.6)',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
        borderBottom: '1px solid rgba(0, 212, 255, 0.08)',
      }}
    >
      {/* Left: Logo + Status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '1.5rem' }}>
        <h1
          style={{
            fontFamily: "'Orbitron', monospace",
            fontSize: '1.3rem',
            fontWeight: 800,
            letterSpacing: '4px',
            background: 'linear-gradient(135deg, #00d4ff, #00ff88)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            margin: 0,
          }}
        >
          MAX
        </h1>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <span
            style={{
              width: '7px',
              height: '7px',
              borderRadius: '50%',
              background: connected ? '#00ff88' : '#ff3a3a',
              display: 'inline-block',
              animation: connected ? 'statusDotPulse 2s ease infinite' : 'none',
            }}
          />
          <span
            style={{
              fontFamily: "'Share Tech Mono', monospace",
              fontSize: '0.7rem',
              color: connected ? '#4a7a9b' : '#ff3a3a',
              letterSpacing: '1px',
            }}
          >
            {connected ? 'ONLINE' : 'OFFLINE'}
          </span>

          {/* Ambient Listening Badge */}
          <AnimatePresence>
            {continuousListening && (
              <motion.div
                initial={{ opacity: 0, scale: 0.8, x: -10 }}
                animate={{ opacity: 1, scale: 1, x: 0 }}
                exit={{ opacity: 0, scale: 0.8, x: -10 }}
                style={{
                  marginLeft: '1rem',
                  padding: '0.2rem 0.6rem',
                  background: 'rgba(0, 255, 136, 0.1)',
                  border: '1px solid rgba(0, 255, 136, 0.3)',
                  borderRadius: '12px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                }}
              >
                <span
                  style={{
                    width: '6px',
                    height: '6px',
                    borderRadius: '50%',
                    background: '#00ff88',
                    animation: 'statusDotPulse 1.5s ease infinite',
                  }}
                />
                <span
                  style={{
                    fontFamily: "'Share Tech Mono', monospace",
                    fontSize: '0.65rem',
                    color: '#00ff88',
                    letterSpacing: '1px',
                  }}
                >
                  AMBIENT LISTENING
                </span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* Center: State */}
      <AnimatePresence mode="wait">
        <motion.div
          key={state}
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 8 }}
          transition={{ duration: 0.2 }}
          style={{
            fontFamily: "'Orbitron', monospace",
            fontSize: '0.75rem',
            fontWeight: 600,
            letterSpacing: '3px',
            color: cfg.color,
            textShadow: `0 0 15px ${cfg.color}50`,
          }}
        >
          {cfg.text}
        </motion.div>
      </AnimatePresence>

      {/* Right: Error / Time / Chat Toggle */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
        {/* Error badge */}
        <AnimatePresence>
          {error && (
            <motion.div
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              style={{
                padding: '0.3rem 0.8rem',
                background: 'rgba(255, 58, 58, 0.15)',
                border: '1px solid rgba(255, 58, 58, 0.3)',
                borderRadius: '8px',
                color: '#ff3a3a',
                fontSize: '0.7rem',
                fontFamily: "'Share Tech Mono', monospace",
                maxWidth: '200px',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              ⚠ {error}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Time */}
        <span
          style={{
            fontFamily: "'Share Tech Mono', monospace",
            fontSize: '0.75rem',
            color: 'var(--text-dim)',
            letterSpacing: '1px',
          }}
        >
          {time}
        </span>

        {/* Chat toggle */}
        <button
          onClick={onToggleChat}
          style={{
            background: chatOpen
              ? 'rgba(0, 212, 255, 0.15)'
              : 'rgba(0, 212, 255, 0.05)',
            border: '1px solid rgba(0, 212, 255, 0.2)',
            borderRadius: '10px',
            padding: '0.45rem 0.8rem',
            color: '#00d4ff',
            cursor: 'pointer',
            fontSize: '0.85rem',
            transition: 'all 0.2s ease',
          }}
          title={chatOpen ? 'Close chat' : 'Open chat'}
        >
          💬
        </button>
      </div>
    </header>
  )
}