/**
 * 🔊 Audio Player Hook — v2 (Fixed Overlapping Voices)
 * Uses useRef for synchronous playing state to prevent stale closures.
 * Stop-before-play pattern: new audio always stops the previous one.
 */
import { useState, useRef, useCallback } from 'react'

export function useAudioPlayer() {
  const [isPlaying, setIsPlaying] = useState(false)
  const audioRef = useRef(null)
  const isPlayingRef = useRef(false)
  const onEndRef = useRef(null)

  /**
   * Stop any currently playing audio immediately.
   */
  const stopAudio = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.currentTime = 0
      audioRef.current.onended = null
      audioRef.current.onerror = null
      audioRef.current = null
    }
    isPlayingRef.current = false
    setIsPlaying(false)
    onEndRef.current = null
  }, [])

  /**
   * Play base64 audio. Always stops previous audio first.
   * @param {string} base64Audio - Raw base64 string (no prefix)
   * @param {Function} onEnd - Callback when playback ends
   */
  const playAudio = useCallback((base64Audio, onEnd) => {
    if (!base64Audio) {
      onEnd?.()
      return
    }

    // STOP any currently playing audio first — no overlap ever
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.currentTime = 0
      audioRef.current.onended = null
      audioRef.current.onerror = null
      audioRef.current = null
    }

    isPlayingRef.current = true
    setIsPlaying(true)
    onEndRef.current = onEnd

    const dataUri = `data:audio/mpeg;base64,${base64Audio}`
    const audio = new Audio(dataUri)
    audioRef.current = audio

    audio.onended = () => {
      isPlayingRef.current = false
      setIsPlaying(false)
      audioRef.current = null
      const cb = onEndRef.current
      onEndRef.current = null
      cb?.()
    }

    audio.onerror = (err) => {
      console.error('Audio playback error:', err)
      isPlayingRef.current = false
      setIsPlaying(false)
      audioRef.current = null
      const cb = onEndRef.current
      onEndRef.current = null
      cb?.()
    }

    audio.play().catch((err) => {
      console.error('Play failed:', err)
      isPlayingRef.current = false
      setIsPlaying(false)
      audioRef.current = null
      const cb = onEndRef.current
      onEndRef.current = null
      onEnd?.()
    })
  }, [])  // No dependencies — refs handle all mutable state

  return { playAudio, stopAudio, isPlaying }
}