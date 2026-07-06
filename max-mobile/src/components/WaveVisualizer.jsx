/**
 * 🌊 Circular Audio Waveform Visualizer
 * Renders a radial waveform around the orb using Canvas API
 * Supports real microphone analyser data and simulated waves
 */
import { useEffect, useRef, useCallback } from 'react'

const MODE_COLORS = {
  idle: { r: 0, g: 212, b: 255 },
  listening: { r: 0, g: 255, b: 136 },
  thinking: { r: 255, g: 215, b: 0 },
  speaking: { r: 255, g: 58, b: 138 },
}

export default function WaveVisualizer({
  isActive = false,
  mode = 'idle',
  analyserNode = null,
}) {
  const canvasRef = useRef(null)
  const animationRef = useRef(null)
  const timeRef = useRef(0)

  const animate = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    const { width, height } = canvas
    const centerX = width / 2
    const centerY = height / 2
    const baseRadius = Math.min(width, height) * 0.28
    const color = MODE_COLORS[mode] || MODE_COLORS.idle
    timeRef.current += 0.02

    ctx.clearRect(0, 0, width, height)

    // Get audio data if available
    let audioData = null
    if (analyserNode) {
      const bufferLength = analyserNode.frequencyBinCount
      audioData = new Uint8Array(bufferLength)
      analyserNode.getByteFrequencyData(audioData)
    }

    const barCount = 120
    const angleStep = (Math.PI * 2) / barCount

    for (let i = 0; i < barCount; i++) {
      const angle = i * angleStep + timeRef.current * 0.3

      // Calculate bar height
      let barHeight
      if (audioData && isActive) {
        const dataIndex = Math.floor((i / barCount) * audioData.length)
        barHeight = (audioData[dataIndex] / 255) * baseRadius * 0.6
      } else if (isActive) {
        // Simulated wave when active but no analyser
        const wave1 = Math.sin(i * 0.15 + timeRef.current * 3) * 0.5
        const wave2 = Math.sin(i * 0.08 + timeRef.current * 2) * 0.3
        const wave3 = Math.sin(i * 0.25 + timeRef.current * 5) * 0.2
        barHeight = Math.abs(wave1 + wave2 + wave3) * baseRadius * 0.5
      } else {
        // Subtle idle wave
        barHeight = Math.abs(Math.sin(i * 0.1 + timeRef.current)) * baseRadius * 0.08
      }

      const innerR = baseRadius + 8
      const outerR = innerR + barHeight

      const x1 = centerX + Math.cos(angle) * innerR
      const y1 = centerY + Math.sin(angle) * innerR
      const x2 = centerX + Math.cos(angle) * outerR
      const y2 = centerY + Math.sin(angle) * outerR

      const alpha = isActive ? 0.4 + (barHeight / (baseRadius * 0.6)) * 0.5 : 0.15
      ctx.beginPath()
      ctx.moveTo(x1, y1)
      ctx.lineTo(x2, y2)
      ctx.strokeStyle = `rgba(${color.r}, ${color.g}, ${color.b}, ${alpha})`
      ctx.lineWidth = 2
      ctx.lineCap = 'round'
      ctx.stroke()
    }

    // Glow ring
    const glowAlpha = isActive ? 0.15 + Math.sin(timeRef.current * 2) * 0.05 : 0.05
    ctx.beginPath()
    ctx.arc(centerX, centerY, baseRadius + 6, 0, Math.PI * 2)
    ctx.strokeStyle = `rgba(${color.r}, ${color.g}, ${color.b}, ${glowAlpha})`
    ctx.lineWidth = 1
    ctx.stroke()

    animationRef.current = requestAnimationFrame(animate)
  }, [isActive, mode, analyserNode])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const resize = () => {
      const rect = canvas.parentElement?.getBoundingClientRect()
      if (rect) {
        canvas.width = rect.width
        canvas.height = rect.height
      } else {
        canvas.width = 500
        canvas.height = 500
      }
    }
    resize()
    window.addEventListener('resize', resize)

    animationRef.current = requestAnimationFrame(animate)

    return () => {
      window.removeEventListener('resize', resize)
      if (animationRef.current) cancelAnimationFrame(animationRef.current)
    }
  }, [animate])

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'absolute',
        inset: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        zIndex: 2,
      }}
    />
  )
}