/**
 * 🤖 MAX v2.0 — Immersive 3D AI Assistant
 * Full integration: 3D Orb + Voice Pipeline + Text Chat + Skills
 * Built with Three.js, Framer Motion & Glassmorphism
 */
import { useState, useCallback, Suspense, useEffect } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { motion, AnimatePresence } from 'framer-motion'

// Components
import BootSequence from './components/BootSequence.jsx'
import BackgroundEngine from './components/BackgroundEngine'
import InputBar from './components/InputBar.jsx'
import OrbCore from './components/OrbCore.jsx'
import WaveVisualizer from './components/WaveVisualizer.jsx'
import { Settings } from 'lucide-react'
import ChatPanel from './components/ChatPanel.jsx'
// Hooks
import { useWebSocket } from './hooks/useWebSocket.js'
import { useVoiceInput } from './hooks/useVoiceInput.js'
import { useAudioPlayer } from './hooks/useAudioPlayer.js'

// ── Helpers ──
function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    if (!blob || !(blob instanceof Blob)) {
      reject(new Error('Invalid audio blob'))
      return
    }
    const reader = new FileReader()
    reader.onloadend = () => resolve(reader.result.split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

function App() {
  // ── State ──
  const [booting, setBooting] = useState(true)
  const [jarvisState, setMaxState] = useState('idle')
  const [messages, setMessages] = useState([])
  const [error, setError] = useState(null)
  const [continuousListening, setContinuousListening] = useState(false)
  const [serverUrl, setServerUrl] = useState(() => localStorage.getItem("max_server_url") || "10.127.214.90:8000")
  const [showSettings, setShowSettings] = useState(false)

  // ── Hooks ──
  const { playAudio, stopAudio, isPlaying } = useAudioPlayer()
  const { startRecording, stopRecording, startContinuousListening, stopContinuousListening, updateJarvisState, isRecording, analyserNode } = useVoiceInput()

  // ── WebSocket Event Handler ──
  const handleWsEvent = useCallback(
    (data) => {
      const event = data?.event || data?.type

      switch (event) {
        case 'greeting':
          if (data.text) {
            setMessages((prev) => [
              ...prev,
              { role: 'jarvis', content: data.text },
            ])
          }
          setMaxState('idle')
          break

        case 'status_update':
          if (data.state) setMaxState(data.state)
          break

        case 'transcript':
          if (data.text) {
            setMessages((prev) => [
              ...prev,
              { role: 'user', content: data.text },
            ])
          }
          break

        case 'response_text':
          if (data.text) {
            setMessages((prev) => [
              ...prev,
              { role: 'jarvis', content: data.text },
            ])
          }
          setMaxState('idle')
          break

        case 'response':
          if (data.text) {
            setMessages((prev) => [
              ...prev,
              { role: 'jarvis', content: data.text },
            ])
          }
          setMaxState('idle')
          break

        case 'audio_response':
          if (data.audio) {
            playAudio(data.audio, () => {
              // Reset state only if we haven't received a new processing event
              setMaxState((current) => (current === 'speaking' ? 'idle' : current))
            })
            setMaxState('speaking')
          }
          break

        case 'skill_event':
          console.log('Skill executed:', data.skill)
          break

        case 'error':
          setError(data.message)
          setMaxState('idle')
          break

        case 'start_continuous_listening':
           setContinuousListening(true)
           setMessages((prev) => [
            ...prev,
            { role: 'jarvis', content: 'Continuous listening mode enabled.' },
          ])
          break

        case 'stop_continuous_listening':
           setContinuousListening(false)
           setMessages((prev) => [
            ...prev,
            { role: 'jarvis', content: 'Continuous listening mode disabled.' },
          ])
          break

        case 'stale_discard':
          setMaxState('listening')
          break

        case 'pong':
          break

        case 'SWITCH_ACTIVE':
          if (data.device === 'phone') {
             setMaxState('idle')
             setMessages((prev) => [
              ...prev,
              { role: 'jarvis', content: 'Phone is now active.' },
            ])
          } else if (data.device === 'laptop') {
             setMaxState('transferred')
             setContinuousListening(false)
             setMessages((prev) => [
              ...prev,
              { role: 'jarvis', content: 'Control transferred to laptop.' },
            ])
             stopAudio()
          }
          break

        default:
          console.log('Unknown WS event:', data)
      }
    },
    [playAudio]
  )

  const { isConnected, sendVoice, sendText ,sendImage} = useWebSocket(
    `ws://${serverUrl}/ws?token=${import.meta.env.VITE_WS_AUTH_TOKEN || ''}&device=phone`,
    { onEvent: handleWsEvent }
  )

  // ── Global Kill Switch ──
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        console.log('🛑 Kill switch triggered')
        stopAudio()
        setMaxState('idle')

        try {
          const ws = new WebSocket(`ws://localhost:8000/ws?token=${import.meta.env.VITE_WS_AUTH_TOKEN || ''}`)
          ws.onopen = () => {
            ws.send(JSON.stringify({ type: 'abort' }))
            ws.close()
          }
        } catch (err) {
          console.warn('Could not send abort signal', err)
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [stopAudio])

  // ── Continuous Listening VAD Hook ──
  const handleSpeechCaptured = useCallback(async (audioBlob) => {
    if (!audioBlob || audioBlob.size < 512) return
    setMaxState('thinking')
    try {
      const base64 = await blobToBase64(audioBlob)
      if (isConnected) {
        sendVoice(base64, Date.now())
      }
    } catch (err) {
      console.error('Speech send error:', err)
      setMaxState('idle')
    }
  }, [isConnected, sendVoice])

  useEffect(() => {
    updateJarvisState(jarvisState)
  }, [jarvisState, updateJarvisState])

  useEffect(() => {
    if (continuousListening) {
      startContinuousListening(handleSpeechCaptured, jarvisState).catch(console.error)
    } else {
      stopContinuousListening()
    }
  }, [continuousListening, startContinuousListening, stopContinuousListening, handleSpeechCaptured])

  // ── Voice Handlers ──
  const handleMicPress = async () => {
    if (isRecording || jarvisState === 'thinking' || jarvisState === 'speaking')
      return
    try {
      setError(null)
      await startRecording()
      setMaxState('listening')
    } catch (err) {
      setError('Microphone access denied. Please allow mic permissions.')
      setMaxState('idle')
    }
  }

  const handleMicRelease = async () => {
    if (!isRecording) return
    setMaxState('thinking')

    try {
      const audioBlob = await stopRecording()
      if (!audioBlob || audioBlob.size < 512) {
        setError('Audio too short. Hold button longer.')
        setMaxState('idle')
        return
      }

      const base64 = await blobToBase64(audioBlob)

      // Try WebSocket first, fallback to REST
      if (isConnected) {
        sendVoice(base64, Date.now())
      } else {
        // REST fallback
        const response = await fetch('http://localhost:8000/api/voice', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ audio: base64 }),
        })

        if (!response.ok) {
          const err = await response.json()
          throw new Error(err.detail || 'Processing failed')
        }

        const result = await response.json()

        setMessages((prev) => [
          ...prev,
          { role: 'user', content: result.transcript },
          { role: 'jarvis', content: result.response },
        ])

        if (result.audio) {
          setMaxState('speaking')
          playAudio(result.audio, () => setMaxState('idle'))
        } else {
          setMaxState('idle')
        }
      }
    } catch (err) {
      console.error('Voice error:', err)
      setError(err.message)
      setMaxState('idle')
    }
  }

  // ── Text Chat Handler ──
  const handleSendText = async (text) => {
    if (!text.trim() || jarvisState === 'thinking' || jarvisState === 'speaking')
      return

    setMessages((prev) => [...prev, { role: 'user', content: text }])
    setMaxState('thinking')
    setError(null)

    // Try WebSocket first
    if (isConnected) {
      sendText(text)
    } else {
      // REST fallback
      try {
        const response = await fetch('http://localhost:8000/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: text, tts: true }),
        })

        if (!response.ok) {
          const err = await response.json()
          throw new Error(err.detail || 'Chat failed')
        }

        const result = await response.json()

        setMessages((prev) => [
          ...prev,
          { role: 'jarvis', content: result.response },
        ])

        if (result.audio) {
          setMaxState('speaking')
          playAudio(result.audio, () => setMaxState('idle'))
        } else {
          setMaxState('idle')
        }
      } catch (err) {
        console.error('Chat error:', err)
        setError(err.message)
        setMaxState('idle')
      }
    }
  }
  // ── Image Handlers (NEW) ──
  const handleSendImage = async (file, promptText) => {
    if (jarvisState === 'thinking' || jarvisState === 'speaking') return

    setMessages((prev) => [
      ...prev,
      { role: 'user', content: `🖼️ [Image Attached] ${promptText}` },
    ])
    setMaxState('thinking')
    setError(null)

    try {
      // Convert File Object to Base64 String
      const base64 = await blobToBase64(file)

      if (isConnected) {
        // useWebSocket se naya sendImage function call hoga
        sendImage(base64, promptText)
      } else {
        throw new Error('WebSocket disconnected. Please reconnect to send image.')
      }
    } catch (err) {
      console.error('Image upload error:', err)
      setError(err.message)
      setMaxState('idle')
    }
  }

  // ── Boot Screen ──
  if (booting) {
    return <BootSequence onComplete={() => setBooting(false)} />
  }

  const isInputDisabled = jarvisState === 'thinking' || jarvisState === 'speaking' || jarvisState === 'transferred'

  const handleAbort = () => {
    console.log('🛑 Kill switch triggered via UI')
    stopAudio()
    setMaxState('idle')
    try {
      const ws = new WebSocket(`ws://${serverUrl}/ws?token=${import.meta.env.VITE_WS_AUTH_TOKEN || ''}`)
      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'abort' }))
        ws.close()
      }
    } catch (err) {
      console.warn('Could not send abort signal', err)
    }
  }

  return (
    <div className="max-shell">
      <BackgroundEngine maxState={jarvisState} />

      <header className="max-header">
        <div>
          <h1 className="max-title">MAX</h1>
          <p className="max-subtitle">AI that feels alive</p>
        </div>
        <button
          className="max-icon-button glass"
          type="button"
          onClick={() => setShowSettings(true)}
          aria-label="Open settings"
        >
          <Settings size={18} strokeWidth={1.8} />
        </button>
      </header>

      <main className="max-stage">
        <div className="orb-canvas-wrap" aria-hidden="true">
          <Canvas
            camera={{ position: [0, 0, 8], fov: 45 }}
            gl={{ antialias: true, alpha: true }}
            style={{ background: 'transparent' }}
          >
            <Suspense fallback={null}>
              <OrbCore state={jarvisState} />
            </Suspense>
            <OrbitControls
              enableZoom={false}
              enablePan={false}
              autoRotate
              autoRotateSpeed={0.3}
              maxPolarAngle={Math.PI / 1.5}
              minPolarAngle={Math.PI / 3}
            />
          </Canvas>
        </div>

        <div className="orb-wave-wrap" aria-hidden="true">
          <WaveVisualizer
            isActive={isRecording || isPlaying}
            mode={jarvisState}
            analyserNode={analyserNode}
          />
        </div>

        <ChatPanel
          messages={messages}
          isProcessing={jarvisState === 'thinking'}
          userName="Arjun"
        />
      </main>

      <div className="bottom-controls">
        <AnimatePresence>
          {(jarvisState === 'thinking' || jarvisState === 'speaking') && (
            <motion.button
              className="max-stop-button glass"
              type="button"
              initial={{ opacity: 0, y: 8, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.96 }}
              onClick={handleAbort}
            >
              Stop MAX
            </motion.button>
          )}
        </AnimatePresence>

        <InputBar
          onSendText={handleSendText}
          onSendImage={handleSendImage}
          onMicPress={handleMicPress}
          onMicRelease={handleMicRelease}
          disabled={isInputDisabled}
          isRecording={isRecording}
          state={jarvisState}
        />
      </div>

      <AnimatePresence>
        {error && (
          <motion.div
            className="max-error glass"
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            role="alert"
          >
            {error}
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showSettings && (
          <motion.div
            className="settings-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <motion.div
              className="settings-card glass"
              initial={{ scale: 0.94, y: 12 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.94, y: 12 }}
            >
              <h2>Connection Settings</h2>
              <label htmlFor="server-url">Backend Server IP:PORT</label>
              <input
                id="server-url"
                type="text"
                value={serverUrl}
                onChange={(e) => setServerUrl(e.target.value)}
                placeholder="e.g. 192.168.1.100:8000"
              />
              <div className="settings-actions">
                <button type="button" onClick={() => setShowSettings(false)}>
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => {
                    localStorage.setItem('max_server_url', serverUrl)
                    setShowSettings(false)
                    window.location.reload()
                  }}
                >
                  Save & Reconnect
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

export default App