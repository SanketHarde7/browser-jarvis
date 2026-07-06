/**
 * 🎙️ Voice Input Hook
 * Handles MediaRecorder API for mic recording with AnalyserNode
 * Also supports Continuous Listening via Local VAD (RMS Volume Tracking)
 */
import { useState, useRef, useCallback, useEffect } from 'react'

export function useVoiceInput() {
  const [isRecording, setIsRecording] = useState(false)
  const mediaRecorderRef = useRef(null)
  const audioChunksRef = useRef([])
  const analyserRef = useRef(null)
  const streamRef = useRef(null)
  const audioContextRef = useRef(null)

  // VAD Refs
  const processorRef = useRef(null)
  const isContinuousActiveRef = useRef(false)
  const isSpeakingRef = useRef(false)
  const silenceTimerRef = useRef(null)
  const onSpeechCapturedRef = useRef(null)
  const jarvisStateRef = useRef('idle')
  const cooldownActiveRef = useRef(false)
  const cooldownTimerRef = useRef(null)
  const speakingStartRef = useRef(0)

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopContinuousListening()
      stopAllTracks()
      if (audioContextRef.current?.state !== 'closed') {
        audioContextRef.current?.close()
      }
      if (cooldownTimerRef.current) {
        clearTimeout(cooldownTimerRef.current)
      }
    }
  }, [])

  const stopAllTracks = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
  }

  // Common init for Mic and AudioContext (Runs ONCE per session)
  const initAudio = async () => {
    if (streamRef.current && audioContextRef.current) {
      if (audioContextRef.current.state === 'suspended') {
        try {
          await audioContextRef.current.resume()
        } catch (e) {
          console.warn("Failed to resume AudioContext:", e)
        }
      }
      return
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }
    })
    streamRef.current = stream

    const audioContext = new (window.AudioContext || window.webkitAudioContext)()
    audioContextRef.current = audioContext

    // Resume context if suspended
    if (audioContext.state === 'suspended') {
      try {
        await audioContext.resume()
      } catch (e) {
        console.warn("Failed to resume new AudioContext:", e)
      }
    }

    const source = audioContext.createMediaStreamSource(stream)
    const analyser = audioContext.createAnalyser()
    analyser.fftSize = 512
    analyser.smoothingTimeConstant = 0.7
    source.connect(analyser)
    analyserRef.current = analyser

    // Initialize the ScriptProcessor here so it's ready for continuous mode
    const processor = audioContext.createScriptProcessor(4096, 1, 1)
    processorRef.current = processor
    source.connect(processor)
    // Connect to destination but we won't hear ourselves because we don't output anything in onaudioprocess
    processor.connect(audioContext.destination)

    let currentRecorder = null
    let currentChunks = []

    const stopCurrentRecordingAndEmit = () => {
      if (currentRecorder && currentRecorder.state !== 'inactive') {
        currentRecorder.onstop = () => {
          const blob = new Blob(currentChunks, { type: 'audio/webm' })
          const duration = Date.now() - speakingStartRef.current
          // Only emit if recording is at least 500ms and has content
          if (blob.size > 1500 && duration >= 500 && onSpeechCapturedRef.current) {
            onSpeechCapturedRef.current(blob)
          } else {
            console.log(`Speech chunk discarded: size=${blob.size} bytes, duration=${duration}ms`)
          }
        }
        currentRecorder.stop()
      }
      currentRecorder = null
      currentChunks = []
    }

    const startNewRecording = () => {
      speakingStartRef.current = Date.now()
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm'
      currentRecorder = new MediaRecorder(stream, { mimeType })
      currentChunks = []
      currentRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) currentChunks.push(e.data)
      }
      currentRecorder.start(100)
    }

    processor.onaudioprocess = (e) => {
      // Mute mic when MAX is actively thinking/speaking or during post-speech cooldown
      const isMuted = jarvisStateRef.current === 'speaking' || jarvisStateRef.current === 'thinking' || cooldownActiveRef.current
      if (!isContinuousActiveRef.current || isMuted) {
        if (isSpeakingRef.current) {
          isSpeakingRef.current = false
          setIsRecording(false)
          stopCurrentRecordingAndEmit()
        }
        return
      }

      const input = e.inputBuffer.getChannelData(0)
      let sum = 0.0
      for (let i = 0; i < input.length; i++) {
        sum += input[i] * input[i]
      }
      const rms = Math.sqrt(sum / input.length)

      const threshold = 0.027 // Lowered from 0.038 to increase mic sensitivity

      // Frequency Analysis Check (Change 2)
      let hasVoiceFrequency = true
      if (analyserRef.current) {
        const sampleRate = audioContextRef.current?.sampleRate || 16000
        const binSize = sampleRate / 512
        const minBin = Math.floor(200 / binSize)
        const maxBin = Math.ceil(3000 / binSize)

        const bufferLength = analyserRef.current.frequencyBinCount
        const dataArray = new Uint8Array(bufferLength)
        analyserRef.current.getByteFrequencyData(dataArray)

        let voiceSum = 0
        let count = 0
        for (let i = minBin; i <= maxBin && i < bufferLength; i++) {
          voiceSum += dataArray[i]
          count++
        }
        const voiceAverage = count > 0 ? voiceSum / count : 0

        // If average energy in 200-3000Hz range is not enough, discard it
        if (voiceAverage < 15) {
          hasVoiceFrequency = false
        }
      }

      if (rms > threshold && hasVoiceFrequency) {
        if (!isSpeakingRef.current) {
          isSpeakingRef.current = true
          setIsRecording(true)
          startNewRecording()
        }
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current)
          silenceTimerRef.current = null
        }
      } else {
        if (isSpeakingRef.current && !silenceTimerRef.current) {
          silenceTimerRef.current = setTimeout(() => {
            isSpeakingRef.current = false
            setIsRecording(false)
            stopCurrentRecordingAndEmit()
            silenceTimerRef.current = null
          }, 1200)
        }
      }
    }
  }

  // ── MANUAL PUSH-TO-TALK ──
  const startRecording = useCallback(async () => {
    try {
      await initAudio()
      // Pause continuous listening while manually recording
      const previousContinuousState = isContinuousActiveRef.current
      isContinuousActiveRef.current = false

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm'
      const mediaRecorder = new MediaRecorder(streamRef.current, { mimeType })
      mediaRecorderRef.current = mediaRecorder
      audioChunksRef.current = []

      mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data) }
      mediaRecorder.start(100)
      setIsRecording(true)

      // Store the previous state to resume later
      mediaRecorder.previousContinuousState = previousContinuousState
    } catch (err) {
      console.error('Mic access error:', err)
      throw err
    }
  }, [])

  const stopRecording = useCallback(() => {
    if (!mediaRecorderRef.current || !isRecording) return Promise.resolve(null)
    return new Promise((resolve) => {
      mediaRecorderRef.current.onstop = () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
        setIsRecording(false)

        // Restore continuous listening state with a safety delay to prevent tail-end VAD triggers
        if (mediaRecorderRef.current.previousContinuousState) {
          setTimeout(() => {
            isContinuousActiveRef.current = true
          }, 1000)
        }

        resolve(audioBlob)
      }
      mediaRecorderRef.current.stop()
    })
  }, [isRecording])

  // ── CONTINUOUS VAD MODE ──
  const startContinuousListening = useCallback(async (onSpeechCaptured, jarvisState) => {
    try {
      await initAudio()
      onSpeechCapturedRef.current = onSpeechCaptured
      jarvisStateRef.current = jarvisState
      isContinuousActiveRef.current = true
    } catch (err) {
      console.error('Continuous mic error:', err)
      throw err
    }
  }, [])

  const updateJarvisState = useCallback((state) => {
    // If transitioning away from speaking/thinking to idle, trigger a temporary cooldown
    if ((jarvisStateRef.current === 'speaking' || jarvisStateRef.current === 'thinking') && state === 'idle') {
      cooldownActiveRef.current = true
      if (cooldownTimerRef.current) clearTimeout(cooldownTimerRef.current)
      cooldownTimerRef.current = setTimeout(() => {
        cooldownActiveRef.current = false
      }, 500) // 0.5 seconds cooldown to let audio echo clear out completely
    }
    jarvisStateRef.current = state
  }, [])

  const stopContinuousListening = useCallback(() => {
    isContinuousActiveRef.current = false
    if (isSpeakingRef.current) {
      setIsRecording(false)
      isSpeakingRef.current = false
    }
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current)
      silenceTimerRef.current = null
    }
    // We intentionally DO NOT stop all tracks or close the audio context here!
    // This allows seamless resuming without requesting mic permissions again.
  }, [])

  return {
    startRecording,
    stopRecording,
    startContinuousListening,
    stopContinuousListening,
    updateJarvisState,
    isRecording,
    analyserNode: analyserRef.current,
  }
}