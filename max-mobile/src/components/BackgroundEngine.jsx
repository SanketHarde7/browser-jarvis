/**
 * 🌌 Background Engine — Lightweight ambient depth layer
 * Uses pure CSS animations instead of framer-motion for zero React re-renders.
 * This is critical for mobile performance.
 */
import { useMemo } from 'react'
import { motion } from 'framer-motion'

const STATE_GLOWS = {
  listening: 'rgba(82, 200, 255, 0.10)',
  thinking: 'rgba(139, 92, 246, 0.08)',
  speaking: 'rgba(217, 70, 239, 0.07)',
  offline: 'rgba(255, 255, 255, 0.01)',
  idle: 'rgba(82, 200, 255, 0.04)',
}

const BackgroundEngine = ({ maxState }) => {
  const glowColor = STATE_GLOWS[maxState] || STATE_GLOWS.idle

  // Generate stars once with pure CSS animations (no React re-renders)
  const stars = useMemo(() => {
    return Array.from({ length: 30 }).map((_, i) => {
      const x = Math.random() * 100
      const y = Math.random() * 100
      const size = Math.random() * 1.5 + 0.5
      const opacity = Math.random() * 0.25 + 0.05
      const duration = Math.random() * 8 + 6
      const delay = Math.random() * -10

      return (
        <div
          key={i}
          style={{
            position: 'absolute',
            left: `${x}%`,
            top: `${y}%`,
            width: size,
            height: size,
            backgroundColor: 'rgba(255, 255, 255, 0.8)',
            borderRadius: '50%',
            opacity,
            animation: `starTwinkle ${duration}s ease-in-out ${delay}s infinite`,
            willChange: 'opacity',
          }}
        />
      )
    })
  }, [])

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 0,
      overflow: 'hidden', backgroundColor: '#030508', pointerEvents: 'none'
    }}>
      {/* Layer 1: Radial depth vignette */}
      <div style={{
        position: 'absolute', inset: '-30%',
        background: 'radial-gradient(ellipse at 50% 45%, rgba(14, 17, 24, 0.4) 0%, transparent 65%)',
        opacity: 0.5,
      }} />

      {/* Layer 2: Aurora fog — only thing using framer-motion (single element) */}
      <motion.div
        animate={{ backgroundColor: glowColor }}
        transition={{ backgroundColor: { duration: 1.8, ease: 'easeInOut' } }}
        style={{
          position: 'absolute',
          width: '140%', height: '140%',
          top: '-20%', left: '-20%',
          filter: 'blur(100px)',
          opacity: maxState === 'offline' ? 0 : 0.6,
          animation: 'auroraFloat 40s linear infinite',
          willChange: 'transform',
        }}
      />

      {/* Layer 3: Star field — pure CSS, zero React re-renders */}
      <div style={{ position: 'absolute', inset: 0 }}>
        {stars}
      </div>

      {/* Layer 4: Noise texture */}
      <div style={{
        position: 'absolute', inset: 0,
        opacity: 0.018,
        backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")',
        mixBlendMode: 'overlay',
      }} />
    </div>
  )
}

export default BackgroundEngine
