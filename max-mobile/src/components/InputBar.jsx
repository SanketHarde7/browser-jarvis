/**
 * 🎙️ Input Bar — Frosted glass input with mic/send toggle
 * Uses glass-3 tier for elevated depth feel
 */
import { useRef, useState } from 'react'
import { ImagePlus, Mic, Send } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'

const btnTransition = { type: 'spring', stiffness: 400, damping: 25 }

export default function InputBar({
  onSendText,
  onSendImage,
  onMicPress,
  onMicRelease,
  disabled = false,
  isRecording = false,
  state = 'idle',
}) {
  const [text, setText] = useState('')
  const [selectedImage, setSelectedImage] = useState(null)
  const fileInputRef = useRef(null)

  const canSubmit = text.trim().length > 0 || selectedImage

  const submit = (event) => {
    event.preventDefault()
    if (disabled || !canSubmit) return

    const prompt = text.trim()
    if (selectedImage) {
      onSendImage?.(selectedImage, prompt)
      setSelectedImage(null)
      if (fileInputRef.current) fileInputRef.current.value = ''
    } else {
      onSendText?.(prompt)
    }
    setText('')
  }

  const handleFileChange = (event) => {
    const file = event.target.files?.[0]
    if (file?.type.startsWith('image/')) {
      setSelectedImage(file)
    }
  }

  return (
    <form className="input-bar" onSubmit={submit}>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        onChange={handleFileChange}
        hidden
      />

      <button
        className="input-action"
        type="button"
        aria-label="Attach image"
        disabled={disabled}
        onClick={() => fileInputRef.current?.click()}
      >
        <ImagePlus size={19} strokeWidth={1.7} />
      </button>

      <label className="input-field-wrap">
        <span className="sr-only">Ask MAX anything</span>
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          disabled={disabled}
          placeholder={selectedImage ? selectedImage.name : 'Ask anything...'}
          autoComplete="off"
        />
      </label>

      <AnimatePresence mode="wait" initial={false}>
        {canSubmit ? (
          <motion.button
            key="send"
            className="mic-btn send-btn"
            type="submit"
            disabled={disabled}
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.8, opacity: 0 }}
            transition={btnTransition}
            aria-label="Send message"
          >
            <Send size={17} fill="currentColor" />
          </motion.button>
        ) : (
          <motion.button
            key="mic"
            className={`mic-btn ${isRecording ? 'recording' : ''}`}
            type="button"
            disabled={disabled}
            onPointerDown={(event) => {
              event.preventDefault()
              onMicPress?.()
            }}
            onPointerUp={(event) => {
              event.preventDefault()
              onMicRelease?.()
            }}
            onPointerCancel={onMicRelease}
            onPointerLeave={() => {
              if (isRecording) onMicRelease?.()
            }}
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.8, opacity: 0 }}
            transition={btnTransition}
            aria-label={isRecording ? 'Release to send voice message' : 'Hold to speak'}
          >
            <Mic size={21} strokeWidth={2} />
          </motion.button>
        )}
      </AnimatePresence>
    </form>
  )
}
