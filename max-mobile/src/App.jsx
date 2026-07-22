/**
 * 🤖 MAX v2.0 — Immersive 3D AI Assistant
 * Full integration: 3D Orb + Voice Pipeline + Text Chat + Skills
 * Built with Three.js, Framer Motion & Glassmorphism
 */
import { useState, useCallback, Suspense, useEffect } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls } from '@react-three/drei'
import { motion, AnimatePresence } from 'framer-motion'
import { EffectComposer, Bloom } from '@react-three/postprocessing'
import { Settings } from 'lucide-react'

// Components
import BootSequence from './components/BootSequence.jsx'
import BackgroundEngine from './components/BackgroundEngine'
import InputBar from './components/InputBar.jsx'
import OrbCore from './components/OrbCore.jsx'
import WaveVisualizer from './components/WaveVisualizer.jsx'
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

export default function App() {
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
          if (data.text) setMessages((prev) => [...prev, { role: 'jarvis', content: data.text }])
          setMaxState('idle')
          break
        case 'status_update':
          if (data.state) setMaxState(data.state)
          break
        case 'transcript':
          if (data.text) setMessages((prev) => [...prev, { role: 'user', content: data.text }])
          break
        case 'response_text':
        case 'response':
          if (data.text) setMessages((prev) => [...prev, { role: 'jarvis', content: data.text }])
          setMaxState('idle')
          break
        case 'audio_response':
          if (data.audio) {
            playAudio(data.audio, () => setMaxState((c) => (c === 'speaking' ? 'idle' : c)))
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
          break
        case 'stop_continuous_listening':
          setContinuousListening(false)
          break
        case 'stale_discard':
          setMaxState('listening')
          break
        case 'SWITCH_ACTIVE':
          if (data.device === 'phone') {
            setMaxState('idle')
            setMessages((prev) => [...prev, { role: 'jarvis', content: 'Phone is now active.' }])
          } else if (data.device === 'laptop') {
            setMaxState('transferred')
            setContinuousListening(false)
            stopAudio()
            setMessages((prev) => [...prev, { role: 'jarvis', content: 'Control transferred to laptop.' }])
          }
          break
        default:
          if (event !== 'pong') console.log('Unknown WS event:', data)
      }
    },
    [playAudio, stopAudio]
  )

  const { isConnected, sendVoice, sendText, sendImage } = useWebSocket(
    `ws://${serverUrl}/ws?token=${import.meta.env.VITE_WS_AUTH_TOKEN || ''}&device=phone`,
    { onEvent: handleWsEvent }
  )

  // ── Global Kill Switch ──
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') handleAbort()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, []) // handleAbort extracted below

  // ── Voice & Audio ──
  const handleSpeechCaptured = useCallback(async (audioBlob) => {
    if (!audioBlob || audioBlob.size < 512) return
    setMaxState('thinking')
    try {
      const base64 = await blobToBase64(audioBlob)
      if (isConnected) sendVoice(base64, Date.now())
    } catch (err) {
      console.error('Speech send error:', err)
      setMaxState('idle')
    }
  }, [isConnected, sendVoice])

  useEffect(() => { updateJarvisState(jarvisState) }, [jarvisState, updateJarvisState])

  useEffect(() => {
    if (continuousListening) startContinuousListening(handleSpeechCaptured, jarvisState).catch(console.error)
    else stopContinuousListening()
  }, [continuousListening, startContinuousListening, stopContinuousListening, handleSpeechCaptured])

  const handleMicPress = async () => {
    if (isRecording || jarvisState === 'thinking' || jarvisState === 'speaking') return
    try {
      setError(null)
      await startRecording()
      setMaxState('listening')
    } catch (err) {
      setError('Microphone access denied.')
      setMaxState('idle')
    }
  }

  const handleMicRelease = async () => {
    if (!isRecording) return
    setMaxState('thinking')
    try {
      const audioBlob = await stopRecording()
      if (!audioBlob || audioBlob.size < 512) throw new Error('Audio too short.')
      const base64 = await blobToBase64(audioBlob)
      if (isConnected) sendVoice(base64, Date.now())
      else throw new Error('WebSocket disconnected.')
    } catch (err) {
      setError(err.message)
      setMaxState('idle')
    }
  }

  // ── Text & Image ──
  const handleSendText = async (text) => {
    if (!text.trim() || jarvisState === 'thinking' || jarvisState === 'speaking') return
    setMessages((prev) => [...prev, { role: 'user', content: text }])
    setMaxState('thinking')
    setError(null)
    if (isConnected) sendText(text)
    else {
      setError('WebSocket disconnected.')
      setMaxState('idle')
    }
  }

  const handleSendImage = async (file, promptText) => {
    if (jarvisState === 'thinking' || jarvisState === 'speaking') return
    setMessages((prev) => [...prev, { role: 'user', content: `🖼️ [Image] ${promptText}` }])
    setMaxState('thinking')
    setError(null)
    try {
      const base64 = await blobToBase64(file)
      if (isConnected) sendImage(base64, promptText)
      else throw new Error('WebSocket disconnected.')
    } catch (err) {
      setError(err.message)
      setMaxState('idle')
    }
  }

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

  // ── Render ──
  if (booting) return <BootSequence onComplete={() => setBooting(false)} />

  const isInputDisabled = jarvisState === 'thinking' || jarvisState === 'speaking' || jarvisState === 'transferred'

  const stateColors = {
    idle: '#4AB8E8',
    listening: '#00D4B4',
    thinking: '#7C52D9',
    speaking: '#C840D8',
  }
  const currentStateColor = stateColors[jarvisState] || stateColors.idle

  return (
    <div className="max-shell" style={{ '--state-color': currentStateColor }}>
      <BackgroundEngine maxState={jarvisState} />

      {/* 1. HEADER */}
      <header className="max-header">
        <div>
          <h1 className="max-title">MAX</h1>
          <p className="max-subtitle">AI that feels alive</p>
        </div>
        <button className="max-icon-btn" onClick={() => setShowSettings(true)} aria-label="Settings">
          <Settings size={20} strokeWidth={1.8} />
        </button>
      </header>

      {/* 2. ORB STAGE */}
      <main className="orb-stage" aria-hidden="true">
        <Canvas camera={{ position: [0, 0, 8], fov: 45 }} gl={{ antialias: true, alpha: true }} style={{ background: 'transparent' }}>
          <Suspense fallback={null}>
            <OrbCore state={jarvisState} />
            <EffectComposer>
              <Bloom intensity={0.45} luminanceThreshold={0.2} luminanceSmoothing={0.5} mipmapBlur />
            </EffectComposer>
          </Suspense>
          <OrbitControls enableZoom={false} enablePan={false} autoRotate autoRotateSpeed={0.15} maxPolarAngle={Math.PI / 1.5} minPolarAngle={Math.PI / 3} />
        </Canvas>
        {/* Wave Overlay */}
        <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: 'min(118vw, 31rem)', height: 'min(118vw, 31rem)' }}>
          <WaveVisualizer isActive={isRecording || isPlaying} mode={jarvisState} analyserNode={analyserNode} />
        </div>
      </main>

      {/* 3. CONVERSATION LAYER */}
      <ChatPanel messages={messages} isProcessing={jarvisState === 'thinking'} userName="Sanket" />

      {/* 4. BOTTOM BAR */}
      <div className="bottom-bar">
        <AnimatePresence>
          {(jarvisState === 'thinking' || jarvisState === 'speaking') && (
            <motion.button
              className="stop-btn"
              type="button"
              initial={{ opacity: 0, y: 10, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.95 }}
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

      {/* 5. OVERLAYS */}
      <AnimatePresence>
        {error && (
          <motion.div className="error-toast" initial={{ opacity: 0, y: -10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} role="alert">
            {error}
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {showSettings && (
          <motion.div className="settings-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <motion.div className="settings-card" initial={{ scale: 0.94, y: 12 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.94, y: 12 }}>
              <h2>Connection Settings</h2>
              <label htmlFor="server-url">Backend Server IP:PORT</label>
              <input id="server-url" type="text" value={serverUrl} onChange={(e) => setServerUrl(e.target.value)} placeholder="e.g. 192.168.1.100:8000" />
              <div className="settings-actions">
                <button type="button" className="cancel-btn" onClick={() => setShowSettings(false)}>Cancel</button>
                <button
                  type="button"
                  className="save-btn"
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