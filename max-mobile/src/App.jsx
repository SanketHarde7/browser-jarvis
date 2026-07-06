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
import ErrorBoundary from './components/ErrorBoundary'
import BackgroundEngine from './components/BackgroundEngine'
import TypingInput from './components/TypingInput.jsx'
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

function App() {
  // ── State ──
  const [booting, setBooting] = useState(true)
  const [jarvisState, setMaxState] = useState('idle')
  const [messages, setMessages] = useState([])
  const [error, setError] = useState(null)
  const [chatOpen, setChatOpen] = useState(true)
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

  // ── Clear Chat ──
  const handleClearChat = async () => {
    setMessages([])
    try {
      await fetch('http://localhost:8000/api/memory', { method: 'DELETE' })
    } catch (e) {
      console.warn('Memory clear failed:', e)
    }
  }

  // ── Skill Chip Handler ──
  const handleSkillSelect = (text) => {
    handleSendText(text)
  }

  // ── Boot Screen ──
  if (booting) {
    return <BootSequence onComplete={() => setBooting(false)} />
  }

  // ── Mic Button Colors ──
  const micColors = {
    idle: {
      bg: 'linear-gradient(135deg, #00d4ff 0%, #0099cc 100%)',
      shadow: '0 0 30px rgba(0, 212, 255, 0.35)',
      text: '#050a0f',
    },
    listening: {
      bg: 'linear-gradient(135deg, #00ff88 0%, #00cc6a 100%)',
      shadow: '0 0 40px rgba(0, 255, 136, 0.5)',
      text: '#050a0f',
    },
    thinking: {
      bg: 'linear-gradient(135deg, #ffd700 0%, #cc9900 100%)',
      shadow: '0 0 30px rgba(255, 215, 0, 0.3)',
      text: '#050a0f',
    },
    speaking: {
      bg: 'rgba(255, 58, 138, 0.2)',
      shadow: '0 0 20px rgba(255, 58, 138, 0.2)',
      text: '#ff3a8a',
    },
  }
  const micStyle = micColors[jarvisState] || micColors.idle

  return (
    <div
      style={{
        width: '100vw',
        height: '100vh',
        backgroundColor: '#05070B', // Primary Background from PRD
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      {/* 9-Layer Environment Engine */}
      <BackgroundEngine maxState={jarvisState} />

      {/* 3D Scene — Centered Orb */}
      <div
        style={{
          position: 'absolute',
          top: 0,
          bottom: 0,
          left: 0,
          right: 0,
          zIndex: 1,
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          transition: 'filter 0.5s ease-in-out',
        }}
      >
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

      {/* Circular Waveform Overlay */}
      <div
        style={{
          position: 'absolute',
          top: '50%',
          left: chatOpen ? 'calc(50% - 190px)' : '50%',
          transform: 'translate(-50%, -50%)',
          width: '500px',
          height: '500px',
          zIndex: 2,
          pointerEvents: 'none',
          transition: 'left 0.4s cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        <WaveVisualizer
          isActive={isRecording || isPlaying}
          mode={jarvisState}
          analyserNode={analyserNode}
        />
      </div>

      {/* Center Status Text + Mic Button */}
      <div
        style={{
          position: 'absolute',
          bottom: '6rem',
          left: chatOpen ? 'calc(50% - 190px)' : '50%',
          transform: 'translateX(-50%)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '1.5rem',
          zIndex: 10,
          transition: 'left 0.4s cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        {/* State Label */}
        <AnimatePresence mode="wait">
          <motion.div
            key={jarvisState}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2 }}
            style={{
              fontFamily: "'Orbitron', monospace",
              fontSize: '0.85rem',
              fontWeight: 600,
              letterSpacing: '4px',
              color:
                jarvisState === 'idle'
                  ? '#00d4ff'
                  : jarvisState === 'listening'
                  ? '#00ff88'
                  : jarvisState === 'thinking'
                  ? '#ffd700'
                  : '#ff3a8a',
              textShadow: `0 0 20px currentColor`,
            }}
          >
            {jarvisState === 'idle' && '◆ SYSTEM READY'}
            {jarvisState === 'listening' && '● LISTENING...'}
            {jarvisState === 'thinking' && '◇ PROCESSING...'}
            {jarvisState === 'speaking' && '♪ RESPONDING...'}
          </motion.div>
        </AnimatePresence>

        {/* Main Mic Button */}
        <motion.button
          id="mic-button"
          onMouseDown={handleMicPress}
          onMouseUp={handleMicRelease}
          onTouchStart={(e) => {
            e.preventDefault()
            handleMicPress()
          }}
          onTouchEnd={(e) => {
            e.preventDefault()
            handleMicRelease()
          }}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.92 }}
          disabled={jarvisState === 'thinking' || jarvisState === 'speaking' || jarvisState === 'transferred'}
          style={{
            padding: '1rem 2.8rem',
            fontSize: '0.9rem',
            fontWeight: 700,
            fontFamily: "'Orbitron', monospace",
            letterSpacing: '3px',
            background: jarvisState === 'transferred' ? '#222' : micStyle.bg,
            color: jarvisState === 'transferred' ? '#555' : micStyle.text,
            border: 'none',
            borderRadius: '50px',
            cursor:
              jarvisState === 'thinking' || jarvisState === 'speaking' || jarvisState === 'transferred'
                ? 'not-allowed'
                : 'pointer',
            boxShadow: jarvisState === 'transferred' ? 'none' : micStyle.shadow,
            transition: 'all 0.3s ease',
            opacity:
              jarvisState === 'thinking' || jarvisState === 'speaking' || jarvisState === 'transferred'
                ? 0.5
                : 1,
            userSelect: 'none',
            WebkitUserSelect: 'none',
          }}
        >
          {jarvisState === 'transferred'
            ? '📱 PHONE IS ACTIVE'
            : isRecording
            ? '🎙️  RELEASE TO SEND'
            : jarvisState === 'thinking'
            ? '⏳  PROCESSING...'
            : jarvisState === 'speaking'
            ? '🔊  SPEAKING...'
            : '🎙️  HOLD TO SPEAK'}
        </motion.button>
        
        {/* Typing Input for Text Commands */}
        <TypingInput 
          onSend={handleSendText} 
          disabled={jarvisState === 'thinking' || jarvisState === 'speaking' || jarvisState === 'transferred'} 
        />

        {/* NEW: Explicit Kill Switch Button */}
        <AnimatePresence>
          {(jarvisState === 'thinking' || jarvisState === 'speaking') && (
            <motion.button
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              onClick={() => {
                console.log('🛑 Kill switch triggered via UI');
                stopAudio();
                setMaxState('idle');
                try {
                  const ws = new WebSocket(`ws://${serverUrl}/ws?token=${import.meta.env.VITE_WS_AUTH_TOKEN || ''}`);
                  ws.onopen = () => {
                    ws.send(JSON.stringify({ type: 'abort' }));
                    ws.close();
                  };
                } catch (err) {
                  console.warn('Could not send abort signal', err);
                }
              }}
              style={{
                marginTop: '10px',
                padding: '0.6rem 1.5rem',
                fontSize: '0.8rem',
                fontWeight: 600,
                fontFamily: "'Orbitron', monospace",
                background: 'rgba(255, 58, 58, 0.1)',
                color: '#ff3a3a',
                border: '1px solid rgba(255, 58, 58, 0.5)',
                borderRadius: '8px',
                cursor: 'pointer',
                boxShadow: '0 0 10px rgba(255, 58, 58, 0.2)',
                transition: 'all 0.2s',
              }}
              whileHover={{ background: 'rgba(255, 58, 58, 0.2)', scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
            >
              🛑 STOP MAX
            </motion.button>
          )}
        </AnimatePresence>
      </div>

      {/* Settings Modal */}
      <AnimatePresence>
        {showSettings && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            style={{
                position: 'fixed',
                inset: 0,
                zIndex: 50,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: 'rgba(0, 0, 0, 0.6)',
                backdropFilter: 'blur(4px)'
            }}
          >
            <motion.div
              initial={{ scale: 0.9 }}
              animate={{ scale: 1 }}
              exit={{ scale: 0.9 }}
              style={{
                backgroundColor: '#111',
                border: '1px solid rgba(255,255,255,0.1)',
                padding: '1.5rem',
                borderRadius: '1rem',
                width: '90%',
                maxWidth: '400px',
                boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.25)'
              }}
            >
              <h2 style={{color: 'white', fontSize: '1.25rem', marginBottom: '1rem', fontWeight: 600}}>Connection Settings</h2>
              <label style={{color: 'rgba(255,255,255,0.6)', fontSize: '0.875rem', marginBottom: '0.5rem', display: 'block'}}>Backend Server IP:PORT</label>
              <input 
                type="text"
                value={serverUrl}
                onChange={(e) => setServerUrl(e.target.value)}
                style={{
                    width: '100%',
                    backgroundColor: 'rgba(255,255,255,0.05)',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: '0.75rem',
                    padding: '0.75rem',
                    color: 'white',
                    marginBottom: '1.5rem',
                    outline: 'none'
                }}
                placeholder="e.g. 192.168.1.100:8000"
              />
              <div style={{display: 'flex', justifyContent: 'flex-end', gap: '0.75rem'}}>
                <button 
                  onClick={() => setShowSettings(false)}
                  style={{padding: '0.5rem 1rem', borderRadius: '0.75rem', color: 'rgba(255,255,255,0.7)', backgroundColor: 'transparent', border: 'none', cursor: 'pointer'}}
                >
                  Cancel
                </button>
                <button 
                  onClick={() => {
                    localStorage.setItem("max_server_url", serverUrl);
                    setShowSettings(false);
                    window.location.reload();
                  }}
                  style={{padding: '0.5rem 1rem', borderRadius: '0.75rem', backgroundColor: 'rgba(0, 212, 255, 0.2)', color: '#00d4ff', fontWeight: 500, border: 'none', cursor: 'pointer'}}
                >
                  Save & Reconnect
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Settings Button */}
      <div style={{ position: 'absolute', top: '1rem', right: '1rem', zIndex: 100 }}>
        <button
          onClick={() => setShowSettings(true)}
          style={{
            background: 'rgba(255, 255, 255, 0.05)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            borderRadius: '50%',
            width: '40px',
            height: '40px',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#aaa',
            fontSize: '1.2rem'
          }}
        >
          ⚙️
        </button>
      </div>

      {/* Chat Panel */}
      <AnimatePresence>
        {chatOpen && (
          <ChatPanel
            messages={messages}
            isProcessing={jarvisState === 'thinking'}
            onSendText={handleSendText}
            onSendImage={handleSendImage} // NEW  
            onClear={handleClearChat}
            isVisible={chatOpen}
          />
        )}
      </AnimatePresence>

    </div>
  )
}

export default App